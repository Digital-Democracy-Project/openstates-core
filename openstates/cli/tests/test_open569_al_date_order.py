"""OPEN-569: Alabama's versions are ordered by date, and a stored document that is no longer a version
of its bill stays out of the comparisons.

From OPEN-569 the Alabama scraper dates every version from the action that carries its file. The notes
and dates below are HB 315's (2026RS) as that scraper reads ALISON's history: the House committee's
substitute (reported 03-12) was tabled on the floor, Tillman's substitute was adopted (03-17, roll call
895), the bill was engrossed the same day, and the Senate committee's substitute (04-02) was adopted on
the Senate floor (04-07). Texts are short stand-ins, one marker line per version.
"""

import pytest

from openstates.cli.text_extract import (
    recompute_bill_diff_order,
    recomputed_diffs_for_documents,
)
from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument
from openstates.utils.version_ordering import version_sort_key

AL = "ocd-jurisdiction/country:us/state:al/government"
TILLMAN = "Tillman 1st Substitute Offered"
SENATE_COMMITTEE = "Jefferson County Legislation 1st Substitute"
TABLED = "Reported Out of Committee House of Origin"

# (note, date) in true order
HB315 = [
    ("Introduced", "2026-01-22"),
    (TILLMAN, "2026-03-17"),
    ("Engrossed", "2026-03-17"),
    (SENATE_COMMITTEE, "2026-04-02"),
    ("Coleman 1st Amendment Offered", "2026-04-07"),
    ("Enrolled", "2026-04-08"),
]


def _key(note, date, state):
    return version_sort_key(note, date, (), "house", state)


def test_alabama_orders_hb315_by_date():
    shuffled = list(reversed(HB315))
    assert sorted(shuffled, key=lambda v: _key(*v, "Alabama")) == HB315


def test_without_the_date_rule_the_senate_substitute_ranks_before_engrossed():
    """The fault this fixes: by note alone both substitutes outrank Engrossed."""
    order = [n for n, _ in sorted(HB315, key=lambda v: _key(*v, None))]
    assert order.index(SENATE_COMMITTEE) < order.index("Engrossed")


def test_same_day_tie_goes_to_the_stage():
    assert _key(TILLMAN, "2026-03-17", "Alabama") < _key("Engrossed", "2026-03-17", "Alabama")


@pytest.mark.parametrize("state", [None, "United States", "Arizona", "Louisiana"])
def test_other_states_keep_the_stage_first_key(state):
    for note, date in HB315:
        assert _key(note, date, state) == version_sort_key(note, date, (), "house")


def test_undated_alabama_versions_keep_their_old_order():
    """Before the re-scrape every Alabama version is undated: the order is the stage order, as before."""
    undated = [(n, "") for n, _ in HB315]
    assert [n for n, _ in sorted(undated, key=lambda v: _key(*v, "Alabama"))] == [
        n for n, _ in sorted(undated, key=lambda v: _key(*v, None))
    ]


TEXT = "A BILL TO BE ENTITLED AN ACT\nBE IT ENACTED BY THE LEGISLATURE OF ALABAMA:\nSection 1. Vacant property.\n"


class Doc:
    def __init__(self, note, date, marker=None):
        self.version_note, self.version_date = note, date
        self.source_url = f"https://x.test/{note}-{date}.pdf"
        self.media_type = "application/pdf"
        self.raw_text = TEXT + f"Section 2. {marker or note}.\n"
        self.is_error = False
        self.diff_from_previous_version = None


def _diffs(docs, current):
    result = recomputed_diffs_for_documents(
        docs,
        jurisdiction_name="Alabama",
        is_bill=True,
        bill_identifier="HB 315",
        current_versions=current,
    )
    return {(d.version_note, d.version_date): diff for d, diff in result["changed"]}


def test_engrossed_is_compared_with_the_tillman_substitute():
    docs = [Doc(n, d) for n, d in reversed(HB315)]
    diffs = _diffs(docs, current=set(HB315))
    assert "-Section 2. Tillman 1st Substitute Offered." in diffs[("Engrossed", "2026-03-17")]
    assert "-Section 2. Engrossed." in diffs[(SENATE_COMMITTEE, "2026-04-02")]


def test_documents_that_are_not_current_versions_stay_out_of_the_lineage():
    """After the re-scrape: the undated copy of Engrossed saved before dates existed, and the tabled
    House committee substitute (now a document of the bill, not a version)."""
    docs = [Doc(n, d) for n, d in HB315]
    old_copy = Doc("Engrossed", "", marker="old engrossed copy")
    old_copy.diff_from_previous_version = "a stored diff"
    tabled = Doc(TABLED, "", marker="tabled committee substitute")
    diffs = _diffs(docs + [old_copy, tabled], current=set(HB315))
    assert diffs[("Engrossed", "")] is None  # its stored diff is cleared
    assert (TABLED, "") not in diffs  # no diff before, none now
    assert "-Section 2. Introduced." in diffs[(TILLMAN, "2026-03-17")]
    assert "tabled" not in diffs[(TILLMAN, "2026-03-17")]

    # without the current versions both would sit in the lineage (undated ranks first)
    everything = _diffs([Doc(n, d) for n, d in HB315] + [old_copy, tabled], current=None)
    assert "-Section 2. tabled committee substitute." in everything[("Introduced", "2026-01-22")]


@pytest.mark.django_db
def test_recompute_reads_the_bills_current_versions():
    bill = _make_bill(jid=AL, jurisdiction_name="Alabama")
    bill.identifier = "HB 315"
    bill.save()
    for note, date in [("Introduced", "2026-01-22"), (TILLMAN, "2026-03-17"), ("Engrossed", "2026-03-17")]:
        bill.versions.create(note=note, date=date)
    for doc in [
        Doc("Introduced", "2026-01-22"),
        Doc(TILLMAN, "2026-03-17"),
        Doc("Engrossed", "2026-03-17"),
        Doc(TABLED, "", marker="tabled committee substitute"),
    ]:
        BillVersionDocument.objects.create(
            bill=bill,
            version_note=doc.version_note,
            version_date=doc.version_date,
            source_url=doc.source_url,
            media_type=doc.media_type,
            raw_text=doc.raw_text,
        )
    diffs = {
        (d.version_note, d.version_date): diff
        for d, diff in recompute_bill_diff_order(bill)["changed"]
    }
    assert set(diffs) == {(TILLMAN, "2026-03-17"), ("Engrossed", "2026-03-17")}
    assert "-Section 2. Introduced." in diffs[(TILLMAN, "2026-03-17")]
    assert "-Section 2. Tillman 1st Substitute Offered." in diffs[("Engrossed", "2026-03-17")]


@pytest.mark.django_db
def test_archive_orders_alabama_versions_by_date():
    """The archive path: HB 315's dated versions, stored in reverse, are compared in date order."""
    bill = _make_bill(jid=AL, jurisdiction_name="Alabama")
    bill.identifier = "HB 315"
    bill.classification = ["bill"]
    bill.save()
    texts = {}
    for note, date in reversed(HB315):
        doc = Doc(note, date)
        bill.versions.create(note=note, date=date).links.create(url=doc.source_url, media_type=doc.media_type)
        texts[doc.source_url] = doc.raw_text
    _archive(bill, texts)
    diffs = {
        (d.version_note, d.version_date): d.diff_from_previous_version
        for d in BillVersionDocument.objects.filter(bill=bill)
    }
    assert "-Section 2. Tillman 1st Substitute Offered." in diffs[("Engrossed", "2026-03-17")]
    assert "-Section 2. Engrossed." in diffs[(SENATE_COMMITTEE, "2026-04-02")]


@pytest.mark.django_db
def test_remove_stale_documents_takes_the_undated_copy_of_a_dated_version():
    """After the re-scrape and archive: Engrossed is saved again under its date. The undated copy, same file, goes;
    the tabled substitute's row has no current copy and stays (the last copy of a file is never removed)."""
    from openstates.cli.text_extract import _stale_document_plan
    from openstates.data.models import Bill

    bill = _make_bill(jid=AL, jurisdiction_name="Alabama")
    bill.versions.create(note="Engrossed", date="2026-03-17")
    kw = dict(bill=bill, media_type="application/pdf", raw_text="text", archive_location="s3://b/k")
    old = BillVersionDocument.objects.create(
        version_note="Engrossed", version_date="", source_url="https://x.test/eng.pdf", sha256_hash="a" * 64, **kw
    )
    new = BillVersionDocument.objects.create(
        version_note="Engrossed", version_date="2026-03-17", source_url="https://x.test/eng.pdf", sha256_hash="a" * 64,
        **kw
    )
    BillVersionDocument.objects.create(
        version_note=TABLED, version_date="", source_url="https://x.test/sub.pdf", sha256_hash="b" * 64, **kw
    )
    rows, removals, kept = _stale_document_plan(Bill.objects.filter(id=bill.id))
    assert [(r["id"], c["id"]) for r, c in removals] == [(old.id, new.id)]
    assert kept == {"last copy": 1, "no file hash": 0}
