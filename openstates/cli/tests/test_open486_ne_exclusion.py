"""OPEN-486: Nebraska's amendment papers take no part in the diff lineage (end to end).

The note shapes are in test_version_ordering.py; this proves the archiver acts on them: an AM paper
gets no diff and is not the baseline the next real version diffs against.
"""

import pytest

from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument

NE = "ocd-jurisdiction/country:us/state:ne/government"

INTRODUCED = (
    "A BILL FOR AN ACT relating to taxes.\nSection 1. A court may consider the award.\n"
)
AMENDMENT_PAPER = (
    "AM12\nStrike the enacting clause and insert a different bill entirely.\n"
)
FINAL_READING = "A BILL FOR AN ACT relating to taxes.\nSection 1. A court shall consider the award.\n"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "paper_note", ["Judiciary AM12", "Bosn FA3", "Enrollment and Review ER5"]
)
def test_a_nebraska_amendment_paper_gets_no_diff_and_is_not_a_baseline(paper_note):
    bill = _make_bill(jid=NE, jurisdiction_name="Nebraska")
    bill.classification = ["bill"]
    bill.save()
    texts = {
        "Introduced": INTRODUCED,
        paper_note: AMENDMENT_PAPER,
        "Final Reading": FINAL_READING,
    }
    urls = {note: f"https://x.test/ne/{note.replace(' ', '_')}.pdf" for note in texts}
    for note in (
        "Final Reading",
        paper_note,
        "Introduced",
    ):  # out of row order on purpose
        bill.versions.create(note=note, date="").links.create(
            url=urls[note], media_type="application/pdf"
        )
    _archive(bill, {urls[note]: text for note, text in texts.items()})
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    assert docs[paper_note].diff_from_previous_version is None
    # Final Reading diffs against Introduced, the last real version, not against the paper
    diff = docs["Final Reading"].diff_from_previous_version
    assert "may consider" in diff and "shall consider" in diff
    assert "enacting clause" not in diff
    # the paper stays archived with its text
    assert docs[paper_note].raw_text == AMENDMENT_PAPER
