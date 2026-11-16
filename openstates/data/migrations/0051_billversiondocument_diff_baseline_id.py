# OPEN-539: the document each stored comparison was made against (PLAN-state-specific-handling
# §4.4, "the one new stored value"). Hand-written, matching 0049_billversiondocument_is_bill_version.py:
# a nullable column with no default and no index or constraint, so on PostgreSQL this is a
# catalog-only change (no table rewrite) and no existing query changes. No backfill here: existing
# comparisons are stamped per state by `os-text-extract recompute-diff-order <state> --baseline-only`.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0050_billdocumentnotfound'),
    ]

    operations = [
        migrations.AddField(
            model_name='billversiondocument',
            name='diff_baseline_id',
            field=models.IntegerField(
                blank=True,
                help_text=(
                    "OPEN-539: the id of the ddp_bill_version_document row this row's "
                    "diff_from_previous_version was actually computed against (same bill), written in "
                    "the same statement as the comparison by the archiver and by "
                    "`recompute-diff-order`. Null when there is no stored comparison (a bill's first "
                    "version, a not-a-version paper, an unknown stage), and on a comparison stored "
                    "before this column existed until `recompute-diff-order <state> --baseline-only "
                    "--commit` stamps it. A plain id, not a foreign key: no constraint or index on "
                    "the table; a removed baseline row clears it (archiver retry, "
                    "remove-stale-documents)."
                ),
                null=True,
            ),
        ),
    ]
