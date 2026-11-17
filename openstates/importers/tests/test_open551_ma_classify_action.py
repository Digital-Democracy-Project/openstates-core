"""OPEN-551: Massachusetts' `classify_action` (openstates/ddp_states/ma.py), applied on import (OPEN-550) and by
`os-text-extract relabel ma`.

The actions are real, in stored order, from the Mac replica (2026-11-17): Massachusetts H 4240 (194th, the FY2026
budget), cut to the lines that matter plus their neighbours, with the labels the scraper's categorizer gives them.
They hold the only "Signed (in part)" and "vetoes and amendments to certain sections" lines stored.
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.ma import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

MA = "ocd-jurisdiction/country:us/state:ma/government"

# (date, chamber, labels as scraped, text)
MA_H4240 = [
    ("2025-06-30", "lower", ["passage"], "Enacted -\r\n139 YEAS to 6 NAYS (See YEA and NAY No. 62 )"),
    ("2025-06-30", "upper", ["passage"], "Enacted and laid before the Governor"),
    ("2025-07-04", "executive", [], "Signed (in part) by the Governor, Chapter 9 of the Acts of 2025"),
    (
        "2025-07-04",
        "lower",
        ["executive-veto"],
        "Governor returned to the House with vetoes and amendments to certain sections",
    ),
    ("2025-07-10", "lower", [], "For message, see H4250"),
    (
        "2025-10-08",
        "lower",
        ["veto-override-passage"],
        "Item 0330-0300 passed over veto - 132 YEAS to 23 NAYS (See YEA and NAY No. 74 )",
    ),
]
# the same, as Massachusetts' rule reads them: the signature in part is the signature, the line-item vetoes no veto
MA_H4240_RELABELLED = [["passage"], ["passage"], ["executive-signature"], [], [], ["veto-override-passage"]]


def _rule(description, labels, chamber="executive", date="2025-07-04"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        ("Signed (in part) by the Governor, Chapter 9 of the Acts of 2025", [], ["executive-signature"]),
        (
            "Governor returned to the House with vetoes and amendments to certain sections",
            ["executive-veto"],
            [],
        ),
        # labels other than the one the rule reads are kept, in order, and a signature is added at the end
        (
            "Governor returned to the House with vetoes and amendments to certain sections",
            ["filing", "executive-veto", "reading-1"],
            ["filing", "reading-1"],
        ),
        (
            "Signed (in part) by the Governor, Chapter 9 of the Acts of 2025",
            ["became-law"],
            ["became-law", "executive-signature"],
        ),
        # already tagged: no second signature
        (
            "Signed (in part) by the Governor, Chapter 9 of the Acts of 2025",
            ["executive-signature"],
            ["executive-signature"],
        ),
        # a whole signature, and a real veto, are left as they are
        ("Signed by the Governor, Chapter 89 of the Acts of 2025", ["executive-signature"], ["executive-signature"]),
        ("Vetoed by the Governor, see H4321", ["executive-veto"], ["executive-veto"]),
        # "in part" elsewhere is not a signature
        ("Reported, in part, by H1094", [], []),
        ("Discharged (in part) by S2907", [], []),
    ],
)
def test_line_item_vetoes_are_no_veto_and_a_signature_in_part_is_a_signature(description, labels, expected):
    assert _rule(description, labels) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = []
    out = _rule("Signed (in part) by the Governor, Chapter 9 of the Acts of 2025", labels)
    assert out == ["executive-signature"] and labels == []
    vetoed = ["executive-veto"]
    _rule("Governor returned to the House with vetoes and amendments to certain sections", vetoed)
    assert vetoed == ["executive-veto"]
    same = ["passage"]
    assert _rule("Enacted and laid before the Governor", same) is not same


@pytest.mark.parametrize("date,chamber,labels,description", MA_H4240)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_a_line_matching_both_checks_gets_both_corrections_in_one_call():
    # pm-review (OPEN-551): the veto check used to return early, so a second call added the signature
    description = "Signed (in part) by the Governor; returned with vetoes and amendments to certain sections"
    once = classify_action(
        description=description, classification=["executive-veto"], chamber="executive", date="2025-07-04"
    )
    assert once == ["executive-signature"]
    assert classify_action(description=description, classification=once, chamber="executive", date="2025-07-04") == once


def test_state_rule_finds_massachusetts_actions_only():
    assert state_rule("ma", "classify_action") is classify_action
    # bill labels stay with relabel-non-bills (OPEN-517)
    assert state_rule("ma", "classify_bill") is None


def _ma():
    _jurisdiction(MA, "Massachusetts", session="194th", chambers=("lower", "upper", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_massachusetts_rule_and_a_rescrape_changes_nothing():
    _ma()

    BillImporter(MA).import_item(_scraped(MA_H4240, "H 4240", session="194th", chamber="lower"))
    assert _stored("H 4240") == MA_H4240_RELABELLED

    _, what = BillImporter(MA).import_item(_scraped(MA_H4240, "H 4240", session="194th", chamber="lower"))
    assert what == "noop"
    assert _stored("H 4240") == MA_H4240_RELABELLED
    assert Bill.objects.get().classification == ["bill"]


@pytest.mark.django_db
def test_relabel_ma_now_runs_and_finds_the_two_stored_lines():
    bill = _bill(MA, "Massachusetts", "H 4240", MA_H4240)
    before = _labels(bill)

    result = _relabel_dry_run("ma")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=2 unchanged=4" in result.output
    assert "action: executive-veto -> (none): 1" in result.output
    assert "action: (none) -> executive-signature: 1" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _ma()
    BillImporter(MA).import_data([_scraped(MA_H4240, "H 4240", session="194th", chamber="lower")])

    result = _relabel_dry_run("ma")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=6" in result.output
