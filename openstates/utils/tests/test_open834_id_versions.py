"""OPEN-834: Idaho's amendment papers are not versions of the bill (openstates/ddp_states/id.py).

The names are every Idaho version name on the Mac replica (2026 session, 924 documents, 2026-11-24), as the scraper
writes them (scrapers/id/bills.py); the texts are stored amendment papers (H0730A1.pdf, H0542A1.pdf, H0678A1.pdf).
"""
import pytest  # type: ignore
from openstates.utils.version_ordering import (
    STAGE_UNKNOWN,
    is_bill_version,
    note_stage,
    version_sort_key,
)

ID_VERSION_NOTES = ["Bill Text", "Engrossment 1", "Engrossment 2"]

ID_PAPER_NOTES = ["Amendment", "Amendment (269284)"]

# Stored amendment papers' texts, verbatim openings: they are line edits to the printed bill, not its text.
ID_PAPER_TEXTS = [
    'AMENDMENT TO SECTION 1 On page 3 of the printed bill, in line 8, delete "four (4)" and insert: "six (6)".',
    "AMENDMENT TO SECTION 1 On page 1 of the printed bill, delete lines 19 and 20.",
]


@pytest.mark.parametrize("note", ID_VERSION_NOTES)
def test_idaho_texts_stay_versions_with_a_stage(note):
    assert is_bill_version("Idaho", note) is True
    assert note_stage(note)[0] != STAGE_UNKNOWN


@pytest.mark.parametrize("note", ID_PAPER_NOTES)
@pytest.mark.parametrize("raw_text", [None] + ID_PAPER_TEXTS)
def test_idaho_amendment_papers_are_not_versions(note, raw_text):
    assert is_bill_version("Idaho", note, raw_text=raw_text) is False


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes are not Idaho's papers: the rule is its own name, nothing wider.
        "Amendments",
        "Amendment 1",
        "Bill Text Amendment",
        "Engrossment 1 (Amendment)",
        "Amendment (abc)",
        "Statement of Purpose / Fiscal Note",
    ],
)
def test_idaho_paper_rule_does_not_widen(note):
    assert is_bill_version("Idaho", note) is True


@pytest.mark.parametrize("note", ID_PAPER_NOTES)
def test_idaho_rule_does_not_reach_other_states(note):
    assert is_bill_version("Montana", note) is True


def test_idaho_versions_keep_their_order():
    # H 542: Bill Text, then (paper, no longer a version), then Engrossment 1; S 1247-style second engrossment.
    true_order = ["Bill Text", "Engrossment 1", "Engrossment 2"]
    for stored_order in (true_order, list(reversed(true_order))):
        ranked = sorted(stored_order, key=lambda n: version_sort_key(n, "", [], None, "Idaho"))
        assert ranked == true_order
