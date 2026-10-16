"""OPEN-382: Arizona's House/Senate Engrossed Versions sorted before every "HOUSE - ..." /
"SENATE - ..." action document, so committee texts that precede a chamber's passage were diffed
against that chamber's later Engrossed Version. They are now placed by the bill's origin chamber:
origin action documents, origin Engrossed, other chamber's action documents, its Engrossed.
"""

import pytest

from openstates.cli.text_extract import recomputed_diffs_for_documents
from openstates.data.models import BillVersionDocument
from openstates.utils.version_ordering import (
    note_stage,
    origin_chamber,
    version_sort_key,
)

from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill

AZ_JID = "ocd-jurisdiction/country:us/state:az/government"


def _order(notes, origin):
    return sorted(notes, key=lambda n: version_sort_key(n, "", [], origin))


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("HB 2014", "house"),
        ("HCR 2040", "house"),
        ("hb2014", "house"),
        ("SB 1649", "senate"),
        ("SCR 1049", "senate"),
        ("SJR 1001", "senate"),
        ("", None),
        (None, None),
        ("CHAPTER 5", None),
        ("H", None),
    ],
)
def test_origin_chamber_from_the_identifier(identifier, expected):
    assert origin_chamber(identifier) == expected


HOUSE_BILL_NOTES = [
    "Introduced Version",
    "HOUSE - Natural Resources, Energy & Water",
    "HOUSE - Floor Amend to Bill - Griffin - passed",
    "House Engrossed Version",
    "HOUSE - Floor Amend to Engrossed Bill - Rivero - passed",
    "SENATE - Natural Resources",
    "SENATE - Mesnard flr amend (ref Bill) adopted",
    "Senate Engrossed Version (03/09/2026)",
    "SENATE - Mesnard flr amend (ref Senate Engrossed House Bill) adopted",
    "Conference Committee",
    "Chaptered Version",
]


def test_house_bill_orders_house_then_senate_with_each_engrossed_in_between():
    shuffled = HOUSE_BILL_NOTES[::-1]
    ordered = _order(shuffled, "house")
    # ties inside a band (e.g. the two committee documents) are broken by document number
    # elsewhere, so compare band by band

    def band(n):
        return version_sort_key(n, "", [], "house")[:3]

    assert [band(n) for n in ordered] == sorted(band(n) for n in HOUSE_BILL_NOTES)
    position = {n: i for i, n in enumerate(ordered)}
    assert position["Introduced Version"] == 0
    assert position["Chaptered Version"] == len(ordered) - 1
    assert position["Conference Committee"] == len(ordered) - 2
    assert (
        position["HOUSE - Natural Resources, Energy & Water"]
        < position["House Engrossed Version"]
        < position["HOUSE - Floor Amend to Engrossed Bill - Rivero - passed"]
        < position["SENATE - Natural Resources"]
        < position["Senate Engrossed Version (03/09/2026)"]
        < position["SENATE - Mesnard flr amend (ref Senate Engrossed House Bill) adopted"]
        < position["Conference Committee"]
    )
    assert (
        position["HOUSE - Floor Amend to Bill - Griffin - passed"]
        < position["House Engrossed Version"]
    )
    assert (
        position["SENATE - Mesnard flr amend (ref Bill) adopted"]
        < position["Senate Engrossed Version (03/09/2026)"]
    )


def test_senate_bill_mirrors_it():
    notes = [
        "Introduced Version",
        "SENATE - Judiciary",
        "Senate Engrossed Version",
        "HOUSE - Judiciary",
        "House Engrossed Version",
        "HOUSE - Floor Amend to Engrossed Bill - Bliss - passed",
        "Chaptered Version",
    ]
    assert _order(notes[::-1], "senate") == notes
    assert _order(notes[::-1], "house") != notes  # the origin is what decides


def test_nothing_changes_without_an_origin_or_for_other_notes():
    notes = ["Introduced Version", "Conference Committee", "Chaptered Version"]
    assert [version_sort_key(n, "", [], "house") for n in notes] == [
        version_sort_key(n, "", []) for n in notes
    ]
    # no origin: today's placement (Engrossed first among the tie), exactly as before
    assert version_sort_key("House Engrossed Version", "", []) < version_sort_key(
        "HOUSE - Education", "", []
    )
    # other jurisdictions' notes never match
    for n in ("Engrossed", "Enrolled", "Substitute #2", "Senate Amendment 1"):
        assert version_sort_key(n, "", [], "house") == version_sort_key(n, "", [])


def test_every_placed_note_keeps_its_stage():
    for n in HOUSE_BILL_NOTES:
        assert version_sort_key(n, "", [], "house")[0] == note_stage(n)[0]


INTRO = "Section 1. A county may adopt an ordinance.\n"
COMMITTEE = "Section 1. A county may adopt an ordinance on schools.\n"
ENGROSSED = "Section 1. A county may adopt an ordinance on schools and roads.\n"


def _bill(identifier):
    bill = _make_bill(jid=AZ_JID, jurisdiction_name="Arizona")
    bill.identifier = identifier
    bill.classification = ["bill"]
    bill.save()
    # stored in the order the database used to hand back: Engrossed first, committee text last
    v = bill.versions.create(note="Introduced Version", date="")
    v.links.create(url="https://x.test/intro.pdf", media_type="application/pdf")
    v = bill.versions.create(note="House Engrossed Version", date="")
    v.links.create(url="https://x.test/hb1h.pdf", media_type="application/pdf")
    v = bill.versions.create(note="HOUSE - Education", date="")
    v.links.create(
        url="https://apps.azleg.gov/BillStatus/GetDocumentPdf/537487",
        media_type="application/pdf",
    )
    return bill


TEXTS = {
    "https://x.test/intro.pdf": INTRO,
    "https://x.test/hb1h.pdf": ENGROSSED,
    "https://apps.azleg.gov/BillStatus/GetDocumentPdf/537487": COMMITTEE,
}


@pytest.mark.django_db
def test_archive_diffs_the_engrossed_version_against_the_committee_text():
    bill = _bill("HB 2014")
    _archive(bill, TEXTS)

    committee = BillVersionDocument.objects.get(bill=bill, version_note="HOUSE - Education")
    engrossed = BillVersionDocument.objects.get(bill=bill, version_note="House Engrossed Version")
    assert "-Section 1. A county may adopt an ordinance." in committee.diff_from_previous_version
    assert "-Section 1. A county may adopt an ordinance on schools." in (
        engrossed.diff_from_previous_version
    )
    assert "on schools and roads" in engrossed.diff_from_previous_version


@pytest.mark.django_db
def test_recompute_agrees_with_archive_for_an_arizona_house_bill():
    bill = _bill("HB 2014")
    _archive(bill, TEXTS)
    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))

    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Arizona", is_bill=True, bill_identifier=bill.identifier
    )

    assert result["changed"] == []
    assert len(result["unchanged"]) == len(docs)
