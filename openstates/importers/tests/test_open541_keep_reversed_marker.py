"""OPEN-541: a re-scrape keeps the marker `reverse-action-order` sets on a bill it reversed.

The pass runs after the state's scraper already stores actions oldest first, so a re-scraped bill is still
oldest first; without the kept marker a later run would no longer know it was reversed.
"""

import pytest

from openstates.data.models import Bill, BillAction
from openstates.importers import BillImporter
from openstates.importers.tests.test_bill_importer import (
    create_jurisdiction,
    create_org,
)
from openstates.scrape import Bill as ScrapeBill
from openstates.utils.version_ordering import ACTIONS_REVERSED_KEY

# Louisiana HB 100 (2026), oldest first, as the fixed scraper yields it
OLDEST_FIRST = [
    ("2026-02-09", "Prefiled."),
    ("2026-03-09", "Read by title, under the rules, referred to the Committee."),
    ("2026-03-25", "Becomes HB 1009."),
]


def _scraped(actions):
    bill = ScrapeBill("HB 100", "1900", "A test bill", chamber="lower")
    for date, description in actions:
        bill.add_action(description, date, chamber="lower")
    return bill.as_dict()


@pytest.mark.django_db
def test_a_rescrape_in_the_new_order_keeps_the_marker_and_changes_nothing():
    create_jurisdiction()
    create_org()
    # stored newest first, then reversed by the pass
    BillImporter("jid").import_item(_scraped(list(reversed(OLDEST_FIRST))))
    actions = list(BillAction.objects.order_by("order"))
    for action, order in zip(reversed(actions), range(len(actions))):
        action.order = order
        action.save()
    Bill.objects.update(extras={ACTIONS_REVERSED_KEY: True})

    _, what = BillImporter("jid").import_item(_scraped(OLDEST_FIRST))

    assert what == "noop"
    assert Bill.objects.get().extras == {ACTIONS_REVERSED_KEY: True}
    assert [a.description for a in BillAction.objects.order_by("order")] == [
        d for _, d in OLDEST_FIRST
    ]
