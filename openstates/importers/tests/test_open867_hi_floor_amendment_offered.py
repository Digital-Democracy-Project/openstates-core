"""OPEN-867: Hawaii's "Floor Amendment No. N offered." lines lose `introduction` (openstates/ddp_states/hi.py
`classify_action`); `amendment-introduction` stays. Offering a floor amendment is not the bill's introduction.

Every text is real, from the Mac replica (openstates_rds_repl_20260911, 2026-11-24), with the labels it is stored with;
the House form names the member who offered it, replaced here by "Member". The bill is the Senate's 2026 SB 2203, cut to
its Senate lines from its introduction to its Third Reading, member names left out.
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.ddp_states.hi import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

HI = "ocd-jurisdiction/country:us/state:hi/government"
SESSION = "2026"
OFFERED = ["amendment-introduction", "introduction"]  # as every stored line carries it

# (date, chamber, labels as stored, text), oldest first
HI_SB2203_SENATE = [
    ("2026-01-14", "upper", [], "Pending Introduction."),
    ("2026-01-21", "upper", ["introduction", "reading-1"], "Introduced and passed First Reading."),
    ("2026-01-26", "upper", ["referral-committee"], "Referred to PSM/EIG, JDC."),
    ("2026-02-11", "upper", ["passage", "reading-2", "referral-committee"], "Report adopted; Passed Second Reading, as amended (SD 1)."),
    ("2026-02-13", "upper", OFFERED, "Floor Amendment No. 1 offered."),
    ("2026-02-13", "upper", ["amendment-passage", "passage"], "Floor amendment adopted as amended (SD 2)."),
    ("2026-02-17", "upper", ["passage"], "Passed Third Reading, as amended (SD 2). Ayes, 22.  Transmitted to House."),
]
HI_SB2203_SENATE_RELABELLED = [
    [],
    ["introduction", "reading-1"],  # the bill's own introduction stays
    ["referral-committee"],
    ["reading-2", "referral-committee"],  # OPEN-824
    ["amendment-introduction"],
    ["amendment-passage"],  # OPEN-856
    ["passage"],
]


def _rule(description, labels, chamber="upper", date="2026-02-13"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "chamber,description",
    [
        ("upper", "Floor Amendment No. 1 offered."),
        ("upper", "Floor Amendment No. 15 offered."),
        ("lower", "Floor Amendment No. 9 offered by Rep. Member."),
        ("lower", "Floor Amendment No. 12 offered by Rep. Member."),
    ],
)
def test_a_floor_amendment_offered_is_not_the_bills_introduction(chamber, description):
    assert _rule(description, OFFERED, chamber) == ["amendment-introduction"]


@pytest.mark.parametrize(
    "chamber,description,labels",
    [
        # the bill's (or a resolution's) own introduction keeps it
        ("upper", "Introduced and passed First Reading.", ["introduction", "reading-1"]),
        ("upper", "Introduced.", ["introduction"]),
        ("lower", "Offered", ["introduction"]),
        ("upper", "Offered.", ["introduction"]),
        ("lower", "To be offered.", ["introduction"]),
        # the floor amendment's other lines keep theirs
        ("upper", "Floor amendment adopted as amended (SD 2).", ["amendment-passage"]),
        ("lower", "Floor amendment failed with Noes, 1.", ["amendment-failure"]),
    ],
)
def test_the_other_introductions_and_amendment_lines_are_unchanged(chamber, description, labels):
    assert _rule(description, labels, chamber) == labels


def test_the_rule_is_stable_on_its_own_output_and_leaves_its_input_alone():
    given = list(OFFERED)
    once = _rule("Floor Amendment No. 1 offered.", given)
    assert given == OFFERED
    assert _rule("Floor Amendment No. 1 offered.", once) == once == ["amendment-introduction"]


@pytest.mark.django_db
def test_the_importer_stores_the_offered_amendment_without_introduction():
    _jurisdiction(HI, "Hawaii", session=SESSION, chambers=("lower", "upper"))

    BillImporter(HI).import_item(_scraped(HI_SB2203_SENATE, "SB 2203", session=SESSION, chamber="upper"))

    assert _stored("SB 2203") == HI_SB2203_SENATE_RELABELLED


@pytest.mark.django_db
def test_relabel_hi_finds_the_offered_line():
    bill = _bill(HI, "Hawaii", "SB 2203", HI_SB2203_SENATE)
    before = _labels(bill)

    result = _relabel_dry_run("hi")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=3 unchanged=4" in result.output
    assert "action: amendment-introduction, introduction -> amendment-introduction: 1" in result.output
    assert _labels(bill) == before
