"""OPEN-831: Missouri's `classify_action` (openstates/ddp_states/mo.py), applied on import (OPEN-550) and by
`os-text-extract relabel mo`.

Every text is real, from the Mac replica (2026-11-24, session 2026), with the labels it is stored with and the
organization it is stored under. The bills are cut to the lines that carry no member names, except the two motion
lines of SR 668, whose text ends with the mover's name: there it is written "(<member>)". The Senate's SB 1534 (its
only floor line is a substitute's adoption; the bill was never third read), SB 841 (two amendments adopted to a
substitute that was never adopted), SR 668 (an amendment tabled, then the resolution itself), SCR 21 (its committee
substitute adopted, then the resolution) and SB 973 (from its Third Readings to "Truly Agreed To and Finally Passed",
with the Senate's adoption of the conference committee's report before the substitute's final vote).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.mo import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

MO = "ocd-jurisdiction/country:us/state:mo/government"
SESSION = "2026"

P3 = ["passage", "reading-3"]

# (date, chamber, labels as stored, text)
MO_SB1534 = [
    ("2026-01-12", "upper", ["reading-1"], "S First Read"),
    (
        "2026-02-05",
        "upper",
        ["reading-2", "referral-committee"],
        "Second Read and Referred S Economic and Workforce Development Committee",
    ),
    (
        "2026-03-04",
        "upper",
        ["committee-passage"],
        "SCS Voted Do Pass S Economic and Workforce Development Committee (6578S.02C)",
    ),
    ("2026-03-09", "upper", [], "Reported from S Economic and Workforce Development Committee w/SCS"),
    ("2026-03-25", "upper", ["passage"], "SS for SCS, as amended, S adopted"),
    ("2026-03-25", "upper", [], "Perfected"),
    ("2026-03-30", "upper", ["referral-committee"], "Referred S Fiscal Oversight Committee"),
    ("2026-05-15", "upper", ["reading-3"], "Formal Calendar S Bills for Third Reading"),
]
MO_SB1534_RELABELLED = [list(labels) for _, _, labels, _ in MO_SB1534]
MO_SB1534_RELABELLED[4] = []

MO_SB841 = [
    ("2025-12-01", "upper", [], "Prefiled"),
    ("2026-01-07", "upper", ["reading-1"], "S First Read"),
    (
        "2026-01-08",
        "upper",
        ["reading-2", "referral-committee"],
        "Second Read and Referred S Families, Seniors and Health Committee",
    ),
    (
        "2026-03-09",
        "upper",
        ["committee-passage"],
        "SCS Voted Do Pass S Families, Seniors and Health Committee (4822S.02C)",
    ),
    ("2026-03-25", "upper", [], "Reported from S Families, Seniors and Health Committee w/SCS"),
    ("2026-04-28", "upper", ["passage"], "SA 1 to SS for SCS, as amended, S adopted"),
    ("2026-04-28", "upper", ["passage"], "SA 2 to SS for SCS S adopted"),
    ("2026-04-28", "upper", [], "SA 4 to SS for SCS, as amended, S withdrawn"),
    ("2026-04-28", "upper", [], "Bill Placed on Informal Calendar"),
    ("2026-05-15", "upper", [], "Informal Calendar S Bills for Perfection"),
]
MO_SB841_RELABELLED = [list(labels) for _, _, labels, _ in MO_SB841]
MO_SB841_RELABELLED[5] = MO_SB841_RELABELLED[6] = []

MO_SR668 = [
    ("2026-02-11", "upper", ["introduction"], "S Offered"),
    ("2026-04-22", "upper", ["passage"], "Motion to table SA 1 to SR 668 S adopted (<member>)"),
    ("2026-04-22", "upper", ["passage"], "Motion to table SR 668 S adopted (<member>)"),
]
MO_SR668_RELABELLED = [["introduction"], [], []]

MO_SCR21 = [
    ("2026-03-10", "upper", ["introduction"], "S Offered"),
    ("2026-03-12", "upper", ["referral-committee"], "Referred S Rules, Joint Rules, Resolutions and Ethics Committee"),
    (
        "2026-04-08",
        "upper",
        ["committee-passage"],
        "SCS Voted Do Pass S Rules, Joint Rules, Resolutions and Ethics Committee (7532S.02C)",
    ),
    ("2026-04-13", "upper", ["passage"], "SCS S adopted"),
    ("2026-04-13", "upper", ["passage"], "S adopted"),
    ("2026-04-13", "lower", [], "Reported to the House"),
    ("2026-04-14", "lower", ["reading-2"], "H Second Read"),
    ("2026-05-14", "lower", [], "H adopted"),
]
MO_SCR21_RELABELLED = [list(labels) for _, _, labels, _ in MO_SCR21]
MO_SCR21_RELABELLED[3] = []
MO_SCR21_RELABELLED[7] = ["passage"]  # OPEN-857: the House's adoption of the resolution is its House passage

MO_SB973 = [
    ("2026-03-05", "upper", P3, "S Third Read and Passed"),
    ("2026-04-29", "lower", [], "HCS, as amended, H adopted --(4981H.05C)"),
    ("2026-04-29", "lower", P3, "H Third Read and Passed"),
    ("2026-05-06", "upper", [], "S distributes CCR (4981S06.1SR)"),
    ("2026-05-07", "lower", [], "H distributes CCR/CCS (4981S06.1SR)"),
    ("2026-05-11", "upper", ["passage"], "S adopted CCR (4981S06.1SR)"),
    ("2026-05-15", "upper", P3, "CCS S Third Read and Passed (4981S.06S)"),
    ("2026-05-15", "lower", [], "H adopts CCR --(4981S06.1SR)"),
    ("2026-05-15", "lower", P3, "CCS H Third Read and Passed"),
    ("2026-05-15", "upper", ["passage"], "Truly Agreed To and Finally Passed"),
]
MO_SB973_RELABELLED = [list(labels) for _, _, labels, _ in MO_SB973]
MO_SB973_RELABELLED[5] = []


def _rule(description, labels, chamber="upper", date="2026-04-28"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


# every distinct stored text the rule changes (Mac replica, 2026-11-24: 18 texts, 44 lines, all `passage` -> none)
@pytest.mark.parametrize(
    "description",
    [
        "SS for SCS, as amended, S adopted",
        "SS for SCS S adopted",
        "SS, as amended, S adopted",
        "SS#2, as amended, S adopted",
        "SS#3, as amended S adopted",
        "SS#2 for SCS, as amended, S adopted",
        "SCS S adopted",
        "SA 2 to SS, as amended, S adopted",
        "SA 3 to SS, as amended, S adopted",
        "SA 1 to SS for SCS, as amended, S adopted",
        "SA 2 to SS for SCS S adopted",
        "S adopted CCR (4981S06.1SR)",
        "Motion to table SA 1 to SR 668 S adopted (<member>)",
        "Motion to table SR 668 S adopted (<member>)",
        "Motion to lay SA on table S adopted (<member>)",
        "Motion to reconsider Perfection Vote S adopted (<member>)",
        "Motion to reconsider the adoption vote for SS S adopted (<member>)",
        "Motion to allow S conferees to exceed differences S adopted",
    ],
)
def test_a_substitute_amendment_report_or_motion_adopted_loses_passage(description):
    assert _rule(description, ["passage"]) == []


# the House's same lines are stored with no label, and the rule leaves them so
@pytest.mark.parametrize(
    "description,labels",
    [
        ("HCS, as amended, H adopted --(4981H.05C)", []),
        ("H adopts CCR --(4981S06.1SR)", []),
        ("House conferees allowed to exceed the differences H adopted", []),
        ("Motion to reconsider H Third Reading Vote H adopted", ["reading-3"]),
    ],
)
def test_the_houses_lines_keep_their_labels(description, labels):
    assert _rule(description, labels, "lower") == labels


def test_a_house_substitute_or_report_adoption_would_not_be_passage_either():
    # none stored with passage (the scraper tags only "S adopted"), but the House's wording is the same rule's
    assert _rule("HCS, as amended, H adopted --(4981H.05C)", ["passage"], "lower") == []
    assert _rule("House conferees allowed to exceed the differences H adopted", ["passage"], "lower") == []


# every stored passage form that keeps it (Mac replica, 2026-11-24)
@pytest.mark.parametrize(
    "chamber,description,labels",
    [
        ("upper", "S adopted", ["passage"]),
        ("lower", "Third Read and Passed (H) - AYES: 150 NOES: 0 PRESENT: 0", P3),
        ("upper", "S Third Read and Passed", P3),
        ("upper", "S Third Read and Passed - Consent", P3),
        ("upper", "S Third Read and Passed - EC adopted", P3),
        ("lower", "H Third Read and Passed", P3),
        ("lower", "H Third Read and Passed, as amended EC - adopted", P3),
        ("lower", "H Third Read and Passed - EC defeated", P3),
        ("upper", "Third Read and Passed (S) - AYES: 32 NOES: 0 PRESENT: 0", P3),
        ("upper", "Third Read and Passed with Amendments (S) - SA 1 - AYES: 33 NOES: 0 PRESENT: 0", P3),
        ("upper", "Third Read and Passed with Amendments (S) - SA 1, SA 2, adopted - AYES: 30 NOES: 2 PRESENT: 0", P3),
        ("upper", "Third Read and Passed with Amendments (S) - SS SCS, as amended - AYES: 25 NOES: 7 PRESENT: 0", P3),
        ("lower", "Third Read and Passed with Amendments (H) - HPA 1, adopted - AYES: 140 NOES: 5 PRESENT: 0", P3),
        ("upper", "CCS S Third Read and Passed (4981S.06S)", P3),
        ("upper", "CCS S Third Read and Passed - EC adopted", P3),
        ("lower", "CCS H Third Read and Passed --(4981S.06S)", P3),
        ("lower", "CCS H Third Read and Passed - EC adopted", P3),
        ("upper", "Truly Agreed To and Finally Passed", ["passage"]),
        ("lower", "Truly Agreed To and Finally Passed - AYES: 150 NOES: 0 PRESENT: 0", ["passage"]),
        # none stored with passage: a House resolution's bare adoption is its passage too
        ("lower", "H adopted", ["passage"]),
        # none stored: a Third Read and Passed line that also names an adoption is still the passage
        ("upper", "S Third Read and Passed - SA 1 S adopted", P3),
        ("upper", "Truly Agreed To and Finally Passed - CCR S adopted", ["passage"]),
    ],
)
def test_a_passage_or_resolution_adoption_keeps_passage(chamber, description, labels):
    assert _rule(description, labels, chamber) == labels


def test_nothing_is_added_and_other_labels_keep_their_order():
    assert _rule("SS for SCS, as amended, S adopted", []) == []
    assert _rule("SS for SCS, as amended, S adopted", ["reading-2", "passage", "referral-committee"]) == [
        "reading-2",
        "referral-committee",
    ]
    assert _rule("SS Adopted (S)", []) == []
    assert _rule("", ["passage"]) == ["passage"]


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["passage"]
    assert _rule("SCS S adopted", labels) == [] and labels == ["passage"]
    out = _rule("S adopted", labels)
    assert out == ["passage"] and out is not labels
    assert classify_action(description=None, classification=["passage"], chamber=None, date=None) == ["passage"]


@pytest.mark.parametrize("date,chamber,labels,description", MO_SB1534 + MO_SB841 + MO_SR668 + MO_SCR21 + MO_SB973)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_the_rule_reads_the_text_not_the_organization():
    for chamber in ("upper", "lower", "legislature", None):
        assert _rule("SCS S adopted", ["passage"], chamber) == []
        assert _rule("S adopted", ["passage"], chamber) == ["passage"]


def test_state_rule_finds_missouris_actions_only():
    assert state_rule("mo", "classify_action") is classify_action
    assert state_rule("mo", "classify_bill") is None


def _mo():
    _jurisdiction(MO, "Missouri", session=SESSION, chambers=("lower", "upper"))


@pytest.mark.django_db
def test_the_importer_takes_passage_off_bills_and_a_resolution_that_did_not_pass():
    _mo()

    BillImporter(MO).import_item(_scraped(MO_SB1534, "SB 1534", session=SESSION, chamber="upper"))
    BillImporter(MO).import_item(_scraped(MO_SB841, "SB 841", session=SESSION, chamber="upper"))
    BillImporter(MO).import_item(_scraped(MO_SR668, "SR 668", session=SESSION, chamber="upper"))

    assert _stored("SB 1534") == MO_SB1534_RELABELLED
    assert _stored("SB 841") == MO_SB841_RELABELLED
    assert _stored("SR 668") == MO_SR668_RELABELLED
    # none of them passed the Senate
    for identifier in ("SB 1534", "SB 841", "SR 668"):
        assert Bill.objects.get(identifier=identifier).latest_passage_date is None


@pytest.mark.django_db
def test_the_importer_keeps_a_resolutions_adoption_and_a_bills_final_votes_and_a_rescrape_changes_nothing():
    _mo()

    BillImporter(MO).import_item(_scraped(MO_SCR21, "SCR 21", session=SESSION, chamber="upper"))
    BillImporter(MO).import_item(_scraped(MO_SB973, "SB 973", session=SESSION, chamber="upper"))
    assert _stored("SCR 21") == MO_SCR21_RELABELLED
    assert _stored("SB 973") == MO_SB973_RELABELLED
    assert Bill.objects.get(identifier="SCR 21").latest_passage_date == "2026-05-14"  # OPEN-857: the House's adoption
    assert Bill.objects.get(identifier="SB 973").latest_passage_date == "2026-05-15"

    _, what = BillImporter(MO).import_item(_scraped(MO_SB973, "SB 973", session=SESSION, chamber="upper"))
    assert what == "noop"
    assert _stored("SB 973") == MO_SB973_RELABELLED


@pytest.mark.django_db
def test_relabel_mo_now_runs_and_finds_the_stored_adoptions():
    bills = [
        _bill(MO, "Missouri", identifier, actions)
        for identifier, actions in (
            ("SB 1534", MO_SB1534),
            ("SB 841", MO_SB841),
            ("SR 668", MO_SR668),
            ("SCR 21", MO_SCR21),
            ("SB 973", MO_SB973),
        )
    ]
    before = [_labels(b) for b in bills]

    result = _relabel_dry_run("mo")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=8 unchanged=31" in result.output
    assert "action: passage -> (none): 7" in result.output
    assert "action: (none) -> passage: 1" in result.output  # OPEN-857: SCR 21's "H adopted"
    assert [_labels(b) for b in bills] == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _mo()
    BillImporter(MO).import_data(
        [
            _scraped(MO_SB1534, "SB 1534", session=SESSION, chamber="upper"),
            _scraped(MO_SB841, "SB 841", session=SESSION, chamber="upper"),
            _scraped(MO_SR668, "SR 668", session=SESSION, chamber="upper"),
            _scraped(MO_SCR21, "SCR 21", session=SESSION, chamber="upper"),
            _scraped(MO_SB973, "SB 973", session=SESSION, chamber="upper"),
        ]
    )

    result = _relabel_dry_run("mo")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=39" in result.output
