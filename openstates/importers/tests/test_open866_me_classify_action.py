"""OPEN-866: Maine's `classify_action` (openstates/ddp_states/me.py), applied on import (OPEN-550) and by
`os-text-extract relabel me`.

Ramon, 2026-11-24: in Maine "passed one chamber" is the originating chamber's "PASSED TO BE ENGROSSED" vote, not its
enactment. The actions are real, in stored order, from the Mac replica (2026-11-24, 132nd Legislature, session 132R1),
with the labels the scraper's categorizer gave them and the organization they are stored under: LD 17 (a Senate bill
the House enacts first), LD 85 (a House bill), LD 460 (a Senate resolve, "FINALLY PASSED") and SP 175 (a Senate joint
order). The committee's own lines (stored under the legislature) are left out. Members' names are replaced by
"MEMBER of County".
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.me import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

ME = "ocd-jurisdiction/country:us/state:me/government"

# (date, chamber, labels as scraped, text)
ME_LD17 = [
    ("2025-01-08", "upper", ["receipt", "referral-committee"], "Received by the Secretary of the Senate on January 6, 2025 and REFERRED to the Committee on CRIMINAL JUSTICE AND PUBLIC SAFETY pursuant to Joint Rule 308.2"),
    ("2025-02-25", "upper", [], "Report READ and ACCEPTED."),
    ("2025-02-25", "upper", ["reading-1"], "READ ONCE"),
    ("2025-02-25", "upper", ["reading-2", "reading-3"], "Under suspension of the Rules, READ A SECOND TIME and PASSED TO BE ENGROSSED."),
    ("2025-02-25", "upper", [], "Ordered sent down forthwith for concurrence."),
    ("2025-03-04", "lower", [], "CONSENT CALENDAR - FIRST DAY"),
    ("2025-03-04", "lower", [], "Under suspension of the rules CONSENT CALENDAR - SECOND DAY."),
    ("2025-03-04", "lower", ["reading-3"], "The Bill was PASSED TO BE ENGROSSED."),
    ("2025-03-04", "lower", [], "In concurrence. ORDERED SENT FORTHWITH."),
    ("2025-03-11", "lower", ["passage"], "PASSED TO BE ENACTED."),
    ("2025-03-11", "lower", [], "Sent for concurrence. ORDERED SENT FORTHWITH."),
    ("2025-03-18", "upper", ["passage"], "PASSED TO BE ENACTED, in concurrence."),
    ("2025-03-21", "executive", ["executive-signature"], "Signed by Governor"),
]
# the Senate's engrossment (its own chamber: "Passed one chamber") and the House's ("Passed both chambers") gain
# passage; both enactments lose it
ME_LD17_RELABELLED = [
    ["receipt", "referral-committee"],
    [],
    ["reading-1"],
    ["reading-2", "reading-3", "passage"],
    [],
    [],
    [],
    ["reading-3", "passage"],
    [],
    [],
    [],
    [],
    ["executive-signature"],
]

ME_LD85 = [
    ("2025-01-06", "lower", ["receipt"], "Received by the Clerk of the House on January 6, 2025."),
    ("2025-01-06", "lower", ["referral-committee"], "The Bill was REFERRED to the Committee on HEALTH AND HUMAN SERVICES pursuant to Joint Rule 308.2 and ordered printed pursuant to Joint Rule 401."),
    ("2025-02-25", "lower", [], "CONSENT CALENDAR - FIRST DAY"),
    ("2025-02-25", "lower", [], "Under suspension of the rules CONSENT CALENDAR - SECOND DAY."),
    ("2025-02-25", "lower", ["reading-3"], "The Bill was PASSED TO BE ENGROSSED."),
    ("2025-02-25", "lower", [], "Sent for concurrence. ORDERED SENT FORTHWITH."),
    ("2025-03-04", "upper", [], "Report READ and ACCEPTED, in concurrence."),
    ("2025-03-04", "upper", ["reading-1"], "READ ONCE"),
    ("2025-03-04", "upper", ["reading-2", "reading-3"], "Under suspension of the Rules, READ A SECOND TIME and PASSED TO BE ENGROSSED, in concurrence."),
    ("2025-03-04", "upper", [], "Ordered sent down forthwith."),
    ("2025-03-11", "lower", ["passage"], "PASSED TO BE ENACTED."),
    ("2025-03-11", "lower", [], "Sent for concurrence. ORDERED SENT FORTHWITH."),
    ("2025-03-18", "upper", ["passage"], "PASSED TO BE ENACTED, in concurrence."),
    ("2025-03-21", "executive", ["executive-signature"], "Signed by Governor"),
]
ME_LD85_RELABELLED = [
    ["receipt"],
    ["referral-committee"],
    [],
    [],
    ["reading-3", "passage"],
    [],
    [],
    ["reading-1"],
    ["reading-2", "reading-3", "passage"],
    [],
    [],
    [],
    [],
    ["executive-signature"],
]

ME_LD460 = [
    ("2025-02-11", "upper", ["receipt", "referral-committee"], "Received by the Secretary of the Senate on February 5, 2025 and REFERRED to the Committee on TRANSPORTATION pursuant to Joint Rule 308.2"),
    ("2025-03-11", "upper", [], "Report READ and ACCEPTED."),
    ("2025-03-11", "upper", ["reading-1"], "READ ONCE"),
    ("2025-03-11", "upper", ["reading-2", "reading-3"], "Under suspension of the Rules, READ A SECOND TIME and PASSED TO BE ENGROSSED."),
    ("2025-03-11", "upper", [], "Ordered sent down forthwith for concurrence."),
    ("2025-03-18", "lower", ["reading-3"], "The Resolve was PASSED TO BE ENGROSSED."),
    ("2025-03-18", "lower", [], "In concurrence. ORDERED SENT FORTHWITH."),
    ("2025-03-20", "lower", ["passage"], "FINALLY PASSED."),
    ("2025-03-20", "lower", [], "Sent for concurrence. ORDERED SENT FORTHWITH."),
    ("2025-03-20", "upper", ["passage"], "FINALLY PASSED, in concurrence."),
    ("2025-03-25", "executive", ["executive-signature"], "Signed by Governor"),
]
ME_LD460_RELABELLED = [
    ["receipt", "referral-committee"],
    [],
    ["reading-1"],
    ["reading-2", "reading-3", "passage"],
    [],
    ["reading-3", "passage"],
    [],
    [],
    [],
    [],
    ["executive-signature"],
]

ME_SP175 = [
    ("2025-02-04", "upper", [], "On motion by Senator MEMBER of County READ and PASSED"),
    ("2025-02-04", "upper", [], "Ordered sent down forthwith for concurrence"),
    ("2025-02-04", "lower", ["passage"], "READ and PASSED."),
    ("2025-02-04", "lower", [], "In concurrence. ORDERED SENT FORTHWITH."),
]
ME_SP175_RELABELLED = [["passage"], [], ["passage"], []]


def _rule(description, labels, chamber="lower", date="2025-03-04"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,chamber,expected",
    [
        # a chamber's vote to pass the bill to be engrossed gains passage, in each of its printed forms
        ("The Bill was PASSED TO BE ENGROSSED.", ["reading-3"], "lower", ["reading-3", "passage"]),
        ('The Bill was PASSED TO BE ENGROSSED as Amended by Committee Amendment "A" (H-12).', ["reading-3"], "lower", ["reading-3", "passage"]),
        ("The Resolve was PASSED TO BE ENGROSSED.", ["reading-3"], "lower", ["reading-3", "passage"]),
        ("Under suspension of the Rules, READ A SECOND TIME and PASSED TO BE ENGROSSED.", ["reading-2", "reading-3"], "upper", ["reading-2", "reading-3", "passage"]),
        # the second chamber's engrossment ("in concurrence") is passage too: by the other chamber, "Passed both chambers"
        ("Under suspension of the Rules, READ A SECOND TIME and PASSED TO BE ENGROSSED, in concurrence.", ["reading-2", "reading-3"], "upper", ["reading-2", "reading-3", "passage"]),
        ('PASSED TO BE ENGROSSED AS AMENDED BY Committee Amendment "A" (S-206) as Amended by Senate Amendment "A" (S-466) thereto in NON-CONCURRENCE.', ["reading-3"], "upper", ["reading-3", "passage"]),
        ("Under suspension of the rules and WITHOUT REFERENCE to a Committee, the Bill was READ TWICE and PASSED TO BE ENGROSSED.", ["reading-3"], "lower", ["reading-3", "passage"]),
        ("Subsequently, the Bill was PASSED TO BE ENGROSSED.", ["reading-3"], "lower", ["reading-3", "passage"]),
        # "PASSAGE TO BE ENGROSSED" once the chamber agreed to it: receded and concurred, or the vote prevailed
        ('The House RECEDED and CONCURRED to PASSAGE TO BE ENGROSSED as Amended by Committee Amendment "A" (S-206).', ["reading-3"], "lower", ["reading-3", "passage"]),
        ("The House RECEDED and CONCURRED to ACCEPTANCE of the Majority Ought to Pass as Amended Report and PASSAGE TO BE ENGROSSED as Amended by Committee Amendment \"A\" (H-410).", ["reading-3"], "lower", ["reading-3", "passage"]),
        ("On motion by Senator MEMBER of County the Senate RECEDED and CONCURRED Suspension of the Rules, READ TWICE AND PASSAGE TO BE ENGROSSED.", ["reading-3"], "upper", ["reading-3", "passage"]),
        ('READ A SECOND TIME and PASSAGE TO BE ENGROSSED AS AMENDED BY Committee Amendment "A" (S-534) PREVAILED', ["reading-2", "reading-3"], "upper", ["reading-2", "reading-3", "passage"]),
        ("Subsequently, the Bill was PASSAGE TO BE ENGROSSED AS AMENDED", ["reading-3"], "lower", ["reading-3", "passage"]),
        # a line that is not the chamber's vote to pass gets nothing: a reconsideration or a reference back to the vote
        ('On motion by Same Senator the Senate SUSPENDED THE RULES to RECONSIDER their actions whereby the Bill was PASSED TO BE ENGROSSED AS AMENDED by Committee Amendment "A" (S-206)', ["reading-3"], "upper", ["reading-3"]),
        ("On motion by Senator MEMBER of County the Senate RECEDED whereby the Bill was PASSED TO BE ENGROSSED", ["reading-3"], "upper", ["reading-3"]),
        ("Subsequently, the motion to RECONSIDER PASSAGE TO BE ENGROSSED PREVAILS", ["reading-3"], "upper", ["reading-3"]),
        # ... a chamber insisting or adhering keeps the vote it already took: not a new passage
        ("The House INSISTED on ACCEPTANCE of the Majority Ought to Pass as Amended Report and PASSAGE TO BE ENGROSSED as Amended by Committee Amendment \"A\" (H-5).", ["reading-3"], "lower", ["reading-3"]),
        ("Subsequently, the Senate ADHERED to The Majority Ought to Pass As Amended by Committee Amendment \"A\" (S-5) Report and PASSAGE TO BE ENGROSSED AS AMENDED", ["reading-3"], "upper", ["reading-3"]),
        ("On motion by Senator MEMBER of County the Senate INSISTED to PASSED TO BE ENGROSSED", ["reading-3"], "upper", ["reading-3"]),
        # ... a motion not yet decided, or one that failed
        ("Senator MEMBER of County moved to INSIST on PASSAGE TO BE ENGROSSED AS AMENDED", ["reading-3"], "upper", ["reading-3"]),
        ("Speaker MEMBER of Town MOVED to RECEDE and CONCUR to ACCEPTANCE of the Majority Ought to Pass as Amended Report and PASSAGE TO BE ENGROSSED as Amended", ["reading-3"], "lower", ["reading-3"]),
        ("Motion of Representative MEMBER of Town to RECEDE and CONCUR to PASSAGE TO BE ENGROSSED as Amended FAILED.", ["reading-3"], "lower", ["reading-3"]),
        # ... the bill failing, the chamber insisting on that failure, or receding from its engrossment
        ('Subsequently, the Bill FAILED PASSAGE TO BE ENGROSSED AS AMENDED by Committee Amendment "A" (S-5) in NON-CONCURRENCE.', ["reading-3"], "upper", ["reading-3"]),
        ("On motion by Senator MEMBER of County the Senate INSISTED to FAILURE OF PASSAGE TO BE ENGROSSED In NON-CONCURRENCE", ["reading-3"], "upper", ["reading-3"]),
        ("Subsequently, the Senate INSISTED to FAILING OF PASSAGE TO BE ENGROSSED in NON-CONCURRENCE.", ["reading-3"], "upper", ["reading-3"]),
        ("On motion by Senator MEMBER of County the Senate RECEDED from PASSAGE TO BE ENGROSSED AS AMENDED", ["reading-3"], "upper", ["reading-3"]),
        ("Senator MEMBER of County moved the Senate RECEDE from PASSAGE TO BE ENGROSSED AS AMENDED PREVAILED", ["reading-3"], "upper", ["reading-3"]),
        ("On motion by Senator MEMBER of County the Senate RECEDE from PASSAGE TO BE ENGROSSED AS AMENDED PREVAILED", ["reading-3"], "upper", ["reading-3"]),
        # ... or a line that puts the bill aside
        ("On motion by Senator MEMBER of County PLACED ON THE SPECIAL APPROPRIATIONS TABLE pending PASSAGE TO BE ENGROSSED", ["reading-3"], "upper", ["reading-3"]),
        ("TABLED until Later in Today's Session PASSAGE TO BE ENGROSSED AS AMENDED", ["reading-3"], "upper", ["reading-3"]),
        # the enactment lines lose passage, in both chambers, for a bill ("PASSED TO BE ENACTED") and a resolve
        # ("FINALLY PASSED"), and a reconsideration of the enactment gets nothing either
        ("PASSED TO BE ENACTED.", ["passage"], "lower", []),
        ("PASSED TO BE ENACTED, in concurrence.", ["passage"], "upper", []),
        ("PASSED TO BE ENACTED - Emergency - 104/37 Elected Required, in concurrence.", ["passage"], "upper", []),
        ("Subsequently, the Bill was PASSED TO BE ENACTED.", [], "lower", []),
        ("FINALLY PASSED.", ["passage"], "lower", []),
        ("FINALLY PASSED, in concurrence.", ["passage"], "upper", []),
        ("On motion by Same Senator the Senate SUSPENDED THE RULES to RECONSIDER their actions whereby the Bill was PASSED TO BE ENACTED", ["passage"], "upper", []),
        # a failed enactment keeps the categorizer's failure
        ("FAILED PASSAGE TO BE ENACTED.", ["failure"], "lower", ["failure"]),
        ("The House RECEDED and CONCURRED to PASSAGE TO BE ENACTED.", [], "lower", []),
        # a joint order's or resolution's single vote is its passage, in the Senate's forms too
        ("READ and PASSED.", ["passage"], "lower", ["passage"]),
        ("READ and ADOPTED, in concurrence.", ["passage"], "upper", ["passage"]),
        ("On motion by Senator MEMBER of County READ and PASSED", [], "upper", ["passage"]),
        ("On motion by Senator MEMBER of County READ and PASSED PREVAILED", [], "upper", ["passage"]),
        ("READ and ADOPTED, in concurrence. PREVAILED", [], "upper", ["passage"]),
        ("READ and ADOPTED in Joint Convention.", [], "lower", ["passage"]),
        ("Subsequently, the Joint Resolution was ADOPTED.", [], "lower", ["passage"]),
        ("Subsequently, the Joint Order was PASSED.", [], "lower", ["passage"]),
        ("Subsequently, PASSED in concurrence.", [], "upper", ["passage"]),
        ("Subsequently PASSED 2/3 Vote Required in concurrence", [], "upper", ["passage"]),
        # ... but not when the line goes on to an engrossment or enactment ("READ and PASSED TO BE ...")
        ("READ and PASSED TO BE ENACTED.", ["passage"], "lower", []),
        ("Subsequently, PASSED TO BE ENACTED in concurrence.", ["passage"], "upper", []),
        ("Subsequently, PASSED TO BE ENACTED - Emergency - 31/35 Elected Required, in concurrence.", ["passage"], "upper", []),
        # ... but an amendment's adoption, and a reconsideration of the adoption, are not
        ('Committee Amendment "A" (H-681) READ. READ and ADOPTED in concurrence', ["committee-passage-favorable"], "upper", ["committee-passage-favorable"]),
        ('On further motion of the same Representative House Amendment "A" (H-865) was READ and ADOPTED.', [], "lower", []),
        ("On motion by Senator MEMBER of County the Senate RECONSIDERED whereby we ADOPTED the Joint Resolution PREVAILED", [], "upper", []),
        # every other line and label as given
        ("Report READ and ACCEPTED.", [], "upper", []),
        ("Subsequently, on motion of Representative MEMBER of Town, the Minority Ought Not to Pass Report was ACCEPTED.", ["committee-failure"], "lower", ["committee-failure"]),
        ("Placed in Legislative Files (DEAD)", ["failure"], "upper", ["failure"]),
        ("Signed by Governor", ["executive-signature"], "executive", ["executive-signature"]),
        ("Became Law without Governor's Signature", ["became-law"], "lower", ["became-law"]),
        ("CONSENT CALENDAR - FIRST DAY", [], "lower", []),
    ],
)
def test_maines_engrossment_enactment_and_single_vote_lines(description, labels, chamber, expected):
    assert _rule(description, labels, chamber) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["passage"]
    assert _rule("PASSED TO BE ENACTED.", labels) == [] and labels == ["passage"]
    stored = ["reading-3"]
    assert _rule("The Bill was PASSED TO BE ENGROSSED.", stored) == ["reading-3", "passage"] and stored == ["reading-3"]


@pytest.mark.parametrize("date,chamber,labels,description", ME_LD17 + ME_LD85 + ME_LD460 + ME_SP175)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


@pytest.mark.parametrize(
    "actions,origin",
    [(ME_LD17, "upper"), (ME_LD85, "lower"), (ME_LD460, "upper"), (ME_SP175, "upper")],
)
def test_the_first_passage_is_the_originating_chambers_and_the_second_the_other_chambers(actions, origin):
    # what the stage rule reads: the first passage by the bill's own chamber ("Passed one chamber"), the next by the
    # other chamber ("Passed both chambers"), and none after (the House's enactment of a Senate bill no longer
    # comes first)
    passages = [chamber for _, chamber, labels, d in actions if "passage" in _rule(d, labels, chamber)]
    other = {"upper": "lower", "lower": "upper"}[origin]
    assert passages == [origin, other]


def test_state_rule_finds_maines_actions_only():
    assert state_rule("me", "classify_action") is classify_action
    assert state_rule("me", "classify_bill") is None


def _me():
    _jurisdiction(ME, "Maine", session="132R1", chambers=("lower", "upper", "executive"))


@pytest.mark.django_db
def test_the_importer_applies_maines_rule_and_a_rescrape_changes_nothing():
    _me()
    BillImporter(ME).import_data(
        [
            _scraped(ME_LD17, "LD 17", session="132R1", chamber="upper"),
            _scraped(ME_LD85, "LD 85", session="132R1", chamber="lower"),
            _scraped(ME_LD460, "LD 460", session="132R1", chamber="upper"),
            _scraped(ME_SP175, "SP 175", session="132R1", chamber="upper"),
        ]
    )
    assert _stored("LD 17") == ME_LD17_RELABELLED
    assert _stored("LD 85") == ME_LD85_RELABELLED
    assert _stored("LD 460") == ME_LD460_RELABELLED
    assert _stored("SP 175") == ME_SP175_RELABELLED
    # the bill's last passage is the second chamber's engrossment, not the Senate's enactment two weeks later
    assert Bill.objects.get(identifier="LD 17").latest_passage_date == "2025-03-04"
    assert Bill.objects.get(identifier="LD 460").latest_passage_date == "2025-03-18"
    _, what = BillImporter(ME).import_item(_scraped(ME_LD17, "LD 17", session="132R1", chamber="upper"))
    assert what == "noop"
    assert _stored("LD 17") == ME_LD17_RELABELLED


@pytest.mark.django_db
def test_relabel_me_now_runs_and_finds_the_stored_lines_the_rule_changes():
    bill = _bill(ME, "Maine", "LD 17", ME_LD17)
    before = _labels(bill)

    result = _relabel_dry_run("me")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=4 unchanged=9" in result.output
    assert "action: passage -> (none): 2" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _me()
    BillImporter(ME).import_data([_scraped(ME_LD85, "LD 85", session="132R1", chamber="lower")])
    result = _relabel_dry_run("me")
    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=14" in result.output
