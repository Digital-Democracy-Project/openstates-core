"""OPEN-826: Montana's `classify_action` (openstates/ddp_states/mt.py), applied on import (OPEN-550) and by
`os-text-extract relabel mt`.

The actions are real. 2027 HB 16 and SB 1 are as stored on the Mac replica (2026-11-24, after re-import
mt-da06a3dfea66), drafting lines and all, with the labels they are stored with (none) and their organization; HB 16's
"(H) Introduced" is stored twice. 2025 HB 135 is the bill's status list from api.legmt.gov (bills/v1/bills/search,
session 2; 2025 is not loaded), drafting lines left out, written by the scraper's own `scrape_actions` as OPEN-851
(scrapers #275) writes it: the status row's result after the name ("(H) 3rd Reading Passed", "--Bill Passed as
Amended"), then " - " and the committee's name, with the labels the scraper gives.
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.ddp_states import state_rule
from openstates.ddp_states.mt import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

MT = "ocd-jurisdiction/country:us/state:mt/government"

_DRAFTING = [
    "(LC) Drafter Assigned",
    "(LC) Draft in Legal Review",
    "(LC) Draft in Edit",
    "(LC) Draft in Input/Proofing",
    "(LC) Draft in Final Drafter Review",
    "(LC) Draft in Assembly",
    "(LC) Draft Ready for Delivery",
    "(LC) Draft Delivered to Requester",
]

# (date, chamber, labels as stored, text)
MT_HB16_2027 = [("2026-10-01", "legislature", [], d) for d in _DRAFTING] + [
    ("2026-11-16", "lower", [], "(H) Introduced"),
    ("2026-11-16", "lower", [], "(H) Introduced"),
]
MT_HB16_2027_RELABELLED = [[]] * 8 + [["introduction"], ["introduction"]]

MT_SB1_2027 = [("2026-10-01", "legislature", [], d) for d in _DRAFTING] + [
    ("2026-11-16", "upper", [], "(S) Introduced"),
]
MT_SB1_2027_RELABELLED = [[]] * 8 + [["introduction"]]

MT_HB135_2025 = [
    ("2025-01-08", "lower", [], "(H) Introduced"),
    ("2025-01-08", "lower", ["referral-committee"], "(H) Referred to Committee - (H) Business and Labor"),
    ("2025-01-08", "lower", [], "(H) 1st Reading"),
    ("2025-01-15", "lower", [], "(H) Hearing - (H) Business and Labor"),
    (
        "2025-01-21",
        "lower",
        ["committee-passage"],
        "(H) Committee Executive Action--Bill Passed as Amended - (H) Business and Labor",
    ),
    (
        "2025-01-21",
        "lower",
        ["committee-passage"],
        "(H) Committee Executive Action--Bill Passed as Amended - (H) Business and Labor",
    ),
    (
        "2025-01-21",
        "lower",
        ["committee-passage"],
        "(H) Committee Report--Bill Passed as Amended - (H) Business and Labor",
    ),
    ("2025-01-24", "lower", ["reading-2"], "(H) Scheduled for 2nd Reading"),
    ("2025-01-24", "lower", ["reading-2"], "(H) 2nd Reading Passed"),
    ("2025-01-27", "lower", ["reading-3"], "(H) Scheduled for 3rd Reading"),
    ("2025-01-27", "lower", ["passage"], "(H) 3rd Reading Passed"),
    ("2025-01-27", "lower", [], "(H) Transmitted to Senate"),
    ("2025-01-28", "upper", [], "(S) 1st Reading"),
    ("2025-02-17", "upper", ["referral-committee"], "(S) Referred to Committee - (S) Judiciary"),
    ("2025-03-18", "upper", [], "(S) Hearing - (S) Judiciary"),
    ("2025-03-20", "upper", ["committee-passage"], "(S) Committee Executive Action--Bill Concurred - (S) Judiciary"),
    ("2025-03-21", "upper", ["committee-passage"], "(S) Committee Report--Bill Concurred - (S) Judiciary"),
    ("2025-03-26", "upper", ["reading-2"], "(S) Scheduled for 2nd Reading"),
    ("2025-03-26", "upper", ["reading-2"], "(S) 2nd Reading Concurred"),
    ("2025-03-27", "upper", ["reading-3"], "(S) Scheduled for 3rd Reading"),
    ("2025-03-27", "upper", ["passage"], "(S) 3rd Reading Concurred"),
    ("2025-03-27", "lower", [], "(H) Sent to Enrolling"),
    ("2025-03-28", "lower", [], "(H) Returned from Enrolling"),
    ("2025-04-07", "lower", ["passage"], "(H) Signed by Speaker"),
    ("2025-04-09", "upper", ["passage"], "(S) Signed by President"),
    ("2025-04-09", "lower", ["executive-receipt"], "(H) Transmitted to Governor"),
    ("2025-04-17", "lower", ["executive-signature"], "(H) Signed by Governor"),
    ("2025-04-18", "legislature", ["became-law"], "Chapter Number Assigned"),
]
MT_HB135_2025_RELABELLED = [
    ["introduction"],
    ["referral-committee"],
    ["reading-1"],
    [],
    ["committee-passage"],
    ["committee-passage"],
    ["committee-passage"],
    [],
    ["reading-2"],
    [],
    ["reading-3", "passage"],
    [],
    ["reading-1"],
    ["referral-committee"],
    [],
    ["committee-passage"],
    ["committee-passage"],
    [],
    ["reading-2"],
    [],
    ["reading-3", "passage"],
    [],
    [],
    [],
    [],
    ["executive-receipt"],
    ["executive-signature"],
    ["became-law"],
]


def _rule(description, labels, chamber="lower", date="2025-01-27"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,expected",
    [
        # introduction, in both chambers' prefix forms (the categorizer's "^introduced$" never matched them)
        ("(H) Introduced", [], ["introduction"]),
        ("(S) Introduced", [], ["introduction"]),
        # the presiding officers' signatures are not a passage
        ("(H) Signed by Speaker", ["passage"], []),
        ("(S) Signed by President", ["passage"], []),
        # the readings themselves carry the reading; being scheduled for one, or moved onto one, does not
        ("(H) 1st Reading", [], ["reading-1"]),
        ("(S) 2nd Reading", ["reading-2"], ["reading-2"]),
        ("(H) 3rd Reading", [], ["reading-3"]),
        ("(S) 3rd Reading House Amendments", [], ["reading-3"]),
        ("(H) 3rd Reading Free Conference Committee Report", [], ["reading-3"]),
        ("(S) 3rd Reading; 2nd Chamber Vote Required", [], ["reading-3"]),
        ("(H) Scheduled for 2nd Reading", ["reading-2"], []),
        ("(S) Scheduled for 3rd Reading", ["reading-3"], []),
        ("(S) Take from Committee; Place on 2nd Reading", ["reading-2"], []),
        ("(H) Taken from 3rd Reading; Placed on 2nd Reading", ["reading-2", "reading-2"], []),
        (
            "(S) Taken from 2nd Reading; Rereferred to Committee",
            ["reading-2", "referral-committee", "referral-committee"],
            ["referral-committee"],
        ),
        # committee lines, with the committee's name after the status as the scraper writes it
        ("(H) Referred to Committee - (H) Business and Labor", ["referral-committee"], ["referral-committee"]),
        ("(S) Died in Committee - (S) Judiciary", [], ["committee-failure"]),
        ("(H) Tabled in Committee - (H) Judiciary", ["amendment-deferral"], ["deferral"]),
        # a fiscal note received is not the bill received
        ("(H) Fiscal Note Received", ["receipt"], []),
        ("(S) Revised Fiscal Note Received", ["receipt"], []),
        ("(H) Sponsor Rebuttal to Fiscal Note Received", ["receipt"], []),
        # law
        ("(S) Became Law Without Governor's Signature", [], ["became-law"]),
        ("Chapter Number Assigned", ["became-law"], ["became-law"]),
        ("(H) Signed by Governor", ["executive-signature"], ["executive-signature"]),
        # failure: the bill's own loss keeps it
        ("(H) Died in Process", ["failure"], ["failure"]),
        ("(LC) Draft Died in Process", ["failure"], ["failure"]),
        ("(S) 3rd Reading Failed", ["failure"], ["reading-3", "failure"]),
        # ... a lost motion or amendment does not (the older archive API's names, labelled `failure` by "failed")
        ("(H) 2nd Reading Pass Motion Failed; 3rd Reading Vote Required", ["reading-2", "failure"], ["reading-2"]),
        ("(S) 2nd Reading Motion to Amend Failed", ["amendment-failure", "failure"], ["amendment-failure"]),
        ("(H) Veto Override Motion Failed", ["veto-override-failure", "failure"], ["veto-override-failure"]),
        # OPEN-851's outcome words: set aside for the lookup, the scraper's outcome labels kept
        ("(H) 3rd Reading Passed", ["passage"], ["reading-3", "passage"]),
        ("(S) 3rd Reading Concurred", ["passage"], ["reading-3", "passage"]),
        ("(H) 3rd Reading Senate Amendments Concurred as Amended", ["passage"], ["reading-3", "passage"]),
        ("(S) 3rd Reading Conference Committee Report Adopted", ["passage"], ["reading-3", "passage"]),
        ("(H) 3rd Reading Senate Amendments Not Concurred", [], ["reading-3"]),
        ("(S) 3rd Reading Governor's Amendments Concurred", [], ["reading-3"]),
        ("(H) 3rd Reading Free Conference Committee Report Rejected", [], ["reading-3"]),
        ("(S) 2nd Reading Not Adopted", ["failure", "reading-2"], ["failure", "reading-2"]),
        ("(H) 2nd Reading Adopted", ["passage", "reading-2"], ["passage", "reading-2"]),
        ("(H) 2nd Reading Not Passed", ["reading-2"], ["reading-2"]),
        ("(S) 2nd Reading Motion to Amend Failed", ["reading-2"], ["reading-2"]),
        ("(S) Motion Failed", [], []),
        ("(H) Veto Override Failed", ["veto-override-failure"], ["veto-override-failure"]),
        (
            "(H) Committee Executive Action--Bill Passed as Amended - (H) Judiciary",
            ["committee-passage"],
            ["committee-passage"],
        ),
        ("(S) Committee Report--Resolution Not Adopted - (S) Judiciary", ["committee-failure"], ["committee-failure"]),
        # outcome words never bring back a label the name does not carry
        ("(H) 3rd Reading Passed", ["passage", "receipt"], ["reading-3", "passage"]),
        # any other line keeps its labels
        ("(H) Transmitted to Governor", ["executive-receipt"], ["executive-receipt"]),
        ("(S) Vetoed by Governor", ["executive-veto"], ["executive-veto"]),
        ("(LC) Drafter Assigned", [], []),
        ("", ["failure"], []),
    ],
)
def test_montanas_status_names(description, labels, expected):
    assert _rule(description, labels) == expected


@pytest.mark.parametrize(
    "description",
    [
        "Introduced in committee",
        "(H) Introduced Bill",
        "(S) Scheduled for 3rd Reading Again",
        "(H) Signed by Speaker Pro",
    ],
)
def test_a_name_must_match_whole(description):
    assert _rule(description, ["passage", "reading-3"]) == ["passage", "reading-3"]


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["passage"]
    assert _rule("(H) Signed by Speaker", labels) == [] and labels == ["passage"]
    same = ["executive-receipt"]
    assert _rule("(H) Transmitted to Governor", same) is not same
    assert classify_action(description=None, classification=["passage"], chamber=None, date=None) == ["passage"]


@pytest.mark.parametrize("date,chamber,labels,description", MT_HB16_2027 + MT_SB1_2027 + MT_HB135_2025)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_state_rule_finds_montanas_actions():
    assert state_rule("mt", "classify_action") is classify_action
    assert state_rule("mt", "classify_bill") is None


def _mt(session):
    _jurisdiction(MT, "Montana", session=session, chambers=("lower", "upper", "legislature"))


@pytest.mark.django_db
def test_the_importer_gives_the_2027_introductions_their_label_and_a_rescrape_changes_nothing():
    _mt("2027")

    BillImporter(MT).import_data(
        [
            _scraped(MT_HB16_2027, "HB 16", session="2027", chamber="lower"),
            _scraped(MT_SB1_2027, "SB 1", session="2027", chamber="upper"),
        ]
    )
    assert _stored("HB 16") == MT_HB16_2027_RELABELLED
    assert _stored("SB 1") == MT_SB1_2027_RELABELLED

    _, what = BillImporter(MT).import_item(_scraped(MT_HB16_2027, "HB 16", session="2027", chamber="lower"))
    assert what == "noop"


@pytest.mark.django_db
def test_the_importer_relabels_a_whole_2025_bill():
    _mt("2025")

    BillImporter(MT).import_item(_scraped(MT_HB135_2025, "HB 135", session="2025", chamber="lower"))

    assert _stored("HB 135") == MT_HB135_2025_RELABELLED


@pytest.mark.django_db
def test_relabel_mt_now_runs_and_finds_the_stored_lines():
    hb16 = _bill(MT, "Montana", "HB 16", MT_HB16_2027)
    sb1 = _bill(MT, "Montana", "SB 1", MT_SB1_2027)
    before = (_labels(hb16), _labels(sb1))

    result = _relabel_dry_run("mt")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=3 unchanged=16" in result.output
    assert "action: (none) -> introduction: 3" in result.output
    assert (_labels(hb16), _labels(sb1)) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _mt("2027")
    BillImporter(MT).import_data(
        [
            _scraped(MT_HB16_2027, "HB 16", session="2027", chamber="lower"),
            _scraped(MT_SB1_2027, "SB 1", session="2027", chamber="upper"),
        ]
    )

    result = _relabel_dry_run("mt")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=19" in result.output
