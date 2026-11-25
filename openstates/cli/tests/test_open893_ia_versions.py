"""OPEN-893: Iowa's "Prefiled", "Reprinted" and "Reprinted Marked Up" versions have a stage.

The notes are legis.iowa.gov's own Bill Book version names (Mac replica, 2026-11-25). A reprint is headed "(As Amended
and Passed by the House|Senate <date>)": the bill as a chamber passed it after amending it. A prefiled text is a draft
("SENATE/HOUSE FILE _____ BY (PROPOSED ... BILL)") posted before the bill is introduced.
"""

import itertools

import pytest

from openstates.utils.version_ordering import (
    STAGE_AMENDMENT,
    STAGE_CHAMBER_PASSAGE,
    STAGE_FINAL_PASSAGE,
    STAGE_INTRODUCED,
    STAGE_UNKNOWN,
    note_stage,
    version_sort_key,
)

# notes Iowa stores that already had a stage, with the (stage, ordinal) clean main gives them
IOWA_NOTES_BEFORE = {
    "Introduced": (STAGE_INTRODUCED, 0.0),
    "Enrolled with Governor's Action": (STAGE_FINAL_PASSAGE, 1.0),
    "Signed Enrolled Bill (PDF)": (STAGE_FINAL_PASSAGE, 0.0),
    "Enrolled": (STAGE_FINAL_PASSAGE, 0.0),
    "Amendment S-3087": (STAGE_AMENDMENT, 3087.0),
    "Amendment H-1154": (STAGE_AMENDMENT, 1154.0),
}


@pytest.mark.parametrize("note", IOWA_NOTES_BEFORE)
def test_iowa_notes_that_had_a_stage_keep_it(note):
    stage, ordinal = note_stage(note)
    assert (stage, ordinal) == IOWA_NOTES_BEFORE[note]


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("Prefiled", (STAGE_INTRODUCED, -1.0)),
        ("Reprinted", (STAGE_CHAMBER_PASSAGE, 0.0)),
        ("Reprinted Marked Up", (STAGE_CHAMBER_PASSAGE, 0.5)),
        ("prefiled", (STAGE_INTRODUCED, -1.0)),
    ],
)
def test_note_stage_iowa(note, expected):
    assert note_stage(note) == expected


@pytest.mark.parametrize(
    "note",
    ["Reprint", "Reprinted 1", "Reprinted Marked", "Marked Up", "Prefile", "Prefiled Bill", "Re-Reprinted", "Reprinted Copy"],
)
def test_close_shapes_stay_unknown(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


# SF 426 (2025), every stored version; the order the database happened to return is the reverse of the real one
SF426 = ["Reprinted Marked Up", "Reprinted", "Introduced"]
# HF 862 (2025): the real order is Introduced, Amendment, Reprinted, then the enrolled texts
HF862 = [
    "Amendment H-1245",
    "Signed Enrolled Bill (PDF)",
    "Enrolled with Governor's Action",
    "Amendment S-3107",
    "Introduced",
    "Amendment H-1176",
    "Reprinted",
]


def _sorted(notes):
    return sorted(notes, key=lambda n: version_sort_key(n, "", jurisdiction_name="Iowa"))


@pytest.mark.parametrize("order", list(itertools.permutations(SF426)))
def test_sf426_sorts_introduced_reprinted_marked_up(order):
    assert _sorted(order) == ["Introduced", "Reprinted", "Reprinted Marked Up"]


def test_prefiled_sorts_before_introduced():
    assert _sorted(["Introduced", "Prefiled"]) == ["Prefiled", "Introduced"]


def test_reprinted_sorts_after_amendment_papers_and_before_enrolled_texts():
    ordered = _sorted(HF862)
    assert ordered.index("Introduced") < ordered.index("Reprinted")
    assert ordered.index("Amendment H-1176") < ordered.index("Reprinted")
    assert ordered.index("Reprinted") < ordered.index("Enrolled with Governor's Action")
    assert ordered.index("Reprinted") < ordered.index("Signed Enrolled Bill (PDF)")


@pytest.mark.parametrize("note", ["Reprinted", "Reprinted Marked Up", "Prefiled"])
def test_gate_counts_them_as_staged(note):
    # quality_check.py's Stage 3 gate counts a version as "no stage" when note_stage(note)[0] == STAGE_UNKNOWN
    assert note_stage(note)[0] != STAGE_UNKNOWN
