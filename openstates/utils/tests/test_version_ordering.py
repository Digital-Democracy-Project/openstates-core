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
    is_procedural_document,
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


# --- OPEN-224: is_procedural_document() -------------------------------------------------


@pytest.mark.parametrize(
    "note",
    [
        "Conference Report",
        "Governor's Recommendation",
        "Governor's Recommendations",
        "Governor's Veto Explanation",
    ],
)
def test_is_procedural_document_virginia_known_stubs(note):
    assert is_procedural_document("Virginia", note) is True


@pytest.mark.parametrize(
    "note",
    [
        # AC6's named regression case -- a genuine full-replacement text, not a stub.
        "Amendment in the Nature of a Substitute",
        # This module's own audit found these are real, large documents too (median tens of
        # thousands of characters) -- despite "Substitute"/"Report" sharing a word with an
        # excluded note above, exact-match only, no substring/regex over those words.
        "Governor Substitute",
        "Conference Report Substitute",
        # A real Virginia full-text stage, unaffected.
        "Enrolled",
        "Introduced",
    ],
)
def test_is_procedural_document_virginia_does_not_exclude_real_text(note):
    assert is_procedural_document("Virginia", note) is False


@pytest.mark.parametrize(
    "note",
    [
        "House Amendment 1",
        "house amendment 1",  # case-insensitive
        "Senate Amendment 2",
        "House Amendment 12",  # not limited to the specific numbers sampled by the audit
    ],
)
def test_is_procedural_document_utah_amendment_pattern(note):
    assert is_procedural_document("Utah", note) is True


@pytest.mark.parametrize(
    "note",
    [
        "Introduced",
        "Enrolled",
        # Must not match as a substring inside a longer, different note.
        "House Amendment 1 to Substitute",
        "House Amendment",  # no trailing number at all
    ],
)
def test_is_procedural_document_utah_does_not_exclude_real_text(note):
    assert is_procedural_document("Utah", note) is False


def test_is_procedural_document_jurisdiction_specific():
    """"Conference Report" is a Virginia stub (median ~813 chars) but a real, large Michigan
    document (median well over 1M chars) -- this module's own real-data audit found both, and
    the exclusion table is per-jurisdiction specifically because of this."""
    assert is_procedural_document("Virginia", "Conference Report") is True
    assert is_procedural_document("Michigan", "Conference Report") is False


def test_is_procedural_document_unknown_jurisdiction_excludes_nothing():
    assert is_procedural_document("Some Never-Onboarded State", "Conference Report") is False
