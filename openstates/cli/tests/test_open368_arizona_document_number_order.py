"""OPEN-368: Arizona's "HOUSE - ..." / "SENATE - ..." committee and floor-action versions tie on
one note_stage() rank and have no date, so their diff order used to be whatever order the database
returned. Their links carry the site's document number (GetDocumentPdf/<id>), which rises with
publication date (30-bill spot check, 0 contradictions), and now breaks the tie.
"""

import pytest

from openstates.cli.text_extract import recomputed_diffs_for_documents
from openstates.data.models import BillVersionDocument
from openstates.utils.version_ordering import document_number, version_sort_key

from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill

AZ_JID = "ocd-jurisdiction/country:us/state:az/government"


def _az(doc_id):
    return f"https://apps.azleg.gov/BillStatus/GetDocumentPdf/{doc_id}"


def test_document_number_reads_the_azleg_id_and_nothing_else():
    assert document_number([_az(537487)]) == 537487
    assert document_number(["https://apps.azleg.gov/BillStatus/GetDocumentPdf/42.pdf"]) == 42
    # smallest wins when a version has several links
    assert document_number(["https://x.test/a.pdf", _az(9), _az(5)]) == 5
    assert document_number([]) == 0
    assert document_number([None, ""]) == 0
    assert document_number(["https://legis.example/GetDocumentPdf/77"]) == 0
    assert document_number(["https://www.azleg.gov/legtext/57leg/2R/bills/HB1.pdf"]) == 0


def test_tied_arizona_versions_sort_by_document_number():
    low = version_sort_key("HOUSE - Education", "", [_az(537487)])
    high = version_sort_key("HOUSE - Appropriations", "", [_az(537720)])
    assert low[:3] == high[:3]  # they do tie on stage, date and ordinal
    assert low < high
    # a house and a senate action document are ordered by number too, not by chamber
    assert version_sort_key("SENATE - Judiciary", "", [_az(540756)]) < version_sort_key(
        "HOUSE - Floor Amend to Engrossed Bill - passed", "", [_az(541861)]
    )


def test_urls_do_not_change_the_order_of_anything_with_a_distinct_rank():
    notes = [
        "Introduced Version",
        "House Engrossed Version",
        "Conference Committee",
        "HOUSE - Education",
        "Chaptered Version",
    ]
    with_urls = sorted(notes, key=lambda n: version_sort_key(n, "", [_az(900)]))
    without = sorted(notes, key=lambda n: version_sort_key(n, ""))
    assert with_urls == without == notes


def test_versions_without_a_number_are_unaffected():
    assert version_sort_key("Introduced", "") == version_sort_key("Introduced", "", [])
    assert version_sort_key("Introduced", "", ["https://x.test/a.pdf"])[:3] == (0, "", 0.0)


INTRO = "Section 1. A county may adopt an ordinance.\n"
EDUCATION = "Section 1. A county may adopt an ordinance on schools.\n"
APPROPRIATIONS = "Section 1. A county may adopt an ordinance on schools and roads.\n"


def _bill_created_in_the_wrong_order():
    bill = _make_bill(jid=AZ_JID, jurisdiction_name="Arizona")
    bill.classification = ["bill"]
    bill.save()
    v = bill.versions.create(note="Introduced Version", date="")
    v.links.create(url="https://x.test/intro.pdf", media_type="application/pdf")
    # Appropriations (higher number) is stored BEFORE Education (lower number).
    v = bill.versions.create(note="HOUSE - Appropriations", date="")
    v.links.create(url=_az(537720), media_type="application/pdf")
    v = bill.versions.create(note="HOUSE - Education", date="")
    v.links.create(url=_az(537487), media_type="application/pdf")
    return bill


TEXTS = {
    "https://x.test/intro.pdf": INTRO,
    _az(537720): APPROPRIATIONS,
    _az(537487): EDUCATION,
}


@pytest.mark.django_db
def test_archive_diffs_each_action_document_against_the_one_with_the_lower_number():
    bill = _bill_created_in_the_wrong_order()
    _archive(bill, TEXTS)

    education = BillVersionDocument.objects.get(bill=bill, version_note="HOUSE - Education")
    appropriations = BillVersionDocument.objects.get(
        bill=bill, version_note="HOUSE - Appropriations"
    )
    assert "-Section 1. A county may adopt an ordinance.\n" in education.diff_from_previous_version
    assert "+Section 1. A county may adopt an ordinance on schools." in (
        education.diff_from_previous_version
    )
    assert "-Section 1. A county may adopt an ordinance on schools.\n" in (
        appropriations.diff_from_previous_version
    )
    assert "on schools and roads" in appropriations.diff_from_previous_version


@pytest.mark.django_db
def test_recompute_agrees_with_archive_for_arizona_action_documents():
    bill = _bill_created_in_the_wrong_order()
    _archive(bill, TEXTS)
    docs = list(BillVersionDocument.objects.filter(bill=bill).order_by("id"))

    result = recomputed_diffs_for_documents(docs, jurisdiction_name="Arizona", is_bill=True)

    assert result["changed"] == []
    assert len(result["unchanged"]) == len(docs)


def test_a_tied_version_without_a_number_sorts_before_numbered_ones():
    """Pinned, not relied on: the replica has no such pair (all 1,732 HOUSE-/SENATE- link rows
    carry a number), but if one appears it sorts first among the tie, ahead of every numbered
    version, and several unnumbered ones keep the order they came in (sorted() is stable)."""
    numbered = version_sort_key("HOUSE - Education", "", [_az(537487)])
    unnumbered = version_sort_key("HOUSE - Appropriations", "", ["https://x.test/a.pdf"])
    assert unnumbered < numbered
    notes = ["HOUSE - B", "HOUSE - A"]
    assert sorted(notes, key=lambda n: version_sort_key(n, "", [])) == notes
