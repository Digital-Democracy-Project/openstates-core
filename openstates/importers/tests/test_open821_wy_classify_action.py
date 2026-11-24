"""OPEN-821: Wyoming's `classify_action` (openstates/ddp_states/wy.py), applied on import (OPEN-550) and by
`os-text-extract relabel wy`.

The actions are real, in stored order, from the Mac replica (2026-11-24, session 2026), with the labels the scraper's
categorizer gave them and the organization they are stored under: HB 50 (failed introduction, nothing after) and
HB 116 (failed introduction, revived under House Rule 12-11 (a), passed the House, died in a Senate committee).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.ddp_states import state_rule
from openstates.ddp_states.wy import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

WY = "ocd-jurisdiction/country:us/state:wy/government"

# (date, chamber, labels as scraped, text)
WY_HB50 = [
    ("2026-01-21T00:20:09+00:00", "executive", ["reading-1"], "Bill Number Assigned"),
    ("2026-01-22T16:26:08+00:00", "lower", ["filing"], "H Received for Introduction"),
    ("2026-02-09T23:24:30+00:00", "lower", [], "H Failed Introduction 39-23-0-0-0"),
]
WY_HB50_RELABELLED = [[], ["filing"], ["failure"]]

WY_HB116 = [
    ("2026-02-07T02:45:57+00:00", "executive", ["reading-1"], "Bill Number Assigned"),
    ("2026-02-09T15:24:56+00:00", "lower", ["filing"], "H Received for Introduction"),
    ("2026-02-11T18:35:16+00:00", "lower", [], "H Failed Introduction 39-21-2-0-0"),
    ("2026-02-13T18:04:00+00:00", "lower", [], "H Pursuant to HR 12-11 (a) Passed by Roll Call 45-14-3-0-0"),
    (
        "2026-02-13T18:15:00+00:00",
        "lower",
        ["introduction", "referral-committee"],
        "H Introduced and Referred to H05 - Agriculture Passed by Roll Call 47-12-3-0-0",
    ),
    (
        "2026-02-18T07:24:01+00:00",
        "lower",
        ["committee-passage-favorable"],
        "H05 - Agriculture:Recommend Do Pass 8-1-0-0-0",
    ),
    ("2026-02-19T21:19:12+00:00", "lower", ["committee-passage-favorable"], "H COW:Passed"),
    ("2026-02-20T17:57:30+00:00", "lower", ["reading-2"], "H 2nd Reading:Passed"),
    ("2026-02-22T04:13:47+00:00", "lower", ["passage", "reading-3"], "H 3rd Reading:Passed 44-17-1-0-0"),
    ("2026-02-23T16:22:06+00:00", "upper", ["filing"], "S Received for Introduction"),
    (
        "2026-02-23T17:43:21+00:00",
        "upper",
        ["introduction", "referral-committee"],
        "S Introduced and Referred to S09 - Minerals",
    ),
    ("2026-03-03T02:15:00+00:00", "upper", [], "S No report prior to CoW Cutoff"),
    (
        "2026-03-04T15:24:50+00:00",
        "upper",
        ["committee-failure"],
        "S:Died in Committee Returned Bill Pursuant to SR 5-4",
    ),
]
# only the number line and the failed introduction change; the revival and everything after read as scraped
WY_HB116_RELABELLED = [[], ["filing"], ["failure"]] + [list(labels) for _, _, labels, _ in WY_HB116[3:]]


def _rule(description, labels, chamber="lower", date="2026-02-09"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,chamber,expected",
    [
        # a chamber's vote on introduction that failed ends the bill: failure added, in either chamber
        ("H Failed Introduction 39-23-0-0-0", [], "lower", ["failure"]),
        ("S Failed Introduction 18-13-0-0-0", [], "upper", ["failure"]),  # SF 103: a majority, not two thirds
        ("H Failed Introduction 41-21-0-0-0", ["filing"], "lower", ["filing", "failure"]),
        # the Legislative Service Office numbering the draft is not a first reading
        ("Bill Number Assigned", ["reading-1"], "executive", []),
        ("Bill Number Assigned", ["reading-1", "filing"], "executive", ["filing"]),
        # judgement call (OPEN-821 PR): no vote took place, so the scraper's deferral stays and nothing is added
        ("H Did not Consider for Introduction", ["deferral"], "lower", ["deferral"]),
        ("S Did not Consider for Introduction Vote", ["deferral"], "upper", ["deferral"]),
        # control sample: every other label set stored for Wyoming in 2026, on its commonest text, as given
        ("H Received for Introduction", ["filing"], "lower", ["filing"]),
        (
            "S Introduced and Referred to S01 - Judiciary",
            ["introduction", "referral-committee"],
            "upper",
            ["introduction", "referral-committee"],
        ),
        ("H Pursuant to HR 12-11 (a) Passed by Roll Call 45-14-3-0-0", [], "lower", []),
        ("H COW:Passed", ["committee-passage-favorable"], "lower", ["committee-passage-favorable"]),
        ("S 2nd Reading:Passed", ["reading-2"], "upper", ["reading-2"]),
        ("S 3rd Reading:Passed 31-0-0-0-0", ["passage", "reading-3"], "upper", ["passage", "reading-3"]),
        ("S 3rd Reading:Failed 13-18-0-0-0", ["failure"], "upper", ["failure"]),
        ("H COW:H Did not consider for COW", ["committee-failure"], "lower", ["committee-failure"]),
        ("H Placed on General File", [], "lower", []),
        (":Rerefer to S02 - Appropriations", ["referral-committee"], "upper", ["referral-committee"]),
        ("H Withdrawn by Sponsor", ["withdrawal"], "lower", ["withdrawal"]),
        ("Assigned Number SEA No. 0034", [], "executive", []),
        ("Governor Signed SEA No. 0034", ["executive-signature"], "legislature", ["executive-signature"]),
        ("Governor Vetoed HEA No. 0035", ["executive-veto"], "legislature", ["executive-veto"]),
        (
            "Governor Signed and Line Item Vetoed",
            ["executive-signature", "executive-veto-line-item"],
            "legislature",
            ["executive-signature", "executive-veto-line-item"],
        ),
        ("Assigned Chapter Number 69", ["became-law"], "executive", ["became-law"]),
    ],
)
def test_wyomings_failed_introduction_and_number_lines(description, labels, chamber, expected):
    assert _rule(description, labels, chamber) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["reading-1"]
    assert _rule("Bill Number Assigned", labels, "executive") == [] and labels == ["reading-1"]
    empty = []
    assert _rule("H Failed Introduction 39-23-0-0-0", empty) == ["failure"] and empty == []
    same = ["filing"]
    assert _rule("H Received for Introduction", same) is not same


@pytest.mark.parametrize("date,chamber,labels,description", WY_HB50 + WY_HB116)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_state_rule_finds_wyomings_actions_only():
    assert state_rule("wy", "classify_action") is classify_action
    assert state_rule("wy", "classify_bill") is None


def _wy():
    _jurisdiction(WY, "Wyoming", session="2026", chambers=("lower", "upper", "legislature", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_wyomings_rule_and_a_rescrape_changes_nothing():
    _wy()

    BillImporter(WY).import_data(
        [
            _scraped(WY_HB50, "HB 50", session="2026", chamber="lower"),
            _scraped(WY_HB116, "HB 116", session="2026", chamber="lower"),
        ]
    )
    assert _stored("HB 50") == WY_HB50_RELABELLED
    assert _stored("HB 116") == WY_HB116_RELABELLED

    _, what = BillImporter(WY).import_item(_scraped(WY_HB50, "HB 50", session="2026", chamber="lower"))
    assert what == "noop"
    assert _stored("HB 50") == WY_HB50_RELABELLED


@pytest.mark.django_db
def test_relabel_wy_now_runs_and_finds_the_stored_lines_the_rule_changes():
    bill = _bill(WY, "Wyoming", "HB 50", WY_HB50)
    before = _labels(bill)

    result = _relabel_dry_run("wy")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=2 unchanged=1" in result.output
    assert "action: reading-1 -> (none): 1" in result.output
    assert "action: (none) -> failure: 1" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _wy()
    BillImporter(WY).import_data([_scraped(WY_HB116, "HB 116", session="2026", chamber="lower")])

    result = _relabel_dry_run("wy")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=13" in result.output
