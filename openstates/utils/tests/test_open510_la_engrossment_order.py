"""OPEN-510: Louisiana's versions are ordered by their names, not by storage order.

"<bill> Engrossed", "<bill> Reengrossed" and "<bill> Re-Reengrossed" used to share one rank, and
Louisiana versions carry no date, so the three kept whatever order they were stored in (often
reversed). The notes below are real Louisiana notes read on the Mac replica (2026-11-08).
"""

import random
from types import SimpleNamespace

import pytest  # type: ignore

from openstates.cli.text_extract import recomputed_diffs_for_documents
from openstates.utils.version_ordering import (
    STAGE_CHAMBER_PASSAGE,
    STAGE_UNKNOWN,
    note_stage,
    version_sort_key,
)

# SB 217 (2026 regular session): its five real notes, in their true order.
SB217 = [
    "SB217 Original",
    "SB217 Engrossed",
    "SB217 Reengrossed",
    "SB217 Enrolled",
    "SB217 Act",
]


def _sorted(notes):
    return sorted(notes, key=lambda n: version_sort_key(n, ""))


def test_sb217_orders_by_name_whatever_order_it_was_stored_in():
    rng = random.Random(510)
    for _ in range(20):
        shuffled = SB217[:]
        rng.shuffle(shuffled)
        assert _sorted(shuffled) == SB217
    # the order the ticket saw: Original, Reengrossed, Engrossed (Re-Reengrossed first too)
    assert _sorted(list(reversed(SB217))) == SB217


@pytest.mark.parametrize(
    "notes_in_true_order",
    [
        # every Louisiana version shape on the replica, one bill each
        [
            "HB519 Original",
            "HB519 Engrossed",
            "HB519 Reengrossed",
            "HB519 Re-Reengrossed",
            "HB519 Enrolled",
            "HB519 Act 303",
        ],
        ["HCR12 Original", "HCR12 Engrossed", "HCR12 Reengrossed", "HCR12 Enrolled"],
        ["SB40 Original", "SB40 Reengrossed", "SB40 Re-Reengrossed", "SB40 Act"],
    ],
)
def test_louisiana_stages_in_order(notes_in_true_order):
    assert _sorted(list(reversed(notes_in_true_order))) == notes_in_true_order


def test_the_three_engrossments_rank_one_after_another_in_one_stage():
    assert note_stage("SB217 Engrossed") == (STAGE_CHAMBER_PASSAGE, 0.0)
    assert note_stage("SB217 Reengrossed") == (STAGE_CHAMBER_PASSAGE, 1.0)
    assert note_stage("HB519 Re-Reengrossed") == (STAGE_CHAMBER_PASSAGE, 2.0)


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        # Virginia's bare notes keep the generic rule's rank (ordinal 0)
        ("Reengrossed", (STAGE_CHAMBER_PASSAGE, 0.0)),
        ("Engrossed", (STAGE_CHAMBER_PASSAGE, 0.0)),
        # other states' engrossed notes are untouched
        ("Senate Engrossed Version", (STAGE_CHAMBER_PASSAGE, 0.0)),
        ("House Engrossed Version", (STAGE_CHAMBER_PASSAGE, 0.0)),
        ("Engrossed in House", (STAGE_CHAMBER_PASSAGE, 0.0)),
        ("Engrossed Second Substitute Bill", (1, 2.5)),
    ],
)
def test_other_engrossed_notes_keep_their_rank(note, expected):
    assert note_stage(note) == expected


@pytest.mark.parametrize(
    "note",
    [
        "SB217 Re-Re-Reengrossed",
        "SB217 Engrossed Copy",
        "SB217 Reengrossed Copy",
        "SB 217 Reengrossed",
    ],
)
def test_the_louisiana_shape_is_exact(note):
    """Near misses fall through to the generic rule (same stage, ordinal not 1 or 2)."""
    stage, ordinal = note_stage(note)
    assert stage != STAGE_UNKNOWN
    assert ordinal not in (1.0, 2.0)


def test_recompute_diffs_each_version_against_the_one_before_it():
    """End to end through the recompute path: documents stored in the reversed order the
    ticket found still diff Original -> Engrossed -> Reengrossed -> Re-Reengrossed."""
    texts = {
        "SB217 Original": "Section 1. The court shall have nine judges.\n",
        "SB217 Engrossed": "Section 1. The court shall have twelve judges.\n",
        "SB217 Reengrossed": "Section 1. The court shall have twelve judges.\nSection 2. Effective.\n",
        "SB217 Re-Reengrossed": "Section 1. The court shall have twelve judges.\nSection 2. Effective now.\n",
    }
    stored_order = [
        "SB217 Original",
        "SB217 Re-Reengrossed",
        "SB217 Reengrossed",
        "SB217 Engrossed",
    ]
    docs = [
        SimpleNamespace(
            version_note=note,
            version_date="",
            source_url=f"https://legis.la.gov/{i}.pdf",
            media_type="application/pdf",
            raw_text=texts[note],
            is_error=False,
            diff_from_previous_version=None,
        )
        for i, note in enumerate(stored_order)
    ]
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Louisiana", is_bill=True, bill_identifier="SB 217"
    )
    diffs = {doc.version_note: diff for doc, diff in result["changed"]}
    # Original is first in the lineage: nothing to compare it with
    assert "SB217 Original" not in diffs
    assert "-Section 1. The court shall have nine judges." in diffs["SB217 Engrossed"]
    assert "+Section 1. The court shall have twelve judges." in diffs["SB217 Engrossed"]
    assert "+Section 2. Effective." in diffs["SB217 Reengrossed"]
    assert "nine" not in diffs["SB217 Reengrossed"]
    assert "-Section 2. Effective." in diffs["SB217 Re-Reengrossed"]
    assert "+Section 2. Effective now." in diffs["SB217 Re-Reengrossed"]
