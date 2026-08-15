"""Bill-version chronological ordering (OPEN-34) — the one canonical
implementation of "which version came first."

`BillVersion` has no `Meta.ordering` and no reliable timestamp column, and
`BillVersion.date` is blank 100% of the time for every state jurisdiction
audited (FL/MI/AZ/UT/WA/VA -- confirmed against real archived data); only US
federal populates it (~99.4%). Walking `bill.versions.all()` and trusting
whatever order Postgres happens to return for an unordered SELECT tracks DB
insertion order in practice, which is real and inconsistent across
jurisdictions (confirmed via a ~10-12-bill-per-jurisdiction sample as part of
OPEN-34, 2026-08-06/07):

  - FL: forward in 9/12 sampled bills, but "Filed" (the introduced stage) was
    NOT first in 3/12 (e.g. real bills "SB 1668", "SB 1220") -- forward is
    the majority pattern, not a guarantee.
  - MI: forward ONLY for Resolutions and for the rare Bill with no
    Substitute version. Any Bill with a Substitute gets that Substitute
    inserted BEFORE "House/Senate Introduced Bill" in walk order (8/12
    sampled bills).
  - AZ: forward in 11/12 sampled bills; one real exception where a
    floor-amendment-style version landed first.
  - VA: backward -- already confirmed at real scale by OPEN-33 (604 affected
    rows, full audit).
  - UT: does NOT fit a simple reversal at all. "Enrolled" appears at wildly
    inconsistent positions across real bills.
  - US (federal): backward in 10/12 sampled bills, but `BillVersion.date` is
    reliably populated (~99.4%) -- a real date-based fix, not a workaround,
    fully covers US.
  - WA: root-caused, not left ambiguous. scrapers/wa/bills.py's
    `_load_versions()` fetches one page per bill_type ("Bills",
    "Resolutions", ..., "Passed Legislature" -- in that dict order), so "X
    Passed Legislature" documents are structurally always walked last (a
    deterministic code-order effect, not scrambled DB rows or interleaved
    re-scrapes). Within the "Bills" page, WA's own site lists "Engrossed <N>
    Substitute" before the plain "<N> Substitute" it amends, and the bare
    introduced "Bill" near the end instead of first -- deterministic, just
    not chronological.

A static per-jurisdiction "reverse" flag is therefore not supportable by this
data -- no jurisdiction sampled is 100% one direction, and MI/UT/WA aren't
even binary. `version_sort_key()` never trusts DB walk order at all: it
ranks each version by (1) `BillVersion.date` when it's actually populated
and parses as a date -- covers US federal outright and any future
jurisdiction that starts populating it -- and otherwise (2) a content-based
stage rank built directly from the real `version_note` vocabulary. A
version whose note matches neither is never guessed into a position: it's
excluded from the diff lineage entirely (returns `STAGE_UNKNOWN`) rather
than risking a backward diff, per the ticket's own framing that a
wrong-direction diff is worse than a missing one.

Originally private to `openstates/cli/text_extract.py` (the only caller,
`archive_bill_versions()`/`recompute_bill_diff_order()`); extracted here
(OPEN-91) as a public, importable module so other consumers -- notably
api-v3's bill-detail endpoint (OPEN-92) -- can depend on the real,
audited implementation instead of reinventing an approximation of it. This
is a pure move: the logic/regex table below is unchanged from
`text_extract.py`'s own `_note_stage()`/`_version_sort_key()`.
"""

from __future__ import annotations

import re
import typing

STAGE_INTRODUCED = 0
STAGE_AMENDMENT = 1
STAGE_CHAMBER_PASSAGE = 2
STAGE_FINAL_PASSAGE = 3
STAGE_ENACTED = 4
STAGE_UNKNOWN = 99  # excluded from diff lineage entirely -- see version_sort_key()

_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}

_DATE_RE = re.compile(r"\A\d{4}(-\d{2}(-\d{2})?)?\Z")


def extract_ordinal(note: str) -> float:
    """
    Best-effort numeric ordinal embedded in a version_note, used to rank same-stage numbered
    variants against each other (MI's "Substitute (S-2)", UT's "Substitute #3", WA's "Second
    Substitute", FL's "c2"/"e2"). 0.0 if no ordinal is found -- the unnumbered/first-of-its-
    kind case (FL's "c1", WA's plain "Substitute Bill" with no ordinal word).

    MI's "(S-1)"/(H-2)" parenthesized number is checked first and takes priority over a
    trailing "- N" suffix on the same note (a second file for that *same* substitute stage,
    e.g. "Substitute (S-1) - 2" -- a minor tiebreak, not a different amendment stage; folded
    in as a small fraction so it sorts immediately after "Substitute (S-1)" rather than being
    conflated with "Substitute (S-2)").
    """
    lowered = note.lower()

    paren = re.search(r"\([sh]-(\d+)\)", lowered)
    if paren:
        base = float(paren.group(1))
        tail = re.search(r"\)\s*-\s*(\d+)\s*\Z", lowered)
        return base + (int(tail.group(1)) / 100.0 if tail else 0.0)

    for word, value in _ORDINAL_WORDS.items():
        if word in lowered:
            return float(value)

    m = (
        re.search(r"#\s*(\d+)\b", note)
        or re.search(r"\b[a-z](\d+)\b", lowered)
        or re.search(r"(\d+)\s*\Z", note)
    )
    if m:
        return float(m.group(1))
    return 0.0


def note_stage(note: str) -> tuple:
    """
    Classify a version_note into (stage, ordinal) using the content-based stage table built
    from the OPEN-34 audit (see this module's own docstring). Never looks at DB order or
    position -- purely a function of the note text itself, so it's stable no matter what
    order versions are walked in or what row order Postgres happens to return.
    """
    lowered = note.lower()

    if re.search(
        r"public act|public law|\bchapter|passed legislature|concurred", lowered
    ):
        return (STAGE_ENACTED, extract_ordinal(note))

    # Final-passage sub-stages, most-final-first, checked in this specific order since a
    # note can match more than one (e.g. VA's "Governor's Veto Explanation" contains neither
    # "reenroll" nor plain "enroll"). Sub-ranks encode the real chronology confirmed against
    # VA's own examples (OPEN-33/the ticket): Enrolled -> Governor Substitute -> Reenrolled ->
    # Governor's Veto Explanation. Note: "enroll" (no leading \b) deliberately matches
    # "Reenrolled" too ("re" + "enrolled" has no word boundary between them for \benroll to
    # anchor on) -- the explicit "reenroll" check above it takes priority so the two don't
    # collide.
    if "veto" in lowered:
        return (STAGE_FINAL_PASSAGE, 3.0)
    if "reenroll" in lowered:
        return (STAGE_FINAL_PASSAGE, 2.0)
    if "governor" in lowered:
        return (STAGE_FINAL_PASSAGE, 1.0)
    if "enroll" in lowered or re.search(r"\ber\b", lowered):
        return (STAGE_FINAL_PASSAGE, 0.0)

    if re.match(r"(senate|house)\s*-", lowered):
        # AZ floor/committee-action notes that leak into version_note -- observed after
        # engrossment in every real sample checked (a floor amendment applies to the
        # already-engrossed bill), so rank just after plain chamber-passage. Checked before
        # the generic "engross" test below since these notes sometimes *reference* an
        # engrossed version by name (e.g. "ref Senate Engrossed House Bill") without
        # themselves being one -- a leading "senate -"/"house -" is the more specific,
        # reliable signal for this AZ-specific note shape.
        return (STAGE_CHAMBER_PASSAGE, 0.5)

    if re.search(r"\be\d+\b", lowered) and not re.search(
        r"substitute|committee", lowered
    ):
        # FL's own engrossed shorthand ("e1", "e2") -- distinct token pattern from the
        # "engross" word check below but the same chamber-passage stage.
        return (STAGE_CHAMBER_PASSAGE, extract_ordinal(note))

    if "engross" in lowered and not re.search(r"substitute|committee", lowered):
        # AZ-style whole-bill floor engrossment ("Senate Engrossed Version") -- a later,
        # chamber-passage-level stage, distinct from WA's per-substitute "Engrossed <N>
        # Substitute Bill" handled below.
        return (STAGE_CHAMBER_PASSAGE, extract_ordinal(note))

    if re.search(r"conference|\breport|\breferr|placed on calendar|as passed", lowered):
        return (STAGE_CHAMBER_PASSAGE, extract_ordinal(note) + 0.25)

    if re.search(r"substitute|amend|comparison|\bc\d+\b", lowered):
        if "engross" in lowered:
            # WA's "Engrossed <N> Substitute Bill" amends that specific substitute number --
            # ranks immediately after it, not after every substitute regardless of number.
            return (STAGE_AMENDMENT, extract_ordinal(note) + 0.5)
        return (STAGE_AMENDMENT, extract_ordinal(note))

    if re.search(r"introduced|\bfiled\b|\bpb\b|original|^bill$", lowered):
        return (STAGE_INTRODUCED, extract_ordinal(note))

    if lowered == "bill text":
        # MA's only version_note until OPEN-37 added a second (scrapers/ma/bills.py's
        # add_version_link("Bill Text", ...)) -- MA has no other stage name for its
        # introduced text, and "bill text" doesn't match "^bill$" (extra word) or any other
        # case above, so without this it fell through to STAGE_UNKNOWN and was excluded from
        # the diff lineage entirely. Exact match, not a substring check, so this can't
        # accidentally swallow some other jurisdiction's differently-worded note.
        return (STAGE_INTRODUCED, 0.0)

    return (STAGE_UNKNOWN, 0.0)


def version_sort_key(note: str, date: typing.Optional[str]) -> tuple:
    """
    Rank a single version (by its note + date) for chronological ordering, without ever
    trusting the order it was returned from the DB in. See this module's own docstring for
    the audit this encodes.

    Returns (stage, date-or-empty, ordinal). The macro stage always comes from the note (see
    note_stage()) -- a real, parseable date is used only as a same-stage tiebreaker, not as
    an override of the note-based stage. This matters for jurisdictions that could have a mix
    of dated and undated versions on the same bill (US federal is ~99.4% dated, not 100%):
    letting a date win globally would make any dated version sort before every undated one
    regardless of true chronology. Confirmed via audit: 0% of state-jurisdiction versions
    (FL/MI/AZ/UT/WA/VA) have a date at all, so this tiebreaker is inert for them and they rely
    entirely on the note-based stage; US federal's real dates resolve same-stage ordering
    (e.g. "Reported to Senate" vs. "Engrossed in Senate") more precisely than the ordinal
    heuristic alone would.

    A note matching none of the known patterns returns stage STAGE_UNKNOWN -- the caller
    excludes those versions from the diff lineage entirely rather than guessing a position for
    them.
    """
    stage, ordinal = note_stage(note)
    has_date = bool(date) and bool(_DATE_RE.match(date))
    return (stage, date if has_date else "", ordinal)
