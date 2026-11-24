"""OPEN-845: Kentucky's `classify_action` (openstates/ddp_states/ky.py), applied on import (OPEN-550) and by
`os-text-extract relabel ky`.

The actions are real, in stored order, from the Mac replica (2026-11-24, session 2026RS), with the labels the scraper's
categorizer gave them and the organization they are stored under: SB 137 (passed over twice, passed by the Senate,
passed by the House, signed) and SJR 62 (passed over, then adopted by the Senate 38-0 the next day).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.ddp_states import state_rule
from openstates.ddp_states.ky import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

KY = "ocd-jurisdiction/country:us/state:ky/government"

# (date, chamber, labels as scraped, text)
KY_SB137 = [
    ("2026-01-29", "upper", ["introduction"], "introduced in Senate"),
    (
        "2026-02-25",
        "upper",
        ["committee-passage"],
        "reported favorably, 1st reading, to Calendar with Committee Substitute (1)",
    ),
    ("2026-03-02", "upper", [], "posted for passage in the Regular Orders of the Day for Tuesday, March 03 2026"),
    ("2026-03-03", "upper", ["passage"], "passed over and retained in the Orders of the Day"),
    ("2026-03-04", "upper", ["passage"], "passed over and retained in the Orders of the Day"),
    ("2026-03-05", "upper", ["reading-3"], "3rd reading"),
    ("2026-03-05", "upper", ["passage"], "passed 30-7 with Committee Substitute (1)"),
    ("2026-03-06", "lower", [], "received in House"),
    ("2026-03-31", "upper", ["passage", "reading-3"], "3rd reading, passed 79-11"),
    ("2026-03-31", "upper", ["passage"], "enrolled, signed by President of the Senate"),
    ("2026-03-31", "lower", ["passage"], "enrolled, signed by Speaker of the House"),
    ("2026-04-10", "upper", ["executive-signature"], "signed by Governor (Acts Ch. 89)"),
]
# the two deferrals lose passage; the votes and (on purpose, see ky.py) the signatures keep it
KY_SB137_RELABELLED = [list(labels) for _, _, labels, _ in KY_SB137]
KY_SB137_RELABELLED[3] = KY_SB137_RELABELLED[4] = []

KY_SJR62 = [
    ("2026-01-27", "upper", ["introduction"], "introduced in Senate"),
    ("2026-03-05", "upper", ["reading-2"], "2nd reading, to Rules"),
    ("2026-03-09", "upper", [], "posted for passage in the Regular Orders of the Day for Wednesday, March 11 2026"),
    ("2026-03-11", "upper", ["passage"], "passed over and retained in the Orders of the Day"),
    ("2026-03-12", "upper", ["reading-3"], "3rd reading, adopted 38-0"),
    ("2026-03-13", "lower", [], "received in House"),
]
# the Senate's passage moves from the deferral (3/11) to its vote (3/12)
KY_SJR62_RELABELLED = [["introduction"], ["reading-2"], [], [], ["reading-3", "passage"], []]


def _rule(description, labels, chamber="upper", date="2026-03-11"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        # every deferral shape stored for Kentucky loses passage, and nothing is added
        ("passed over and retained in the Orders of the Day", ["passage"], []),
        ("passed over and retained in the Consent Orders of the Day", ["passage"], []),
        (
            "passed over and retained for concurrence in House Floor Amendment (1) and  Floor Amendment (2-title)",
            ["passage"],
            [],
        ),
        (
            "passed over and retained for concurrence in House Committee Substitute (1),  Floor Amendment (1) and  "
            "Committee Amendment (1-title)",
            ["passage"],
            [],
        ),
        ("passed over and retained for concurrence in House Committee Amendment (1)", ["passage"], []),
        # a resolution's roll-call adoption on third reading is its passage
        ("3rd reading, adopted 38-0", ["reading-3"], ["reading-3", "passage"]),
        (
            "3rd reading, adopted 86-4 with Committee Substitute (1) and  Floor Amendments (4), (3) and (2)",
            ["reading-3"],
            ["reading-3", "passage"],
        ),
        (
            "3rd reading, adopted with 87-1 Committee Substitute (1) and  Committee Amendment (1-title)",
            ["reading-3"],
            ["reading-3", "passage"],
        ),
        ("3rd reading, adopted with Committee Substitute (1)", ["reading-3"], ["reading-3", "passage"]),
        # the real votes keep passage
        ("3rd reading, passed 38-0", ["passage", "reading-3"], ["passage", "reading-3"]),
        (
            "3rd reading, passed 37-0 with Committee Substitute (1) and  Committee Amendment (1-title)",
            ["passage", "reading-3"],
            ["passage", "reading-3"],
        ),
        ("3rd reading, passed 36-0-1", ["passage", "reading-3"], ["passage", "reading-3"]),
        ("passed 32-6", ["passage"], ["passage"]),
        ("passed 30-7 with Committee Substitute (1)", ["passage"], ["passage"]),
        ("adopted by voice vote", ["passage"], ["passage"]),
        # held on purpose (ky.py): the signatures are the only lines stored under the second chamber
        ("enrolled, signed by President of the Senate", ["passage"], ["passage"]),
        ("enrolled, signed by Speaker of the House", ["passage"], ["passage"]),
        # left alone: a simple resolution's bare adoption, the veto override's vote, the calendar line, the rest
        ("adopted 38-0", [], []),
        ("posted for passage in the Regular Orders of the Day for Tuesday, March 03 2026", [], []),
        ("3rd reading", ["reading-3"], ["reading-3"]),
        ("veto overridden", ["veto-override-passage"], ["veto-override-passage"]),
        ("Free Conference Committee report adopted in House", [], []),
        ("signed by Governor (Acts Ch. 89)", ["executive-signature"], ["executive-signature"]),
        ("delivered to Secretary of State (Acts Ch. 160)", ["became-law"], ["became-law"]),
    ],
)
def test_kentuckys_deferral_and_resolution_adoption_lines(description, labels, expected):
    assert _rule(description, labels) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["passage"]
    assert _rule("passed over and retained in the Orders of the Day", labels) == [] and labels == ["passage"]
    reading = ["reading-3"]
    assert _rule("3rd reading, adopted 38-0", reading) == ["reading-3", "passage"] and reading == ["reading-3"]
    same = ["passage"]
    assert _rule("passed 32-6", same) is not same


@pytest.mark.parametrize("date,chamber,labels,description", KY_SB137 + KY_SJR62)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_state_rule_finds_kentuckys_actions_only():
    assert state_rule("ky", "classify_action") is classify_action
    assert state_rule("ky", "classify_bill") is None


def _ky():
    _jurisdiction(KY, "Kentucky", session="2026RS", chambers=("lower", "upper", "legislature", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_kentuckys_rule_and_a_rescrape_changes_nothing():
    _ky()

    BillImporter(KY).import_data(
        [
            _scraped(KY_SB137, "SB 137", session="2026RS", chamber="upper"),
            _scraped(KY_SJR62, "SJR 62", session="2026RS", chamber="upper"),
        ]
    )
    assert _stored("SB 137") == KY_SB137_RELABELLED
    assert _stored("SJR 62") == KY_SJR62_RELABELLED

    _, what = BillImporter(KY).import_item(_scraped(KY_SJR62, "SJR 62", session="2026RS", chamber="upper"))
    assert what == "noop"
    assert _stored("SJR 62") == KY_SJR62_RELABELLED


@pytest.mark.django_db
def test_relabel_ky_now_runs_and_finds_the_stored_lines_the_rule_changes():
    bill = _bill(KY, "Kentucky", "SJR 62", KY_SJR62, classification=("joint resolution",))
    before = _labels(bill)

    result = _relabel_dry_run("ky")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=2 unchanged=4" in result.output
    assert "action: passage -> (none): 1" in result.output
    assert "action: reading-3 -> reading-3, passage: 1" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _ky()
    BillImporter(KY).import_data([_scraped(KY_SB137, "SB 137", session="2026RS", chamber="upper")])

    result = _relabel_dry_run("ky")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=12" in result.output
