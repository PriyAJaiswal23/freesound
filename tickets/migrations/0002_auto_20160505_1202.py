# -*- coding: utf-8 -*-
# Generated by Django 1.9.5 on 2016-05-05 12:02
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticketcomment',
            name='moderator_only',
            field=models.BooleanField(default=False),
        ),
    ]
