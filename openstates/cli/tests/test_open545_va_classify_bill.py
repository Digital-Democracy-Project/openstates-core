"""OPEN-545: Virginia's `classify_bill` (openstates/ddp_states/va.py), and `os-text-extract relabel va` applying it to
the stored history with the real module (no stand-in rule).

The bills are real, from the Mac replica of production (2026-11-17, Virginia 2026): their numbers, the labels the
scraper stored (every resolution `bill`; a `constitutional amendment` when the title says so) and, for HR 1 and SJ 10,
their stored actions in order.
"""

from unittest import mock

import pytest
from click.testing import CliRunner

from openstates.cli.tests.test_open546_relabel import VA, _actions, _bill, _fresh, _labels, _rows
from openstates.cli.text_extract import relabel
from openstates.ddp_states import state_rule
from openstates.ddp_states.va import classify_bill

HR1 = [
    ("2025-11-24", "lower", ["filing"], "Prefiled and laid on Speaker's table; Offered 01-14-2026 26101082D"),
    ("2026-01-19", "lower", [], "Engrossed by House"),
    ("2026-01-19", "lower", [], "Agreed to by House"),
    ("2026-04-16", "lower", ["passage"], "Bill text as passed House (HR1ER)"),
]
SJ10 = [
    ("2025-12-03", "upper", ["filing"], "Prefiled and laid on Clerk's desk; Offered 01-14-2026 26100026D"),
    ("2026-01-15", "upper", ["passage"], "Agreed to by Senate by voice vote (Voice Vote)"),
    ("2026-01-16", "lower", [], "Received"),
    ("2026-01-16", "lower", [], "Laid on Speaker's table"),
    ("2026-01-19", "lower", ["passage"], "Agreed to by House by voice vote"),
    ("2026-01-21", "upper", ["passage"], "Bill text as passed Senate and House (SJ10ER)"),
]

# (number, title, stored labels, labels by Virginia's rule)
REAL = [
    ("HB 1", "Minimum wage; increases incrementally to $15.00 per hour by January 1, 2028.", ["bill"], ["bill"]),
    ("SB 1", "Minimum wage; increases incrementally to $15.00 per hour by January 1, 2028.", ["bill"], ["bill"]),
    ("HR 1", "Celebrating the life of Christina Kaye Shinall.", ["bill"], ["resolution"]),
    ("SR 1", "Commending Pat B. Huber.", ["bill"], ["resolution"]),
    ("HJ 10", "Commending Charles W. Foster, Jr.", ["bill"], ["joint resolution"]),
    ("SJ 10", "Commending Historic Huntley.", ["bill"], ["joint resolution"]),
    # a label the scraper read from the title is kept, whatever the number
    (
        "HJ 1",
        "Constitutional amendment (second reference); fundamental right to reproductive freedom.",
        ["constitutional amendment"],
        ["constitutional amendment"],
    ),
    (
        "HR 5",
        "Right to reproductive freedom; legal effects & consequences of a constitutional amendment.",
        ["constitutional amendment"],
        ["constitutional amendment"],
    ),
    (
        "HB 1384",
        "Constitutional amendment; gen. approp. act, reapportionment, redistricting congressional districts.",
        ["constitutional amendment"],
        ["constitutional amendment"],
    ),
]


@pytest.mark.parametrize("identifier,title,stored,expected", REAL)
def test_the_bill_number_says_what_a_virginia_item_is(identifier, title, stored, expected):
    assert classify_bill(identifier=identifier, title=title, classification=stored) == expected


@pytest.mark.parametrize(
    "identifier,expected",
    # the LIS API's own form ("HR1"), stray spaces and case read the same; anything else is left alone
    [("HR1", ["resolution"]), (" hj 10", ["joint resolution"]), ("HB1", ["bill"]), ("", ["bill"]), (None, ["bill"])],
)
def test_the_number_is_read_whatever_its_spacing(identifier, expected):
    assert classify_bill(identifier=identifier, title=None, classification=["bill"]) == expected


@pytest.mark.parametrize("identifier,title,stored,expected", REAL)
def test_the_rule_is_stable_on_its_own_output(identifier, title, stored, expected):
    once = classify_bill(identifier=identifier, title=title, classification=stored)
    assert classify_bill(identifier=identifier, title=title, classification=once) == once


@pytest.mark.parametrize(
    "identifier,expected",
    # the scraper's order on a `bill` whose title names a constitutional amendment (none is stored so today): the
    # title first, whatever the number, so a relabel and a re-scrape agree (pm-review)
    [
        ("HJ 1", ["constitutional amendment"]),
        ("HR 5", ["constitutional amendment"]),
        ("HB 1384", ["constitutional amendment"]),
    ],
)
def test_a_title_naming_a_constitutional_amendment_comes_first(identifier, expected):
    title = "Constitutional amendment (second reference); fundamental right to reproductive freedom."
    assert classify_bill(identifier=identifier, title=title, classification=["bill"]) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["bill"]
    out = classify_bill(identifier="HR 1", title=None, classification=labels)
    assert out == ["resolution"] and labels == ["bill"]


def test_state_rule_finds_virginia():
    assert state_rule("va", "classify_bill") is classify_bill
    # Virginia's action rule is OPEN-549's (openstates/importers/tests/test_open549_va_classify_action.py)
    from openstates.ddp_states.va import classify_action

    assert state_rule("va", "classify_action") is classify_action


def _relabel(*args):
    # the real module through the real state_rule: only Django set-up and the state's id are stood in
    with mock.patch("openstates.cli.text_extract.init_django"), mock.patch(
        "openstates.cli.text_extract.abbr_to_jid", return_value=VA
    ):
        result = CliRunner().invoke(relabel, ["va", *args])
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture
def stored(db):
    actions = {"HR 1": HR1, "SJ 10": SJ10}
    bills = {}
    for identifier, title, labels, _ in REAL:
        bills[identifier] = _bill(VA, "Virginia", identifier, actions.get(identifier, []), classification=labels)
        bills[identifier].title = title
        bills[identifier].save()
    return bills


def test_relabel_va_changes_only_the_resolutions_labels(stored, tmp_path):
    before = {k: (_actions(b), _labels(b), _fresh(b).updated_at) for k, b in stored.items()}

    dry = _relabel("--dry-run")
    assert "bills changed=4 unchanged=5" in dry
    assert {k: (_actions(b), _labels(b), _fresh(b).updated_at) for k, b in stored.items()} == before

    out = _relabel("--commit", "--expect", "4", "--list", str(tmp_path / "va.csv"))

    assert "bills changed=4 unchanged=5" in out
    assert "bill: bill -> resolution: 2" in out and "bill: bill -> joint resolution: 2" in out
    assert {k: _fresh(b).classification for k, b in stored.items()} == {k: new for k, _, _, new in REAL}
    # each bill's action sequence, action labels and updated_at are exactly as stored: only the bill label moved
    assert {k: (_actions(b), _labels(b), _fresh(b).updated_at) for k, b in stored.items()} == before
    assert sorted((r["identifier"], r["old"], r["new"]) for r in _rows(tmp_path / "va.csv")) == [
        ("HJ 10", "bill", "joint resolution"),
        ("HR 1", "bill", "resolution"),
        ("SJ 10", "bill", "joint resolution"),
        ("SR 1", "bill", "resolution"),
    ]

    # a second run (or a re-scrape that labels the same way) finds nothing left to change
    assert "bills changed=0 unchanged=9" in _relabel("--dry-run")
