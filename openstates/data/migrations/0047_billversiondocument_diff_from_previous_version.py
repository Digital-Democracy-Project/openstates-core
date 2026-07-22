# PLAN-bill-document-provenance.md, Phase 1: diff_from_previous_version, added 2026-07-20.
# Hand-written, matching 0046_billversiondocument.py's style.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0046_billversiondocument'),
    ]

    operations = [
        migrations.AddField(
            model_name='billversiondocument',
            name='diff_from_previous_version',
            field=models.TextField(
                blank=True,
                help_text=(
                    "difflib.unified_diff() of this version's raw_text against the immediately-"
                    "preceding version's raw_text (added 2026-07-20). Null if no prior version "
                    "exists to diff against (first version ever archived, or a gap predating "
                    "this pipeline going live)."
                ),
                null=True,
            ),
        ),
    ]
