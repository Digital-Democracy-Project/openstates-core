"""OPEN-841: Oregon's `classify_action` (openstates/ddp_states/or.py), applied on import (OPEN-550) and by
`os-text-extract relabel or`.

The actions are real, in stored order, from the Mac replica (2026-11-24, session 2026R1), with the labels the scraper's
categorizer gave them and the organization they are stored under: HB 4134 (the House result on its own "Passed." line
a day after the third reading), HCR 201 (adopted by the House as a special order of business) and HB 4045 (third
reading and "Passed." on one line in both chambers). All three end with "Speaker signed." / "President signed.".
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

classify_action = state_rule("or", "classify_action")  # "or" is a Python keyword, so the module is not imported by name

OR = "ocd-jurisdiction/country:us/state:or/government"

# (date, chamber, labels as scraped, text)
OR_HB4134 = [
    ("2026-02-02T16:50:03+00:00", "lower", ["introduction", "reading-1", "referral-committee"], "First reading. Referred to Speaker's desk."),
    ("2026-02-02T17:51:51+00:00", "lower", ["referral-committee"], "Referred to Revenue."),
    ("2026-02-04T23:01:00+00:00", "lower", [], "Public Hearing held."),
    ("2026-02-16T23:02:00+00:00", "lower", [], "Work Session held."),
    ("2026-02-17T22:37:53+00:00", "lower", ["committee-passage-favorable"], "Recommendation: Do pass with amendments and be printed A-Engrossed."),
    ("2026-02-19T01:33:46+00:00", "lower", ["reading-2"], "Second reading."),
    ("2026-02-19T20:50:42+00:00", "lower", [], "Rules suspended. Carried over to February 23, 2026 Calendar."),
    ("2026-02-24T01:00:00+00:00", "lower", [], "Carried over to February 24, 2026 Calendar by virtue of adjournment."),
    ("2026-02-23T21:00:00+00:00", "lower", [], "Third reading. Carried by Helm."),
    ("2026-02-24T20:31:10+00:00", "lower", [], "Passed."),
    ("2026-02-24T20:32:10+00:00", "lower", [], "Vote explanation(s) filed by Bunch, Rieke Smith."),
    ("2026-02-25T00:39:33+00:00", "upper", ["introduction", "reading-1", "referral-committee"], "First reading. Referred to President's desk."),
    ("2026-02-25T01:25:13+00:00", "upper", ["referral-committee"], "Referred to Finance and Revenue."),
    ("2026-02-27T01:01:00+00:00", "upper", [], "Public Hearing held."),
    ("2026-02-27T23:02:00+00:00", "upper", [], "Public Hearing and Work Session held."),
    ("2026-03-03T17:05:36+00:00", "upper", ["committee-passage-favorable"], "Recommendation: Do pass the A-Eng. bill."),
    ("2026-03-03T19:11:21+00:00", "upper", ["reading-2"], "Second reading."),
    ("2026-03-05T00:10:49+00:00", "upper", ["passage", "reading-3"], "Third reading.  Carried by Golden, Nash.  Passed."),
    ("2026-03-05T00:11:49+00:00", "upper", [], "Vote explanation(s) filed by Sollman."),
    ("2026-03-05T21:30:14+00:00", "lower", ["passage"], "Speaker signed."),
    ("2026-03-06T16:59:18+00:00", "upper", ["passage"], "President signed."),
    ("2026-04-09T23:31:00+00:00", "lower", ["executive-signature"], "Governor signed."),
    ("2026-04-13T15:31:00+00:00", "lower", [], "Chapter 140, (2026 Laws): Effective date June 5, 2026."),
]
# the bare "Passed." gains passage; the two signatures lose it; every other line as scraped
OR_HB4134_RELABELLED = (
    [list(labels) for _, _, labels, _ in OR_HB4134[:9]]
    + [["passage"]]
    + [list(labels) for _, _, labels, _ in OR_HB4134[10:19]]
    + [[], []]
    + [list(labels) for _, _, labels, _ in OR_HB4134[21:]]
)

OR_HCR201 = [
    ("2026-02-02T16:50:03+00:00", "lower", ["introduction", "reading-1", "referral-committee"], "First reading. Referred to Speaker's desk."),
    ("2026-02-02T17:51:51+00:00", "lower", ["referral-committee"], "Referred to Emergency Management and Veterans."),
    ("2026-02-12T21:02:00+00:00", "lower", [], "Public Hearing and Work Session held."),
    ("2026-02-13T18:22:46+00:00", "lower", [], "Recommendation: Be adopted."),
    ("2026-02-16T21:37:28+00:00", "lower", [], "Rules suspended. Carried over to February 17, 2026 Calendar."),
    ("2026-02-17T21:56:31+00:00", "lower", [], "Rules suspended. Made a Special Order of Business immediately."),
    ("2026-02-17T22:07:43+00:00", "lower", [], "Read as Special Order of Business. Carried by Boice, Grayber. Adopted."),
    ("2026-02-19T22:10:27+00:00", "upper", ["introduction", "reading-1", "referral-committee"], "First reading. Referred to President's desk."),
    ("2026-02-19T23:17:20+00:00", "upper", ["referral-committee"], "Referred to Veterans, Emergency Management, Federal and World Affairs."),
    ("2026-02-24T21:47:00+00:00", "upper", [], "Public Hearing and Work Session held."),
    ("2026-02-25T18:22:24+00:00", "upper", [], "Recommendation: Do adopt."),
    ("2026-02-25T18:50:06+00:00", "upper", ["reading-2"], "Second reading."),
    ("2026-02-26T18:45:12+00:00", "upper", ["passage"], "Final reading.  Carried by Thatcher.  Adopted."),
    ("2026-02-27T20:09:27+00:00", "lower", ["passage"], "Speaker signed."),
    ("2026-02-27T22:32:26+00:00", "upper", ["passage"], "President signed."),
    ("2026-02-27T22:58:55+00:00", "lower", [], "Filed with Secretary of State."),
]
OR_HCR201_RELABELLED = (
    [list(labels) for _, _, labels, _ in OR_HCR201[:6]]
    + [["passage"]]
    + [list(labels) for _, _, labels, _ in OR_HCR201[7:13]]
    + [[], []]
    + [[]]
)

OR_HB4045 = [
    ("2026-02-16T19:37:59+00:00", "lower", ["reading-2"], "Second reading."),
    ("2026-02-17T21:04:42+00:00", "lower", [], "Rules suspended. Made a Special Order of Business immediately."),
    ("2026-02-17T21:09:25+00:00", "lower", ["passage", "reading-3"], "Third reading as Special Order of Business. Carried by Mannix. Passed."),
    ("2026-02-27T20:49:42+00:00", "upper", ["reading-2"], "Second reading."),
    ("2026-03-02T21:50:23+00:00", "upper", ["passage", "reading-3"], "Third reading.  Carried by Prozanski.  Passed."),
    ("2026-03-04T17:42:52+00:00", "lower", ["passage"], "Speaker signed."),
    ("2026-03-05T16:38:22+00:00", "upper", ["passage"], "President signed."),
    ("2026-03-31T21:07:00+00:00", "lower", ["executive-signature"], "Governor signed."),
]
OR_HB4045_RELABELLED = [["reading-2"], [], ["passage", "reading-3"], ["reading-2"], ["passage", "reading-3"], [], [], ["executive-signature"]]


def _rule(description, labels, chamber="lower", date="2026-02-24"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


@pytest.mark.parametrize(
    "description,labels,chamber,expected",
    [
        # the presiding officers signing the enrolled bill: not a vote, so no passage
        ("Speaker signed.", ["passage"], "lower", []),
        ("President signed.", ["passage"], "upper", []),
        # the chamber's result on its own line (HB 4134, HB 4145, HB 4014, SB 1507, SB 1599)
        ("Passed.", [], "lower", ["passage"]),
        # a special order of business adopted (HCR 201): the categorizer's "Read. ... Adopted" shape misses it
        ("Read as Special Order of Business. Carried by Boice, Grayber. Adopted.", [], "lower", ["passage"]),
        # a third reading with "Passed." on the same line: already passage, kept once
        ("Third reading. Carried by Helm. Passed.", ["passage", "reading-3"], "lower", ["passage", "reading-3"]),
        ("Rules suspended. Third reading.  Carried by Lieber.  Passed.", ["passage", "reading-3"], "upper", ["passage", "reading-3"]),
        ("Third reading as Special Order of Business. Carried by Mannix. Passed.", ["passage", "reading-3"], "lower", ["passage", "reading-3"]),
        ("Rules suspended. Read. Carried by Helm. Adopted.", ["passage"], "lower", ["passage"]),
        ("Read. Carried by Nguyen D. Adopted.", ["passage"], "lower", ["passage"]),
        ("Rules suspended. Final reading.  Carried by Jama.  Adopted.", ["passage"], "upper", ["passage"]),
        # a result the categorizer missed on a line that is otherwise right also gains passage (same class of line)
        ("Third reading.  Carried by Reynolds.  Passed.", ["reading-3"], "upper", ["reading-3", "passage"]),
        # no result on the line, or a failed one: no passage
        ("Third reading. Carried by Helm.", [], "lower", []),
        ("Third reading. Carried by Dobson. Failed.", ["reading-3"], "lower", ["reading-3"]),
        ("Failed.", [], "lower", []),
        # only the bare line is the result: "Passed over" (skipped on the day's agenda, Nebraska's trap) is not
        ("Passed over.", [], "lower", []),
        # committee recommendations and motions that say "adopted" or "passed" are not the floor's result
        ("Recommendation: Be adopted.", [], "lower", []),
        ("Recommendation: Do pass.", ["committee-passage-favorable"], "lower", ["committee-passage-favorable"]),
        ("Motion to re-refer to Revenue failed.", [], "lower", []),
        ("Made a Special Order of Business by voice vote.", [], "upper", []),
        # the Governor's signature is a different label and stays
        ("Governor signed.", ["executive-signature"], "lower", ["executive-signature"]),
        # control sample: every other label set stored for Oregon in 2026R1, on its commonest text, as given
        ("First reading. Referred to Speaker's desk.", ["introduction", "reading-1", "referral-committee"], "lower", ["introduction", "reading-1", "referral-committee"]),
        ("Referred to Rules.", ["referral-committee"], "lower", ["referral-committee"]),
        ("Work Session held.", [], "lower", []),
        ("Second reading.", ["reading-2"], "upper", ["reading-2"]),
        ("First reading.", ["introduction", "reading-1"], "lower", ["introduction", "reading-1"]),
    ],
)
def test_oregons_signature_and_floor_result_lines(description, labels, chamber, expected):
    assert _rule(description, labels, chamber) == expected


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = ["passage"]
    assert _rule("Speaker signed.", labels) == [] and labels == ["passage"]
    empty = []
    assert _rule("Passed.", empty) == ["passage"] and empty == []


@pytest.mark.parametrize("date,chamber,labels,description", OR_HB4134 + OR_HCR201 + OR_HB4045)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_state_rule_finds_oregons_actions_only():
    assert classify_action is not None
    assert state_rule("or", "classify_bill") is None


def _or():
    _jurisdiction(OR, "Oregon", session="2026R1", chambers=("lower", "upper"))


@pytest.mark.django_db
def test_the_importer_applies_oregons_rule_and_a_rescrape_changes_nothing():
    _or()
    BillImporter(OR).import_data(
        [
            _scraped(OR_HB4134, "HB 4134", session="2026R1", chamber="lower"),
            _scraped(OR_HCR201, "HCR 201", session="2026R1", chamber="lower"),
            _scraped(OR_HB4045, "HB 4045", session="2026R1", chamber="lower"),
        ]
    )
    assert _stored("HB 4134") == OR_HB4134_RELABELLED
    assert _stored("HCR 201") == OR_HCR201_RELABELLED
    assert _stored("HB 4045") == OR_HB4045_RELABELLED
    # the bill's last passage is the Senate's vote, not the President's signature a day later
    assert Bill.objects.get(identifier="HB 4134").latest_passage_date == "2026-03-05T00:10:49+00:00"
    _, what = BillImporter(OR).import_item(_scraped(OR_HB4134, "HB 4134", session="2026R1", chamber="lower"))
    assert what == "noop"
    assert _stored("HB 4134") == OR_HB4134_RELABELLED


@pytest.mark.django_db
def test_relabel_or_now_runs_and_finds_the_stored_lines_the_rule_changes():
    bill = _bill(OR, "Oregon", "HB 4134", OR_HB4134)
    before = _labels(bill)

    result = _relabel_dry_run("or")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=3 unchanged=20" in result.output
    assert "action: passage -> (none): 2" in result.output
    assert "action: (none) -> passage: 1" in result.output
    assert _labels(bill) == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _or()
    BillImporter(OR).import_data([_scraped(OR_HCR201, "HCR 201", session="2026R1", chamber="lower")])
    result = _relabel_dry_run("or")
    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=16" in result.output
