# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-08-05 16:26
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('thresher', '0006_auto_20170805_1625'),
    ]

    operations = [
        migrations.AddField(
            model_name='submittedanswer',
            name='answer',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='submitted_answers', to='thresher.Answer'),
        ),
    ]
