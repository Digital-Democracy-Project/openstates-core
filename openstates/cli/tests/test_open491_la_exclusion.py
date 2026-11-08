"""OPEN-491: Louisiana's amendment papers take no part in the diff lineage (end to end).

The note shapes are in test_version_ordering.py; this proves the archiver acts on them: an amendment
paper gets no diff and is not the baseline the next real version diffs against.
"""

import pytest

from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

LA = "ocd-jurisdiction/country:us/state:la/government"

ORIGINAL = "AN ACT relating to bail.\nSection 1. A court may consider the award.\n"
AMENDMENT_PAPER = "AMENDMENT NO. 1\nStrike the enacting clause and insert a different bill entirely.\n"
ENGROSSED = "AN ACT relating to bail.\nSection 1. A court shall consider the award.\n"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "paper_note",
    [
        "House Committee Amendment, #4774, INS, Adopted",
        "Senate Floor Amendment, #512, Owen, Robert, Adopted",
        "Senate Floor Bureau Note, #1238, Fesi, Adopted",
        # OPEN-511: a conference committee report is left out the same way
        "Conference Committee Report, #4594, House Adopted, Senate Adopted",
    ],
)
def test_a_louisiana_amendment_paper_gets_no_diff_and_is_not_a_baseline(paper_note):
    bill = _make_bill(jid=LA, jurisdiction_name="Louisiana")
    bill.classification = ["bill"]
    bill.save()
    texts = {
        "HB374 Original": ORIGINAL,
        paper_note: AMENDMENT_PAPER,
        "HB374 Engrossed": ENGROSSED,
    }
    urls = {note: f"https://x.test/la/{abs(hash(note))}.pdf" for note in texts}
    for note in (
        "HB374 Engrossed",
        paper_note,
        "HB374 Original",
    ):  # out of row order on purpose
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    _archive(bill, {urls[note]: text for note, text in texts.items()})
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    assert docs[paper_note].diff_from_previous_version is None
    # Engrossed diffs against Original, the last real version, not against the paper
    diff = docs["HB374 Engrossed"].diff_from_previous_version
    assert "may consider" in diff and "shall consider" in diff
    assert "enacting clause" not in diff
    # the paper stays archived with its text
    assert docs[paper_note].raw_text == AMENDMENT_PAPER
