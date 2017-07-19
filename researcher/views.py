import logging
logger = logging.getLogger(__name__)
import tarfile, tempfile

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import FormView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Min, Max

from researcher.forms import UploadArticlesForm, UploadSchemaForm
from researcher.forms import NLPArticlesForm
from researcher.forms import CreateProjectForm, CreateProjectDebugForm
from researcher.forms import EditProjectForm, EditProjectDebugForm
from researcher.forms import AddTasksForm

from data.document_importer import import_archive
from data.schema_importer import import_schema

from data.pybossa_api import create_or_update_remote_project_worker
from data.pybossa_api import delete_remote_project
from data.pybossa_api import generate_tasks_worker
from data.pybossa_api import generate_get_taskruns_worker
from data.nlp_exporter import generate_nlp_tasks_worker
from thresher.models import Article, Topic, UserProfile, Project

class IndexView(View):
    template_name = 'researcher/index.html'

    def get(self, request):
        # We need 'request' in the context so use render
        return render(request,
                      self.template_name,
                      {'projects': Project.objects.all().order_by('name')}
        )

class UploadArticlesView(PermissionRequiredMixin, View):
    form_class = UploadArticlesForm
    template_name = 'researcher/upload_article_form.html'
    login_url = reverse_lazy('admin:login')
    redirect_field_name = 'next'
    permission_required = u'thresher.add_article'

    def get(self, request):
        return render(
            request,
            self.template_name,
            {'form': self.form_class()}
        )

    def post(self, request):
        bound_form = self.form_class(request.POST, request.FILES)
        if bound_form.is_valid():
            f = request.FILES['article_archive_file']
            with_annotations = bound_form.cleaned_data["with_annotations"]
            logger.info("Request to import article archive %s, length %d" % (f.name, f.size))
            with tempfile.NamedTemporaryFile(delete=False) as archive_file:
                for chunk in f.chunks():
                    archive_file.write(chunk)
                archive_file.flush()
                logger.info("Archive copied to temp file %s: tar file format: %s"
                            % (archive_file.name, tarfile.is_tarfile(archive_file.name)))
                import_archive(archive_file.name, request.user.userprofile.id, with_annotations)

            return redirect('/admin/thresher/article/')
        else:
            return render(
                request,
                self.template_name,
                {'form': bound_form}
            )

class UploadSchemaView(PermissionRequiredMixin, View):
    form_class = UploadSchemaForm
    template_name = 'researcher/upload_schema_form.html'
    login_url = reverse_lazy('admin:login')
    redirect_field_name = 'next'
    permission_required = (
        u'thresher.add_topic',
        u'thresher.add_question',
        u'thresher.add_answer',
    )

    def get(self, request):
        return render(
            request,
            self.template_name,
            {'form': self.form_class()}
        )

    def post(self, request):
        bound_form = self.form_class(request.POST, request.FILES)
        if bound_form.is_valid():
            f = request.FILES['schema_file']
            logger.info("Request to import schema %s, length %d" % (f.name, f.size))
            with tempfile.NamedTemporaryFile(delete=True) as schema_file:
                for chunk in f.chunks():
                    schema_file.write(chunk)
                logger.info("Schema copied to temp file %s" % schema_file.name)
                schema_file.seek(0)
                schema_contents = schema_file.read()
                import_schema.delay(schema_contents, request.user.userprofile.id)

            return redirect('/admin/thresher/topic/')
        else:
            return render(
                request,
                self.template_name,
                {'form': bound_form}
            )


class NLPArticlesView(PermissionRequiredMixin, View):
    form_class = NLPArticlesForm
    template_name = 'researcher/nlp_articles.html'
    login_url = reverse_lazy('admin:login')
    redirect_field_name = 'next'
    # Put some requirements on form access.
    permission_required = (
        u'thresher.add_nlphints',
        u'thresher.change_nlphints',
        u'thresher.delete_nlphints',
    )

    def get(self, request):
        agg = Article.objects.aggregate(Min('id'), Max('id'))
        initial = { 'starting_article_id': agg['id__min'],
                    'ending_article_id': agg['id__max'],
        }
        return render(
            request,
            self.template_name,
            {'form': self.form_class(initial=initial),
             'user': request.user,
            }
        )

    def post(self, request):
        bound_form = self.form_class(request.POST)
        if request.user.is_authenticated and bound_form.is_valid():
            profile_id = request.user.userprofile.id

            starting_article_id = bound_form.cleaned_data['starting_article_id']
            ending_article_id = bound_form.cleaned_data['ending_article_id']
            articles = Article.objects.filter(
                id__gte=starting_article_id,
                id__lte=ending_article_id
            ).order_by("id")
            article_ids = list(articles.values_list('id', flat=True))
            logger.info("%d articles in selected range" % len(article_ids))

            generate_nlp_tasks_worker.delay(
                profile_id=profile_id,
                article_ids=article_ids,
            )

            return redirect(reverse('rq_home'))
        else:
            return render(
                request,
                self.template_name,
                {'form': bound_form,
                 'user': request.user
                }
            )

class CreateProjectView(PermissionRequiredMixin, View):
    template_name = 'researcher/create_project.html'
    login_url = reverse_lazy('admin:login')
    redirect_field_name = 'next'
    # Create a local Project record to with the desired config for the remote.
    # If remote creation fails, the Pybossa id and related fields will not
    # be filled in. The Project record won't be deleted, as the update screen
    # can be used to attempt to create the remote project again.
    permission_required = (
        u'thresher.add_project',
        u'thresher.change_project',
        u'thresher.add_task',
        u'thresher.change_task',
    )

    def __init__(self, **kwargs):
        super(CreateProjectView, self).__init__(**kwargs)
        self.form_class = CreateProjectForm

    def get(self, request):
        agg = Article.objects.aggregate(Min('id'), Max('id'))
        profile = request.user.userprofile
        # Developers can add "?debugPresenter=true" to the URL to
        # have the task presenter set to a script tag pointed at
        # the developer's localhost running 'npm run dev' for
        # easy debugging in the Pybossa page context.
        debug_presenter = request.GET.get("debugPresenter", False)
        initial = { 'starting_article_id': agg['id__min'],
                    'ending_article_id': agg['id__max'],
                    'pybossa_url': profile.pybossa_url,
                    'pybossa_api_key': profile.pybossa_api_key,
                    'debug_presenter': debug_presenter
        }
        if debug_presenter:
            # Show the field that allows editing the debug server
            self.form_class = CreateProjectDebugForm
        else:
            self.form_class = CreateProjectForm
        return render(
            request,
            self.template_name,
            {'form': self.form_class(initial=initial),
             'user': request.user,
            }
        )

    def post(self, request):
        bound_form = self.form_class(request.POST)
        if request.user.is_authenticated and bound_form.is_valid():
            cleaned_data = bound_form.cleaned_data
            starting_article_id = cleaned_data['starting_article_id']
            ending_article_id = cleaned_data['ending_article_id']
            articles = Article.objects.filter(
                id__gte=starting_article_id,
                id__lte=ending_article_id
            ).order_by("id")
            article_ids = list(articles.values_list('id', flat=True))
            logger.info("%d articles in selected range" % len(article_ids))

            topic_ids = list(cleaned_data['topics']
                             .values_list('id', flat=True))
            logger.info("%d topics selected" % len(topic_ids))
            task_config = { 'topic_ids': topic_ids }

            # Need to catch failure if attempting to create a duplicate name
            # or short_name on remote. Project model sets unique_together.
            project = Project.objects.create(
                owner_profile = request.user.userprofile,
                name = cleaned_data['name'],
                short_name = cleaned_data['short_name'],
                description = cleaned_data['description'],
                task_type = cleaned_data['task_type'],
                task_config = task_config,
                pybossa_url = cleaned_data['pybossa_url'],
                pybossa_api_key = cleaned_data['pybossa_api_key']
            )
            project_id = project.id
            job = None
            debug_presenter = cleaned_data['debug_presenter']
            debug_server = cleaned_data['debug_server']
            job = create_or_update_remote_project_worker.delay(
                project_id=project_id,
                debug_presenter=debug_presenter,
                debug_server=debug_server
            )
            generate_tasks_worker.delay(
                project_id=project_id,
                article_ids=article_ids,
                topic_ids=topic_ids,
                depends_on=job
            )

            return redirect(reverse('rq_home'))
        else:
            return render(
                request,
                self.template_name,
                {'form': bound_form,
                 'user': request.user
                }
            )


class EditProjectView(PermissionRequiredMixin, UpdateView):
    template_name = 'researcher/edit_project.html'
    queryset = Project.objects.all()
    login_url = reverse_lazy('admin:login')
    redirect_field_name = 'next'
    permission_required = (
        u'thresher.add_project',
        u'thresher.change_project',
        u'thresher.add_task',
        u'thresher.change_task',
    )
    success_url = reverse_lazy('rq_home')

    def get_form_class(self):
        debug_presenter = self.request.GET.get("debugPresenter", False)
        if debug_presenter:
            # Show the field that allows editing the debug server
            return EditProjectDebugForm
        else:
            return EditProjectForm

    def get_context_data(self, **kwargs):
        context = super(EditProjectView, self).get_context_data(**kwargs)
        context.update({
            'user': self.request.user,
        })
        return context

    def get_initial(self):
        # Developers can add "?debugPresenter=true" to the URL to
        # have the task presenter set to a script tag pointed at
        # the developer's localhost running 'npm run dev' for
        # easy debugging in the Pybossa page context.
        debug_presenter = self.request.GET.get('debugPresenter', False)
        initial = { 'debug_presenter': debug_presenter }
        return initial

    def form_valid(self, form):
        # need to save Project updates promptly as the new data
        # will be needed in the worker process that updates the
        # remote project.
        response = super(EditProjectView, self).form_valid(form)

        project = self.get_object()
        debug_presenter = form.cleaned_data['debug_presenter']
        debug_server = form.cleaned_data['debug_server']
        job = create_or_update_remote_project_worker.delay(
            project_id=project.id,
            debug_presenter=debug_presenter,
            debug_server=debug_server
        )
        return response


class AddTasksView(PermissionRequiredMixin, FormView):
    template_name = 'researcher/add_project_tasks.html'
    form_class = AddTasksForm
    login_url = reverse_lazy('admin:login')
    redirect_field_name = 'next'
    permission_required = (
        u'thresher.add_project',
        u'thresher.change_project',
        u'thresher.add_task',
        u'thresher.change_task',
    )
    success_url = reverse_lazy('rq_home')

    def get_initial(self):
        project_id = self.kwargs['pk']
        agg = Article.objects.aggregate(Min('id'), Max('id'))
        initial = {
            'project_id': project_id,
            'starting_article_id': agg['id__min'],
            'ending_article_id': agg['id__max'],
        }
        return initial

    def get_context_data(self, **kwargs):
        context = super(AddTasksView, self).get_context_data(**kwargs)
        project_id = self.kwargs['pk']
        project = Project.objects.get(pk=project_id)
        context.update({
            'project': project,
        })
        return context

    def form_valid(self, form):
        cleaned_data = form.cleaned_data
        starting_article_id = cleaned_data['starting_article_id']
        ending_article_id = cleaned_data['ending_article_id']
        articles = Article.objects.filter(
            id__gte=starting_article_id,
            id__lte=ending_article_id
        ).order_by("id")
        article_ids = list(articles.values_list('id', flat=True))
        logger.info("%d articles in selected range" % len(article_ids))

        project_id = cleaned_data['project_id']
        project = Project.objects.get(pk=project_id)
        topic_ids = project.task_config['topic_ids']
        logger.info("%d topics selected" % len(topic_ids))

        job = None
        generate_tasks_worker.delay(
            project_id=project_id,
            article_ids=article_ids,
            topic_ids=topic_ids,
            depends_on=job
        )

        return super(AddTasksView, self).form_valid(form)


class RetrieveTaskrunsView(PermissionRequiredMixin, View):
    template_name = 'researcher/retrieve_taskruns.html'
    login_url = reverse_lazy('admin:login')
    redirect_field_name = 'next'
    # Put a basic requirement on form access.
    permission_required = (
        u'thresher.add_articlehighlight',
        u'thresher.change_articlehighlight',
        u'thresher.delete_articlehighlight',
        u'thresher.add_highlightgroup',
        u'thresher.change_highlightgroup',
        u'thresher.delete_highlightgroup',
     )

    def get(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        return render(request, self.template_name, {'project': project})

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        job = generate_get_taskruns_worker.delay(request.user.userprofile.id, project.id)
        return redirect(reverse('rq_home'))


class RemoteProjectDeleteView(PermissionRequiredMixin, View):
    template_name = 'researcher/confirm_remote_project_delete.html'
    login_url = reverse_lazy('admin:login')
    redirect_field_name = 'next'
    # We are deleting remotely, so correct Pybossa API key must be set
    # Put a basic requirement on form access.
    permission_required = ( u'thresher.delete_project',
                            u'thresher.delete_task')

    def get(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        return render(request, self.template_name, {'project': project})

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        job = delete_remote_project(project)
        return redirect(reverse('researcher:index'))
