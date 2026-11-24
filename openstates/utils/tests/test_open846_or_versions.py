"""OPEN-846: Oregon's amendment papers and minority reports are not versions of the bill (openstates/ddp_states/or.py).

The names are every distinct Oregon version note on the Mac replica (2026R1, 1,085 documents, 2026-11-23), as the
scraper writes them (scrapers/or/bills.py).
"""

import pytest  # type: ignore

from openstates.utils.version_ordering import STAGE_UNKNOWN, is_bill_version, note_stage, version_sort_key

# The bill's texts: stay versions, each with a stage (652 documents).
OR_VERSION_NOTES = ["Introduced", "A-Engrossed", "B-Engrossed", "C-Engrossed", "Enrolled"]

# Every other stored note: a committee's or a minority's amendments, a minority report, or a committee's proposed
# amendment (433 documents).
OR_PAPER_NOTES = [
    "HALNRW Amendment -1",
    "HALNRW Amendment -2",
    "HALNRW Amendment -3",
    "HALNRW Amendment -11",
    "HALNRW Amendment -13",
    "HALNRW Amendment -14",
    "HALNRW Amendment -A8",
    "HBH Amendment -1",
    "HBH Amendment -2",
    "HBH Amendment -3",
    "HBH Amendment -5",
    "HBH Amendment -6",
    "HBH Amendment -9",
    "HBH Amendment -A3",
    "HCCP Amendment -1",
    "HCCP Amendment -2",
    "HCEE Amendment -1",
    "HCEE Amendment -2",
    "HCEE Amendment -3",
    "HCT Amendment -2",
    "HCT Amendment -3",
    "HCT Amendment -5",
    "HCT Amendment -13",
    "HCT Amendment -14",
    "HECDSB Amendment -1",
    "HECDSB Amendment -2",
    "HECDSB Amendment -3",
    "HECDSB Amendment -4",
    "HECHS Amendment -1",
    "HECHS Amendment -2",
    "HECHS Amendment -3",
    "HECHS Amendment -6",
    "HED Amendment -1",
    "HED Amendment -2",
    "HED Amendment -3",
    "HED Amendment -4",
    "HED Amendment -5",
    "HED Amendment -A2",
    "HEMV Amendment -1",
    "HEMV Amendment -2",
    "HEMV Amendment -3",
    "HEMV Amendment -5",
    "HHC Amendment -1",
    "HHC Amendment -2",
    "HHC Amendment -3",
    "HHC Amendment -6",
    "HHC Amendment -36",
    "HHC Amendment -A3",
    "HHOUSH Amendment -1",
    "HHOUSH Amendment -2",
    "HHOUSH Amendment -3",
    "HHOUSH Amendment -10",
    "HJUD Amendment -1",
    "HJUD Amendment -2",
    "HJUD Amendment -3",
    "HJUD Amendment -4",
    "HJUD Amendment -5",
    "HJUD Amendment -6",
    "HJUD Amendment -7",
    "HJUD Amendment -8",
    "HJUD Amendment -A3",
    "HLWD Amendment -1",
    "HLWD Amendment -2",
    "HLWD Amendment -4",
    "HLWD Amendment -7",
    "HREV Amendment -1",
    "HREV Amendment -2",
    "HREV Amendment -7",
    "HREV Amendment -A14",
    "HREV Amendment -A17",
    "HRULES Amendment -1",
    "HRULES Amendment -2",
    "HRULES Amendment -3",
    "HRULES Amendment -8",
    "HRULES Amendment -A6",
    "HRULES Amendment -A12",
    "HRULES Amendment -A25",
    "House Amendments to A-Engrossed",
    "House Amendments to B-Engrossed",
    "House Amendments to Introduced",
    "House Minority Amendments to Introduced",
    "JLCIMT Amendment -4",
    "JSRP Amendment -2",
    "JWM Amendment -1",
    "JWM Amendment -2",
    "JWM Amendment -3",
    "JWM Amendment -A2",
    "JWM Amendment -A3",
    "JWM Amendment -A4",
    "JWM Amendment -A5",
    "JWM Amendment -A7",
    "JWM Amendment -A12",
    "JWM Amendment -A13",
    "JWM Amendment -B26",
    "Minority Report A-Engrossed",
    "Minority Report to B-Engrossed",
    "SCGG Amendment -1",
    "SCGG Amendment -2",
    "SCGG Amendment -5",
    "SCT Amendment -2",
    "SCT Amendment -4",
    "SCT Amendment -5",
    "SCT Amendment -6",
    "SECBH Amendment -1",
    "SECBH Amendment -2",
    "SECBH Amendment -A11",
    "SED Amendment -1",
    "SED Amendment -2",
    "SED Amendment -7",
    "SEE Amendment -1",
    "SEE Amendment -2",
    "SEE Amendment -3",
    "SEE Amendment -4",
    "SEE Amendment -A2",
    "SFR Amendment -3",
    "SFR Amendment -4",
    "SFR Amendment -7",
    "SFR Amendment -9",
    "SHC Amendment -1",
    "SHC Amendment -2",
    "SHC Amendment -4",
    "SHC Amendment -5",
    "SHDEV Amendment -1",
    "SHDEV Amendment -2",
    "SHDEV Amendment -4",
    "SHDEV Amendment -6",
    "SHDEV Amendment -A4",
    "SHDEV Amendment -A5",
    "SHDEV Amendment -A17",
    "SHS Amendment -1",
    "SHS Amendment -8",
    "SHS Amendment -9",
    "SHS Amendment -A12",
    "SJUD Amendment -2",
    "SJUD Amendment -3",
    "SJUD Amendment -4",
    "SJUD Amendment -7",
    "SJUD Amendment -14",
    "SJUD Amendment -A12",
    "SLB Amendment -1",
    "SLB Amendment -2",
    "SNRW Amendment -1",
    "SNRW Amendment -2",
    "SNRW Amendment -4",
    "SRULES Amendment -1",
    "SRULES Amendment -2",
    "SRULES Amendment -3",
    "SRULES Amendment -A3",
    "SRULES Amendment -A9",
    "SRULES Amendment -A11",
    "Senate Amendments to A-Engrossed",
    "Senate Amendments to Introduced",
    "Senate Minority Amendments to A-Engrossed",
    "Senate Minority Amendments to Introduced",
]


@pytest.mark.parametrize("note", OR_VERSION_NOTES)
def test_oregon_texts_stay_versions_with_a_stage(note):
    assert is_bill_version("Oregon", note) is True
    assert note_stage(note)[0] != STAGE_UNKNOWN


@pytest.mark.parametrize("note", OR_PAPER_NOTES)
def test_oregon_amendment_papers_are_not_versions(note):
    assert is_bill_version("Oregon", note) is False


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes are not Oregon's papers: the rule is its own names, nothing wider.
        "House Amendments",
        "Amendments to Introduced",
        "House Amendments to Enrolled",
        "House Amendments to Introduced bill",
        "Minority Report",
        "HED Amendment",
        "HED Amendment -0",
        "HED Amendment 2",
        "HED Amendment -A2 to A-Engrossed",
        "Engrossed",
    ],
)
def test_oregon_paper_rule_does_not_widen(note):
    assert is_bill_version("Oregon", note) is True


@pytest.mark.parametrize("note", OR_PAPER_NOTES)
def test_oregon_rule_does_not_reach_other_states(note):
    assert is_bill_version("Alabama", note) is True


def test_oregon_versions_keep_their_order():
    # HB 4134: introduced, A-engrossed by the House committee, enrolled. A-, B- and C-Engrossed share one stage and
    # carry no date, so this sort alone does not order them among themselves; the stored comparisons already chain B on
    # A (39) and C on B (1), and this rule does not touch them.
    true_order = ["Introduced", "A-Engrossed", "Enrolled"]
    for stored_order in (true_order, list(reversed(true_order))):
        ranked = sorted(stored_order, key=lambda n: version_sort_key(n, "", [], None, "Oregon"))
        assert ranked == true_order
