# OPEN-492: whether a saved document is a real bill version, decided once by the archiver.
# Hand-written, matching 0047_billversiondocument_diff_from_previous_version.py's style.
# A nullable column with no default: existing rows stay NULL ("not decided yet"), so on
# PostgreSQL this is a catalog-only change (no table rewrite) and no existing query changes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0048_merge_20260821_1955'),
    ]

    operations = [
        migrations.AddField(
            model_name='billversiondocument',
            name='is_bill_version',
            field=models.BooleanField(
                blank=True,
                help_text=(
                    "OPEN-492: whether this document is a real bill version (True) or a "
                    "procedural paper such as an amendment list or comparison sheet (False), "
                    "decided once by the archiver from openstates/utils/version_ordering.py's "
                    "per-state rules (is_bill_version()) and stored here so readers never keep "
                    "a list of their own. Null means not decided yet (a row saved before this "
                    "column existed, until `recompute-diff-order <state> --commit` fills it)."
                ),
                null=True,
            ),
        ),
    ]
