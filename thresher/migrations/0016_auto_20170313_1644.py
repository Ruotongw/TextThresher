# -*- coding: utf-8 -*-
# Generated by Django 1.10.5 on 2017-03-13 16:44
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('thresher', '0015_auto_20170303_0048'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='answer',
            name='next_question',
        ),
        migrations.AddField(
            model_name='answer',
            name='next_questions',
            field=models.TextField(default=b'[]'),
        ),
    ]
