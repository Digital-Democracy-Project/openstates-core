"""OPEN-835: Wyoming's amendment papers and the governor's veto letters are not versions of the bill
(openstates/ddp_states/wy.py).

The names are Wyoming's stored version names on the Mac replica (session 2026, 1,295 documents, 2026-11-24), as the
scraper writes them; a member's name in a sponsor is replaced by a placeholder (no legislator names in code).
"""
import pytest  # type: ignore
from openstates.utils.version_ordering import (
    STAGE_UNKNOWN,
    is_bill_version,
    note_stage,
    version_sort_key,
)

# The bill's texts: stay versions, each with a stage ("Chaptered Law with Final Veto Actions" is SF 1's enacted budget
# with the vetoes marked in it).
WY_VERSION_NOTES = [
    "Introduced",
    "Enrolled",
]
WY_OTHER_TEXTS = ["Chaptered Law with Final Veto Actions"]

# Every shape of amendment paper stored for Wyoming: each stage, each disposition, the ".NN" parts of a divided
# amendment, a committee's double space, and the sponsor-less committee, conference and veto-override papers.
WY_AMENDMENT_NOTES = [
    "Amendment HB0126SS001 (Standing Committee) - Senate Labor, Health and Social Services Committee (Adopted)",
    "Amendment SF0099HS001 (Standing Committee) - House Minerals, Business and Economic Development  (Adopted)",
    "Amendment SF0056SS001 (Standing Committee) - Senate Judiciary Committee (Failed)",
    "Amendment HB0130HS001 (Standing Committee) - House Appropriations Committee (Corrected, Adopted)",
    "Amendment HB0001HS001 (Standing Committee)",
    "Amendment SF0001HS001 (Standing Committee) - House Appropriations Committee (Reported)",
    "Amendment HB0001H3013.01 (3rd reading) - Representative X (Corrected, Failed)",
    "Amendment HB0001H3072 (3rd reading) - Representative X (Divided)",
    "Amendment SF0001S3017 (3rd reading) - Senator X, D (Adopted)",
    "Amendment SF0002S2001 (2nd reading) - Senator X (Failed)",
    "Amendment SF0032S3002 (3rd reading) - Senator X (Withdrawn)",
    "Amendment HB0001H3017 (3rd reading) - Representative X (Corrected, Corrected, Failed)",
    "Amendment SF0031SW001 (Committee of the Whole) - Senator X (Adopted)",
    "Amendment HB0107SW001 (Committee of the Whole) - Senator X (Corrected, Ruled Out of Order)",
    "Amendment HB0036JC001 (Conference Committee)",
    "Amendment SF0001VT001 (Veto Override)",
    "Amendment SJ0001S3002 (3rd reading) - Senator X (Failed)",
    "Amendment HJ0002SW001 (Committee of the Whole) - Senator X (Adopted)",
]

WY_VETO_NOTES = [
    "Governor's Veto Message",
    "Governor's Line Item Veto Message",
    "Governor's Line Item Vetoes",
]


@pytest.mark.parametrize("note", WY_VERSION_NOTES)
def test_wyoming_texts_stay_versions_with_a_stage(note):
    assert is_bill_version("Wyoming", note) is True
    assert note_stage(note)[0] != STAGE_UNKNOWN


@pytest.mark.parametrize("note", WY_OTHER_TEXTS)
def test_wyoming_chaptered_law_stays_a_version(note):
    assert is_bill_version("Wyoming", note) is True


@pytest.mark.parametrize("note", WY_AMENDMENT_NOTES + WY_VETO_NOTES)
def test_wyoming_amendment_papers_and_veto_letters_are_not_versions(note):
    assert is_bill_version("Wyoming", note) is False


@pytest.mark.parametrize(
    "note",
    [
        # Close names are not Wyoming's papers: the rule is its own names, nothing wider.
        "Amendment",
        "Amended",
        "Amendments",
        "Engrossed",
        "Governor's Veto",
        "Governor's Veto Message (Overridden)",
        "Veto Message",
        "Enrolled Act",
    ],
)
def test_wyoming_paper_rule_does_not_widen(note):
    assert is_bill_version("Wyoming", note) is True


@pytest.mark.parametrize("note", WY_AMENDMENT_NOTES + WY_VETO_NOTES)
def test_wyoming_rule_does_not_reach_other_states(note):
    assert is_bill_version("Alabama", note) is True


def test_wyoming_enrolled_keeps_its_place_after_introduced():
    true_order = ["Introduced", "Enrolled"]
    for stored_order in (true_order, list(reversed(true_order))):
        ranked = sorted(stored_order, key=lambda n: version_sort_key(n, "", [], None, "Wyoming"))
        assert ranked == true_order
