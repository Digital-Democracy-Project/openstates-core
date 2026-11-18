"""OPEN-547: Utah's `classify_action` (openstates/ddp_states/ut.py), applied on import (OPEN-550) and by
`os-text-extract relabel ut`.

The actions are real, in stored order, from the Mac replica (2026-11-17), with the labels the scraper's categorizer
gave them and the organization they are stored under: Utah HB 136 (2026), cut to the lines that matter plus their
neighbours. It holds every shape the rule reads but the Lieutenant Governor line (HJR 28) and the failed floor
amendment (both in the single-line cases below).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.ut import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

UT = "ocd-jurisdiction/country:us/state:ut/government"

# (date, chamber, labels as scraped, text)
UT_HB136 = [
    ("2026-01-22", "lower", ["referral-committee"], "House/ to standing committee [House Transportation Committee]"),
    ("2026-02-09", "lower", ["failure"], "House/ substitute adoption failed"),
    ("2026-02-09", "upper", ["passage"], "House/ passed 3rd reading"),
    ("2026-02-09", "upper", [], "House/ to Senate"),
    ("2026-03-05", "upper", [], "Senate/ 2nd & 3rd readings/ suspension"),
    ("2026-03-05", "lower", [], "Senate/ passed 2nd & 3rd readings/ suspension"),
    ("2026-03-06", "upper", ["passage"], "Senate Conference Committee - Final Passage [Conference Committee]"),
    ("2026-03-06", "upper", ["passage"], "House Conference Committee - Final Passage [Senate President]"),
    ("2026-03-06", "lower", ["passage"], "Senate/ signed by President/ returned to House"),
    ("2026-03-06", "lower", ["passage"], "House/ signed by Speaker/ sent for enrolling"),
    ("2026-03-12", "executive", ["executive-receipt"], "House/ to Governor"),
    ("2026-03-26", "executive", ["executive-signature"], "Governor Signed"),
]
# the same, as Utah's rule reads them: passage on the four floor votes only (3rd reading, suspension, and each
# chamber's conference final passage, OPEN-622), no failure on a lost substitute
UT_HB136_RELABELLED = [
    ["referral-committee"],
    [],
    ["passage"],
    [],
    [],
    ["passage"],
    ["passage"],
    ["passage"],
    [],
    [],
    ["executive-receipt"],
    ["executive-signature"],
]


def _rule(description, labels, chamber="lower", date="2026-03-06"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        # the floor votes: tagged, or (suspension) not tagged by the scraper at all
        ("House/ passed 3rd reading", ["passage"], ["passage"]),
        ("Senate/ passed 3rd reading", ["passage"], ["passage"]),
        ("Senate/ passed 2nd & 3rd readings/ suspension", [], ["passage"]),
        ("House/ passed 2nd & 3rd readings/ suspension", ["reading-3"], ["reading-3", "passage"]),
        # each chamber's vote on the conference report is a floor vote (OPEN-622: HB 136, House 57-13-5, Senate
        # 20-6-3), whatever office the line names
        ("Senate Conference Committee - Final Passage [Conference Committee]", ["passage"], ["passage"]),
        ("Senate Conference Committee - Final Passage [House Speaker]", ["passage"], ["passage"]),
        ("House Conference Committee - Final Passage [Senate President]", ["passage"], ["passage"]),
        ("House Conference Committee - Final Passage [Conference Committee]", [], ["passage"]),
        # presiding officers' signatures and the other conference committee lines pass nothing
        ("House/ signed by Speaker/ sent for enrolling", ["passage"], []),
        ("Senate/ signed by President/ returned to House", ["passage"], []),
        ("House/ signed by Speaker/ returned to Senate", ["passage"], []),
        ("House Motion to Adopt Joint Conference Comm Rpt [Conference Committee]", ["passage"], []),
        ("Senate Conference Committee Appointed [Conference Committee]", ["referral-committee"], ["referral-committee"]),
        ("Conference Committee Report [Conference Committee]", ["passage"], []),
        # a third reading that is not a vote, and a bill sent on, get no passage
        ("Senate/ 2nd & 3rd readings/ suspension", [], []),
        ("House/ 3rd reading", [], []),
        # failure: only the chamber's "<chamber>/ failed" is the bill's
        ("Senate/ failed", ["failure"], ["failure"]),
        ("House/ failed", ["failure"], ["failure"]),
        ("House/ substitute adoption failed", ["failure"], []),
        ("House/ floor amendment failed", ["amendment-failure", "failure"], ["amendment-failure"]),
        ("Senate Comm - Motion to Recommend Failed [Senate Revenue and Taxation Committee]", ["failure"], []),
        # the Lieutenant Governor files a resolution; the Governor's receipt stays
        ("House/ to Lieutenant Governor", ["executive-receipt"], []),
        ("Senate/ to Governor", ["executive-receipt"], ["executive-receipt"]),
        # every other line, and every other label, as given
        ("Governor Signed", ["executive-signature"], ["executive-signature"]),
        ("Senate/ concurs with House amendment", ["amendment-passage"], ["amendment-passage"]),
        ("Senate/ failed", ["filing", "failure"], ["filing", "failure"]),
    ],
)
def test_utahs_passage_failure_and_governor_lines(description, labels, expected):
    assert _rule(description, labels) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["passage"]
    out = _rule("House/ signed by Speaker/ sent for enrolling", labels)
    assert out == [] and labels == ["passage"]
    empty = []
    assert _rule("Senate/ passed 2nd & 3rd readings/ suspension", empty) == ["passage"] and empty == []
    same = ["executive-signature"]
    assert _rule("Governor Signed", same) is not same


@pytest.mark.parametrize("date,chamber,labels,description", UT_HB136)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_the_rule_reads_the_text_not_the_organization():
    # a passage line is often stored under the chamber the bill goes to next; the label does not depend on it
    for chamber in ("upper", "lower", "legislature", None):
        assert _rule("House/ passed 3rd reading", ["passage"], chamber) == ["passage"]
        assert _rule("House/ signed by Speaker/ sent for enrolling", ["passage"], chamber) == []


def test_state_rule_finds_utahs_actions_only():
    assert state_rule("ut", "classify_action") is classify_action
    assert state_rule("ut", "classify_bill") is None


def _ut():
    _jurisdiction(UT, "Utah", session="2026", chambers=("lower", "upper", "legislature", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_utahs_rule_and_a_rescrape_changes_nothing():
    _ut()

    BillImporter(UT).import_item(_scraped(UT_HB136, "HB 136", session="2026", chamber="lower"))
    assert _stored("HB 136") == UT_HB136_RELABELLED
    # the bill's passage date is its last floor vote (the conference final passage), not the Speaker's signature
    assert Bill.objects.get().latest_passage_date == "2026-03-06"

    _, what = BillImporter(UT).import_item(_scraped(UT_HB136, "HB 136", session="2026", chamber="lower"))
    assert what == "noop"
    assert _stored("HB 136") == UT_HB136_RELABELLED


@pytest.mark.django_db
def test_relabel_ut_now_runs_and_finds_the_stored_lines_the_rule_changes():
    bill = _bill(UT, "Utah", "HB 136", UT_HB136)
    before = _labels(bill)

    result = _relabel_dry_run("ut")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=4 unchanged=8" in result.output
    assert "action: passage -> (none): 2" in result.output
    assert "action: failure -> (none): 1" in result.output
    assert "action: (none) -> passage: 1" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _ut()
    BillImporter(UT).import_data([_scraped(UT_HB136, "HB 136", session="2026", chamber="lower")])

    result = _relabel_dry_run("ut")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=12" in result.output
