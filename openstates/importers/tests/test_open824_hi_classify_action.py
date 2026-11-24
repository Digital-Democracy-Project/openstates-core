"""OPEN-824: Hawaii's `classify_action` (openstates/ddp_states/hi.py), applied on import (OPEN-550) and by
`os-text-extract relabel hi`.

Every text is real, from the Mac replica (2026-11-24, session 2026), with the labels it is stored with and the
organization it is stored under. The bills are cut to the lines that carry no member names (the committee vote lines
are left out): the Senate's 2026 HB 1800 (its Second Reading report adoption, then its Third and Final Readings),
SB 2563 (its only floor line is a Second Reading report adoption; the bill then died in WAM) and the House's HB 2566
(its only floor lines are a Second Reading and a report adoption referring it to FIN).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
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

P2 = ["passage", "reading-2", "referral-committee"]  # the Senate's report adoption at Second Reading, as stored
R2 = ["reading-2", "referral-committee"]
PREF = ["passage", "referral-committee"]  # a report adoption with a referral, as stored

# (date, chamber, labels as stored, text)
HI_HB1800_SENATE = [
    ("2026-03-19", "upper", ["introduction", "receipt"], "Received from House (Hse. Com. No. 446)."),
    ("2026-03-19", "upper", ["reading-1"], "Passed First Reading."),
    ("2026-03-19", "upper", ["referral-committee"], "Referred to WAM."),
    ("2026-04-10", "upper", P2, "Report adopted; Passed Second Reading, as amended (SD 1)."),
    ("2026-04-10", "upper", [], "48 Hrs. Notice 04-14-26."),
    (
        "2026-04-14",
        "upper",
        ["passage"],
        "Passed Third Reading, as amended (SD 1).  Ayes, 25; Aye(s) with reservations: none.  Noes, 0 (none).  "
        "Excused, 0 (none).  Transmitted to House.",
    ),
    (
        "2026-05-06",
        "upper",
        ["passage"],
        "Passed Final Reading, as amended (CD 1).  Ayes, 25; Aye(s) with reservations: none.  Noes, 0 (none).  "
        "Excused, 0 (none).",
    ),
]
HI_HB1800_SENATE_RELABELLED = [list(labels) for _, _, labels, _ in HI_HB1800_SENATE]
HI_HB1800_SENATE_RELABELLED[3] = R2

HI_SB2563 = [
    ("2026-01-23", "upper", ["introduction"], "Introduced."),
    ("2026-01-23", "upper", ["reading-1"], "Passed First Reading."),
    ("2026-01-28", "upper", ["referral-committee"], "Referred to HHS, WAM."),
    (
        "2026-02-20",
        "upper",
        [],
        "Reported from HHS (Stand. Com. Rep. No. 2657) with recommendation of passage on Second Reading, as amended "
        "(SD 1) and referral to WAM.",
    ),
    ("2026-02-20", "upper", P2, "Report adopted; Passed Second Reading, as amended (SD 1) and referred to WAM."),
    ("2026-03-05", "upper", ["deferral"], "The committee on WAM deferred the measure."),
]
HI_SB2563_RELABELLED = [list(labels) for _, _, labels, _ in HI_SB2563]
HI_SB2563_RELABELLED[4] = R2

HI_HB2566 = [
    ("2026-01-28", "lower", ["introduction", "reading-1"], "Introduced and Pass First Reading."),
    ("2026-02-02", "lower", ["referral-committee"], "Referred to CAA, PBS, FIN, referral sheet 6"),
    (
        "2026-02-06",
        "lower",
        ["reading-2", "referral-committee"],
        "Passed Second Reading as amended in HD 1 and referred to the committee(s) on PBS with none voting aye with "
        "reservations; none voting no (0) and none excused (0).",
    ),
    ("2026-02-19", "lower", [], "Reported from PBS (Stand. Com. Rep. No. 533-26), recommending referral to FIN."),
    (
        "2026-02-19",
        "lower",
        PREF,
        "Report adopted; referred to the committee(s) on FIN with none voting aye with reservations; none voting "
        "no (0) and none excused (0).",
    ),
]
HI_HB2566_RELABELLED = [list(labels) for _, _, labels, _ in HI_HB2566]
HI_HB2566_RELABELLED[4] = ["referral-committee"]


def _rule(description, labels, chamber="upper", date="2026-04-10"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


# every stored form of a committee report's adoption before Third Reading (Mac replica, 2026-11-24)
@pytest.mark.parametrize(
    "chamber,description,labels,expected",
    [
        ("upper", "Report adopted; Passed Second Reading, as amended (SD 1) and referred to WAM.", P2, R2),
        ("upper", "Report adopted; Passed Second Reading, as amended (SD 1) and referred to JDC/WAM.", P2, R2),
        ("upper", "Report adopted; Passed Second Reading, as amended (SD 1).", P2, R2),
        ("upper", "Report adopted; Passed Second Reading and referred to WAM.", P2, R2),
        ("upper", "Report adopted; Passed Second Reading and referred to JDC.", P2, R2),
        ("upper", "Report adopted; Passed Second Reading.", P2, R2),
        ("upper", "Report adopted, referred to WAM.", PREF, ["referral-committee"]),
        ("upper", "Report adopted, as amended (SD 1) and referred to WAM.", PREF, ["referral-committee"]),
        ("upper", "Report adopted, as amended (SD 1) and recommitted to EDU.", ["passage"], []),
        # the Senate also capitalises "Report Adopted" (stored on Third Reading lines): the same report, the same rule
        ("upper", "Report Adopted; Passed Second Reading.", P2, R2),
        (
            "lower",
            "Report adopted; referred to the committee(s) on FIN with none voting aye with reservations; none voting "
            "no (0) and none excused (0).",
            PREF,
            ["referral-committee"],
        ),
        (
            "lower",
            "Report adopted; referred to the committee(s) on FIN as amended in HD 1 with none voting aye with "
            "reservations; none voting no (0) and none excused (0).",
            PREF,
            ["referral-committee"],
        ),
        (
            "lower",
            "Report adopted; referred to the committee(s) on JHA with none voting aye with reservations; none voting "
            "no (0) and none excused (0).",
            PREF,
            ["referral-committee"],
        ),
    ],
)
def test_a_committee_report_adopted_before_third_reading_loses_passage(chamber, description, labels, expected):
    assert _rule(description, labels, chamber) == expected


# the floor passages and resolution adoptions keep it (Mac replica, 2026-11-24)
@pytest.mark.parametrize(
    "chamber,description",
    [
        (
            "upper",
            "Report adopted; Passed Third Reading.  Ayes, 25; Aye(s) with reservations: none .  Noes, 0 (none). "
            "Excused, 0 (none).  Transmitted to House.",
        ),
        (
            "upper",
            "Report adopted; Passed Third Reading, as amended (SD  2). Ayes, 25; Aye(s) with reservations: none .  "
            "Noes, 0 (none). Excused, 0 (none).  Transmitted to House.",
        ),
        (
            "upper",
            "Report Adopted; Passed Third Reading, as amended (SD 1).  Ayes, 25; Aye(s) with reservations: none.  "
            "Noes, 0 (none).  Excused, 0 (none).  Transmitted to House.",
        ),
        (
            "upper",
            "Passed Third Reading, as amended (SD 1).  Ayes, 25; Aye(s) with reservations: none.  Noes, 0 (none).  "
            "Excused, 0 (none).  Transmitted to House.",
        ),
        (
            "upper",
            "Passed Final Reading, as amended (CD 1).  Ayes, 25; Aye(s) with reservations: none.  Noes, 0 (none).  "
            "Excused, 0 (none).",
        ),
        (
            "lower",
            "Passed Third Reading with none voting aye with reservations; none voting no (0) and none excused (0).  "
            "Transmitted to Senate.",
        ),
        (
            "lower",
            "Passed Third Reading as amended in HD 1 with none voting aye with reservations; none voting no (0) and "
            "none excused (0).  Transmitted to Senate.",
        ),
        (
            "lower",
            "Passed Final Reading as amended in CD 1 with none voting aye with reservations; none voting no (0) and "
            "none excused (0).",
        ),
        # none stored: a report adopted on Final Reading would be a passage, so it is kept like Third Reading
        ("upper", "Report adopted; Passed Final Reading, as amended (CD 1)."),
        ("upper", "Report and Resolution Adopted.  Transmitted to House."),
        ("upper", "Report and Resolution Adopted, as amended (SD 1).  Transmitted to House."),
        ("upper", "Resolution adopted in final form."),
        ("lower", "Resolution adopted in final form."),
        ("lower", "Adopted with none voting aye with reservations; none voting no (0) and  none excused (0)."),
        ("upper", "Adopted."),
    ],
)
def test_a_floor_passage_or_resolution_adoption_keeps_passage(chamber, description):
    assert _rule(description, ["passage"], chamber) == ["passage"]


def test_nothing_is_added_and_other_labels_keep_their_order():
    assert _rule("Report adopted; Passed Second Reading.", ["reading-2"]) == ["reading-2"]
    assert _rule("Report adopted; Passed Second Reading.", ["referral-committee", "passage", "reading-2"]) == [
        "referral-committee",
        "reading-2",
    ]
    assert _rule("Passed Third Reading.", []) == []
    assert _rule("", ["passage"]) == ["passage"]


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = list(P2)
    assert _rule("Report adopted; Passed Second Reading.", labels) == R2 and labels == P2
    same = ["passage"]
    assert _rule("Resolution adopted in final form.", same) is not same
    assert classify_action(description=None, classification=["passage"], chamber=None, date=None) == ["passage"]


@pytest.mark.parametrize("date,chamber,labels,description", HI_HB1800_SENATE + HI_SB2563 + HI_HB2566)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_the_rule_reads_the_text_not_the_organization():
    for chamber in ("upper", "lower", "legislature", None):
        assert _rule("Report adopted; Passed Second Reading.", P2, chamber) == R2
        assert _rule("Resolution adopted in final form.", ["passage"], chamber) == ["passage"]


def test_state_rule_finds_hawaiis_actions_only():
    assert state_rule("hi", "classify_action") is classify_action
    assert state_rule("hi", "classify_bill") is None


def _hi():
    _jurisdiction(HI, "Hawaii", session=SESSION, chambers=("lower", "upper"))


@pytest.mark.django_db
def test_the_importer_takes_passage_off_a_bill_still_in_committee():
    _hi()

    BillImporter(HI).import_item(_scraped(HI_SB2563, "SB 2563", session=SESSION, chamber="upper"))
    BillImporter(HI).import_item(_scraped(HI_HB2566, "HB 2566", session=SESSION, chamber="lower"))

    assert _stored("SB 2563") == HI_SB2563_RELABELLED
    assert _stored("HB 2566") == HI_HB2566_RELABELLED
    # neither bill passed a chamber
    assert Bill.objects.get(identifier="SB 2563").latest_passage_date is None
    assert Bill.objects.get(identifier="HB 2566").latest_passage_date is None


@pytest.mark.django_db
def test_the_importer_keeps_third_and_final_reading_and_a_rescrape_changes_nothing():
    _hi()

    BillImporter(HI).import_item(_scraped(HI_HB1800_SENATE, "HB 1800", session=SESSION, chamber="lower"))
    assert _stored("HB 1800") == HI_HB1800_SENATE_RELABELLED
    assert Bill.objects.get(identifier="HB 1800").latest_passage_date == "2026-05-06"

    _, what = BillImporter(HI).import_item(_scraped(HI_HB1800_SENATE, "HB 1800", session=SESSION, chamber="lower"))
    assert what == "noop"
    assert _stored("HB 1800") == HI_HB1800_SENATE_RELABELLED


@pytest.mark.django_db
def test_relabel_hi_now_runs_and_finds_the_stored_report_adoptions():
    hb1800 = _bill(HI, "Hawaii", "HB 1800", HI_HB1800_SENATE)
    sb2563 = _bill(HI, "Hawaii", "SB 2563", HI_SB2563)
    hb2566 = _bill(HI, "Hawaii", "HB 2566", HI_HB2566)
    before = (_labels(hb1800), _labels(sb2563), _labels(hb2566))

    result = _relabel_dry_run("hi")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=3 unchanged=15" in result.output
    assert "action: passage, reading-2, referral-committee -> reading-2, referral-committee: 2" in result.output
    assert "action: passage, referral-committee -> referral-committee: 1" in result.output
    assert (_labels(hb1800), _labels(sb2563), _labels(hb2566)) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _hi()
    BillImporter(HI).import_data(
        [
            _scraped(HI_HB1800_SENATE, "HB 1800", session=SESSION, chamber="lower"),
            _scraped(HI_SB2563, "SB 2563", session=SESSION, chamber="upper"),
            _scraped(HI_HB2566, "HB 2566", session=SESSION, chamber="lower"),
        ]
    )

    result = _relabel_dry_run("hi")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=18" in result.output
