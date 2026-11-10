"""OPEN-518: a re-scrape keeps the label `relabel-non-bills` (OPEN-517) saved in extras.

The import replaces a stored bill's extras with the scraper's. A scraper never sends the saved old label,
so without the kept key a Massachusetts re-scrape would wipe it and `relabel-non-bills --undo` could no
longer put the scraper's original label back.
"""

import pytest

from openstates.data.models import Bill
from openstates.importers import BillImporter
from openstates.importers.tests.test_bill_importer import (
    create_jurisdiction,
    create_org,
)
from openstates.scrape import Bill as ScrapeBill
from openstates.utils.version_ordering import ORIGINAL_CLASSIFICATION_KEY


def _scraped(classification="report", **extras):
    bill = ScrapeBill(
        "SD 4338",
        "1900",
        "Middlesex County Restoration Center Commission Eighth Annual Report",
        chamber="lower",
        classification=classification,
    )
    bill.extras.update(extras)
    return bill.as_dict()


def _relabelled(**other_extras):
    """A stored item as `relabel-non-bills --commit` leaves it."""
    create_jurisdiction()
    create_org()
    BillImporter("jid").import_item(_scraped("proposed bill"))
    Bill.objects.update(
        classification=["report"],
        extras={ORIGINAL_CLASSIFICATION_KEY: ["proposed bill"], **other_extras},
    )


@pytest.mark.django_db
def test_a_rescrape_keeps_the_saved_old_label_and_the_new_label():
    _relabelled()

    _, what = BillImporter("jid").import_item(_scraped("report"))

    bill = Bill.objects.get()
    assert bill.classification == ["report"]
    assert bill.extras == {ORIGINAL_CLASSIFICATION_KEY: ["proposed bill"]}
    # nothing else changed, so the kept key does not turn every re-scrape into an update
    assert what == "noop"


@pytest.mark.django_db
def test_the_scraped_extras_still_replace_everything_else():
    _relabelled(stale="from an older scrape")

    _, what = BillImporter("jid").import_item(_scraped("report", fresh="from this one"))

    assert what == "update"
    assert Bill.objects.get().extras == {
        ORIGINAL_CLASSIFICATION_KEY: ["proposed bill"],
        "fresh": "from this one",
    }


@pytest.mark.django_db
def test_a_scrape_label_change_still_lands_and_the_saved_label_stays():
    _relabelled()

    BillImporter("jid").import_item(_scraped("order"))

    bill = Bill.objects.get()
    assert bill.classification == ["order"]
    assert bill.extras[ORIGINAL_CLASSIFICATION_KEY] == ["proposed bill"]


@pytest.mark.django_db
def test_a_bill_never_relabelled_gets_no_saved_label():
    create_jurisdiction()
    create_org()
    BillImporter("jid").import_item(_scraped("report"))
    BillImporter("jid").import_item(_scraped("report"))

    assert Bill.objects.get().extras == {}
