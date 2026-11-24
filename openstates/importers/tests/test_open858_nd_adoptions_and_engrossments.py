"""OPEN-858: North Dakota (openstates/ddp_states/nd.py).

1. A resolution's adoption on Second Reading is its passage (`classify_action`), so SCR 4008 (69), which failed in the
   House and was then reconsidered and adopted, no longer reads as failed.
2. An engrossment its chamber did not adopt ("SECOND ENGROSSMENT - NOT ADOPTED") is not a version of the measure
   (`PROCEDURAL_DOCUMENT_NOTE_PATTERN`).

Every text and version name is real, from the Mac replica (openstates_rds_repl_20260911, 2026-11-24, session 69), with
the labels it is stored with. SCR 4008 is its whole stored history.
"""

import pytest

from openstates.cli.tests.test_open546_relabel import _bill, _labels
from openstates.data.models import Bill
from openstates.ddp_states.nd import classify_action
from openstates.importers import BillImporter
from openstates.importers.tests.test_open550_ne_classify_action import (
    _jurisdiction,
    _relabel_dry_run,
    _scraped,
    _stored,
)
from openstates.utils.version_ordering import is_bill_version

ND = "ocd-jurisdiction/country:us/state:nd/government"
SESSION = "69"

R2 = ["reading-2"]
R2P = ["reading-2", "passage"]

GVA = "Government and Veterans Affairs Committee"
# (date, chamber, labels as stored, text)
ND_SCR4008 = [
    (
        "2025-01-27",
        "upper",
        ["introduction"],
        "Introduced, first reading, referred State and Local Government Committee",
    ),
    ("2025-02-20", "upper", [], "Committee Hearing 09:30"),
    (
        "2025-03-14",
        "upper",
        ["committee-passage", "committee-passage-favorable"],
        "Reported back amended, do pass, amendment placed on calendar 5 1 0",
    ),
    ("2025-03-17", "upper", [], "Amendment adopted, placed on calendar"),
    ("2025-03-17", "upper", R2, "Second reading, adopted yeas 24 nays 23"),
    ("2025-03-18", "lower", [], "Received from Senate"),
    ("2025-03-19", "lower", ["introduction"], "Introduced, first reading, referred " + GVA),
    ("2025-03-27", "lower", [], "Committee Hearing 02:30"),
    (
        "2025-03-31",
        "lower",
        ["committee-passage", "committee-passage-unfavorable"],
        "Reported back, do not pass, placed on calendar 8 5 1",
    ),
    ("2025-04-01", "lower", R2, "Second reading, failed, lacks constitutional majority yeas 46 nays 43"),
    ("2025-04-02", "lower", [], "Reconsidered"),
    ("2025-04-02", "lower", R2, "Second reading, adopted yeas 53 nays 39"),
    ("2025-04-03", "upper", [], "Returned to Senate"),
    ("2025-04-07", "lower", [], "Signed by Speaker"),
    ("2025-04-08", "upper", [], "Signed by President"),
    ("2025-04-08", "upper", ["introduction"], "Filed with Secretary Of State 04/08"),
]
ND_SCR4008_RELABELLED = [list(labels) for _, _, labels, _ in ND_SCR4008]
ND_SCR4008_RELABELLED[4] = R2P
ND_SCR4008_RELABELLED[9] = ["reading-2", "failure"]  # OPEN-833
ND_SCR4008_RELABELLED[11] = R2P


def _rule(description, labels, chamber="lower", date="2025-04-02"):
    return classify_action(description=description, classification=labels, chamber=chamber, date=date)


# every stored form of a resolution's adoption (Mac replica, 2026-11-24: 82 lines)
@pytest.mark.parametrize(
    "chamber,description",
    [
        ("upper", "Second reading, adopted"),
        ("lower", "Second reading, adopted"),
        ("lower", "Second reading, adopted yeas 53 nays 39"),
        ("upper", "Second reading, adopted yeas 24 nays 23"),
        ("lower", "Second reading, adopted as amended yeas 68 nays 25"),
        ("upper", "Second reading, adopted as amended"),
    ],
)
def test_a_resolutions_adoption_gains_passage_and_keeps_reading_2(chamber, description):
    assert _rule(description, R2, chamber) == R2P


@pytest.mark.parametrize(
    "description,labels",
    [
        ("Amendment adopted, placed on calendar", []),
        ("Second reading, failed to adopt", ["reading-2", "failure"]),
        ("Second reading, passed, yeas 86 nays 4", ["passage", "reading-2"]),
        ("Reconsidered", []),
        # not stored: a wording outside the forms is left alone until someone looks at it
        ("Second reading, adopted after reconsideration", R2),
        ("Report adopted", []),
    ],
)
def test_other_adoptions_and_second_reading_lines_are_left_alone(description, labels):
    assert _rule(description, labels) == labels


def test_passage_is_added_once_and_the_input_is_left_alone():
    assert _rule("Second reading, adopted", ["passage", "reading-2"]) == ["passage", "reading-2"]
    labels = list(R2)
    assert _rule("second reading, adopted", labels) == R2P and labels == R2


@pytest.mark.parametrize("date,chamber,labels,description", ND_SCR4008)
def test_the_rule_is_stable_on_its_own_output(date, chamber, labels, description):
    once = _rule(description, labels, chamber, date)
    assert _rule(description, once, chamber, date) == once


def _nd():
    _jurisdiction(ND, "North Dakota", session=SESSION, chambers=("lower", "upper", "legislature"))


@pytest.mark.django_db
def test_scr4008_reads_adopted_after_its_reconsideration():
    _nd()

    BillImporter(ND).import_item(_scraped(ND_SCR4008, "SCR 4008", session=SESSION, chamber="upper"))

    assert _stored("SCR 4008") == ND_SCR4008_RELABELLED
    # its last floor action is the House's adoption of 04-02, after the failure of 04-01
    assert Bill.objects.get(identifier="SCR 4008").latest_passage_date == "2025-04-02"


@pytest.mark.django_db
def test_relabel_nd_finds_the_stored_adoptions():
    bill = _bill(ND, "North Dakota", "SCR 4008", ND_SCR4008)
    before = _labels(bill)

    result = _relabel_dry_run("nd")

    assert result.exit_code == 0, result.output
    assert "[DRY RUN] actions changed=3 unchanged=13" in result.output
    assert "action: reading-2 -> reading-2, passage: 2" in result.output
    assert _labels(bill) == before


# ── versions ──

# every stored North Dakota version name (Mac replica, 2026-11-24, 2,654 versions), with N for the numbers it holds
ND_VERSION_NOTES = [
    "INTRODUCED",
    "Enrollment",
    "FIRST ENGROSSMENT",
    "FIRST ENGROSSMENT with Senate Amendments",
    "FIRST ENGROSSMENT with House Amendments",
    "FIRST ENGROSSMENT with Conference Committee Amendments",
    "SECOND ENGROSSMENT",
    "SECOND ENGROSSMENT with House Amendments",
    "SECOND ENGROSSMENT with Senate Amendments",
    "SECOND ENGROSSMENT with Conference Committee Amendments",
    "THIRD ENGROSSMENT",
    "FOURTH ENGROSSMENT",
    "FOURTH ENGROSSMENT with House Amendments",
    "FOURTH ENGROSSMENT with Conference Committee Amendments",
]
# the three stored engrossments not adopted: SB 2308 and SB 2400 (SECOND), SCR 4022 (FIRST)
ND_NOT_ADOPTED = ["SECOND ENGROSSMENT - NOT ADOPTED", "FIRST ENGROSSMENT - NOT ADOPTED"]


@pytest.mark.parametrize("note", ND_NOT_ADOPTED)
def test_an_engrossment_not_adopted_is_not_a_version(note):
    assert is_bill_version("North Dakota", note) is False


@pytest.mark.parametrize("note", ND_VERSION_NOTES)
def test_every_other_stored_name_stays_a_version(note):
    assert is_bill_version("North Dakota", note) is True


@pytest.mark.parametrize("note", ["SECOND ENGROSSMENT - NOT ADOPTED", "FIRST ENGROSSMENT"])
def test_the_rule_does_not_reach_other_states(note):
    assert is_bill_version("South Dakota", note) is True
    assert is_bill_version("Missouri", note) is True
