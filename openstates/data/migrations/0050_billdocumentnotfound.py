# OPEN-506: bill document addresses the site answered "not found", so the archiver of an
# opted-in state (Massachusetts) stops asking for them on every run.
# Hand-written, matching 0049_billversiondocument_is_bill_version.py's style: `makemigrations`
# also picks up unrelated, pre-existing model drift (a vote index, help texts) that is not this
# ticket's to ship. A new, empty table: no existing table or query changes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0049_billversiondocument_is_bill_version'),
    ]

    operations = [
        migrations.CreateModel(
            name='BillDocumentNotFound',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_url', models.URLField(max_length=2000, unique=True)),
                ('first_not_found_at', models.DateTimeField()),
                ('last_checked_at', models.DateTimeField()),
            ],
            options={
                'db_table': 'ddp_bill_document_not_found',
            },
        ),
    ]
