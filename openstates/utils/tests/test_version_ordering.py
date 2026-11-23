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
    document_number,
    extract_ordinal,
    is_bill_version,
    is_procedural_document,
    note_stage,
    origin_chamber,
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


# --- OPEN-350: Utah's redline "Comparison to ..." and "Amended Excerpts ..." families -------


@pytest.mark.parametrize(
    "note",
    [
        # Real notes from the OPEN-349 audit (every distinct Utah note was checked).
        "Comparison to Original Bill",
        "Comparison to Sub #1",
        "Comparison to Sub #6",
        "Amended Excerpts 2/12/2026 10:02:409",
        "Amended Excerpts 3/3/2026 19:03:217",
        "Amended Excerpts 1/28/2026 10:01:41",  # a two-digit tail is real too
        # Case-insensitive, and an open-ended series rather than the numbers sampled.
        "comparison to original bill",
        "Comparison to Sub #12",
    ],
)
def test_is_procedural_document_utah_redline_and_excerpt_families(note):
    assert is_procedural_document("Utah", note) is True


@pytest.mark.parametrize(
    "note",
    [
        # The full-text twin of "Amended Excerpts <ts>": shares the prefix and the timestamp,
        # is a real version (736 documents, min 1,193 chars), and must keep its diff.
        "Amended 2/12/2026 10:02:409",
        "Amended 3/3/2026 19:03:217",
        "Substitute #1",
        "Substitute #7",
        # Anchored: not a substring of a longer note, and not a bare prefix.
        "Comparison to Original Bill 2",
        "Comparison to Original",
        "Comparison to Substitute",
        "Comparison to Sub #0",
        "Comparison to Sub #",
        "Amended Excerpts",
        "Amended Excerpts 2/12/2026",
        "Amended Excerpts 2/12/2026 10:02:409 revised",
    ],
)
def test_is_procedural_document_utah_redline_and_excerpt_entries_stay_narrow(note):
    assert is_procedural_document("Utah", note) is False


def test_is_procedural_document_utah_redline_entries_are_utah_only():
    """The table is per jurisdiction: another state's "Comparison to ..." note is not excluded
    by Utah's evidence."""
    assert is_procedural_document("Virginia", "Comparison to Original Bill") is False
    assert is_procedural_document("Michigan", "Comparison to Sub #1") is False


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


# --- OPEN-367: Alabama's committee/legislator "Amendment" family -------------------------


@pytest.mark.parametrize(
    "note",
    [
        # Every one of the 25 distinct Alabama notes ending in "Amendment" / "Amendment Offered"
        # in the 2026-10-15 OPEN-360 audit (none has trailing whitespace; every one has a prefix).
        "Agriculture, Conservation, and Forestry 1st Amendment",
        "Agriculture, Conservation, and Forestry 1st Amendment Offered",
        "Chesteen 1st Amendment Offered",
        "County and Municipal Government 1st Amendment",
        "Finance and Taxation Education 1st Amendment",
        "Finance and Taxation Education 2nd Amendment",
        "Finance and Taxation General Fund 1st Amendment",
        "Fiscal Responsibility and Economic Development 1st Amendment",
        "Fiscal Responsibility and Economic Development 2nd Amendment",
        "Harrison 1st Amendment Offered",
        "Judiciary 1st Amendment",
        "Rehm 1st Amendment Offered",
        "Reported Out Committee House of Origin- Committee Amendment",
        "Reported Out Committee House of Origin-- Committee 1st Amendment",
        "Reported Out Committee House of Origin-- Committee 2nd Amendment",
        "Reported Out Committee House of Origin-- Committee Amendment",
        "Rigsby 1st Amendment Offered",
        "Robbins 1st Amendment Offered",
        "Robbins 2nd Amendment Offered",
        "State Governmental Affairs 1st Amendment",
        "State Governmental Affairs 2nd Amendment",
        "Transportation and Energy 1st Amendment",
        "Transportation, Utilities and Infrastructure 1st Amendment",
        "Transportation, Utilities and Infrastructure 2nd Amendment",
        "Ways and Means Education 1st Amendment",
        "judiciary 1st amendment",  # case-insensitive
    ],
)
def test_is_procedural_document_alabama_amendment_family(note):
    assert is_procedural_document("Alabama", note) is True


@pytest.mark.parametrize(
    "note",
    [
        # The adjacent family that must stay in: real full replacement text (21 documents,
        # 1,929 to 183,379 characters), same committee-name prefix, never ends in "Amendment".
        "Judiciary 1st Substitute",
        "Finance and Taxation General Fund 1st Substitute Offered",
        "Economic Development and Tourism  Engrossed Substitute Offered",
        # Alabama's real full-text stages.
        "Introduced",
        "Engrossed",
        "Enrolled",
        "Reported Out of Committee House of Origin",
        "Reported Out of Committee Second House",
        "Senate Concurs in Conference Committee Report",
        # Not a note shape that exists, and the pattern must need a prefix.
        "Amendment",
    ],
)
def test_is_procedural_document_alabama_does_not_exclude_real_text(note):
    assert is_procedural_document("Alabama", note) is False


def test_is_procedural_document_alabama_entry_is_alabama_only():
    """The pattern rests on Alabama's evidence; another state's notes are not judged by it."""
    assert is_procedural_document("Michigan", "Judiciary 1st Amendment") is False
    assert is_procedural_document("Arizona", "Judiciary 1st Amendment") is False


# --- OPEN-525: Alabama's floor-motion papers (amendments and substitutes) ----------------------

# One real note of each of the nine shapes seen on the Mac replica (2026-11-10, all 453 documents):
# outcome Adopted or Lost, Roll Call or Voice Vote, with and without a member's name, and the one
# note written "Adopt-" with no space before the dash.
ALABAMA_FLOOR_MOTION_NOTES = [
    "Motion to Adopt - Adopted Roll Call 1001",
    "Motion to Adopt - Adopted Voice Vote",
    "Motion to Adopt - Lost Roll Call 3",
    "Albritton motion to Adopt - Adopted Roll Call 1125",
    "Waggoner motion to Adopt - Adopted Voice Vote",
    "Coleman-Madison motion to Adopt - Lost Roll Call 207",
    "Albritton motion to Adopt- Adopted Roll Call 982",
    "Brown motion to Table - Adopted Roll Call 1239",
    "Albritton motion to Table - Adopted Voice Vote",
]


@pytest.mark.parametrize("note", ALABAMA_FLOOR_MOTION_NOTES)
def test_alabama_floor_motion_paper_is_not_a_bill_version(note):
    # The decision covers both kinds: the note is the same for an amendment ("Replace line 209 on
    # page 8 with the following: ...") and a substitute (a full "A BILL TO BE ENTITLED AN ACT ...").
    assert is_procedural_document("Alabama", note) is True
    assert is_bill_version("Alabama", note) is False


@pytest.mark.parametrize("note", ALABAMA_FLOOR_MOTION_NOTES)
def test_alabama_floor_motion_paper_ranks_as_an_amendment_paper(note):
    # Same stage as Alabama's "<Committee> 1st Amendment" / "... 1st Substitute"; before OPEN-525
    # every one of these was STAGE_UNKNOWN (453 of Alabama's 2,922 versions).
    assert note_stage(note)[0] == STAGE_AMENDMENT


@pytest.mark.parametrize(
    ("note", "expected_stage"),
    [
        # Every real Alabama version name on the Mac replica (2026-11-10) that is not a motion
        # paper or an "... Amendment" paper: unchanged stage, still a bill version.
        ("Introduced", STAGE_INTRODUCED),
        ("Engrossed", STAGE_CHAMBER_PASSAGE),
        ("Enrolled", STAGE_FINAL_PASSAGE),
        ("Reported Out of Committee House of Origin", STAGE_CHAMBER_PASSAGE),
        ("Reported Out of Committee Second House", STAGE_CHAMBER_PASSAGE),
        ("Senate Concurs in Conference Committee Report", STAGE_CHAMBER_PASSAGE),
        ("Judiciary 1st Substitute", STAGE_AMENDMENT),
        ("Finance and Taxation General Fund 1st Substitute Offered", STAGE_AMENDMENT),
        ("Economic Development and Tourism  Engrossed Substitute Offered", STAGE_AMENDMENT),
    ],
)
def test_alabama_real_versions_are_unchanged(note, expected_stage):
    assert is_bill_version("Alabama", note) is True
    assert note_stage(note)[0] == expected_stage


@pytest.mark.parametrize(
    "note",
    [
        # Near misses: not the shape, so not touched (stay versions, stay unknown).
        "Motion to Adopt",
        "Orr motion to Reconsider - Adopted Roll Call 12",
        "Motion to Adopt - Adopted",
        "Motion to Adopt - Adopted Roll Call 0",
    ],
)
def test_alabama_floor_motion_rule_needs_the_whole_shape(note):
    assert is_procedural_document("Alabama", note) is False
    assert note_stage(note)[0] == STAGE_UNKNOWN


def test_alabama_floor_motion_rule_is_alabama_only():
    """The not-a-version rule is Alabama's: another state's note of this shape stays a version. The
    stage is not per state (note_stage() takes no state, like Nebraska's "<name> AM<N>" shape,
    OPEN-482/486), so such a note would rank as an amendment there; no other state has one today
    (every stored note in all 13 jurisdictions on the Mac replica checked, 2026-11-10)."""
    note = "Orr motion to Table - Adopted Voice Vote"
    for state in ("Michigan", "Virginia", "Utah", "Louisiana", "Nebraska", "Arizona"):
        assert is_bill_version(state, note) is True
    assert note_stage(note)[0] == STAGE_AMENDMENT


# --- OPEN-366: North Carolina's "Edition N" / "Ratified" / "SL <year>-<N>" and stamp pages ---


@pytest.mark.parametrize(
    ("note", "expected_stage"),
    [
        ("Filed", STAGE_INTRODUCED),
        ("Edition 1", STAGE_AMENDMENT),
        ("Edition 8", STAGE_AMENDMENT),
        ("Ratified", STAGE_FINAL_PASSAGE),
        ("SL 2025-14", STAGE_ENACTED),
        ("SL 2026-9", STAGE_ENACTED),
    ],
)
def test_note_stage_north_carolina_vocabulary(note, expected_stage):
    assert note_stage(note)[0] == expected_stage


def test_north_carolina_versions_order_filed_editions_ratified_session_law():
    in_true_order = [
        "Filed",
        "Edition 1",
        "Edition 2",
        "Edition 9",
        "Edition 10",  # numeric, not alphabetical: 10 follows 9
        "Ratified",
        "SL 2025-14",
    ]
    keys = [version_sort_key(n, "") for n in in_true_order]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)  # no ties, so the lineage order is never a guess


@pytest.mark.parametrize(
    "note",
    [
        # Shapes that look close but are not North Carolina's, or are real notes of the other
        # states: they must keep their existing classification (unknown here, since no other
        # rule claims them).
        "Edition",
        "Edition 0",
        "Edition 2 Revised",
        "Resolution 2025-4",  # NC's own single-version resolutions stay unknown
        "Adopted",
        "Ratified Bill",
        "SL 2025",
    ],
)
def test_note_stage_north_carolina_rules_do_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


@pytest.mark.parametrize(
    "note",
    [
        # Real notes from the OPEN-359 audit: the numbered and un-numbered stamp pages.
        "A1: Amendment",
        "A7: Amendment",
        "A49: Amendment",
        # Two real un-numbered ones exist (whole text "A" and "A A"), both with a stored diff.
        "A: Amendment",
        # OPEN-594: the amendment text itself, coded by the clerk's amendment number. Real notes
        # from the production replica (2026-11-17), every code shape seen there.
        "A1: ABR-9-V-1",
        "A1: ABB-54-V-2",
        "A1: ABKE-21-V-5",
        "A10: AMT-24-V-5",
        "A8: ACE-16-V-4",
        "A1: ABAXFR-56-V-3",
        "A1: s391-asu-15-v-3",
    ],
)
def test_is_procedural_document_north_carolina_amendment_papers(note):
    assert is_procedural_document("North Carolina", note) is True
    # the value the archiver stores, and api-v3 serves as is_bill_version / is_full_text
    assert is_bill_version("North Carolina", note) is False


@pytest.mark.parametrize(
    "note",
    [
        # Real full-text versions: every shape of North Carolina's 5,947 other notes (2026-11-17).
        "Filed",
        "Edition 1",
        "Edition 2",
        ("Ratified"),
        "SL 2025-14",
        "Adopted",
        "Resolution 2025-12",
        # Amendments are numbered from 1; a zero is not an NC shape.
        "A0: Amendment",
        # Only a NUMBERED paper takes any suffix; the un-numbered form is the stamp page alone.
        "A: Filed",
        "A: Edition 2",
    ],
)
def test_is_procedural_document_north_carolina_does_not_exclude_real_text(note):
    assert is_procedural_document("North Carolina", note) is False
    assert is_bill_version("North Carolina", note) is True


def test_north_carolina_amendment_papers_are_only_north_carolinas():
    # the pattern is keyed on the state: another state's "A1: ..." note is untouched
    assert is_procedural_document("Georgia", "A1: ABR-9-V-1") is False


def test_is_procedural_document_north_carolina_entry_is_north_carolina_only():
    # Michigan and Utah have no entry that matches this shape. (Alabama's and Virginia's
    # end-anchored "...amendment" patterns match "A1: Amendment" by shape, which is harmless for
    # their own notes and is why this test names other states.)
    assert is_procedural_document("Michigan", "A1: Amendment") is False
    assert is_procedural_document("Utah", "A1: Amendment") is False


# --- OPEN-373: Washington's introduced resolutions and memorials ------------------------------


@pytest.mark.parametrize(
    "note",
    ["Resolution", "Joint Memorial", "Joint Resolution", "Concurrent Resolution"],
)
def test_note_stage_washington_introduced_resolutions_and_memorials(note):
    assert note_stage(note) == (STAGE_INTRODUCED, 0.0)


@pytest.mark.parametrize(
    "in_true_order",
    [
        ["Resolution", "Substitute Resolution", "Engrossed Resolution"],
        [
            "Joint Memorial",
            "Substitute Joint Memorial",
            "Engrossed Substitute Joint Memorial",
        ],
        ["Joint Resolution", "Substitute Joint Resolution"],
        ["Concurrent Resolution", "Substitute Concurrent Resolution"],
    ],
)
def test_washington_resolution_families_order_introduced_substitute_engrossed(
    in_true_order,
):
    keys = [version_sort_key(n, "") for n in in_true_order]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)  # no ties, so the lineage order is never a guess
    assert note_stage(in_true_order[0])[0] == STAGE_INTRODUCED
    assert all(note_stage(n)[0] != STAGE_UNKNOWN for n in in_true_order)


@pytest.mark.parametrize(
    "note",
    [
        # Exact notes only: near-misses and other kinds of resolution keep their classification.
        "Resolutions",
        "Joint Resolution 1",
        "Senate Resolution",
        "House Resolution",
        "Resolution of the Senate",
        "Memorial",
        "Joint",
    ],
)
def test_the_washington_resolution_rule_does_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


def test_michigans_resolution_notes_are_untouched():
    """Michigan has real resolution notes of its own; none is one of the four exact Washington
    notes, so each keeps the stage it had."""
    assert note_stage("House Introduced Resolution")[0] == STAGE_INTRODUCED
    assert note_stage("Senate Introduced Resolution")[0] == STAGE_INTRODUCED
    # OPEN-720 gives the adopted texts their own stage (chamber passage), not Washington's introduced one.
    assert note_stage("House Adopted Resolution")[0] == STAGE_CHAMBER_PASSAGE
    assert note_stage("Senate Adopted Resolution")[0] == STAGE_CHAMBER_PASSAGE


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


# --- OPEN-379: Arizona's failed floor amendments -------------------------------------------

_AZ_FAILED_NOTES = [
    # All 11 distinct Arizona notes containing "fail" on the production replica (2026-10-16).
    "SENATE - Ortiz flr amend (ref Resolution) failed",
    "SENATE - Sundareshan flr amend (ref Bill) failed",
    "SENATE - Ortiz flr amend (ref Bill) failed",
    "SENATE - Epstein flr amend (ref Bill) failed",
    "SENATE - Hoffman flr  amend (ref ATT) failed",
    "SENATE - Miranda flr amend (ref RAGE) failed",
    "SENATE - Miranda flr amend (ref Bill) failed",
    "SENATE - Kavanagh flr amend (ref RAGE) failed",
    "SENATE - Sundareshan flr amend (ref FIN) failed",
    "SENATE - Diaz flr amend (ref Bill) failed",
    "SENATE - Sears flr amend (ref Bill) failed",
]


@pytest.mark.parametrize("note", _AZ_FAILED_NOTES)
def test_is_procedural_document_arizona_failed_amendments(note):
    assert is_procedural_document("Arizona", note) is True
    assert is_procedural_document("Arizona", note.upper()) is True


@pytest.mark.parametrize(
    "note",
    [
        "SENATE - Shamp flr amend (ref Bill) adopted",
        "HOUSE - Floor Amend to Engrossed Bill - Nguyen - passed",
        "HOUSE - Appropriations",
        "SENATE - Judiciary and Elections",
        "SENATE - ",
        "SENATE -",
        "Introduced Version",
        "House Engrossed Version",
        "Senate Engrossed Version (02/23/2026)",
        "Conference Engrossed Version",
        "Conference Committee",
        "Chaptered Version",
        # Only a note that ENDS in "failed" and is a chamber action document.
        "SENATE - failed amendments study committee",
        "Failed",
        "Introduced Version failed",
    ],
)
def test_is_procedural_document_arizona_keeps_everything_else(note):
    assert is_procedural_document("Arizona", note) is False


# --- OPEN-428: the " (<document id>)" suffix the scraper may add to a repeated note ----------


@pytest.mark.parametrize("note", _AZ_FAILED_NOTES)
def test_is_procedural_document_arizona_failed_amendment_with_document_id_suffix(note):
    """Upstream #5807 appends the document id to the second and later copies of a note
    repeated inside one bill, e.g. a sponsor's second failed amendment of the same text."""
    assert is_procedural_document("Arizona", note + " (541432)") is True
    assert is_procedural_document("Arizona", note.upper() + " (541432)") is True


@pytest.mark.parametrize(
    "note",
    [
        "SENATE - Shamp flr amend (ref Bill) adopted (541432)",
        "HOUSE - Appropriations - Strike Everything (539012)",
        "SENATE - Appropriations, Transportation and Technology (541432)",
        "Introduced Version (541432)",
        "Senate Engrossed Version (02/26/2026) (541432)",
        "Introduced Version failed (541432)",
        # the suffix must be a bare number in parentheses, after a space, at the very end
        "SENATE - Ortiz flr amend (ref Bill) failed (ref 12)",
        "SENATE - Ortiz flr amend (ref Bill) failed (12) extra",
        "SENATE - Ortiz flr amend (ref Bill) failed ()",
        "SENATE - Ortiz flr amend (ref Bill) failed(541432)",
        "SENATE - Ortiz flr amend (ref Bill) failed (02/26/2026)",
    ],
)
def test_is_procedural_document_arizona_suffix_does_not_widen_the_match(note):
    assert is_procedural_document("Arizona", note) is False


def test_is_procedural_document_arizona_suffix_is_arizona_only():
    note = "SENATE - Ortiz flr amend (ref Bill) failed (541432)"
    for state in ("Utah", "Virginia", "Alabama", "North Carolina", "Michigan"):
        assert is_procedural_document(state, note) is False


@pytest.mark.parametrize(
    "note",
    [
        "Introduced Version",
        "House Engrossed Version",
        "Senate Engrossed Version (02/26/2026)",
        "HOUSE - Appropriations - Strike Everything",
        "SENATE - Ortiz flr amend (ref Bill) failed",
        "SENATE - Shamp flr amend (ref Bill) adopted",
    ],
)
def test_document_id_suffix_moves_neither_stage_nor_sort_key(note):
    """Measured on 213 real suffixed notes (2026-10-22 live sample): the suffix never changed
    note_stage() or version_sort_key(); pinned here so a later stage rule can't start to."""
    suffixed = note + " (541432)"
    assert note_stage(suffixed) == note_stage(note)
    assert version_sort_key(suffixed, "") == version_sort_key(note, "")


def test_is_procedural_document_arizona_failed_entry_is_arizona_only():
    note = "SENATE - Ortiz flr amend (ref Bill) failed"
    for state in ("Utah", "Virginia", "Alabama", "North Carolina", "Michigan"):
        assert is_procedural_document(state, note) is False


# --- OPEN-482: Pennsylvania, Nebraska and Louisiana version names ---


@pytest.mark.parametrize(
    ("note", "expected_stage"),
    [
        # Real notes from the production replica (2026-11-03).
        ("Printer's No. PN1422", STAGE_AMENDMENT),
        ("Printer's No. PN0130", STAGE_AMENDMENT),
        ("Slip Law", STAGE_ENACTED),
        ("Final Reading", STAGE_FINAL_PASSAGE),
        ("von Gillern AM1020", STAGE_AMENDMENT),
        ("McKinney FA134", STAGE_AMENDMENT),
        ("Spivey AM1738 Spivey FA924", STAGE_AMENDMENT),
        ("Government, Military and Veterans Affairs AM1008 Conrad AM1147", STAGE_AMENDMENT),
        ("HB519 Act 303", STAGE_ENACTED),
        ("SB40 Act", STAGE_ENACTED),
        ("SCR65 Act 12", STAGE_ENACTED),
    ],
)
def test_note_stage_pennsylvania_nebraska_louisiana_vocabulary(note, expected_stage):
    assert note_stage(note)[0] == expected_stage


def test_pennsylvania_printings_order_by_printers_number_not_alphabetically():
    # PN9 before PN10 and PN130: numeric, so the lineage is the real order of the printings.
    notes = ["Printer's No. PN9", "Printer's No. PN10", "Printer's No. PN130"]
    keys = [version_sort_key(n, "") for n in notes]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_nebraska_versions_order_introduced_amendments_final_reading_slip_law():
    in_true_order = [
        "Introduced",
        "von Gillern AM1020",
        "Enrollment and Review ER68",
        "Final Reading",
        "Slip Law",
    ]
    keys = [version_sort_key(n, "") for n in in_true_order]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes, or notes of these states that are not bill text: they stay unknown.
        "Printer's No.",
        "Printer's No. PN",
        "Printer's No. PN12 Revised",
        "Slip Law Copy",
        "Final Reading Draft",
        "AM1020",  # no introducer or committee name: not the observed shape
        "Senate Floor Bureau Note, #3797, Miller, G., Adopted",  # LA: not bill text
        "HB519 Acts",
        "Act 303",
    ],
)
def test_note_stage_pennsylvania_nebraska_louisiana_rules_do_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


@pytest.mark.parametrize(
    "note",
    [
        # Notes that already have a stage keep it: the new rules run last and only see notes no
        # earlier rule claims (so a state with a real rule is never pulled into these).
        "Introduced",
        "Enrolled",
        "Edition 2",
        "SL 2025-14",
        "Substitute (S-1)",
        "House Committee Amendment, #3001, CL&P, Draft",
        "Enrollment and Review ER68",
    ],
)
def test_pennsylvania_nebraska_louisiana_rules_leave_existing_stages_alone(note):
    assert note_stage(note)[0] != STAGE_UNKNOWN
    assert note_stage(note) == _stage_before_open_482(note)


def _stage_before_open_482(note):
    # The classification these notes had before the OPEN-482 block, restated per note so the
    # test fails if the block ever claims one of them.
    return {
        "Introduced": (STAGE_INTRODUCED, 0.0),
        "Enrolled": (STAGE_FINAL_PASSAGE, 0.0),
        "Edition 2": (STAGE_AMENDMENT, 2.0),
        "SL 2025-14": (STAGE_ENACTED, 0.0),
        "Substitute (S-1)": (STAGE_AMENDMENT, 1.0),
        "House Committee Amendment, #3001, CL&P, Draft": (STAGE_AMENDMENT, 3001.0),
        "Enrollment and Review ER68": (STAGE_FINAL_PASSAGE, 0.0),
    }[note]


def test_note_stage_nebraska_amendment_ordinal_is_the_last_amendment_number():
    # Exact tuples, so an ordering test cannot pass on the wrong extracted number. Real Nebraska
    # notes list every amendment in the document; the last number is the one that ranks it.
    assert note_stage("von Gillern AM1020") == (STAGE_AMENDMENT, 1020.0)
    assert note_stage("McKinney FA134") == (STAGE_AMENDMENT, 134.0)
    assert note_stage("Spivey AM1738 Spivey FA924") == (STAGE_AMENDMENT, 924.0)
    assert note_stage("Executive Board AM2028 Conrad AM2266 Conrad AM2267") == (
        STAGE_AMENDMENT,
        2267.0,
    )
    assert note_stage("Printer's No. PN0130") == (STAGE_AMENDMENT, 130.0)
    assert note_stage("Slip Law") == (STAGE_ENACTED, 0.0)
    assert note_stage("HB519 Act 303") == (STAGE_ENACTED, 0.0)
    assert note_stage("Final Reading") == (STAGE_FINAL_PASSAGE, 0.5)


# --- OPEN-486: Nebraska's amendment papers are not versions of the bill ----------------------


@pytest.mark.parametrize(
    "note",
    [
        # Real Nebraska notes read on the production replica (2026-11-04): committee, introducer,
        # floor and enrollment-and-review amendment papers.
        "Judiciary AM12",
        "Government, Military and Veterans Affairs AM1130",
        "Banking, Commerce and Insurance AM345",
        "von Gillern AM2",
        "Cavanaugh, J. AM88",
        "Bosn FA3",
        "Enrollment and Review ER57",
        "Enrollment and Review ER5",
    ],
)
def test_is_procedural_document_nebraska_amendment_papers(note):
    assert is_procedural_document("Nebraska", note) is True
    assert is_procedural_document("Nebraska", note.upper()) is True


@pytest.mark.parametrize(
    "note",
    [
        # The real versions keep their place in the lineage.
        "Introduced",
        "Final Reading",
        "Slip Law",
        # Anchored on both ends and needs a number of 1 or more.
        "AM12",
        "Judiciary AM",
        "Judiciary AM0",
        "Judiciary AM12 revised",
        "Judiciary AMENDED12",
        "Enrollment and Review",
        "Enrollment and Review ER",
        "Enrollment and Review ER0",
        "Enrollment and Review ER5 revised",
        "Enrollment ER5",
    ],
)
def test_is_procedural_document_nebraska_real_versions_and_near_misses_stay_in(note):
    assert is_procedural_document("Nebraska", note) is False


def test_is_procedural_document_nebraska_entry_is_nebraska_only():
    """Louisiana, Pennsylvania and every other state keep these shapes in the lineage."""
    for state in ("Louisiana", "Pennsylvania", "Michigan", "Massachusetts"):
        assert is_procedural_document(state, "Judiciary AM12") is False
        assert is_procedural_document(state, "Bosn FA3") is False
        assert is_procedural_document(state, "Enrollment and Review ER5") is False


# --- OPEN-491: Louisiana's amendment papers are not versions of the bill ---------------------


@pytest.mark.parametrize(
    "note",
    [
        # Real Louisiana notes read on the production replica (2026-11-04), one per family and
        # closing word.
        "House Committee Amendment, #4774, INS, Adopted",
        "House Committee Amendment, #2656, ACRJ, Draft",
        "House Committee Amendment, #1, H&W, Proposed",
        "Senate Committee Amendment, #12, JUD, Draft",
        "House Floor Amendment, #3321, Miguez, Adopted",
        "House Floor Amendment, #77, Marcelle, Rejected",
        "Senate Floor Amendment, #512, Owen, Robert, Withdrawn",
        "Senate Floor Amendment, #9, Fesi, Proposed",
        "House Floor Legislative Bureau Amendment, #4, Smith, Adopted",
        "Senate Floor Legislative Bureau Amendment, #12, Smith, Adopted",
        "House Legislative Bureau Amendment, #7, Smith, Proposed",
        "Senate Legislative Bureau Amendment, #7, Smith, Adopted",
        "Senate Floor Bureau Note, #1238, Fesi, Adopted",
        "Senate Floor Bureau Note, #2717, Owen, Robert, Adopted",
    ],
)
def test_is_procedural_document_louisiana_amendment_papers(note):
    assert is_procedural_document("Louisiana", note) is True
    assert is_procedural_document("Louisiana", note.upper()) is True


@pytest.mark.parametrize(
    "note",
    [
        # The real versions keep their place in the lineage.
        "HB374 Original",
        "HB374 Engrossed",
        "HB374 Reengrossed",
        "HB374 Re-Reengrossed",
        "HB374 Enrolled",
        "HB374 Act 12",
        "SB12 Act",
        "HR167 Enrolled",
        "HCR12 Original",
        # Anchored on both ends, needs a number of 1 or more and a known closing word.
        "House Committee Amendment, #4774, INS",
        "House Committee Amendment, #0, INS, Adopted",
        "House Committee Amendment, #4774, INS, Adopted and amended",
        "House Committee Amendment, #4774, INS, Withdrawn",
        "Senate Floor Amendment, #512, Owen, Draft",
        "Senate Floor Bureau Note, #1238, Fesi, Rejected",
        "House Floor Bureau Note, #1238, Fesi, Adopted",
        "Committee Amendment, #4774, INS, Adopted",
        "Amendment, #4774, INS, Adopted",
        "Senate Floor Amendment",
    ],
)
def test_is_procedural_document_louisiana_real_versions_and_near_misses_stay_in(note):
    assert is_procedural_document("Louisiana", note) is False


def test_is_procedural_document_louisiana_entry_is_louisiana_only():
    """Nebraska, Pennsylvania and every other state keep these shapes in the lineage."""
    committee = "House Committee Amendment, #4774, INS, Adopted"
    bureau_note = "Senate Floor Bureau Note, #1238, Fesi, Adopted"
    for state in ("Nebraska", "Pennsylvania", "Michigan", "Massachusetts"):
        assert is_procedural_document(state, committee) is False
        assert is_procedural_document(state, bureau_note) is False


def test_note_stage_louisiana_amendment_papers_keep_their_stage():
    """The exclusion is separate from the stage: nothing about note_stage() changed."""
    committee = "House Committee Amendment, #4774, INS, Adopted"
    bureau_amendment = "Senate Floor Legislative Bureau Amendment, #12, Smith, Adopted"
    assert note_stage(committee)[0] == STAGE_AMENDMENT
    assert note_stage(bureau_amendment)[0] == STAGE_AMENDMENT
    assert note_stage("Senate Floor Bureau Note, #1238, Fesi, Adopted")[0] == STAGE_UNKNOWN


# --- OPEN-511: Louisiana's Conference Committee Reports are not versions of the bill ----------


@pytest.mark.parametrize(
    "note",
    [
        # Real Louisiana notes read on the Mac replica (2026-11-08), one per status pair seen.
        "Conference Committee Report, #4594, House Adopted, Senate Adopted",
        "Conference Committee Report, #5430, House Adopted, Senate Adopted",
        "Conference Committee Report, #6237, House Proposed, Senate Adopted",
        "Conference Committee Report, #3422, House Proposed, Senate Proposed",
        "Conference Committee Report, #4371, House Proposed, Senate Rejected",
        "Conference Committee Report, #5523, House Withdrawn, Senate Proposed",
        "Conference Committee Report, #6160, House Adopted, Senate Proposed",
    ],
)
def test_is_procedural_document_louisiana_conference_committee_reports(note):
    assert is_procedural_document("Louisiana", note) is True
    assert is_procedural_document("Louisiana", note.upper()) is True


@pytest.mark.parametrize(
    "note",
    [
        # Anchored on both ends, needs a number of 1 or more and a known status for each chamber.
        "Conference Committee Report",
        "Conference Committee Report, #4594",
        "Conference Committee Report, #0, House Adopted, Senate Adopted",
        "Conference Committee Report, #4594, House Adopted",
        "Conference Committee Report, #4594, House Adopted, Senate Draft",
        "Conference Committee Report, #4594, Senate Adopted, House Adopted",
        "Conference Committee Report, #4594, House Adopted, Senate Adopted, Enrolled",
        "Senate Concurs in Conference Committee Report",
    ],
)
def test_is_procedural_document_louisiana_conference_near_misses_stay_in(note):
    assert is_procedural_document("Louisiana", note) is False


def test_is_procedural_document_louisiana_conference_entry_is_louisiana_only():
    """Every other state keeps this shape; Michigan's "Conference Report" (a full bill text) stays in."""
    report = "Conference Committee Report, #4594, House Adopted, Senate Adopted"
    for state in ("Michigan", "Virginia", "Nebraska", "Pennsylvania", "Massachusetts"):
        assert is_procedural_document(state, report) is False
    assert is_procedural_document("Michigan", "Conference Report") is False


def test_note_stage_louisiana_conference_report_keeps_its_stage():
    """The exclusion is separate from the stage: note_stage() still places the report."""
    report = "Conference Committee Report, #4594, House Adopted, Senate Adopted"
    assert note_stage(report)[0] == STAGE_CHAMBER_PASSAGE


# --- OPEN-602: Georgia version names, not-a-version papers and document-number order ---

GA_DOC = "https://www.legis.ga.gov/api/legislation/document/{}/{}"


@pytest.mark.parametrize(
    ("note", "expected_stage"),
    [
        # Real Georgia names, one per family: the 2026 special session (govbot copy) and 2025-26 bills
        # from Georgia's web service (2026-11-16). Every one of these had no stage before OPEN-602.
        ("LC 47 4374/a", STAGE_INTRODUCED),
        ("LC 50 1032-EC/a", STAGE_INTRODUCED),
        ("LC 47 4244/a ", STAGE_INTRODUCED),  # HB 1583, trailing space as Georgia stores it
        ("Local Ad", STAGE_INTRODUCED),
        ("SB 4EX Local Ad", STAGE_INTRODUCED),
        ("LC 50 1106S/hs", STAGE_AMENDMENT),
        ("LC 50 1106S/hcs", STAGE_AMENDMENT),
        ("LC 49 2362S/hss", STAGE_AMENDMENT),
        ("LC 55 0644S/hrcs", STAGE_AMENDMENT),
        ("LC 49 2300S/rcs", STAGE_AMENDMENT),
        ("LC 49 2300S/RCS", STAGE_AMENDMENT),
        ("LC 47  4409S/hs", STAGE_AMENDMENT),  # double space, as stored
        ("Sen Ctee Sub LC 50 1215-ECS", STAGE_AMENDMENT),
        ("Sen Ctee Sub LC 62 0177S", STAGE_AMENDMENT),
        ("Sen Ctee sub LC 49 2847S", STAGE_AMENDMENT),
        ("Committee Sub LC 49 2233-ECS", STAGE_AMENDMENT),
        ("Committe sub LC 33 9771S", STAGE_AMENDMENT),
        ("SCSFA to HB 61", STAGE_CHAMBER_PASSAGE),
        ("HB 1193 SCSFA", STAGE_CHAMBER_PASSAGE),
        ("SFA to HB 308", STAGE_CHAMBER_PASSAGE),
        ("Adopted Senate", STAGE_CHAMBER_PASSAGE),
        ("Read and adopted LC 112 4263", STAGE_CHAMBER_PASSAGE),
        ("HB 223/AP*", STAGE_FINAL_PASSAGE),
        ("SB 3EX/AP*", STAGE_FINAL_PASSAGE),
        ("SR 1EX/AP*", STAGE_FINAL_PASSAGE),
        # Already had a stage before OPEN-602; unchanged.
        ("As introduced LC 47 4392", STAGE_INTRODUCED),
        ("As Introduced LC 62 0074-EC", STAGE_INTRODUCED),
        ("As Passed Senate", STAGE_CHAMBER_PASSAGE),
        ("Floor Amend 1 AM 49 0227", STAGE_AMENDMENT),
        ("Sen Floor Amend 1 AM 47 0224", STAGE_AMENDMENT),
    ],
)
def test_note_stage_georgia_vocabulary(note, expected_stage):
    assert note_stage(note)[0] == expected_stage


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes: they stay unknown, so the rules do not widen beyond what Georgia publishes.
        "LC 47 4374/b",
        "LC 47 4374",
        "LC 50 1106/hs",  # a House substitute's drafting number always ends in S
        "LC 50 1106S/hx",
        "Local Advertisement",
        "Sub LC 49 2233-ECS",
        "SCSFA",
        "Adopted",
        "HB 223/APX",
        "HB 223 AP",
        # Georgia papers that are not bill text and keep no stage (they are not versions either).
        "Fiscal Note",
        "Local Government Fiscal Note",
        "Auditor's Certification LC 56 0399S",
    ],
)
def test_note_stage_georgia_rules_do_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


@pytest.mark.parametrize(
    "note",
    [
        "Local Ad",
        "SB 4EX Local Ad",
        "SB 234 Local Ad",
        "Floor Amend 1 AM 47 0219",
        "Floor Amend 2",
        "Floor Amend 2A",
        "Floor Amendment AM 64 0009",
        "Sen Floor Amend 1 AM 47 0224",
        "Sen Floor Amend 1A AM 47 0225",
        "Sen Floor Amend 1C",
        "Sen Floor amend 1 AM 49 0279",
        "Fiscal Note",
        "Local Government Fiscal Note",
        "Auditor's Certification LC 56 0399S",
        "LC 56 0244 Auditor's Certification",
    ],
)
def test_is_bill_version_georgia_papers_are_not_versions(note):
    assert is_procedural_document("Georgia", note) is True
    assert is_bill_version("Georgia", note) is False


@pytest.mark.parametrize(
    "note",
    [
        "LC 47 4374/a",
        "As introduced LC 47 4392",
        "LC 50 1106S/hs",
        "LC 50 1106S/hcs",
        "LC 47 4417S/hss",
        "Sen Ctee Sub LC 62 0177S",
        "Committee Sub LC 49 2233-ECS",
        "As Passed Senate",
        "SCSFA to HB 61",
        "Read and adopted LC 112 4263",
        "HB 223/AP*",
        # near misses stay versions
        "Local Ad Notice",
        "Floor Amend 1 to the Senate substitute",
    ],
)
def test_is_bill_version_georgia_real_versions_stay_in(note):
    assert is_bill_version("Georgia", note) is True


def test_is_bill_version_georgia_entry_is_georgia_only():
    assert is_bill_version("Alabama", "Local Ad") is True
    assert is_bill_version("Virginia", "Floor Amend 1 AM 47 0219") is True


def test_document_number_reads_georgia_links():
    assert document_number([GA_DOC.format("20252026", 236896)]) == 236896
    assert document_number([GA_DOC.format("2026EX", 249298)]) == 249298
    assert document_number(["http://www.legis.ga.gov/Legislation/2021EX/202712.pdf"]) == 202712
    assert document_number(["https://www.legis.ga.gov/legislation/69281"]) == 0
    assert document_number(["https://example.com/api/legislation/document/20252026/1"]) == 0


def _ga_order(versions, identifier):
    shuffled = list(reversed(versions))
    return [
        note
        for note, _ in sorted(
            shuffled,
            key=lambda v: version_sort_key(v[0], "", [v[1]], origin_chamber(identifier), "Georgia"),
        )
    ]


@pytest.mark.parametrize(
    ("identifier", "in_true_order"),
    [
        # Real names and document numbers from Georgia's web service (2025-26), in the order of each
        # bill's status history. SB 68: the House substitute comes after "As Passed Senate", so a
        # stage-first order would put it before (amendment < chamber passage).
        (
            "SB 68",
            [
                ("As Introduced LC 62 0074-EC", GA_DOC.format("20252026", 230894)),
                ("Committee Sub LC 49 2233-ECS", GA_DOC.format("20252026", 231978)),
                ("As Passed Senate", GA_DOC.format("20252026", 233525)),
                ("LC 49 2362S/hss", GA_DOC.format("20252026", 236457)),
                ("SB 68/AP*", GA_DOC.format("20252026", 236944)),
            ],
        ),
        # HB 223: the House's two substitute printings (as reported, as passed), then the Senate's.
        (
            "HB 223",
            [
                ("LC 50 1032-EC/a", GA_DOC.format("20252026", 230994)),
                ("LC 50 1106S/hs", GA_DOC.format("20252026", 232393)),
                ("LC 50 1106S/hcs", GA_DOC.format("20252026", 232657)),
                ("Sen Ctee Sub LC 50 1215-ECS", GA_DOC.format("20252026", 235813)),
                ("HB 223/AP*", GA_DOC.format("20252026", 236896)),
            ],
        ),
        # HB 61: two Senate committee substitutes (2025, then 2026) that nothing in the names orders.
        (
            "HB 61",
            [
                ("LC 39 4465/a", GA_DOC.format("20252026", 229988)),
                ("Sen Ctee Sub LC 57 0278S", GA_DOC.format("20252026", 237050)),
                ("Sen Ctee Sub LC 63 0073S", GA_DOC.format("20252026", 241287)),
                ("SCSFA to HB 61", GA_DOC.format("20252026", 246890)),
            ],
        ),
    ],
)
def test_georgia_versions_order_by_document_number(identifier, in_true_order):
    assert _ga_order(in_true_order, identifier) == [n for n, _ in in_true_order]


def test_georgia_document_order_is_georgia_only():
    # Another state keeps its stage-first key: the same notes and links sort by stage there.
    stage_first = version_sort_key("As Passed Senate", "", [GA_DOC.format("20252026", 233525)])
    house_sub = version_sort_key("LC 49 2362S/hss", "", [GA_DOC.format("20252026", 236457)])
    assert house_sub < stage_first
    assert version_sort_key("Introduced", "", [], None, "Virginia") == version_sort_key("Introduced", "")


def test_georgia_version_without_a_site_number_ranks_first_then_by_stage():
    # The stated fallback (as Alabama's undated versions): no Georgia document number is 0, so it leads,
    # and two such versions fall back to the stage.
    with_number = version_sort_key("LC 50 1032-EC/a", "", [GA_DOC.format("20252026", 230994)], None, "Georgia")
    no_number_ap = version_sort_key("HB 223/AP*", "", ["https://example.com/hb223.pdf"], None, "Georgia")
    no_number_intro = version_sort_key("LC 50 1032-EC/a", "", [], None, "Georgia")
    assert no_number_intro < no_number_ap < with_number


# --- OPEN-635: New Mexico's "Final Version" -----------------------------------------------------

# Every distinct New Mexico version name on the Mac replica (2026-11-18, 1,112 versions), with the
# stage each one has. "Final Version" (129) had none before OPEN-635; the others already classified
# and keep their stage: "introduced version" (812), "Amendments in Context" (84) and the committee
# substitutes "<committee code> substitute".
NM_NOTES = [
    ("introduced version", (STAGE_INTRODUCED, 0.0)),
    ("Final Version", (STAGE_FINAL_PASSAGE, 0.0)),
    ("Amendments in Context", (STAGE_AMENDMENT, 0.0)),
] + [
    (f"{code} substitute", (STAGE_AMENDMENT, 0.0))
    for code in (
        "JC", "HC", "PP", "FC", "JU", "AF", "CT", "PA", "EN", "CE", "RU", "TR", "CP", "EC", "RD",
        "FS", "CO",
    )
]


@pytest.mark.parametrize(("note", "expected"), NM_NOTES)
def test_note_stage_new_mexico_vocabulary(note, expected):
    assert note_stage(note) == expected
    # OPEN-637: "Amendments in Context" is the one New Mexico note that is not a bill version;
    # every other one stays in the lineage.
    assert is_bill_version("New Mexico", note) is (note != "Amendments in Context")


# --- OPEN-637: New Mexico's "Amendments in Context" is not a bill version ------------------------


def test_new_mexico_amendments_in_context_is_not_a_bill_version():
    assert is_procedural_document("New Mexico", "Amendments in Context") is True
    # SB 301's stored text (Mac replica): the page header only, the rest is drawn as shapes.
    header_only = "57TH LEGISLATURE - STATE OF NEW MEXICO - SECOND SESSION, 2026\n"
    assert is_bill_version("New Mexico", "Amendments in Context", "A test bill", header_only) is False


@pytest.mark.parametrize(
    ("jurisdiction_name", "note"),
    [
        # Exact name, exact case, New Mexico only, like Virginia's entries.
        ("New Mexico", "amendments in context"),
        ("New Mexico", "Amendment in Context"),
        ("New Mexico", "Amendments in Context (Draft)"),
        ("Virginia", "Amendments in Context"),
        ("Michigan", "Amendments in Context"),
    ],
)
def test_new_mexico_amendments_in_context_rule_does_not_widen(jurisdiction_name, note):
    assert is_procedural_document(jurisdiction_name, note) is False
    assert is_bill_version(jurisdiction_name, note) is True


def test_new_mexico_final_version_ranks_after_introduced_and_amendments():
    # HB 108 (2026 regular session) has these three versions; the passed text comes last.
    in_true_order = ["introduced version", "Amendments in Context", "Final Version"]
    keys = [version_sort_key(n, "", [], None, "New Mexico") for n in in_true_order]
    assert keys == sorted(keys)
    assert keys[-1] > keys[-2]
    # In the lineage at final passage, not last only because an unknown stage sorts last.
    assert keys[-1][0] == STAGE_FINAL_PASSAGE


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes stay unknown: the rule is New Mexico's exact name, nothing wider.
        "Final",
        "Final Versions",
        "Final Version Draft",
        "Draft Final Version",
        "Version",
    ],
)
def test_note_stage_new_mexico_final_version_rule_does_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


# --- OPEN-720: the text as a chamber agreed to, passed or adopted it (US, Michigan) ----------------

# The ten names that had no stage (Mac replica, 2026-11-21), each from a real bill. No other stored note in any
# jurisdiction changes stage.
OPEN720_NOTES = [
    ("Agreed to Senate", (STAGE_CHAMBER_PASSAGE, 0.0)),  # US SRES 866 (119th)
    ("Received in Senate", (STAGE_CHAMBER_PASSAGE, 0.0)),  # US HR 7730 (119th)
    ("Considered and Passed Senate", (STAGE_CHAMBER_PASSAGE, 0.0)),
    ("Considered and Passed House", (STAGE_CHAMBER_PASSAGE, 0.0)),
    ("Private Law", (STAGE_ENACTED, 0.0)),
    ("House Adopted Resolution", (STAGE_CHAMBER_PASSAGE, 0.0)),  # Michigan HR 10 (2025-2026)
    ("Senate Adopted Resolution", (STAGE_CHAMBER_PASSAGE, 0.0)),  # Michigan SR 61 (2025-2026)
    ("As Adopted by the House", (STAGE_CHAMBER_PASSAGE, 0.0)),
    ("As Adopted by the Senate", (STAGE_CHAMBER_PASSAGE, 0.0)),
    ("Local Act", (STAGE_ENACTED, 0.0)),
]


@pytest.mark.parametrize(("note", "expected"), OPEN720_NOTES)
def test_note_stage_agreed_passed_adopted_and_acts(note, expected):
    assert note_stage(note) == expected


def _latest(jurisdiction_name, versions):
    placed = [(n, d) for n, d in versions if note_stage(n)[0] != STAGE_UNKNOWN]
    return max(placed, key=lambda v: version_sort_key(v[0], v[1], [], None, jurisdiction_name))[0]


def test_us_sres_866_latest_text_is_the_one_the_senate_agreed_to():
    versions = [("Introduced in Senate", "2026-09-17"), ("Agreed to Senate", "2026-09-30")]
    assert _latest("United States", versions) == "Agreed to Senate"


def test_us_hr_7730_received_in_senate_sits_between_engrossed_and_public_law():
    in_true_order = [
        ("Introduced in House", "2026-02-26"),
        ("Reported in House", "2026-08-27"),
        ("Engrossed in House", "2026-09-16"),
        ("Received in Senate", "2026-09-17"),
        ("Enrolled Bill", ""),
        ("Public Law", "2026-11-11"),
    ]
    keys = [version_sort_key(n, d, [], None, "United States") for n, d in in_true_order]
    assert keys == sorted(keys)
    assert _latest("United States", in_true_order) == "Public Law"


def test_michigan_resolutions_rank_introduced_then_adopted_then_enrolled():
    # Michigan HR 10 and SR 61 (2025-2026); Michigan versions carry no date, so the stage alone orders them.
    assert _latest("Michigan", [("House Introduced Resolution", ""), ("House Adopted Resolution", "")]) == (
        "House Adopted Resolution"
    )
    in_true_order = ["Senate Introduced Resolution", "Senate Adopted Resolution", "Senate Enrolled Resolution"]
    keys = [version_sort_key(n, "", [], None, "Michigan") for n in in_true_order]
    assert keys == sorted(keys)
    assert keys[0] < keys[1] < keys[2]


@pytest.mark.parametrize(
    "note",
    [
        # The US's procedural reprints name no stage and stay unknown; close shapes of the new names too.
        "Reference Change Senate",
        "Laid on Table in House",
        "Held at Desk Senate",
        "Committee Discharged House",
        "Returned to the House by Unanimous Consent",
        "Agreed to Senate Draft",
        "Adopted",
        "Private",
        "Local Acts",
    ],
)
def test_note_stage_open720_rules_do_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


# --- OPEN-773: Kentucky's "Current/Final" ------------------------------------------------------------

# Every Kentucky version name on the Mac replica (2026RS, 2026-11-22, 2,561 versions): "Introduced" (1,707),
# "Current/Final" (652, no stage before OPEN-773) and "Acts Chapter <N>" (202, one per enacted bill).
KY_NOTES = [
    ("Introduced", (STAGE_INTRODUCED, 0.0)),
    ("Current/Final", (STAGE_CHAMBER_PASSAGE, 0.0)),
    ("Acts Chapter 4", (STAGE_ENACTED, 4.0)),
    ("Acts Chapter 153", (STAGE_ENACTED, 153.0)),
]


@pytest.mark.parametrize(("note", "expected"), KY_NOTES)
def test_note_stage_kentucky_vocabulary(note, expected):
    assert note_stage(note) == expected
    assert is_bill_version("Kentucky", note) is True


@pytest.mark.parametrize(
    ("bill", "stored_order", "true_order"),
    [
        # Real Kentucky 2026RS bills as stored on the Mac replica (no version carries a date).
        ("HB 1", ["Acts Chapter 4", "Introduced", "Current/Final"], ["Introduced", "Current/Final", "Acts Chapter 4"]),
        (
            "SB 1",
            ["Current/Final", "Acts Chapter 153", "Introduced"],
            ["Introduced", "Current/Final", "Acts Chapter 153"],
        ),
        ("HB 168", ["Introduced", "Current/Final"], ["Introduced", "Current/Final"]),
        ("HR 1", ["Current/Final", "Introduced"], ["Introduced", "Current/Final"]),
    ],
)
def test_kentucky_versions_rank_introduced_then_current_final_then_acts_chapter(bill, stored_order, true_order):
    ranked = sorted(stored_order, key=lambda n: version_sort_key(n, "", [], None, "Kentucky"))
    assert ranked == true_order
    # The latest text: the act for an enacted bill, Current/Final (in the lineage, not unknown) otherwise.
    assert _latest("Kentucky", [(n, "") for n in stored_order]) == true_order[-1]


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes stay unknown: the rule is Kentucky's exact name, nothing wider.
        "Current",
        "Current Final",
        "Current/Final Draft",
        "Current Version",
    ],
)
def test_note_stage_kentucky_current_final_rule_does_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


# --- OPEN-784: Rhode Island's texts --------------------------------------------------------------------------------

# The names the scraper gives Rhode Island's texts, from the files' endings ("", aa, A, Aaa, B, Baa): every ending in
# the legislature's 2026 text indexes (read 2026-11-23). Before OPEN-784 every one after "Introduced" ranked
# (STAGE_AMENDMENT, 0.0), so their order was the database's.
RI_NOTES = [
    ("Introduced", (STAGE_INTRODUCED, 0.0)),
    ("As Amended", (STAGE_AMENDMENT, 0.5)),
    ("Substitute A", (STAGE_AMENDMENT, 1.0)),
    ("Substitute A As Amended", (STAGE_AMENDMENT, 1.5)),
    ("Substitute B", (STAGE_AMENDMENT, 2.0)),
    ("Substitute B As Amended", (STAGE_AMENDMENT, 2.5)),
]


@pytest.mark.parametrize(("note", "expected"), RI_NOTES)
def test_note_stage_rhode_island_vocabulary(note, expected):
    assert note_stage(note) == expected
    assert is_bill_version("Rhode Island", note) is True


@pytest.mark.parametrize(
    ("bill", "true_order"),
    [
        # Real Rhode Island 2026 bills, in the order the legislature's text index prints their texts. None has a date.
        (
            "HR 7532",
            ["Introduced", "Substitute A", "Substitute A As Amended", "Substitute B", "Substitute B As Amended"],
        ),
        ("HR 7031", ["Introduced", "Substitute A", "Substitute A As Amended"]),
        ("HB 7030", ["Introduced", "Substitute A", "Substitute B"]),
        ("HB 7007", ["Introduced", "As Amended"]),
        ("SB 2048", ["Introduced", "As Amended", "Substitute A"]),
    ],
)
def test_rhode_island_texts_rank_in_the_order_they_were_printed(bill, true_order):
    for stored_order in (true_order, true_order[::-1]):
        ranked = sorted(stored_order, key=lambda n: version_sort_key(n, "", [], None, "Rhode Island"))
        assert ranked == true_order
        assert _latest("Rhode Island", [(n, "") for n in stored_order]) == true_order[-1]


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        # Close shapes keep the rank they had before OPEN-784: the rule is Rhode Island's exact names, nothing wider.
        ("Substitute", (STAGE_AMENDMENT, 0.0)),
        ("Substitute AA", (STAGE_AMENDMENT, 0.0)),
        ("Substitute A1", (STAGE_AMENDMENT, 1.0)),
        ("First Substitute", (STAGE_AMENDMENT, 1.0)),
        ("Substitute As Amended", (STAGE_AMENDMENT, 0.0)),
        ("Substitute A as amended in concurrence", (STAGE_AMENDMENT, 0.0)),
        ("As Amended by Senate", (STAGE_AMENDMENT, 0.0)),
    ],
)
def test_note_stage_rhode_island_rule_does_not_widen(note, expected):
    assert note_stage(note) == expected


# --- OPEN-788: Oklahoma's versions and the papers stored beside them ----------------------------------------------

# Every Oklahoma name that is a text of the bill (Mac replica, 2025 and 2026 sessions, 2026-11-23, 17,556 versions):
# the stage note_stage() gives it, origin-free. The first four had no stage before OPEN-788.
OK_VERSION_NOTES = [
    ("Floor (House)", (STAGE_AMENDMENT, 0.0)),
    ("Floor (Senate)", (STAGE_AMENDMENT, 0.0)),
    ("Policy Committee Recommendation", (STAGE_AMENDMENT, 0.0)),
    ("Sub Committee Recommendation", (STAGE_AMENDMENT, 0.0)),
    ("Introduced", (STAGE_INTRODUCED, 0.0)),
    ("House Committee Substitute", (STAGE_AMENDMENT, 0.0)),
    ("Committee Substitute", (STAGE_AMENDMENT, 0.0)),
    ("Senate Committee Substitute for House Bill", (STAGE_AMENDMENT, 0.0)),
    ("House Committee Substitute for Senate Bill", (STAGE_AMENDMENT, 0.0)),
    ("Engrossed", (STAGE_CHAMBER_PASSAGE, 0.0)),
    ("Amended And Engrossed", (STAGE_CHAMBER_PASSAGE, 0.0)),
    ("House Conference Committee Substitute 2", (STAGE_CHAMBER_PASSAGE, 2.25)),
    ("Enrolled (final version)", (STAGE_FINAL_PASSAGE, 0.0)),
]


@pytest.mark.parametrize(("note", "expected"), OK_VERSION_NOTES)
def test_note_stage_oklahoma_versions(note, expected):
    assert note_stage(note) == expected
    assert is_bill_version("Oklahoma", note) is True


# Real Oklahoma names of papers offered along the way (floor and committee amendments, proposed substitutes): not
# versions, and ranked as amendment papers so they do not count as versions with no stage.
OK_PAPER_NOTES = [
    "1 Floor Amendment by KANE",
    "1 Floor Amendment by ROSECRANTS (UNTIMELY FILED)",
    "12 Floor Amendment by FUGATE",
    "1  Amendment to Amendment by CALDWELL",
    "3  Amendment to Amendment by HILBERT (UNTIMELY FILED)",
    "Committee Amendment",
    "Committee Amendment 1",
    "Committee Amendment 4",
    "Proposed Policy Committee Substitute 1",
    "Proposed Policy Committee Substitute 1 - Amendment 2",
    "Proposed Policy Committee Amendment 3",
    "Proposed Committee Substitute (full committee) 4",
    "Proposed Committee Substitute (sub committee) 1 - Amendment 1",
    "Proposed Committee Amendment (sub committee) 2",
    "Proposed Policy Committee Recommendation - - Amendment 2",
    "Amendment to Proposed Committee Substitute (full committee) 2",
    "Amendment to Sub Committee Recommendation",
    "HB1001 (4-29-25) (THOMPSON) RT FA1.PDF",
    "HB1081 (2-24-26) (RADER) FS WDFA1.PDF",
    "HB1374 (4-23-26) (WOODS) FS FA3.PDF",
    "HB1200 (5-07-25) (GRELLNER) FA4.PDF",
    "HB1427 (3-04-26) (PAXTON) FS FA2 (2).PDF",
    "SB346 (3-23-26) (BERGSTROM)  FS FA1.PDF",
]


@pytest.mark.parametrize("note", OK_PAPER_NOTES)
def test_oklahoma_papers_are_not_versions_and_have_a_stage(note):
    assert is_bill_version("Oklahoma", note) is False
    assert note_stage(note)[0] == STAGE_AMENDMENT


@pytest.mark.parametrize("note", OK_PAPER_NOTES + [n for n, _ in OK_VERSION_NOTES])
def test_oklahoma_rules_do_not_reach_other_states(note):
    assert is_bill_version("Florida", note) is True
    stage, ordinal = note_stage(note)
    for origin in ("house", "senate"):
        assert version_sort_key(note, "", [], origin, "Florida") == (stage, "", ordinal, 0)


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes are not Oklahoma's papers: the rules are its exact names, nothing wider.
        "Floor Amendment by KANE",
        "Committee Amendments",
        "Proposed Policy Committee Substitute",
        "Committee Substitute Amendment",
        "HB1001 (4-29-25) RT FA1",
        "Floor (House) Amendment",
    ],
)
def test_oklahoma_paper_rule_does_not_widen(note):
    assert is_bill_version("Oklahoma", note) is True


@pytest.mark.parametrize(
    "note", ["Floor", "Floor (Joint)", "House Floor", "Policy Committee Recommendations", "Sub Committee", "HB1 FA1.PDF"]
)
def test_note_stage_oklahoma_rules_do_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


@pytest.mark.parametrize(
    ("bill", "true_order"),
    [
        # Real Oklahoma bills; the order is the bill page's own version dates (HB 4478: 1/15, 2/17, 3/5, 3/9, 3/17,
        # 4/15/2026) and histories. A committee text's place depends on whether its chamber is the bill's own.
        (
            "HB 4478",
            ["Introduced", "Sub Committee Recommendation", "House Committee Substitute", "Floor (House)", "Engrossed",
             "Floor (Senate)"],
        ),
        (
            "HB 1095",
            ["Introduced", "Policy Committee Recommendation", "House Committee Substitute", "Floor (House)", "Engrossed",
             "Senate Committee Substitute for House Bill", "Floor (Senate)", "Amended And Engrossed",
             "Enrolled (final version)"],
        ),
        (
            "SB 132",
            ["Introduced", "Committee Substitute", "Floor (Senate)", "Engrossed", "Policy Committee Recommendation",
             "House Committee Substitute for Senate Bill", "Floor (House)", "Amended And Engrossed",
             "Senate Conference Committee Substitute", "Enrolled (final version)"],
        ),
        (
            "HB 2104",
            ["Introduced", "House Committee Substitute", "Floor (House)", "Engrossed", "Floor (Senate)",
             "Amended And Engrossed", "House Conference Committee Substitute", "House Conference Committee Substitute 2",
             "Enrolled (final version)"],
        ),
        ("SB 162", ["Introduced", "Floor (Senate)", "Engrossed", "Sub Committee Recommendation", "Floor (House)",
                    "Amended And Engrossed", "Enrolled (final version)"]),
    ],
)
def test_oklahoma_versions_rank_by_the_bills_own_chamber(bill, true_order):
    origin = origin_chamber(bill)
    for stored_order in (true_order, list(reversed(true_order))):
        ranked = sorted(stored_order, key=lambda n: version_sort_key(n, "", [], origin, "Oklahoma"))
        assert ranked == true_order
