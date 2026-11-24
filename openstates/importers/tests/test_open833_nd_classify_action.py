"""OPEN-833: North Dakota's `classify_action` (openstates/ddp_states/nd.py), applied on import (OPEN-550) and by
`os-text-extract relabel nd`.

Every text is real, from the Mac replica (2026-11-24, session 69), with the labels it is stored with and the
organization it is stored under: HB 1457 (failed on Second Reading, reconsidered, failed again), HB 1242 (failed for
lack of a constitutional majority; the motion to reconsider failed), SB 2155 (failed on a tie, reconsidered, passed both
chambers, signed) and HCR 3013 (adopted by the House, failed to adopt in the Senate).
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states import state_rule
from openstates.ddp_states.nd import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)

ND = "ocd-jurisdiction/country:us/state:nd/government"
SESSION = "69"

R2 = ["reading-2"]
R2F = ["reading-2", "failure"]
P2 = ["passage", "reading-2"]
CPF = ["committee-passage", "committee-passage-favorable"]
CPU = ["committee-passage", "committee-passage-unfavorable"]

# (date, chamber, labels as stored, text)
ND_HB1457 = [
    ("2025-01-16", "lower", ["introduction"], "Introduced, first reading, referred Human Services Committee"),
    ("2025-02-04", "lower", [], "Committee Hearing 09:30"),
    ("2025-02-18", "lower", CPF, "Reported back, do pass, place on calendar 7 6 0"),
    ("2025-02-19", "lower", R2, "Second reading, failed to pass, yeas 44 nays 48"),
    ("2025-02-20", "lower", [], "Reconsidered"),
    ("2025-02-20", "lower", R2, "Second reading, failed to pass, yeas 45 nays 42"),
]
ND_HB1457_RELABELLED = [list(labels) for _, _, labels, _ in ND_HB1457]
ND_HB1457_RELABELLED[3] = R2F
ND_HB1457_RELABELLED[5] = R2F

ND_HB1242 = [
    ("2025-01-13", "lower", ["introduction"], "Introduced, first reading, referred Judiciary Committee"),
    ("2025-02-11", "lower", [], "Committee Hearing 10:00"),
    ("2025-02-12", "lower", CPF, "Reported back, do pass, place on calendar 11 3 0"),
    ("2025-02-14", "lower", R2, "Second reading, failed, lacks constitutional majority yeas 47 nays 37"),
    ("2025-02-17", "lower", [], "Motion to reconsider failed"),
]
ND_HB1242_RELABELLED = [list(labels) for _, _, labels, _ in ND_HB1242]
ND_HB1242_RELABELLED[3] = R2F

ENR = "Energy and Natural Resources Committee"
ND_SB2155 = [
    ("2025-01-07", "upper", ["introduction"], "Introduced, first reading, referred " + ENR),
    ("2025-01-23", "upper", [], "Committee Hearing 09:45"),
    ("2025-01-27", "upper", CPU, "Reported back amended, do not pass, placed on calendar 7 0 0"),
    ("2025-01-28", "upper", [], "Amendment adopted, placed on calendar"),
    ("2025-01-29", "upper", R2, "Second reading, failed to pass, yeas 23 nays 23"),
    ("2025-01-30", "upper", [], "Reconsidered"),
    ("2025-01-30", "upper", P2, "Second reading, passed, yeas 27 nays 19"),
    ("2025-01-31", "lower", [], "Received from Senate"),
    ("2025-02-18", "lower", ["introduction"], "Introduced, first reading, referred " + ENR),
    ("2025-03-07", "lower", [], "Committee Hearing 08:30"),
    ("2025-03-28", "lower", CPF, "Reported back amended, do pass, amendment placed on calendar 11 0 2"),
    ("2025-03-31", "lower", [], "Amendment adopted, placed on calendar"),
    ("2025-04-01", "lower", P2, "Second reading, passed as amended, yeas 75 nays 14"),
    ("2025-04-02", "upper", [], "Returned to Senate (12)"),
    ("2025-04-15", "upper", [], "Concurred"),
    ("2025-04-15", "upper", P2, "Second reading, passed, yeas 30 nays 16"),
    ("2025-04-18", "lower", [], "Signed by Speaker"),
    ("2025-04-18", "upper", [], "Signed by President"),
    ("2025-04-18", "upper", ["executive-receipt"], "Sent to Governor"),
    ("2025-04-23", "upper", ["executive-signature"], "Signed by Governor 04/21"),
    ("2025-04-24", "legislature", ["introduction"], "Filed with Secretary Of State 04/22"),
]
ND_SB2155_RELABELLED = [list(labels) for _, _, labels, _ in ND_SB2155]
ND_SB2155_RELABELLED[4] = R2F

ND_HCR3013 = [
    ("2025-01-29", "lower", ["introduction"], "Introduced, first reading, referred Judiciary Committee"),
    ("2025-02-17", "lower", [], "Committee Hearing 10:00"),
    ("2025-02-18", "lower", CPF, "Reported back, do pass, place on calendar 11 1 2"),
    ("2025-02-19", "lower", [], "Laid over one legislative day"),
    ("2025-02-20", "lower", [], "Laid over two legislative days"),
    ("2025-02-24", "lower", R2, "Second reading, adopted yeas 52 nays 40"),
    ("2025-02-25", "upper", [], "Received from House"),
    ("2025-03-07", "upper", ["introduction"], "Introduced, first reading, referred Judiciary Committee"),
    ("2025-03-12", "upper", [], "Committee Hearing 03:15"),
    ("2025-03-12", "upper", ["committee-passage"], "Reported back without recommendation 6 1 0"),
    ("2025-03-13", "upper", R2, "Second reading, failed to adopt"),
]
ND_HCR3013_RELABELLED = [list(labels) for _, _, labels, _ in ND_HCR3013]
ND_HCR3013_RELABELLED[10] = R2F

ALL = ND_HB1457 + ND_HB1242 + ND_SB2155 + ND_HCR3013


def _rule(description, labels, chamber="lower", date="2025-02-19"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


# every stored form of a Second Reading defeat (Mac replica, 2026-11-24: 400 + 30 + 8 lines)
@pytest.mark.parametrize(
    "chamber,description",
    [
        ("lower", "Second reading, failed to pass, yeas 44 nays 48"),
        ("upper", "Second reading, failed to pass, yeas 23 nays 23"),
        ("upper", "Second reading, failed to adopt"),
        ("lower", "Second reading, failed to adopt yeas 15 nays 77"),
        ("upper", "Second reading, failed to adopt yeas 21 nays 26"),
        ("lower", "Second reading, failed, lacks constitutional majority yeas 47 nays 37"),
    ],
)
def test_a_second_reading_defeat_gains_failure_and_keeps_reading_2(chamber, description):
    assert _rule(description, R2, chamber) == R2F


# the other failed motions, and the Second Reading lines that are not a defeat, stay as stored
@pytest.mark.parametrize(
    "chamber,description,labels",
    [
        ("upper", "Amendment failed", ["amendment-failure"]),
        ("lower", "Amendment failed, placed on calendar", ["amendment-failure"]),
        ("lower", "Motion to reconsider failed", []),
        ("upper", "Motion to reconsider failed", []),
        ("lower", "Emergency clause failed", []),
        ("upper", "Division A lost", []),
        ("lower", "Division B lost", []),
        ("lower", "Reconsidered", []),
        ("lower", "Second reading, passed, yeas 86 nays 4", P2),
        ("upper", "Second reading, passed as amended, yeas 42 nays 4", P2),
        ("lower", "Second reading, passed, yeas 82 nays 8, Emergency clause carried", P2),
        ("lower", "Second reading, adopted yeas 52 nays 40", R2),
        ("upper", "Second reading, adopted", R2),
        # not stored: a wording outside the three forms is left alone until someone looks at it
        ("lower", "Second reading, failed to concur", R2),
        ("lower", "Second reading, failed", R2),
        ("lower", "Second reading, failed to pass after reconsideration", R2),
        ("lower", "Second reading, failed, lacks constitutional majority yeas 47 nays 37, Emergency clause failed", R2),
    ],
)
def test_other_failed_motions_and_second_reading_passages_are_left_alone(chamber, description, labels):
    assert _rule(description, labels, chamber) == labels


def test_failure_is_added_once_at_the_end_and_other_labels_keep_their_order():
    assert _rule("Second reading, failed to pass, yeas 10 nays 36", R2F) == R2F
    failure_first = ["failure", "reading-2"]
    assert _rule("Second reading, failed to pass, yeas 10 nays 36", failure_first) == failure_first
    assert _rule("Second reading, failed to adopt", []) == ["failure"]
    assert _rule("second reading, failed to adopt", R2) == R2F
    assert _rule("", R2) == R2


def test_the_rule_returns_a_new_list_and_leaves_its_input_alone():
    labels = list(R2)
    assert _rule("Second reading, failed to pass, yeas 10 nays 36", labels) == R2F and labels == R2
    same = list(P2)
    assert _rule("Second reading, passed, yeas 86 nays 4", same) is not same
    assert classify_action(description=None, classification=R2, chamber=None, date=None) == R2


@pytest.mark.parametrize("date,chamber,labels,description", ALL)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def test_the_rule_reads_the_text_not_the_organization():
    for chamber in ("upper", "lower", "legislature", None):
        assert _rule("Second reading, failed to pass, yeas 10 nays 36", R2, chamber) == R2F
        assert _rule("Motion to reconsider failed", [], chamber) == []


def test_state_rule_finds_north_dakotas_actions_only():
    assert state_rule("nd", "classify_action") is classify_action
    assert state_rule("nd", "classify_bill") is None


def _nd():
    _jurisdiction(ND, "North Dakota", session=SESSION, chambers=("lower", "upper", "legislature"))


@pytest.mark.django_db
def test_the_importer_marks_the_floor_defeats():
    _nd()

    BillImporter(ND).import_item(_scraped(ND_HB1457, "HB 1457", session=SESSION, chamber="lower"))
    BillImporter(ND).import_item(_scraped(ND_HB1242, "HB 1242", session=SESSION, chamber="lower"))
    BillImporter(ND).import_item(_scraped(ND_HCR3013, "HCR 3013", session=SESSION, chamber="lower"))

    assert _stored("HB 1457") == ND_HB1457_RELABELLED
    assert _stored("HB 1242") == ND_HB1242_RELABELLED
    assert _stored("HCR 3013") == ND_HCR3013_RELABELLED
    assert Bill.objects.get(identifier="HB 1457").latest_passage_date is None


@pytest.mark.django_db
def test_a_reconsidered_bill_keeps_its_passages_and_a_rescrape_changes_nothing():
    _nd()

    BillImporter(ND).import_item(_scraped(ND_SB2155, "SB 2155", session=SESSION, chamber="upper"))
    assert _stored("SB 2155") == ND_SB2155_RELABELLED
    assert Bill.objects.get(identifier="SB 2155").latest_passage_date == "2025-04-15"

    _, what = BillImporter(ND).import_item(_scraped(ND_SB2155, "SB 2155", session=SESSION, chamber="upper"))
    assert what == "noop"
    assert _stored("SB 2155") == ND_SB2155_RELABELLED


@pytest.mark.django_db
def test_relabel_nd_now_runs_and_finds_the_stored_defeats():
    bills = [
        _bill(ND, "North Dakota", "HB 1457", ND_HB1457),
        _bill(ND, "North Dakota", "HB 1242", ND_HB1242),
        _bill(ND, "North Dakota", "SB 2155", ND_SB2155),
        _bill(ND, "North Dakota", "HCR 3013", ND_HCR3013),
    ]
    before = [_labels(b) for b in bills]

    result = _relabel_dry_run("nd")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=5 unchanged=38" in result.output
    assert "action: reading-2 -> reading-2, failure: 5" in result.output
    assert [_labels(b) for b in bills] == before


@pytest.mark.django_db
def test_relabel_finds_nothing_left_to_change_after_an_import():
    _nd()
    BillImporter(ND).import_data(
        [
            _scraped(ND_HB1457, "HB 1457", session=SESSION, chamber="lower"),
            _scraped(ND_HB1242, "HB 1242", session=SESSION, chamber="lower"),
            _scraped(ND_SB2155, "SB 2155", session=SESSION, chamber="upper"),
            _scraped(ND_HCR3013, "HCR 3013", session=SESSION, chamber="lower"),
        ]
    )

    result = _relabel_dry_run("nd")

    assert result.exit_code == 0, result.output
    assert "actions changed=0 unchanged=43" in result.output
