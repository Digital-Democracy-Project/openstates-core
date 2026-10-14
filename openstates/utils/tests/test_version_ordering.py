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


def test_is_procedural_document_utah_amendment_zero_is_not_matched():
    """Real amendment numbering starts at 1 in every real note this module's own audit
    observed -- `[1-9]\\d*` deliberately excludes the hypothetical "Amendment 0" rather than
    accepting it via a bare `\\d+`, at zero cost."""
    assert is_procedural_document("Utah", "House Amendment 0") is False


def test_is_procedural_document_jurisdiction_specific():
    """"Conference Report" is a Virginia stub (median ~813 chars) but a real, large Michigan
    document (median well over 1M chars) -- this module's own real-data audit found both, and
    the exclusion table is per-jurisdiction specifically because of this."""
    assert is_procedural_document("Virginia", "Conference Report") is True
    assert is_procedural_document("Michigan", "Conference Report") is False


def test_is_procedural_document_unknown_jurisdiction_excludes_nothing():
    assert is_procedural_document("Some Never-Onboarded State", "Conference Report") is False


# --- OPEN-246: Virginia's committee/subcommittee/legislator "Amendment" family ----------


@pytest.mark.parametrize(
    "note",
    [
        # SB 542's own real note -- the live incident that prompted this ticket.
        "Courts of Justice Amendment",
        "courts of justice amendment",  # case-insensitive
        "Commerce and Labor Amendment",  # OPEN-224's own original named example, never added
        "Senator Sturtevant Amendments",  # legislator-named, plural, the largest real instance
        "Subcommittee #2 Subcommittee Amendment",
        "Delegate Price                Amendment",  # real multi-space formatting from the scraper
    ],
)
def test_is_procedural_document_virginia_committee_amendment_pattern(note):
    assert is_procedural_document("Virginia", note) is True


@pytest.mark.parametrize(
    "note",
    [
        # The critical regression: a lexically-adjacent, same-committee-prefix family that is
        # real committee-substitute full text, not a stub (median 6,810 chars / max 904,438
        # chars across 5,968 real Virginia documents) -- must not be caught by a pattern aimed
        # at the "Amendment" family just because it shares a committee-name prefix.
        "Courts of Justice Substitute",
        "Commerce and Labor Substitute",
        # Still the ticket's own named regression case for Virginia generally -- ends in
        # "Substitute", not "Amendment", so this pattern must not touch it either.
        "Amendment in the Nature of a Substitute",
        # Does not end in the word "Amendment"/"Amendments".
        "Amendment",  # no committee/legislator prefix at all -- not observed in real data
    ],
)
def test_is_procedural_document_virginia_committee_amendment_does_not_exclude_real_text(note):
    assert is_procedural_document("Virginia", note) is False


# --- OPEN-361: Utah's stamped "Amended <M/D/YYYY H:MM:N>" is ranked by date and hour ----------


@pytest.mark.parametrize(
    "notes_in_true_order",
    [
        # Real bills. The true order is that of the timestamps in the archived file URLs; the
        # old rule (the last number) put the first of these as 1, 3, 2 and the second exactly
        # reversed.
        [
            "Amended 1/27/2026 11:01:108",
            "Amended 2/9/2026 11:02:713",
            "Amended 3/3/2026 19:03:587",
        ],
        [
            "Amended 2/13/2026 10:02:552",
            "Amended 2/20/2026 15:02:361",
            "Amended 2/26/2026 12:02:334",
        ],
    ],
)
def test_utah_stamped_amended_versions_are_ordered_by_date(notes_in_true_order):
    shuffled = [notes_in_true_order[i] for i in (2, 0, 1)]
    ranked = sorted(shuffled, key=lambda n: version_sort_key(n, ""))
    assert ranked == notes_in_true_order


def test_utah_stamped_amended_orders_by_hour_within_a_day():
    earlier = "Amended 2/12/2026 09:02:900"
    later = "Amended 2/12/2026 15:02:100"
    assert version_sort_key(earlier, "") < version_sort_key(later, "")


def test_utah_stamped_amended_same_hour_falls_back_to_the_last_number():
    """Deterministic, though the note cannot say which of two same-hour versions is later
    (right for one of the two such Utah bills seen)."""
    assert version_sort_key("Amended 2/12/2026 10:02:100", "") < version_sort_key(
        "Amended 2/12/2026 10:02:409", ""
    )


def test_utah_stamped_amended_still_sorts_after_substitutes_and_before_enrolled():
    stamped = version_sort_key("Amended 1/28/2026 10:01:41", "")
    assert version_sort_key("Substitute #9", "") < stamped < version_sort_key("Enrolled", "")


def test_utah_stamped_amended_stays_in_the_amendment_stage():
    assert note_stage("Amended 2/12/2026 10:02:409")[0] == STAGE_AMENDMENT
    assert note_stage("Amended Excerpts 2/12/2026 10:02:409")[0] == STAGE_AMENDMENT


def test_utah_amended_excerpt_ranks_with_its_full_text_twin():
    assert extract_ordinal("Amended Excerpts 2/12/2026 10:02:409") == extract_ordinal(
        "Amended 2/12/2026 10:02:409"
    )


@pytest.mark.parametrize(
    "note, expected",
    [
        # Not a real date: the generic rule (last number) applies, and nothing raises.
        ("Amended 13/45/2026 10:02:409", 409.0),
        ("Amended 2/30/2026 10:02:409", 409.0),
        # Outside the observed shape: an hour above 23, a last number of more than four
        # digits, a date before 2000. Not ranked on data that cannot be a real time.
        ("Amended 2/12/2026 24:02:409", 409.0),
        ("Amended 2/12/2026 99:02:409", 409.0),
        ("Amended 2/12/2026 10:02:10000", 10000.0),
        ("Amended 12/31/1999 10:02:5", 5.0),
        # Not the stamped shape at all: unchanged.
        ("Amended Engrossed", 0.0),
        ("Substitute #3", 3.0),
        ("Amended 2/12/2026", 2026.0),
    ],
)
def test_extract_ordinal_leaves_everything_else_to_the_generic_rules(note, expected):
    assert extract_ordinal(note) == expected


def test_utah_stamped_amended_last_number_stays_in_order_up_to_four_digits():
    """The last number is a fraction under 1, so four digits never spill into the next hour."""
    assert version_sort_key("Amended 2/12/2026 10:02:1000", "") < version_sort_key(
        "Amended 2/12/2026 10:02:9999", ""
    )
    assert version_sort_key("Amended 2/12/2026 10:02:9999", "") < version_sort_key(
        "Amended 2/12/2026 11:02:1", ""
    )


def test_utah_stamped_amended_ranks_after_substitutes_even_at_the_epoch():
    """The base of 1000 keeps every matched note above numbered substitutes, including the
    earliest date the pattern accepts."""
    assert version_sort_key("Substitute #9", "") < version_sort_key(
        "Amended 1/1/2000 0:02:1", ""
    )
