# PLAN-bill-document-provenance.md, Phase 1: permanent per-version bill document archive.
# Hand-written (not generated via makemigrations against a live DB) — matches Django's standard
# CreateModel migration format used by every other migration in this directory.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0045_auto_20240705_1812'),
    ]

    operations = [
        migrations.CreateModel(
            name='BillVersionDocument',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version_note', models.CharField(max_length=300)),
                ('version_date', models.CharField(max_length=10)),
                ('source_url', models.URLField(max_length=2000)),
                ('media_type', models.CharField(blank=True, max_length=100)),
                ('raw_text', models.TextField(default='')),
                ('is_error', models.BooleanField(default=False)),
                ('sha256_hash', models.CharField(blank=True, max_length=64, null=True)),
                ('archive_location', models.CharField(
                    blank=True,
                    help_text='S3 URI once uploaded to Glacier Deep Archive (Phase 2). Null until archived.',
                    max_length=500,
                    null=True,
                )),
                ('archived_at', models.DateTimeField(blank=True, null=True)),
                ('ocr_applied', models.BooleanField(default=False)),
                ('ocr_version', models.CharField(blank=True, max_length=100, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('bill', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='version_documents',
                    to='data.bill',
                )),
            ],
            options={
                'db_table': 'ddp_bill_version_document',
            },
        ),
        migrations.AddIndex(
            model_name='billversiondocument',
            index=models.Index(
                fields=['bill', 'version_note', 'version_date'],
                name='ddp_bill_ve_bill_id_3829d1_idx',
            ),
        ),
        migrations.AddConstraint(
            model_name='billversiondocument',
            constraint=models.UniqueConstraint(
                fields=('bill', 'version_note', 'version_date', 'source_url'),
                name='unique_bill_version_document',
            ),
        ),
    ]
