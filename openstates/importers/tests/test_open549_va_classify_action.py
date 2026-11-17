"""OPEN-549: Virginia's `classify_action` (openstates/ddp_states/va.py), applied on import (OPEN-550) and by
`os-text-extract relabel va`, which now applies it together with Virginia's `classify_bill` (OPEN-545).

The actions are real, in stored order, from the Mac replica (2026-11-17, session 2026), with the labels they are
stored with and the organization they are stored under: Virginia HB 891 from the Senate's passage to the Governor's
approval (the legislature adopts the governor's recommendation, then the bill is signed), and HB 625 from its referral
to its House passage (a subcommittee fails to recommend it, the committee reconsiders and reports it).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.va import classify_action, classify_bill
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

VA = "ocd-jurisdiction/country:us/state:va/government"

# (date, chamber, labels as stored, text)
VA_HB891 = [
    ("2026-03-04", "upper", ["passage"], "Passed Senate (27-Y 11-N 1-A)"),
    ("2026-03-11", "lower", ["enrolled"], "Enrolled"),
    ("2026-03-11", "upper", ["passage"], "Signed by President"),
    ("2026-03-12", "lower", ["passage"], "Signed by Speaker"),
    (
        "2026-03-14",
        "lower",
        ["enrolled", "executive-receipt"],
        "Enrolled Bill communicated to Governor on March 14, 2026",
    ),
    ("2026-03-14", "executive", ["executive-receipt"], "Governor's Action Deadline 11:59 p.m., April 13, 2026"),
    ("2026-04-12", "executive", ["executive-veto-line-item"], "Governor's recommendation received by House"),
    ("2026-04-22", "lower", ["enrolled"], "Reenrolled bill text (HB891ER2)"),
    ("2026-04-22", "lower", [], "House concurred in Governor's recommendation (65-Y 35-N 0-A)"),
    ("2026-04-22", "upper", [], "Senate concurred in Governor's recommendation (27-Y 11-N 1-A)"),
    ("2026-04-22", "executive", ["executive-signature"], "Governor's recommendation adopted"),
    ("2026-04-22", "lower", ["enrolled"], "Reenrolled"),
    ("2026-04-22", "executive", ["executive-signature"], "Approved by Governor-Chapter 1076 (effective 7/1/2026)"),
    ("2026-04-22", "executive", [], "Acts of Assembly Chapter text (CHAP1076)"),
]
# as Virginia's rule reads them: adopting the governor's recommendation is not the signature
VA_HB891_RELABELLED = [list(labels) for _, _, labels, _ in VA_HB891]
VA_HB891_RELABELLED[10] = []

VA_HB625 = [
    ("2026-01-13", "lower", ["referral-committee"], "Referred to Committee on Labor and Commerce"),
    ("2026-01-20", "lower", ["referral-committee"], "Assigned HCL sub: Subcommittee #1"),
    ("2026-02-03", "lower", ["committee-failure"], "Subcommittee failed to recommend reporting (4-Y 5-N)"),
    ("2026-02-03", "lower", [], "Reconsidered by Labor and Commerce (Voice Vote)"),
    ("2026-02-03", "lower", [], "Subcommittee recommends reporting with amendment(s) (6-Y 3-N)"),
    ("2026-02-05", "lower", ["committee-passage"], "Reported from Labor and Commerce with amendment(s) (15-Y 7-N)"),
    ("2026-02-09", "lower", ["reading-1"], "Read first time"),
    ("2026-02-11", "lower", ["passage", "reading-3"], "Read third time and passed House (63-Y 35-N 0-A)"),
]
# as Virginia's rule reads them: the subcommittee's recommendation is not the committee's failure
VA_HB625_RELABELLED = [list(labels) for _, _, labels, _ in VA_HB625]
VA_HB625_RELABELLED[2] = []


def _rule(description, labels, chamber="executive", date="2026-04-22"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        # adopting the governor's recommendation is not the signature (138 stored lines, all this exact text)
        ("Governor's recommendation adopted", ["executive-signature"], []),
        ("GOVERNOR'S RECOMMENDATION ADOPTED", ["executive-signature"], []),
        # the signature itself, every stored shape, keeps it
        ("Approved by Governor-Chapter 1076 (effective 7/1/2026)", ["executive-signature"], ["executive-signature"]),
        (
            "Approved by Governor - Chapter 12 (effective - see bill)",
            ["executive-signature"],
            ["executive-signature"],
        ),
        ("Approved by Governor-Chapter12 (Effective 7/1/2026)", ["executive-signature"], ["executive-signature"]),
        # the other recommendation lines: their labels are left as they are
        (
            "Governor's recommendation received by House",
            ["executive-veto-line-item"],
            ["executive-veto-line-item"],
        ),
        ("House concurred in Governor's recommendation (65-Y 35-N 0-A)", [], []),
        (
            "Senate rejected Governor's recommendation (17-Y 22-N 0-A)",
            ["veto-override-passage"],
            ["veto-override-passage"],
        ),
        # the rule reads the start of the line, as the broker did, and only "adopted" (none of these is stored with the
        # label; the cases pin the rule)
        ("House concurred; Governor's recommendation adopted", ["executive-signature"], ["executive-signature"]),
        ("Governor's recommendation received by House", ["executive-signature"], ["executive-signature"]),
        # a subcommittee's recommendation is not the committee's failure (11 stored lines with the label, all House)
        ("Subcommittee failed to recommend reporting (4-Y 6-N)", ["committee-failure"], []),
        ("Subcommittee failed to recommend reporting with amendment(s) (3-Y 5-N)", ["committee-failure"], []),
        ("subcommittee FAILED to recommend reporting (3-Y 3-N)", ["committee-failure"], []),
        # the committee's own failure keeps it
        (
            "Failed to report (defeated) in Privileges and Elections (6-Y 9-N)",
            ["committee-failure"],
            ["committee-failure"],
        ),
        (
            "Failed to report from Courts of Justice with substitute (7-Y 8-N 0-A)",
            ["committee-failure"],
            ["committee-failure"],
        ),
        # "recommend" is a whole word, as in the broker's reader; none of these is stored, the case pins the rule
        ("Subcommittee failed to recommendation", ["committee-failure"], ["committee-failure"]),
        # nothing is added: the stored lines without the label stay without it
        ("Subcommittee failed to recommend laying on the table (7-Y 3-N)", [], []),
        ("Governor's recommendation adopted", [], []),
        # only the one label goes; the others stay, in their order
        (
            "Governor's recommendation adopted",
            ["enrolled", "executive-signature", "filing"],
            ["enrolled", "filing"],
        ),
        (
            "Subcommittee failed to recommend reporting (4-Y 6-N)",
            ["committee-failure", "failure"],
            ["failure"],
        ),
        # each rule takes off only its own label
        ("Governor's recommendation adopted", ["committee-failure"], ["committee-failure"]),
        ("Subcommittee failed to recommend reporting (4-Y 6-N)", ["executive-signature"], ["executive-signature"]),
        ("", ["executive-signature"], ["executive-signature"]),
    ],
)
def test_only_the_two_misread_lines_lose_their_label(description, labels, expected):
    assert _rule(description, labels) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["executive-signature"]
    assert _rule("Governor's recommendation adopted", labels) == [] and labels == ["executive-signature"]
    same = ["executive-signature"]
    assert _rule("Approved by Governor-Chapter 1076 (effective 7/1/2026)", same) is not same
    assert classify_action(description=None, classification=["committee-failure"], chamber=None, date=None) == [
        "committee-failure"
    ]


@pytest.mark.parametrize("date,chamber,labels,description", VA_HB891 + VA_HB625)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_the_rule_reads_the_text_not_the_organization():
    for chamber in ("upper", "lower", "executive", None):
        assert _rule("Governor's recommendation adopted", ["executive-signature"], chamber) == []
        assert _rule("Subcommittee failed to recommend reporting (4-Y 6-N)", ["committee-failure"], chamber) == []


def test_state_rule_finds_both_of_virginias_rules():
    assert state_rule("va", "classify_action") is classify_action
    assert state_rule("va", "classify_bill") is classify_bill


def _va():
    _jurisdiction(VA, "Virginia", session="2026", chambers=("lower", "upper", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_virginias_rule_and_a_rescrape_changes_nothing():
    _va()

    BillImporter(VA).import_item(_scraped(VA_HB891, "HB 891", session="2026", chamber="lower"))
    assert _stored("HB 891") == VA_HB891_RELABELLED

    _, what = BillImporter(VA).import_item(_scraped(VA_HB891, "HB 891", session="2026", chamber="lower"))
    assert what == "noop"
    assert _stored("HB 891") == VA_HB891_RELABELLED


@pytest.mark.django_db
def test_the_importer_keeps_the_committees_report_and_the_house_passage():
    _va()

    BillImporter(VA).import_item(_scraped(VA_HB625, "HB 625", session="2026", chamber="lower"))

    assert _stored("HB 625") == VA_HB625_RELABELLED
    assert Bill.objects.get(identifier="HB 625").latest_passage_date == "2026-02-11"


@pytest.mark.django_db
def test_relabel_va_finds_the_stored_lines_and_leaves_the_bills_label_rule_as_it_was():
    hb891 = _bill(VA, "Virginia", "HB 891", VA_HB891)
    hb625 = _bill(VA, "Virginia", "HB 625", VA_HB625)
    hr1 = _bill(VA, "Virginia", "HR 1", [], classification=("bill",))
    before = (_labels(hb891), _labels(hb625))

    result = _relabel_dry_run("va")

    assert result.exit_code == 0, result.output
    # both of Virginia's rules in one pass: two action labels, and HR 1's bill label (OPEN-545)
    assert "[DRY RUN] actions changed=2 unchanged=20 | bills changed=1 unchanged=2" in result.output
    assert "action: executive-signature -> (none): 1" in result.output
    assert "action: committee-failure -> (none): 1" in result.output
    assert "bill: bill -> resolution: 1" in result.output
    assert (_labels(hb891), _labels(hb625)) == before
    assert Bill.objects.get(pk=hr1.pk).classification == ["bill"]


@pytest.mark.django_db
def test_relabel_finds_no_action_left_to_change_after_an_import():
    _va()
    BillImporter(VA).import_data(
        [
            _scraped(VA_HB891, "HB 891", session="2026", chamber="lower"),
            _scraped(VA_HB625, "HB 625", session="2026", chamber="lower"),
        ]
    )

    result = _relabel_dry_run("va")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=22" in result.output
