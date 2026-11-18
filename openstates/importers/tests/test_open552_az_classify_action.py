"""OPEN-552: Arizona's `classify_action` (openstates/ddp_states/az.py), applied on import (OPEN-550) and by
`os-text-extract relabel az`.

The actions are real, in stored order, from the Mac replica (2026-11-17, session 57th-2nd-regular), with the labels
they are stored with and the organization they are stored under: Arizona HB 2946 whole (a third-reading "FAILED", then
the same day's motion to reconsider stored as "PASSED", then passed and signed), and HB 2689 whole (its only floor
action is "FAILED", stored without `reading-3`).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.az import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

AZ = "ocd-jurisdiction/country:us/state:az/government"
SESSION = "57th-2nd-regular"

# (date, chamber, labels as stored, text)
AZ_HB2946 = [
    ("2026-02-02", "lower", ["reading-1"], "House First Reading."),
    ("2026-02-03", "lower", ["reading-2"], "House Second Reading"),
    ("2026-02-19", "lower", ["amendment-passage"], "DPA"),
    ("2026-03-02", "lower", ["amendment-passage"], "DPA"),
    ("2026-03-03", "lower", ["failure", "reading-3"], "FAILED"),
    ("2026-03-03", "lower", ["passage"], "PASSED"),
    ("2026-03-10", "lower", ["passage"], "PASSED"),
    ("2026-03-10", "lower", ["amendment-passage"], "DPA"),
    ("2026-03-11", "lower", ["passage", "reading-3"], "PASSED"),
    ("2026-03-11", "lower", [], "Transmit to Senate"),
    ("2026-03-17", "upper", ["reading-1"], "Senate First Reading"),
    ("2026-03-18", "upper", ["reading-2"], "Senate Second Reading"),
    ("2026-03-25", "upper", ["amendment-passage"], "DPA"),
    ("2026-04-14", "upper", ["amendment-passage"], "DPA"),
    ("2026-04-14", "upper", ["passage", "reading-3"], "PASSED"),
    ("2026-04-14", "upper", [], "Transmit to House"),
    ("2026-06-01", "lower", ["passage"], "PASSED"),
    ("2026-06-01", "lower", ["passage"], "PASSED"),
    ("2026-06-01", "executive", ["executive-receipt"], "Transmit to Governor"),
    ("2026-06-04", "executive", ["executive-signature"], "Signed by Governor"),
]
# the same, as Arizona's rule reads them: the bare "FAILED" is not the bill's failure (reconsidered, passed 3/11)
AZ_HB2946_RELABELLED = [list(labels) for _, _, labels, _ in AZ_HB2946]
AZ_HB2946_RELABELLED[4] = ["reading-3"]

AZ_HB2689 = [
    ("2026-01-20", "lower", ["reading-1"], "House First Reading."),
    ("2026-01-21", "lower", ["reading-2"], "House Second Reading"),
    ("2026-01-26", "lower", ["committee-passage"], "DP"),
    ("2026-02-02", "lower", [], "House Placed on Consent Calendar"),
    ("2026-02-11", "lower", ["informal-passage"], "DP"),
    ("2026-02-11", "lower", ["failure"], "FAILED"),
]
AZ_HB2689_RELABELLED = [list(labels) for _, _, labels, _ in AZ_HB2689]
AZ_HB2689_RELABELLED[5] = []


def _rule(description, labels, chamber="lower", date="2026-03-03"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        # both stored shapes of the bare line (Mac replica, 2026: 107 and 68) lose failure
        ("FAILED", ["failure", "reading-3"], ["reading-3"]),
        ("FAILED", ["failure"], []),
        # committee-failure goes too, as in the broker's reader (none stored today)
        ("FAILED", ["committee-failure"], []),
        ("FAILED", ["committee-failure", "failure", "reading-2"], ["reading-2"]),
        # the shapes the broker's pattern also takes: any case, a full stop, surrounding spaces
        ("Failed", ["failure"], []),
        ("FAILED.", ["failure", "reading-3"], ["reading-3"]),
        ("  FAILED  ", ["failure"], []),
        # a line that says more than the bare word keeps its labels: it names what failed
        ("FAILED TO PASS", ["failure"], ["failure"]),
        ("MOTION FAILED", ["failure"], ["failure"]),
        ("FAILED; 25-35", ["failure", "reading-3"], ["failure", "reading-3"]),
        # nothing is added, and other labels are never touched, nor is their order
        ("FAILED", [], []),
        ("FAILED", ["reading-3", "failure", "amendment-failure"], ["reading-3", "amendment-failure"]),
        ("PASSED", ["passage", "reading-3"], ["passage", "reading-3"]),
        ("Vetoed by Governor", ["executive-veto"], ["executive-veto"]),
        ("", ["failure"], ["failure"]),
    ],
)
def test_only_the_bare_failed_line_loses_failure(description, labels, expected):
    assert _rule(description, labels) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["failure", "reading-3"]
    assert _rule("FAILED", labels) == ["reading-3"] and labels == ["failure", "reading-3"]
    same = ["passage"]
    assert _rule("PASSED", same) is not same
    assert classify_action(description=None, classification=["failure"], chamber=None, date=None) == ["failure"]


@pytest.mark.parametrize("date,chamber,labels,description", AZ_HB2946 + AZ_HB2689)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_the_rule_reads_the_text_not_the_organization():
    for chamber in ("upper", "lower", "executive", None):
        assert _rule("FAILED", ["failure", "reading-3"], chamber) == ["reading-3"]
        assert _rule("FAILED TO PASS", ["failure"], chamber) == ["failure"]


def test_state_rule_finds_arizonas_actions_only():
    assert state_rule("az", "classify_action") is classify_action
    assert state_rule("az", "classify_bill") is None


def _az():
    _jurisdiction(AZ, "Arizona", session=SESSION, chambers=("lower", "upper", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_arizonas_rule_and_a_rescrape_changes_nothing():
    _az()

    BillImporter(AZ).import_item(_scraped(AZ_HB2946, "HB 2946", session=SESSION, chamber="lower"))
    assert _stored("HB 2946") == AZ_HB2946_RELABELLED
    # the labels it removes do not feed the passage date: still the House's last vote
    assert Bill.objects.get(identifier="HB 2946").latest_passage_date == "2026-06-01"

    _, what = BillImporter(AZ).import_item(_scraped(AZ_HB2946, "HB 2946", session=SESSION, chamber="lower"))
    assert what == "noop"
    assert _stored("HB 2946") == AZ_HB2946_RELABELLED


@pytest.mark.django_db
def test_the_importer_takes_failure_off_a_bill_whose_only_floor_line_is_failed():
    _az()

    BillImporter(AZ).import_item(_scraped(AZ_HB2689, "HB 2689", session=SESSION, chamber="lower"))

    assert _stored("HB 2689") == AZ_HB2689_RELABELLED
    assert Bill.objects.get(identifier="HB 2689").latest_passage_date is None


@pytest.mark.django_db
def test_relabel_az_now_runs_and_finds_the_stored_failed_lines():
    hb2946 = _bill(AZ, "Arizona", "HB 2946", AZ_HB2946)
    hb2689 = _bill(AZ, "Arizona", "HB 2689", AZ_HB2689)
    before = (_labels(hb2946), _labels(hb2689))

    result = _relabel_dry_run("az")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=2 unchanged=24" in result.output
    assert "action: failure -> (none): 1" in result.output
    assert "action: failure, reading-3 -> reading-3: 1" in result.output
    assert "latest_passage_date=0" in result.output
    assert (_labels(hb2946), _labels(hb2689)) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _az()
    BillImporter(AZ).import_data(
        [
            _scraped(AZ_HB2946, "HB 2946", session=SESSION, chamber="lower"),
            _scraped(AZ_HB2689, "HB 2689", session=SESSION, chamber="lower"),
        ]
    )

    result = _relabel_dry_run("az")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=26" in result.output
