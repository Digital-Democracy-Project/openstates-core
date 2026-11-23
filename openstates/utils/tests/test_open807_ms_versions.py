"""OPEN-807: Mississippi's amendment papers are not versions of the bill (openstates/ddp_states/ms.py).

The names are every Mississippi version name on the Mac replica (2026 and 20261E sessions, 6,542 versions,
2026-11-23), as the scraper writes them (scrapers/ms/bills.py).
"""
import pytest  # type: ignore
from openstates.utils.version_ordering import (
    STAGE_UNKNOWN,
    is_bill_version,
    note_stage,
    version_sort_key,
)

# The bill's texts: stay versions, each with a stage. "Current version" is left to the scraper fix of the same
# ticket (it is always one of these texts under another name), so it is not here.
MS_VERSION_NOTES = [
    "As Introduced",
    "Committee Substitute",
    "As Passed the lower",
    "As Passed the upper",
    "Approved by the Governor",
]

# Every amendment-paper shape stored for Mississippi (HAM / SAM / AMRPT entries of the bill's history XML).
MS_PAPER_NOTES = [
    "Committee Amendment No 1 (Adopted) (Voice Vote)",
    "Amendment No 1 (Adopted) (Voice Vote)",
    "Amendment No 10 (Cmte Sub) (Adopted) (Voice Vote)",
    "Amendment No 1 (Cmte Sub) (Adopted)",
    "Amendment No 2 (Adopted)",
    "Amendment No 1 (Adopted) ({Vote})",
    "Amendment No 1 to Amendment No 1 (Adopted) (Voice Vote)",
    "Amendment No 1 to Amendment No 1 (Adopted)",
    "Amendment No 1 to Amendment No 1 (Cmte Sub) (Adopted) (Voice Vote)",
    "Amendment No 1 to Committee Amendment No 1 (Adopted) (Voice Vote)",
    "Amendment No 1 to Committee Amendment No 1 (Adopted) ({Vote})",
    "Substitute No 1 for Committee Amendment No 1 (Adopted) (Voice Vote)",
    "Amendment Report for House Bill No. 1924",
    "Amendment Report for Senate Bill No. 3103",
    "Amendment Report for House Bill No.  557",
    "Amendment Report for House Concurrent Resolution No.   21",
]


@pytest.mark.parametrize("note", MS_VERSION_NOTES)
def test_mississippi_texts_stay_versions_with_a_stage(note):
    assert is_bill_version("Mississippi", note) is True
    assert note_stage(note)[0] != STAGE_UNKNOWN


@pytest.mark.parametrize("note", MS_PAPER_NOTES)
def test_mississippi_amendment_papers_are_not_versions(note):
    assert is_bill_version("Mississippi", note) is False


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes are not Mississippi's papers: the rule is its own names, nothing wider.
        "Committee Substitute",
        "Committee Substitute (Adopted)",
        "Amendment No 0 (Adopted)",
        "Amendment No (Adopted)",
        "Amendments No 1 (Adopted)",
        "Amendment No 1 Adopted",
        "Amendment Report",
        "Amendment Report for House Bill 1924",
        "Report for House Bill No. 1924",
        "As Passed the lower (Adopted)",
    ],
)
def test_mississippi_paper_rule_does_not_widen(note):
    assert is_bill_version("Mississippi", note) is True


@pytest.mark.parametrize("note", MS_PAPER_NOTES + MS_VERSION_NOTES)
def test_mississippi_rule_does_not_reach_other_states(note):
    assert is_bill_version("Alabama", note) is True


@pytest.mark.parametrize(
    "true_order",
    [
        # Real Mississippi bills, in the order the legislature produced their texts (the bill page's history):
        # HB 1 (introduced, passed the House, signed), HB 2 (introduced, committee substitute, passed the House).
        ["As Introduced", "As Passed the lower", "Approved by the Governor"],
        ["As Introduced", "Committee Substitute", "As Passed the lower"],
        ["As Introduced", "Committee Substitute", "As Passed the upper", "Approved by the Governor"],
    ],
)
def test_mississippi_versions_keep_their_order(true_order):
    for stored_order in (true_order, list(reversed(true_order))):
        ranked = sorted(stored_order, key=lambda n: version_sort_key(n, "", [], None, "Mississippi"))
        assert ranked == true_order
