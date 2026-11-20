# OPEN-640: the legislature's own word for each bill link (PLAN-companion-lineage §5.1).
# Hand-written, matching 0049_billversiondocument_is_bill_version.py's style.
# A nullable column with no default: existing rows stay NULL, so on PostgreSQL this is a
# catalog-only change (no table rewrite) and no existing query changes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0051_billversiondocument_diff_baseline_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='relatedbill',
            name='ddp_relation_label',
            field=models.CharField(
                blank=True,
                help_text=(
                    "OPEN-640: the legislature's own word for this link, as its site shows it (\"Identical bill\", \"Similar\", "
                    "\"Compare\", \"companion bill(s) passed\"), kept beside relation_type, which stays DDP's two-value rule "
                    "(only what the legislature calls a companion is a companion). Written by a scraper that passes "
                    "add_related_bill(..., label=); null when the scraper has none, and on rows saved before this column existed."
                ),
                max_length=100,
                null=True,
            ),
        ),
    ]
