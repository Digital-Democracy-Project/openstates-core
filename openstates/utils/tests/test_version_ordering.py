"""Direct unit coverage for openstates/utils/version_ordering.py (OPEN-91).

This is a smaller, focused smoke test importing the module's own public
names directly -- the exhaustive real-jurisdiction-sample coverage this
logic already has (OPEN-34's audit) lives in
openstates/cli/tests/test_text_extract.py, which continues to exercise the
exact same implementation via text_extract.py's backward-compatible
`_note_stage`/`_version_sort_key`/`_STAGE_*` aliases -- not duplicated here.
"""

import pytest  # type: ignore

from openstates.utils.version_ordering import (
    STAGE_AMENDMENT,
    STAGE_CHAMBER_PASSAGE,
    STAGE_ENACTED,
    STAGE_FINAL_PASSAGE,
    STAGE_INTRODUCED,
    STAGE_UNKNOWN,
    extract_ordinal,
    note_stage,
    version_sort_key,
)


@pytest.mark.parametrize(
    ("note", "expected_stage"),
    [
        ("Introduced", STAGE_INTRODUCED),
        ("Filed", STAGE_INTRODUCED),
        ("Bill Text", STAGE_INTRODUCED),  # MA's only version_note
        ("Substitute (S-1)", STAGE_AMENDMENT),
        ("Senate Engrossed Version", STAGE_CHAMBER_PASSAGE),
        ("e2", STAGE_CHAMBER_PASSAGE),
        ("Enrolled", STAGE_FINAL_PASSAGE),
        ("Reenrolled", STAGE_FINAL_PASSAGE),
        ("Governor's Veto Explanation", STAGE_FINAL_PASSAGE),
        ("Chapter Law Text (Enacted)", STAGE_ENACTED),
        ("Public Act", STAGE_ENACTED),
        ("Some Never-Before-Seen Document Type", STAGE_UNKNOWN),
    ],
)
def test_note_stage_classifies_known_vocabulary(note, expected_stage):
    stage, _ = note_stage(note)
    assert stage == expected_stage


def test_note_stage_never_looks_at_position_only_content():
    """Same note text always classifies identically -- note_stage() takes no
    positional/index argument at all, only the note string itself."""
    assert note_stage("Introduced") == note_stage("Introduced")


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("Substitute (S-2)", 2.0),
        ("Substitute (S-1) - 2", 1.02),
        ("Second Substitute", 2.0),
        ("c1", 1.0),
        ("c2", 2.0),
        ("Introduced", 0.0),
    ],
)
def test_extract_ordinal(note, expected):
    assert extract_ordinal(note) == expected


def test_version_sort_key_orders_by_stage_not_alphabetically():
    """The whole point of this module: a naive (date, note) string sort
    would put "Enrolled" before "Introduced" alphabetically, but the real
    chronology is the reverse."""
    introduced_key = version_sort_key("Introduced", None)
    enrolled_key = version_sort_key("Enrolled", None)
    assert introduced_key < enrolled_key


def test_version_sort_key_date_is_only_a_same_stage_tiebreaker():
    """A real date on one version must never let it jump ahead of a
    later-stage undated version -- confirmed real for US federal, which
    mixes dated and undated versions on the same bill."""
    early_dated_introduced = version_sort_key("Introduced", "2026-01-01")
    undated_enrolled = version_sort_key("Enrolled", None)
    assert early_dated_introduced < undated_enrolled


def test_version_sort_key_unparseable_date_is_ignored():
    assert version_sort_key("Introduced", "not-a-date") == version_sort_key("Introduced", None)
