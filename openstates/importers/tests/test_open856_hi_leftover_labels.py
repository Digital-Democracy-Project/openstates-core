"""OPEN-856: the Hawaii lines OPEN-824 left with a false `passage` (openstates/ddp_states/hi.py `classify_action`):
a floor amendment's adoption, a House draft adopted and recommitted, and a Senate committee recommendation that was
not adopted (now `committee-failure`).

Every text is real, from the Mac replica (openstates_rds_repl_20260911, 2026-11-24), with the labels it is stored with.
Lines that name members are left out or cut before the names (the two "HD 1 adopted and recommitted" lines end in an
excused list). The bills are cut to their name-free lines: the Senate's 2026 SB 3000 (its committee recommendation
not adopted on 03-04, the bill's last line) and the Senate's floor amendment on 2026 HB 1519 (04-14).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
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

AMENDMENT_ADOPTED = ["amendment-passage", "passage"]  # a floor amendment's adoption, as stored

# (date, chamber, labels as stored, text), oldest first
HI_SB3000 = [
    ("2026-01-23", "upper", ["introduction"], "Introduced."),
    ("2026-01-26", "upper", ["reading-1"], "Passed First Reading."),
    ("2026-01-30", "upper", ["referral-committee"], "Referred to CPN, JDC/WAM."),
    (
        "2026-02-17",
        "upper",
        [],
        "Reported from CPN (Stand. Com. Rep. No. 2280) with recommendation of passage on Second Reading, as amended "
        "(SD 1) and referral to JDC/WAM.",
    ),
    (
        "2026-02-17",
        "upper",
        ["passage", "reading-2", "referral-committee"],
        "Report adopted; Passed Second Reading, as amended (SD 1) and referred to JDC/WAM.",
    ),
    (
        "2026-03-02",
        "upper",
        [],
        "The committee(s) on JDC/WAM will hold a public decision making on 03-04-26 10:35AM; Conference Room 211 & "
        "Videoconference.",
    ),
    ("2026-03-04", "upper", ["passage"], "The recommendation was not adopted."),
]
HI_SB3000_RELABELLED = [list(labels) for _, _, labels, _ in HI_SB3000]
HI_SB3000_RELABELLED[4] = ["reading-2", "referral-committee"]  # OPEN-824
HI_SB3000_RELABELLED[6] = ["committee-failure"]

HI_HB1519_SENATE = [
    ("2026-04-14", "upper", ["amendment-introduction", "introduction"], "Floor Amendment No. 5 offered."),
    ("2026-04-14", "upper", AMENDMENT_ADOPTED, "Floor amendment adopted as amended (SD 3)."),
]
HI_HB1519_SENATE_RELABELLED = [["amendment-introduction", "introduction"], ["amendment-passage"]]


def _rule(description, labels, chamber="upper", date="2026-04-14"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


# every stored form (numbers vary), Mac replica 2026-11-24
@pytest.mark.parametrize(
    "chamber,description,labels,expected",
    [
        ("upper", "Floor amendment adopted as amended (SD 3).", AMENDMENT_ADOPTED, ["amendment-passage"]),
        ("upper", "Floor amendment adopted as amended (CD 2).", AMENDMENT_ADOPTED, ["amendment-passage"]),
        # the one roll-call form, cut before the names of the noes
        (
            "upper",
            "Floor amendment adopted as amended (CD  2) following Roll Call vote:  Ayes, 13.",
            AMENDMENT_ADOPTED,
            ["amendment-passage"],
        ),
        (
            "lower",
            "Floor amendment adopted as amended CD 2 with none voting no and none excused.",
            AMENDMENT_ADOPTED,
            ["amendment-passage"],
        ),
        # cut before the excused members' names
        ("lower", "HD 1 adopted and recommitted to AGR for further consideration with none voting no", ["passage"], []),
        ("lower", "HD 1 adopted and recommitted to CAA for further consideration with none voting no", ["passage"], []),
        ("upper", "The recommendation was not adopted.", ["passage"], ["committee-failure"]),
        # none stored: case is ignored, as for OPEN-824's "Report Adopted" (the Senate capitalises some lines)
        ("upper", "Floor Amendment Adopted as amended (SD 3).", AMENDMENT_ADOPTED, ["amendment-passage"]),
    ],
)
def test_a_line_that_is_not_the_bills_passage_loses_passage(chamber, description, labels, expected):
    assert _rule(description, labels, chamber) == expected


# the lines around them that the rule leaves alone (Mac replica, 2026-11-24)
@pytest.mark.parametrize(
    "chamber,description,labels",
    [
        ("upper", "Floor Amendment No. 5 offered.", ["amendment-introduction", "introduction"]),
        ("lower", "Floor amendment failed with Noes, 1.", ["amendment-failure"]),
        ("upper", "Recommitted to JDC.", []),
        ("lower", "Recommitted to Conference committee as amended in CD 1 with none voting no", []),
        # OPEN-824's keeps: the passages themselves
        (
            "upper",
            "Passed Third Reading, as amended (SD 1).  Ayes, 25; Aye(s) with reservations: none.  Noes, 0 (none).  "
            "Excused, 0 (none).  Transmitted to House.",
            ["passage"],
        ),
        (
            "lower",
            "Passed Final Reading as amended in CD 1 with none voting aye with reservations; none voting no (0) and "
            "none excused (0).",
            ["passage"],
        ),
        ("upper", "Report and Resolution Adopted.  Transmitted to House.", ["passage"]),
        (
            "lower",
            "Adopted with none voting aye with reservations; none voting no (0) and  none excused (0).",
            ["passage"],
        ),
    ],
)
def test_the_lines_around_them_are_unchanged(chamber, description, labels):
    assert _rule(description, labels, chamber) == labels


def test_committee_failure_is_added_once_and_other_labels_keep_their_order():
    text = "The recommendation was not adopted."
    assert _rule(text, []) == ["committee-failure"]
    assert _rule(text, ["committee-failure"]) == ["committee-failure"]
    assert _rule(text, ["receipt", "passage"]) == ["receipt", "committee-failure"]
    # a floor amendment's adoption gains nothing
    assert _rule("Floor amendment adopted as amended (SD 3).", ["passage"]) == []


@pytest.mark.parametrize(
    "description,labels",
    [
        ("The recommendation was not adopted.", ["passage"]),
        ("Floor amendment adopted as amended (SD 3).", AMENDMENT_ADOPTED),
        ("HD 1 adopted and recommitted to AGR for further consideration with none voting no", ["passage"]),
    ],
)
def test_the_rule_is_stable_on_its_own_output_and_leaves_its_input_alone(description, labels):
    given = list(labels)
    once = _rule(description, given)
    assert given == labels
    assert _rule(description, once) == once


def _hi():
    _jurisdiction(HI, "Hawaii", session=SESSION, chambers=("lower", "upper"))


@pytest.mark.django_db
def test_the_importer_stores_the_committees_failure_and_no_passage():
    _hi()

    BillImporter(HI).import_item(_scraped(HI_SB3000, "SB 3000", session=SESSION, chamber="upper"))
    BillImporter(HI).import_item(_scraped(HI_HB1519_SENATE, "HB 1519", session=SESSION, chamber="lower"))

    assert _stored("SB 3000") == HI_SB3000_RELABELLED
    assert _stored("HB 1519") == HI_HB1519_SENATE_RELABELLED
    # neither bill passed a chamber on these lines
    assert Bill.objects.get(identifier="SB 3000").latest_passage_date is None
    assert Bill.objects.get(identifier="HB 1519").latest_passage_date is None


@pytest.mark.django_db
def test_relabel_hi_finds_the_stored_lines():
    sb3000 = _bill(HI, "Hawaii", "SB 3000", HI_SB3000)
    hb1519 = _bill(HI, "Hawaii", "HB 1519", HI_HB1519_SENATE)
    before = (_labels(sb3000), _labels(hb1519))

    result = _relabel_dry_run("hi")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=3 unchanged=6" in result.output
    assert "action: passage -> committee-failure: 1" in result.output
    assert "action: amendment-passage, passage -> amendment-passage: 1" in result.output
    assert (_labels(sb3000), _labels(hb1519)) == before
