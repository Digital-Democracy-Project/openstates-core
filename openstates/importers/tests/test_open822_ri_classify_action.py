"""OPEN-822: Rhode Island's `classify_action` (openstates/ddp_states/ri.py), applied on import (OPEN-550) and by
`os-text-extract relabel ri`.

The actions are real, in stored order, from the Mac replica (2026-11-24, session 2026), with the labels they are stored
with and the organization they are stored under: Rhode Island HB 7434 whole (held for further study on 03/04, then
reported, passed both chambers and signed), the most common shape of the 602 held bills that later passed a chamber.
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.ri import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

RI = "ocd-jurisdiction/country:us/state:ri/government"
SESSION = "2026"
HELD = "Committee recommended measure be held for further study"

# (date, chamber, labels as stored, text)
RI_HB7434 = [
    (
        "2026-01-30",
        "lower",
        ["introduction", "referral-committee"],
        "01/30/2026 Introduced, referred to House Judiciary",
    ),
    ("2026-02-27", "legislature", [], "02/27/2026 Scheduled for hearing and/or consideration  (03/04/2026)"),
    ("2026-03-04", "legislature", ["committee-failure"], f"03/04/2026 {HELD}"),
    ("2026-06-06", "legislature", [], "06/06/2026 Scheduled for consideration  (06/08/2026)"),
    ("2026-06-06", "legislature", ["substitution"], "06/06/2026 Proposed Substitute"),
    ("2026-06-08", "legislature", ["committee-passage-favorable"], "06/08/2026 Committee recommends passage of Sub A"),
    ("2026-06-08", "lower", ["reading-3"], "06/08/2026 Placed on House Calendar  (06/10/2026)"),
    ("2026-06-10", "lower", ["passage"], "06/10/2026 House passed Sub A"),
    ("2026-06-10", "upper", ["reading-3"], "06/10/2026 Placed on Senate Calendar  (06/11/2026)"),
    ("2026-06-11", "upper", ["passage"], "06/11/2026 Senate passed Sub A in concurrence"),
    ("2026-06-18", "executive", ["executive-receipt"], "06/18/2026 Transmitted to Governor"),
    ("2026-06-22", "executive", ["executive-signature"], "06/22/2026 Signed by Governor"),
]
# the same, as Rhode Island's rule reads them: held for further study is not the bill's failure
RI_HB7434_RELABELLED = [list(labels) for _, _, labels, _ in RI_HB7434]
RI_HB7434_RELABELLED[2] = []

# One real stored line for every other label set Rhode Island stores (Mac replica, 2026), with its organization: none
# of them changes.
RI_CONTROL = [
    ("lower", ["introduction", "referral-committee"], "02/27/2026 Introduced, referred to House Finance"),
    ("legislature", [], "05/29/2026 Scheduled for consideration  (06/02/2026)"),
    ("legislature", [], "04/24/2026 Scheduled for hearing and/or consideration  (04/28/2026)"),
    ("upper", ["passage"], "06/04/2026 Senate read and passed"),
    ("lower", ["passage"], "06/08/2026 House passed in concurrence"),
    ("legislature", ["committee-passage-favorable"], "06/02/2026 Committee recommends passage"),
    ("upper", ["reading-3"], "06/02/2026 Placed on Senate Calendar  (06/04/2026)"),
    ("upper", ["referral-committee"], "05/01/2026 Referred to Senate Judiciary"),
    ("executive", ["executive-receipt"], "06/16/2026 Transmitted to Governor"),
    ("executive", ["executive-signature"], "06/23/2026 Signed by Governor"),
    ("legislature", ["substitution"], "06/01/2026 Proposed Substitute"),
    ("executive", ["became-law"], "06/26/2026 Effective without Governor's signature"),
    ("legislature", ["withdrawal"], "03/06/2026 Withdrawn at sponsor's request"),
    ("legislature", ["withdrawal"], "Withdrawn at sponsor's request  (04/07/2026)"),
    ("executive", ["executive-veto"], "06/24/2026 Vetoed by Governor"),
    # held for further study after a substitute: stored with `substitution` only, and keeps it
    ("legislature", ["substitution"], "04/28/2026 Committee recommended Substitute A be held for further study"),
]


def _rule(description, labels, chamber="legislature", date="2026-04-28"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        # the stored text, with the site's date prefix (all 2,222 stored lines), and without it
        (f"04/28/2026 {HELD}", ["committee-failure"], []),
        (HELD, ["committee-failure"], []),
        (f"04/28/2026 {HELD}", ["committee-failure", "referral-committee"], ["referral-committee"]),
        # nothing is added: a held line without the label stays without it
        (f"04/28/2026 {HELD}", [], []),
        # committee-failure on any other line is a real failure and stays
        ("04/28/2026 Committee recommended measure be defeated", ["committee-failure"], ["committee-failure"]),
        ("", ["committee-failure"], ["committee-failure"]),
    ],
)
def test_only_held_for_further_study_loses_committee_failure(description, labels, expected):
    assert _rule(description, labels) == expected


@pytest.mark.parametrize("chamber,labels,description", RI_CONTROL)
def test_every_other_rhode_island_line_keeps_its_labels(chamber, labels, description):
    assert _rule(description, labels, chamber) == labels


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["committee-failure"]
    assert _rule(HELD, labels) == [] and labels == ["committee-failure"]
    same = ["passage"]
    assert _rule("06/04/2026 Senate read and passed", same) is not same
    assert classify_action(description=None, classification=["committee-failure"], chamber=None, date=None) == [
        "committee-failure"
    ]


@pytest.mark.parametrize("date,chamber,labels,description", RI_HB7434)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_state_rule_finds_rhode_islands_actions_only():
    assert state_rule("ri", "classify_action") is classify_action
    assert state_rule("ri", "classify_bill") is None


def _ri():
    _jurisdiction(RI, "Rhode Island", session=SESSION, chambers=("lower", "upper", "legislature", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_rhode_islands_rule_and_a_rescrape_changes_nothing():
    _ri()

    BillImporter(RI).import_item(_scraped(RI_HB7434, "HB 7434", session=SESSION, chamber="lower"))
    assert _stored("HB 7434") == RI_HB7434_RELABELLED
    assert Bill.objects.get(identifier="HB 7434").latest_passage_date == "2026-06-11"

    _, what = BillImporter(RI).import_item(_scraped(RI_HB7434, "HB 7434", session=SESSION, chamber="lower"))
    assert what == "noop"
    assert _stored("HB 7434") == RI_HB7434_RELABELLED


@pytest.mark.django_db
def test_relabel_ri_now_runs_and_finds_the_held_line():
    bill = _bill(RI, "Rhode Island", "HB 7434", RI_HB7434)
    before = _labels(bill)

    result = _relabel_dry_run("ri")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=1 unchanged=11" in result.output
    assert "action: committee-failure -> (none): 1" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _ri()
    BillImporter(RI).import_item(_scraped(RI_HB7434, "HB 7434", session=SESSION, chamber="lower"))

    result = _relabel_dry_run("ri")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=12" in result.output
