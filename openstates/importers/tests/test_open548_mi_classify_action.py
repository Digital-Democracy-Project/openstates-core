"""OPEN-548: Michigan's `classify_action` (openstates/ddp_states/mi.py), applied on import (OPEN-550) and by
`os-text-extract relabel mi`.

The actions are real, in stored order, from the Mac replica (2026-11-17, session 2025-2026), with the labels they are
stored with and the organization they are stored under: Michigan HB 4001 whole, and SB 304 cut to the lines around
its votes. HB 4001's House vote is followed by the Senate journal's "PASSED BY HOUSE" message, stored under the Senate
with `passage`; SB 304 has both the roll-call shape that starts "PASSED BY 2/3 VOTE" (a real vote) and the message
shape "PASSED BY HOUSE BY 2/3 VOTE ...".
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.mi import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

MI = "ocd-jurisdiction/country:us/state:mi/government"

# (date, chamber, labels as stored, text)
MI_HB4001 = [
    ("2025-01-09", "lower", ["introduction"], "introduced by Representative Rep. John Roth"),
    ("2025-01-09", "lower", ["reading-1"], "read a first time"),
    (
        "2025-01-09",
        "lower",
        ["referral-committee"],
        "referred to Committee on Select Committee on Protecting Michigan Employees and Small Businesses",
    ),
    ("2025-01-14", "lower", [], "bill electronically reproduced 01/09/2025"),
    ("2025-01-16", "lower", ["committee-passage"], "reported with recommendation with substitute (H-2)"),
    ("2025-01-16", "lower", [], "referred to second reading"),
    ("2025-01-23", "lower", ["reading-2"], "read a second time"),
    ("2025-01-23", "lower", [], "substitute (H-2) adopted"),
    ("2025-01-23", "lower", [], "placed on third reading"),
    ("2025-01-23", "lower", [], "placed on immediate passage"),
    ("2025-01-23", "lower", ["reading-3"], "read a third time"),
    (
        "2025-01-23",
        "lower",
        ["passage"],
        "passed; given immediate effect Roll Call #4 Yeas 63 Nays 41 Excused 0 Not Voting 6",
    ),
    ("2025-01-23", "lower", [], "transmitted"),
    ("2025-02-04", "upper", ["passage"], "PASSED BY HOUSE WITH IMMEDIATE EFFECT"),
    ("2025-02-04", "upper", ["referral-committee"], "REFERRED TO COMMITTEE ON REGULATORY AFFAIRS"),
]
# the same, as Michigan's rule reads them: the Senate's message of the House vote is not a passage
MI_HB4001_RELABELLED = [list(labels) for _, _, labels, _ in MI_HB4001]
MI_HB4001_RELABELLED[13] = []

MI_SB304 = [
    ("2025-05-14", "upper", ["introduction"], "INTRODUCED BY SENATOR JOHN DAMOOSE"),
    ("2025-07-01", "upper", [], "PLACED ON IMMEDIATE PASSAGE"),
    (
        "2025-07-01",
        "upper",
        ["passage"],
        "PASSED BY 2/3 VOTE ROLL CALL # 203 YEAS 30 NAYS 6 EXCUSED 1 NOT VOTING 0",
    ),
    ("2025-07-01", "lower", ["introduction"], "received on 07/01/2025"),
    ("2026-05-21", "lower", ["reading-3"], "read a third time"),
    (
        "2026-05-21",
        "lower",
        ["passage"],
        "passed by 2/3 vote; given immediate effect Roll Call #163 Yeas 91 Nays 16 Excused 0 Not Voting 3",
    ),
    ("2026-05-21", "lower", [], "returned to Senate"),
    ("2026-06-02", "upper", ["passage"], "PASSED BY HOUSE BY 2/3 VOTE WITHOUT AMENDMENT WITH IMMEDIATE EFFECT"),
    ("2026-06-02", "upper", [], "ORDERED ENROLLED"),
    ("2026-06-04", "upper", ["executive-receipt"], "PRESENTED TO GOVERNOR 6/3/2026 9:14 AM"),
    ("2026-06-04", "upper", ["executive-signature"], "APPROVED BY GOVERNOR 6/3/2026 4:40 PM"),
]
MI_SB304_RELABELLED = [list(labels) for _, _, labels, _ in MI_SB304]
MI_SB304_RELABELLED[7] = []


def _rule(description, labels, chamber="upper", date="2025-02-04"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        # the other chamber's message, every stored variant (Mac replica, 2025-2026), loses passage
        ("PASSED BY HOUSE WITH IMMEDIATE EFFECT", ["passage"], []),
        ("PASSED BY HOUSE WITH SUBSTITUTE (H-5) WITH IMMEDIATE EFFECT", ["passage"], []),
        ("PASSED BY HOUSE WITH SUBSTITUTE (H-5)", ["passage"], []),
        ("PASSED BY HOUSE WITH SUBSTITUTE {0} WITH IMMEDIATE EFFECT", ["passage"], []),
        ("PASSED BY HOUSE WITH AMENDMENT(S) WITH IMMEDIATE EFFECT", ["passage"], []),
        ("PASSED BY HOUSE BY 2/3 VOTE WITHOUT AMENDMENT WITH IMMEDIATE EFFECT", ["passage"], []),
        # the House journal's message of a Senate vote, as the broker reads it (none stored yet)
        ("passed by Senate with immediate effect", ["passage"], []),
        # a chamber's own roll call keeps passage, including the ones that start "PASSED BY 2/3 VOTE"
        ("PASSED BY 2/3 VOTE ROLL CALL # 203 YEAS 30 NAYS 6 EXCUSED 1 NOT VOTING 0", ["passage"], ["passage"]),
        ("PASSED BY 3/4 VOTE ROLL CALL # 342 YEAS 28 NAYS 7 EXCUSED 2 NOT VOTING 0", ["passage"], ["passage"]),
        (
            "passed by 2/3 vote; given immediate effect Roll Call #163 Yeas 91 Nays 16 Excused 0 Not Voting 3",
            ["passage"],
            ["passage"],
        ),
        ("passed; given immediate effect Roll Call #4 Yeas 63 Nays 41 Excused 0 Not Voting 6", ["passage"], ["passage"]),
        ("PASSED ROLL CALL # 12 YEAS 38 NAYS 0 EXCUSED 0 NOT VOTING 0", ["passage"], ["passage"]),
        ("ADOPTED", ["passage"], ["passage"]),
        # the chamber is a whole word (as in the broker's reader); none of these are stored, the case pins the rule
        ("PASSED BY HOUSEHOLD VOTE", ["passage"], ["passage"]),
        # nothing is added: the newer message lines, stored without a label, stay without one
        ("PASSED BY HOUSE WITH IMMEDIATE EFFECT", [], []),
        # labels other than passage are never touched, nor is their order
        ("PASSED BY HOUSE WITH IMMEDIATE EFFECT", ["reading-3", "passage", "filing"], ["reading-3", "filing"]),
        ("REFERRED TO COMMITTEE ON REGULATORY AFFAIRS", ["referral-committee"], ["referral-committee"]),
        ("", ["passage"], ["passage"]),
    ],
)
def test_only_the_other_chambers_message_loses_passage(description, labels, expected):
    assert _rule(description, labels) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["passage"]
    assert _rule("PASSED BY HOUSE WITH IMMEDIATE EFFECT", labels) == [] and labels == ["passage"]
    same = ["passage"]
    assert _rule("PASSED ROLL CALL # 12 YEAS 38", same) is not same
    assert classify_action(description=None, classification=["passage"], chamber=None, date=None) == ["passage"]


@pytest.mark.parametrize("date,chamber,labels,description", MI_HB4001 + MI_SB304)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_the_rule_reads_the_text_not_the_organization():
    for chamber in ("upper", "lower", "legislature", None):
        assert _rule("PASSED BY HOUSE WITH IMMEDIATE EFFECT", ["passage"], chamber) == []
        assert _rule("PASSED BY 3/4 VOTE ROLL CALL # 49 YEAS 34", ["passage"], chamber) == ["passage"]


def test_state_rule_finds_michigans_actions_only():
    assert state_rule("mi", "classify_action") is classify_action
    assert state_rule("mi", "classify_bill") is None


def _mi():
    _jurisdiction(MI, "Michigan", session="2025-2026", chambers=("lower", "upper", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_michigans_rule_and_a_rescrape_changes_nothing():
    _mi()

    BillImporter(MI).import_item(_scraped(MI_HB4001, "HB 4001", session="2025-2026", chamber="lower"))
    assert _stored("HB 4001") == MI_HB4001_RELABELLED
    # the bill's passage date is the House's roll call, not the day the Senate read the message
    assert Bill.objects.get(identifier="HB 4001").latest_passage_date == "2025-01-23"

    _, what = BillImporter(MI).import_item(_scraped(MI_HB4001, "HB 4001", session="2025-2026", chamber="lower"))
    assert what == "noop"
    assert _stored("HB 4001") == MI_HB4001_RELABELLED


@pytest.mark.django_db
def test_the_importer_keeps_both_chambers_roll_calls_on_a_senate_bill():
    _mi()

    BillImporter(MI).import_item(_scraped(MI_SB304, "SB 304", session="2025-2026", chamber="upper"))

    assert _stored("SB 304") == MI_SB304_RELABELLED
    assert Bill.objects.get(identifier="SB 304").latest_passage_date == "2026-05-21"


@pytest.mark.django_db
def test_relabel_mi_now_runs_and_finds_the_stored_message_line():
    bill = _bill(MI, "Michigan", "HB 4001", MI_HB4001)
    before = _labels(bill)

    result = _relabel_dry_run("mi")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=1 unchanged=14" in result.output
    assert "action: passage -> (none): 1" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _mi()
    BillImporter(MI).import_data(
        [
            _scraped(MI_HB4001, "HB 4001", session="2025-2026", chamber="lower"),
            _scraped(MI_SB304, "SB 304", session="2025-2026", chamber="upper"),
        ]
    )

    result = _relabel_dry_run("mi")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=26" in result.output
