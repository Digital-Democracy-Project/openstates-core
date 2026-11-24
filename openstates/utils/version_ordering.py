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

import datetime
import re
import typing

from openstates.ddp_states import NON_BILL_AMENDMENT, state_values_by_name  # noqa: F401 (NON_BILL_AMENDMENT: re-exported)
from openstates.ddp_states.al import _AL_FLOOR_MOTION_PAPER
from openstates.ddp_states.mo import _MO_SENATE_AMENDMENT_CARD, TEXT_NUMBER_PREFIX as _MO_TEXT_NUMBER_PREFIX
from openstates.ddp_states.ok import _OK_SENATE_FLOOR_AMENDMENT_FILE

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

# OPEN-361: Utah's stamped "Amended <M/D/YYYY H:MM:N>" (and the "Amended Excerpts <same stamp>"
# beside it) -- see extract_ordinal(). Only Utah has notes of this shape (checked against every
# distinct stored note in every jurisdiction, 2026-10-13). The pattern is limited to the shape
# observed in all 734 distinct stamped notes (years 2026, hours 7-23, last numbers 1-999): an
# hour of 0-23 and a last number of at most four digits.
_STAMPED_AMENDED_RE = re.compile(
    r"\Aamended (?:excerpts )?(\d{1,2})/(\d{1,2})/(\d{4}) ([01]?\d|2[0-3]):\d{1,2}:(\d{1,4})\Z"
)
_STAMPED_AMENDED_EPOCH = datetime.date(2000, 1, 1)

# OPEN-554: _AL_FLOOR_MOTION_PAPER (Alabama's floor-motion paper names) lives in openstates/ddp_states/al.py.


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

    OPEN-361: Utah's stamped "Amended 2/12/2026 10:02:409" is ranked by the date and hour in
    the note, not by its last number. The generic rule below reads the trailing "409" as an
    ordinal, but that number has nothing to do with order: measured against the real file
    timestamps in the archived URLs (2026-10-13), it put 48 of the 74 Utah bills that have two
    or more such versions in the wrong order, some exactly reversed, while date + hour then
    the last number is right for 73 of the 74 (the date and hour in the note matched the URL's
    for all 368 pairs checked; the "minute" in the note is just the month again, so the real
    minutes and seconds are not in the note at all). Two versions in the same hour fall back to
    the last number, which is deterministic but only right for one of the two such bills seen.
    The value is 1000 + hours since 2000-01-01 + the last number as a fraction (under 1, so it
    only breaks ties), so a stamped "Amended" ranks after every numbered "Substitute #N", as it
    did before. This covers the observed shape only: all 734 distinct stamped notes are from
    2026, with hours 7-23 and last numbers 1-999. A note outside it -- an hour above 23, a last
    number of more than four digits, a date before 2000 or that is not a real date -- falls
    through to the generic rules below rather than being ranked on bad data or failing.
    """
    lowered = note.lower()

    paren = re.search(r"\([sh]-(\d+)\)", lowered)
    if paren:
        base = float(paren.group(1))
        tail = re.search(r"\)\s*-\s*(\d+)\s*\Z", lowered)
        return base + (int(tail.group(1)) / 100.0 if tail else 0.0)

    stamped = _STAMPED_AMENDED_RE.match(lowered)
    if stamped:
        month, day, year, hour, tail_digits = (int(g) for g in stamped.groups())
        try:
            days = (datetime.date(year, month, day) - _STAMPED_AMENDED_EPOCH).days
        except ValueError:
            days = -1
        if days >= 0:
            return 1000.0 + days * 24 + hour + tail_digits / 10000.0

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


# OPEN-373: Washington's introduced forms of resolutions and memorials. A bill's introduced text is
# the note "Bill" (matched by "^bill$" below); the equivalent for these four kinds had no rule, so
# they were STAGE_UNKNOWN and outside the diff lineage, and their substitutes ("Substitute
# Resolution", "Substitute Joint Memorial", ...) were first in the lineage with nothing to diff
# against. Exact notes, not substrings. Checked against every distinct stored note in all 10
# jurisdictions on the production replica (2026-10-15): only Washington has them (Resolution 624
# documents, Joint Memorial 94, Joint Resolution 54, Concurrent Resolution 40).
_INTRODUCED_RESOLUTION_NOTES = frozenset(
    {"resolution", "joint memorial", "joint resolution", "concurrent resolution"}
)


def note_stage(note: str) -> tuple:
    """
    Classify a version_note into (stage, ordinal) using the content-based stage table built
    from the OPEN-34 audit (see this module's own docstring). Never looks at DB order or
    position -- purely a function of the note text itself, so it's stable no matter what
    order versions are walked in or what row order Postgres happens to return.
    """
    lowered = note.lower()

    # OPEN-366: North Carolina's own version names (ncleg.gov's "<bill>v<N>.pdf" series):
    # "Edition <N>" are the successive full printings after "Filed", "Ratified" is the bill as
    # sent to the Governor, and "SL <year>-<N>" is the enacted session law. None matched any rule
    # below, so 3,800 of NC's 6,191 archived versions were out of the lineage. Exact shapes, not
    # substrings, so no other jurisdiction's note can be pulled in (checked against every distinct
    # stored note in every jurisdiction, 2026-10-15: only North Carolina has these three shapes).
    if re.fullmatch(r"sl \d{4}-\d+", lowered):
        return (STAGE_ENACTED, 0.0)
    if lowered == "ratified":
        return (STAGE_FINAL_PASSAGE, 0.0)
    if re.fullmatch(r"edition [1-9]\d*", lowered):
        return (STAGE_AMENDMENT, extract_ordinal(note))
    # OPEN-784: Rhode Island's texts after the introduced one, named from their files' endings by the scraper:
    # "As Amended" (aa), "Substitute A" (A), "Substitute A As Amended" (Aaa), "Substitute B" ... (each text's own
    # header names it so). The rules below give them all (STAGE_AMENDMENT, 0.0), so a bill with two or more (55 of
    # RI's 3,011 2026 bills) kept the database's order. The legislature's text index prints them in this order:
    # As Amended, Substitute A, Substitute A As Amended, Substitute B, Substitute B As Amended (read 2026-11-23).
    # Exact names: no other jurisdiction has them (every distinct stored note on the Mac replica, 2026-11-23).
    if lowered == "as amended":
        return (STAGE_AMENDMENT, 0.5)
    ri_substitute = re.fullmatch(r"substitute ([a-z])( as amended)?", lowered)
    if ri_substitute:
        letter = ord(ri_substitute.group(1)) - ord("a") + 1
        return (STAGE_AMENDMENT, letter + (0.5 if ri_substitute.group(2) else 0.0))

    if lowered in _INTRODUCED_RESOLUTION_NOTES:
        return (STAGE_INTRODUCED, 0.0)

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

    # OPEN-510: Louisiana's successive engrossments, "<bill> Engrossed", "<bill> Reengrossed" and
    # "<bill> Re-Reengrossed" (e.g. "SB217 Engrossed"). The generic rule below gives all three the
    # same rank (no ordinal in the note) and Louisiana versions carry no date (10,468 of 10,468 on
    # the Mac replica, 2026-11-08), so they tied and kept storage order, which was often reversed
    # (Original, Re-Reengrossed, Reengrossed, Engrossed). Exact shapes, checked against every
    # distinct stored note in every jurisdiction on the Mac replica (2026-11-08): only Louisiana's
    # notes match (Virginia's bare "Reengrossed" does not, and keeps its rank).
    la_engrossed = re.fullmatch(r"[a-z]+\d+ (re-re|re)?engrossed", lowered)
    if la_engrossed:
        return (
            STAGE_CHAMBER_PASSAGE,
            {None: 0.0, "re": 1.0, "re-re": 2.0}[la_engrossed.group(1)],
        )

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

    # OPEN-482: version names of Pennsylvania, Nebraska and Louisiana. After their first full scrapes
    # (2026-11-02/03) the Stage 3 gate found 6,194 of 6,194 (PA), 1,360 of 3,383 (NE) and 1,070 of
    # 10,468 (LA) versions with no stage, so they sat outside the diff lineage. This block is LAST on
    # purpose: it runs only for a note no rule above claims, so no note that has a stage today can
    # change (checked over every distinct stored note in every jurisdiction on the production
    # replica, 2026-11-03: only these three states' notes change). Exact shapes, not substrings.
    #   PA "Printer's No. PN<N>": each printing of a bill. The note does not say which printing is
    #   the introduced one, so they all rank as amendments ordered by the printer's number: the
    #   first printing leads the lineage and each later one diffs against the one before it.
    #   NE "Slip Law" (enacted), "Final Reading" (after "Enrollment and Review"), and the
    #   amendment documents "<introducer or committee> AM<N>" / "... FA<N>" (floor amendments).
    #   LA "<bill> Act <N>" and "<bill> Act": the enacted act. "Senate Floor Bureau Note ..." stays
    #   unknown: it is not bill text.
    printers_no = re.fullmatch(r"printer's no\. pn(\d+)", lowered)
    if printers_no:
        return (STAGE_AMENDMENT, float(printers_no.group(1)))
    if lowered == "slip law" or re.fullmatch(r"[a-z]+\d+ act( \d+)?", lowered):
        return (STAGE_ENACTED, 0.0)
    if lowered == "final reading":
        return (STAGE_FINAL_PASSAGE, 0.5)
    if re.fullmatch(r".+ (?:am|fa)\d+(?: .+)?", lowered):
        return (STAGE_AMENDMENT, extract_ordinal(note))
    # OPEN-525: Alabama's floor-motion papers (amendments and substitutes, see
    # _AL_FLOOR_MOTION_PAPER) rank as amendment papers, like Alabama's "<Committee> 1st Amendment"
    # and "... 1st Substitute". They are not bill versions (is_procedural_document), so the rank
    # never puts them in the diff lineage; it only stops them counting as versions with no stage.
    if re.fullmatch(_AL_FLOOR_MOTION_PAPER, lowered):
        return (STAGE_AMENDMENT, 0.0)
    # OPEN-602: Georgia's version names. Georgia names a version after its drafting number ("LC 50
    # 1106S") plus a suffix, or after the step that produced it; 278 of 303 Georgia names had no
    # stage. Last for the same reason as OPEN-482's block above, and exact shapes (checked over every
    # distinct stored note in every jurisdiction on the Mac databases, 2026-11-16: only Georgia's
    # notes change). The names are typed by hand (a double space, a trailing space, "Committe sub",
    # "/RCS"), so spaces are collapsed first. The stage is the label; Georgia's order comes from the
    # site's document number (_DOCUMENT_ORDERED_JURISDICTIONS below), so the ordinals are all 0.
    # "As introduced LC ...", "As Passed Senate" and "Floor Amend ..." already had a stage and keep it.
    #   "LC 47 4374/a", "LC 50 1032-EC/a": the bill as filed ("House Bill 83EX By: ...").
    #   "Local Ad", "SB 4EX Local Ad": the newspaper notice of intent to introduce a local bill. Not
    #   a version (is_procedural_document); ranked introduced only so it does not count as no stage.
    #   "LC 50 1106S/hs", "/hcs", "/hss", "/hrcs", "/rcs": House substitutes ("The House Committee on
    #   ... offers the following substitute to ..."). "Committee Sub LC 49 2233-ECS", "Sen Ctee Sub
    #   LC 62 0177S": Senate committee substitutes.
    #   "SCSFA to HB 61", "HB 1193 SCSFA", "SFA to HB 308": the bill as the Senate passed it, by
    #   substitute or not, as floor amended (status "Senate Passed/Adopted By Substitute", CSFA).
    #   "Adopted Senate", "Read and adopted LC 112 4263": a resolution as its chamber adopted it.
    #   "HB 223/AP*", "SB 3EX/AP*": the bill as passed by both chambers ("AS PASSED").
    ga = " ".join(lowered.split())
    if re.fullmatch(r"lc \d+ \d+(?:-ec)?/a|(?:[hs][br] \d+(?:ex)? )?local ad", ga):
        return (STAGE_INTRODUCED, 0.0)
    if re.fullmatch(
        r"lc \d+ \d+s/(?:h(?:s|cs|ss|rcs)|rcs)|(?:sen ctee|committee?) sub lc \d+ \d+(?:s|-ecs)",
        ga,
    ):
        return (STAGE_AMENDMENT, 0.0)
    if re.fullmatch(
        r"(?:sc)?sfa to [hs][br] \d+(?:ex)?|[hs][br] \d+(?:ex)? scsfa"
        r"|adopted (?:senate|house)|read and adopted lc \d+ \d+",
        ga,
    ):
        return (STAGE_CHAMBER_PASSAGE, 0.0)
    if re.fullmatch(r"[hs][br] \d+(?:ex)?/ap\*?", ga):
        return (STAGE_FINAL_PASSAGE, 0.0)
    # OPEN-635: New Mexico's "Final Version" (nmlegis.gov's Sessions/<session>/final/<bill>.PDF) is
    # the bill's last text, as finally passed: 127 of the 129 bills that have one also have a
    # governor's action. The other 2 (SB 32, SB 179) were postponed indefinitely after committee and
    # their Final Version is the introduced text (SB 179's adds a co-sponsor), so it is still their last text and
    # still ranks last. It had no stage, so 129 of New Mexico's 1,112 versions were outside the lineage
    # (Mac replica, 2026-11-18). Final passage, as North Carolina's "Ratified". Last for the same reason as the
    # blocks above, and an exact name: only New Mexico has it (every distinct stored note in every
    # jurisdiction on the Mac replica, 2026-11-18; Nebraska's "Final Reading" is a different name).
    if lowered == "final version":
        return (STAGE_FINAL_PASSAGE, 0.0)
    # OPEN-720: names of the text as a chamber agreed to, passed or adopted it, and of two kinds of enacted act. They
    # had no stage, so the version was never a bill's latest text (US SRES 866's latest was its introduced text, not
    # "Agreed to Senate"; Michigan HR 10's the same). Last for the same reason as the blocks above, and exact names:
    # on the Mac replica (2026-11-21) only these jurisdictions have them, and no other note changes stage.
    #   US (congress.gov's own version names): "Agreed to Senate" (1,021), "Received in Senate" (150: the House-passed
    #   text as received), "Considered and Passed Senate" (38) / "... House" (1); "Private Law" (2), like "Public Law".
    #   Michigan: "House Adopted Resolution" (284), "Senate Adopted Resolution" (136), "As Adopted by the House" (19) /
    #   "... Senate" (17); "Local Act" (1), like "Public Act".
    #   The US's procedural reprints ("Reference Change Senate", "Laid on Table in House", "Held at Desk Senate",
    #   "Committee Discharged House", "Returned to the House by Unanimous Consent") name no stage and stay unknown.
    if lowered in {
        "agreed to senate", "received in senate", "considered and passed senate", "considered and passed house",
        "house adopted resolution", "senate adopted resolution", "as adopted by the house", "as adopted by the senate",
    }:
        return (STAGE_CHAMBER_PASSAGE, 0.0)
    if lowered in {"private law", "local act"}:
        return (STAGE_ENACTED, 0.0)
    # OPEN-773: Kentucky's "Current/Final" (the record page's second "Bill Documents" link, <bill>/bill.pdf) is the
    # bill's latest printed text, which appears once the bill has passed or been adopted by at least one chamber: all
    # 652 Kentucky bills that have one did, none of the 1,085 without one did (Mac replica, 2026RS, 2026-11-22). For
    # the 202 enacted ones it is the enrolled text, for the rest the text one chamber passed, so it gets the stage that
    # is true for all of them, as Pennsylvania's "Printer's No." (OPEN-482) does (Ramon, 2026-11-22). The order stays
    # Introduced, Current/Final, then "Acts Chapter <N>" (enacted, by the "chapter" rule above). Last for the same
    # reason as the blocks above, and an exact name: only Kentucky has it (Mac replica, 2026-11-22).
    if lowered == "current/final":
        return (STAGE_CHAMBER_PASSAGE, 0.0)
    # OPEN-788: Oklahoma's committee texts and floor versions (see openstates/ddp_states/ok.py). 4,522 of Oklahoma's
    # 17,556 versions had no stage (Mac replica, 2025 and 2026 sessions, 2026-11-23). "Sub Committee Recommendation" and
    # "Policy Committee Recommendation" are the texts a House subcommittee or policy committee recommends to the full
    # committee; "Floor (House)" / "Floor (Senate)" are the texts a chamber's committee reported, placed on that floor.
    # All four are amendment-stage texts here; ok.version_rank places them by the bill's own chamber. The Senate's floor
    # amendments, named after their file ("HB1001 (4-29-25) (THOMPSON) RT FA1.PDF"), are not versions (ok.py's
    # PROCEDURAL_DOCUMENT_NOTE_PATTERN) and rank as amendment papers only so they do not count as versions with no
    # stage, as Alabama's floor-motion papers do. Last for the same reason as the blocks above, and exact names: only
    # Oklahoma has them (Mac replica, 2026-11-23).
    if lowered in {
        "sub committee recommendation", "policy committee recommendation", "floor (house)", "floor (senate)",
    } or re.fullmatch(_OK_SENATE_FLOOR_AMENDMENT_FILE, lowered):
        return (STAGE_AMENDMENT, 0.0)
    # OPEN-801: Montana's "Bill Draft", the name the scraper gives the LC file ("LC0382.pdf", the site's "LC Text"):
    # the drafted bill before introduction ("_____ BILL NO. _____", no sponsor). Every Montana bill has one: 235 of the
    # 294 versions of the 2027 pre-filing scrape (176 drafts not yet introduced, whose only text it is, and the 59
    # pre-introduced bills; Mac replica, 2026-11-23). It comes before the bill's "Introduced" text, so it ranks just
    # below it. Last for the same reason as the blocks above, and an exact name: no other jurisdiction has it (every
    # distinct stored note on the Mac replica, 2026-11-23).
    if lowered == "bill draft":
        return (STAGE_INTRODUCED, -1.0)
    # OPEN-806: Missouri's printings after its committee texts (see openstates/ddp_states/mo.py), named by the House
    # ("Perfected") or by the Senate with the text's number ("5607S.02P - Perfected"). 495 of Missouri's 4,798 versions
    # had no stage (Mac replica, 2026 session, 2026-11-23). "Perfected" (268 with "Re-Perfected") is the bill as its
    # own chamber amended and ordered it printed before third reading (Senate Rules 21, 38, 46), so chamber passage as
    # "Engrossed"; "Re-Perfected" is a second such printing, just after it. "Truly Agreed (to) and Finally Passed" (113)
    # is the text both chambers passed, final passage as "Enrolled". The Senate's floor papers (the "Amendments" tab
    # cards, not versions: mo.py's PROCEDURAL_DOCUMENT_NOTE_PATTERN) rank as amendment papers only so they do not count
    # as versions with no stage, as Alabama's and Oklahoma's do. Last for the same reason as the blocks above, and exact
    # names: only Missouri has them (every distinct stored note on the Mac replica, 2026-11-23).
    mo_printing = re.fullmatch(
        _MO_TEXT_NUMBER_PREFIX + r"(perfected|re-perfected|truly agreed (?:to )?and finally passed)", lowered
    )
    if mo_printing:
        return {
            "perfected": (STAGE_CHAMBER_PASSAGE, 0.0),
            "re-perfected": (STAGE_CHAMBER_PASSAGE, 0.01),
        }.get(mo_printing.group(1), (STAGE_FINAL_PASSAGE, 0.0))
    if re.fullmatch(_MO_SENATE_AMENDMENT_CARD, lowered):
        return (STAGE_AMENDMENT, 0.0)
    # OPEN-804: Hawaii's "House Draft <N>" / "Senate Draft <N>" (the scraper's names for the site's HD<N> / SD<N>
    # files): a committee's amended text of the bill, numbered within its chamber (see openstates/ddp_states/hi.py,
    # whose version_rank puts the other chamber's drafts after the bill's own). Hawaii's "Conference Draft <N>" needs no
    # rule here: "conference" above gives it chamber passage, N + 0.25. Last for the same reason as the blocks above,
    # and exact shapes: no other jurisdiction has them (every distinct stored note on the Mac replica, 2026-11-23).
    hi_draft = re.fullmatch(r"(?:house|senate) draft ([1-9]\d*)", lowered)
    if hi_draft:
        return (STAGE_AMENDMENT, float(hi_draft.group(1)))
    # OPEN-849: Nevada's "Reprint <N>" (the site's <bill>_R<N>.pdf): the bill reprinted with an adopted amendment, in
    # either house, numbered through the session ("Amendment 5" makes "Reprint 1", "Amendment 15" to R1 makes "Reprint
    # 2", ... "As Enrolled" last). 22 of Nevada's 263 versions had no stage (Mac replica, 2026-11-24), so they sat
    # outside the lineage. An amended text, ordered by N, after "As Introduced" and before "As Enrolled", as NELIS
    # lists them. The "Amendment <N>" papers themselves are not versions (nv.py's PROCEDURAL_DOCUMENT_NOTE_PATTERN).
    # Last for the same reason as the blocks above, and an exact shape: only Nevada has it (every distinct stored note
    # on the Mac replica, 2026-11-24).
    nv_reprint = re.fullmatch(r"reprint ([1-9]\d*)", lowered)
    if nv_reprint:
        return (STAGE_AMENDMENT, float(nv_reprint.group(1)))

    return (STAGE_UNKNOWN, 0.0)


# OPEN-368: Arizona's committee / floor-action documents ("HOUSE - <committee>", "SENATE - <name>
# flr amend ...") all share one note_stage() rank and have no date, so nothing in the note or date
# orders them. Their links do carry the site's own document number
# (apps.azleg.gov/BillStatus/GetDocumentPdf/<id>), which rises as documents are published. A
# 30-bill spot check against azleg.gov's committee and floor dates found 0 contradictions (ties
# inside one hearing or one same-day floor session are no worse than before), so it is the
# tiebreaker.
_AZ_DOCUMENT_NUMBER_RE = re.compile(
    r"\Ahttps?://apps\.azleg\.gov/BillStatus/GetDocumentPdf/(\d+)", re.IGNORECASE
)


# OPEN-602: Georgia's site numbers every document it publishes, in the order it publishes them
# (www.legis.ga.gov/api/legislation/document/<session>/<id>; the 2021 special session's older form
# is www.legis.ga.gov/Legislation/<session>/<id>.pdf). See _DOCUMENT_ORDERED_JURISDICTIONS below.
_GA_DOCUMENT_NUMBER_RE = re.compile(
    r"\Ahttps?://www\.legis\.ga\.gov/(?:api/legislation/document|legislation)/[^/]+/(\d+)(?:\.pdf)?\Z",
    re.IGNORECASE,
)

# OPEN-593: Virginia's committee and subcommittee substitutes ("Criminal Subcommittee Substitute",
# "Courts of Justice Substitute", "Delegate Mehta Substitute") all rank as amendments with no
# ordinal and no date, so they tied on everything above and kept the database's walk order, which
# for Virginia is backward (OPEN-33). With the ordinal rule below, 528 bills of the 2026 session
# change order and 108 change their latest version (Mac replica, 2026-11-16). Their links are LIS
# files (lis.blob.core.windows.net/files/<n>.PDF, an .HTML twin at n+1), numbered as they are
# published, so the number is the tiebreaker, as it is for Arizona. A version can hold several
# files (a committee's substitute re-issued: HB 1432 "Courts of Justice Substitute" has 1135485
# and 1142380); the largest is taken, the file whose text the
# version stands for (the archiver and recompute-diff-order take the last file by URL, OPEN-578),
# and the one that puts HB 1432's Criminal Subcommittee (1136021) before Courts of Justice. Checked
# against the dates of each bill's own "Reported from <committee> with substitute" / "Floor offered"
# actions: 462 consecutive pairs agree, 1 contradicts (HB 298, two files a day apart). The 2027
# session's carried-over bills were re-published in bulk, so there the numbers are only mostly
# right (23 agree, 7 contradict, against 0 and 29 for the walk order).
# No Virginia substitute carries a real ordinal either: extract_ordinal() reads the subcommittee's
# number in "Subcommittee #3 Subcommittee Substitute" (135 versions) and the "second" in "Elementary
# & Secondary Education Subcommittee Substitute" (9) as one, which put the subcommittee's text after
# the full committee's in 121 of the 135 bills that have a "Subcommittee #N" one. So for Virginia
# an amendment-stage version's ordinal is 0 and the file number alone orders them
# (version_sort_key). Read on the Mac replica 2026-11-16: every Virginia amendment-stage note with
# an ordinal is one of these two shapes.
_VA_LIS_FILE_RE = re.compile(
    r"\Ahttps?://lis\.blob\.core\.windows\.net/files/(\d+)\.", re.IGNORECASE
)


def document_number(urls: typing.Iterable[typing.Optional[str]]) -> int:
    """The site's own document number from a version's link URLs, read by the link's host: for
    Arizona's GetDocumentPdf links (OPEN-368) and Georgia's document links (OPEN-602) the smallest,
    for Virginia's LIS file links the largest (OPEN-593), and 0 when no link is of these kinds --
    every other state's links."""
    urls = [u or "" for u in urls]
    numbers = [
        int(m.group(1))
        for m in (_AZ_DOCUMENT_NUMBER_RE.match(u) or _GA_DOCUMENT_NUMBER_RE.match(u) for u in urls)
        if m
    ]
    if numbers:
        return min(numbers)
    numbers = [int(m.group(1)) for m in map(_VA_LIS_FILE_RE.match, urls) if m]
    return max(numbers) if numbers else 0


# OPEN-382: Arizona's "House/Senate Engrossed Version" notes get a lower ordinal than every
# "HOUSE - ..." / "SENATE - ..." action document, so the committee texts that come BEFORE a chamber
# passes the bill sorted after its Engrossed version, and diffs ran backward (1,089 of 1,571
# pairs compared against the site's own document ids, on 538 of 605 bills with both kinds). The
# Engrossed notes carry no document number (their links are static files), so they are placed by
# chamber instead: for a bill that starts in chamber X, X's action documents, X's Engrossed
# Version, the other chamber's action documents, its Engrossed Version. The origin chamber comes
# from the bill identifier (HB/HCR/... is House, SB/SCR/... is Senate).
#
# One refinement, measured: a floor amendment whose note names the Engrossed bill ("Floor Amend to
# Engrossed Bill", "flr amend (ref Senate Engrossed House Bill)") amends the already-engrossed
# text, so it goes AFTER its own chamber's Engrossed Version (58 of 84 such documents come after
# it by document id, against 14 of 421 other floor amendments). Measured on the 2026 session
# (azleg.gov's own document ids, 605 bills; ids as the proxy validated in OPEN-368): 1,089 wrong
# pairs today, 72 with the plain placement, 40 with the refinement. Every committee-type
# document (476 of 476) precedes its own chamber's Engrossed Version, and every second-chamber
# document follows the origin chamber's.
#
# The ordinals sit below Conference Committee (0.25), which in all 4 bills that have one comes
# after every action document. Only Arizona uses these note shapes (checked on the replica).
_AZ_CHAMBER_NOTE_RE = re.compile(
    r"\A(house|senate) (- |engrossed version)", re.IGNORECASE
)
_AZ_FLOOR_AMEND_RE = re.compile(r"flr\s+amend|floor amend", re.IGNORECASE)
_AZ_ORIGIN_RE = re.compile(r"\A([HS])[A-Z]*\s*\d", re.IGNORECASE)


def origin_chamber(identifier: typing.Optional[str]) -> typing.Optional[str]:
    """Return house or senate for an identifier like HB 2014 or SCR 1049, else None."""
    m = _AZ_ORIGIN_RE.match((identifier or "").strip())
    if not m:
        return None
    return "house" if m.group(1).upper() == "H" else "senate"


def _az_chamber_ordinal(note: str, origin: str, ordinal: float) -> float:
    m = _AZ_CHAMBER_NOTE_RE.match(note)
    if not m:
        return ordinal
    base = 0.01 if m.group(1).lower() == origin else 0.03
    if not m.group(2).startswith("-"):  # an Engrossed Version
        return base + 0.01
    rest = note[m.end() :]
    if _AZ_FLOOR_AMEND_RE.search(rest) and "engrossed" in rest.lower():
        return base + 0.015
    return base


# OPEN-554: a state orders its versions by date (Alabama, OPEN-569) or by document number (Georgia, OPEN-602) when its
# file says so (`VERSION_ORDER` in openstates/ddp_states/<st>.py; the evidence is there).
_DATE_ORDERED_JURISDICTIONS = frozenset(
    name for name, order in state_values_by_name("VERSION_ORDER").items() if order == "date"
)

# OPEN-593 (moved by OPEN-554): a state whose amendment-stage versions are ordered by document number alone.
_AMENDMENTS_BY_DOCUMENT_NUMBER = frozenset(
    name for name, flag in state_values_by_name("AMENDMENTS_BY_DOCUMENT_NUMBER").items() if flag
)
_DOCUMENT_ORDERED_JURISDICTIONS = frozenset(
    name for name, order in state_values_by_name("VERSION_ORDER").items() if order == "document"
)

# OPEN-788: a state whose version's place depends on the bill's origin chamber, not only on its note (Oklahoma's
# `version_rank` in openstates/ddp_states/ok.py, Missouri's in mo.py (OPEN-806), Hawaii's in hi.py (OPEN-804): (stage,
# ordinal), or None to keep note_stage's).
_VERSION_RANKS = state_values_by_name("version_rank")


def version_sort_key(
    note: str,
    date: typing.Optional[str],
    urls: typing.Iterable[typing.Optional[str]] = (),
    origin: typing.Optional[str] = None,
    jurisdiction_name: typing.Optional[str] = None,
) -> tuple:
    """
    Rank a single version (by its note + date) for chronological ordering, without ever
    trusting the order it was returned from the DB in. See this module's own docstring for
    the audit this encodes.

    Returns (stage, date-or-empty, ordinal, document-number). The macro stage always comes from the note (see
    note_stage()) -- a real, parseable date is used only as a same-stage tiebreaker, not as
    an override of the note-based stage. This matters for jurisdictions that could have a mix
    of dated and undated versions on the same bill (US federal is ~99.4% dated, not 100%):
    letting a date win globally would make any dated version sort before every undated one
    regardless of true chronology. Confirmed via audit: 0% of state-jurisdiction versions
    (FL/MI/AZ/UT/WA/VA) have a date at all, so this tiebreaker is inert for them and they rely
    entirely on the note-based stage; US federal's real dates resolve same-stage ordering
    (e.g. "Reported to Senate" vs. "Engrossed in Senate") more precisely than the ordinal
    heuristic alone would.

    `urls` (the version's link URLs) adds a last element that only matters between versions that
    tie on everything above: Arizona's (OPEN-368) or Georgia's (OPEN-602) document number, or
    Virginia's LIS file number (OPEN-593), 0 for everything else.
    `origin` (origin_chamber() of the bill's identifier) places Arizona's Engrossed Versions
    among its action documents (OPEN-382); it changes nothing for any other note.
    `jurisdiction_name` (`Jurisdiction.name`): for a state in `_DATE_ORDERED_JURISDICTIONS`
    (Alabama, OPEN-569) the date comes first, (date-or-empty, stage, ordinal, document-number);
    for one in `_DOCUMENT_ORDERED_JURISDICTIONS` (Georgia, OPEN-602) the site's document number
    comes first, (document-number, stage, date-or-empty, ordinal). For Virginia an amendment-stage
    version's ordinal is 0, so its LIS file number orders it (OPEN-593).
    For a state with a `version_rank` (Oklahoma, OPEN-788; Missouri, OPEN-806) its (stage, ordinal) replaces note_stage's
    for the notes it names, given `origin`.

    A note matching none of the known patterns returns stage STAGE_UNKNOWN -- the caller
    excludes those versions from the diff lineage entirely rather than guessing a position for
    them.
    """
    stage, ordinal = note_stage(note)
    if origin:
        ordinal = _az_chamber_ordinal(note, origin, ordinal)
    version_rank = _VERSION_RANKS.get(jurisdiction_name)
    if version_rank is not None:
        stage, ordinal = version_rank(note, origin) or (stage, ordinal)
    if jurisdiction_name in _AMENDMENTS_BY_DOCUMENT_NUMBER and stage == STAGE_AMENDMENT:
        ordinal = 0.0  # OPEN-593: Virginia's substitutes are ordered by LIS file number alone
    has_date = bool(date) and bool(_DATE_RE.match(date))
    if jurisdiction_name in _DATE_ORDERED_JURISDICTIONS:
        return (date if has_date else "", stage, ordinal, document_number(urls))
    if jurisdiction_name in _DOCUMENT_ORDERED_JURISDICTIONS:
        return (document_number(urls), stage, date if has_date else "", ordinal)
    return (stage, date if has_date else "", ordinal, document_number(urls))


# OPEN-224: some jurisdictions attach short procedural documents (a governor's specific
# recommended changes, a conference committee's own report, a numbered floor-amendment
# excerpt) to the same diff lineage as real full-text bill versions. note_stage() correctly
# classifies these into a real stage today -- that part of the OPEN-34 audit is unaffected --
# but diffing a full bill against one of these produces a degenerate "replace the whole
# document" hunk in both directions (the full text reads as entirely deleted, then entirely
# re-added at the next real version), not a real changelog. On a large bill this is not just
# noise: the resulting single-hunk diff can exceed a downstream consumer's own prompt-length
# ceiling outright (confirmed live, AGENTS-87 -- Virginia's HB 30 special-session budget bill,
# three of five real transitions failed this way).
#
# This is deliberately NOT a keyword regex over "amendment" or "governor" applied globally --
# the ticket's own named regression case (Virginia's "Amendment in the Nature of a Substitute")
# is a full replacement text and must keep its diff, and this module's own real-data audit
# found the same is true of "Governor Substitute" (median ~11.7K chars, max ~686K across 152
# real Virginia documents) and "Conference Report Substitute" (median ~13.7K, max ~752K across
# 262) -- both stay out of this table. Exclusion is evidence-led per jurisdiction, per OPEN-34's
# own audit standard: each entry below is backed by a real document-length distribution pulled
# from this project's own archived data (2026-09-01), not asserted from the note text alone.
#
# Length-distribution audit query (against ddp_bill_version_document, is_error=false,
# non-empty raw_text; run per jurisdiction against the full real archived population, not a
# sample):
#   SELECT b.classification, d.version_note, length(d.raw_text)
#   FROM ddp_bill_version_document d
#   JOIN opencivicdata_bill b ON b.id = d.bill_id
#   JOIN opencivicdata_legislativesession ls ON b.legislative_session_id = ls.id
#   JOIN opencivicdata_jurisdiction j ON ls.jurisdiction_id = j.id
#   WHERE j.name = '<jurisdiction>' AND d.is_error = false
#     AND d.raw_text IS NOT NULL AND d.raw_text != '';
# Grouped by (version_note), restricted to notes note_stage() classifies into a real stage
# (STAGE_UNKNOWN notes are already excluded and out of this ticket's scope).
#
# Separate AC5 measured-reduction query (this one DOES select diff_from_previous_version --
# the length-distribution query above intentionally does not, since document length and
# current diff status are two different questions):
#   SELECT j.name, d.version_note, count(*)
#   FROM ddp_bill_version_document d
#   JOIN opencivicdata_bill b ON b.id = d.bill_id
#   JOIN opencivicdata_legislativesession ls ON b.legislative_session_id = ls.id
#   JOIN opencivicdata_jurisdiction j ON ls.jurisdiction_id = j.id
#   WHERE j.name = '<jurisdiction>' AND d.diff_from_previous_version IS NOT NULL
#     AND d.version_note IN (<the exact notes below>)  -- or ~* the Utah pattern below
#   GROUP BY j.name, d.version_note;
# This is the query the 380/563 counts further down actually come from.
#
# Virginia (exact note match -- a small, closed, real vocabulary, not a pattern). Every real
# instance audited of each note below (not just its max) stays under Virginia's own real
# full-text medians -- "Introduced"/"Enrolled"/"Chaptered" each median in the 7,700-13,300 char
# range across thousands of real documents, and Michigan's own "Conference Report" separately
# confirms these note types CAN legitimately be full documents (see is_procedural_document()'s
# own docstring) -- this is a claim about these specific note values in Virginia specifically,
# not a universal "short text is always a stub" rule; a genuinely tiny real bill (e.g. a
# one-line resolution) is not claimed to be ruled out by size alone anywhere in this module:
#   "Conference Report"           n=168  min=622    median=750     max=6,381
#   "Governor's Recommendation"   n=180  min=77     median=222     max=2,655
#   "Governor's Recommendations"  n=1    (979 chars -- the same document type; VA's own data
#                                          has both a singular and a plural spelling across
#                                          sessions)
#   "Governor's Veto Explanation" n=31   min=457    median=1,374   max=2,052
# Population: 380 rows, {bill} classification only for this note set in the real archived data
# (no resolutions carry these four notes).
#
# Measured reduction (AC5), same audit date, via the second query above: 380 of these Virginia
# rows currently carry a non-null diff_from_previous_version today. Merging this fix does NOT
# retroactively touch those already-persisted rows by itself -- it only changes behavior going
# forward: a newly archived bill of this shape gets the correct (no-diff) treatment
# immediately, and each of the 380 becomes None only once someone runs `recompute-diff-order
# --commit` for Virginia, a separate, already-existing, explicitly-authorized operator action
# (out of this PR's scope by design). Until that recompute runs, the AC5 count query above will
# keep returning 380/563, not 0 -- that is expected, not a sign this fix didn't work.
# OPEN-554: each state's entry lives in its own file, openstates/ddp_states/<st>.py (`PROCEDURAL_DOCUMENT_NOTES`), keyed here by
# the state's name as before.
_PROCEDURAL_DOCUMENT_NOTES: dict[str, frozenset] = state_values_by_name("PROCEDURAL_DOCUMENT_NOTES")

# Utah (pattern match -- an open-ended numbered series, not a fixed list): "House Amendment
# <N>" / "Senate Amendment <N>" are each a short excerpt of just that specific amendment, not
# a re-typeset full bill -- the naming convention itself ("Amendment <ordinal>", the same
# ordinal-suffix shape note_stage()'s own extract_ordinal() already generalizes across
# jurisdictions rather than enumerating every observed number) is the structural reason a
# pattern is the right representation here, not just an extrapolation from the sizes audited.
# The size evidence confirms the convention holds in practice: real archived data (2026-09-01),
# every one of the 11 real note values audited (House 1-6, Senate 1-5) stays under 13,300 chars
# at its real observed max (most under 2,000), against "Introduced"/"Enrolled" maxes over
# 980,000 chars in the same jurisdiction. Population: 547 {bill} + 16 {resolution}/
# {"concurrent resolution"}/{"joint resolution"} = 563 rows total, confirmed the pattern holds
# for the resolution rows too (max 1,870 chars among them) rather than assumed -- 16 of 16
# resolution rows also currently carry a non-null diff, same as the bill rows. `[1-9]\d*`
# (not `\d+`) deliberately excludes a hypothetical "Amendment 0" -- real amendment numbering
# starts at 1 in every real note observed; this is a zero-cost tightening, not a behavior this
# module has evidence would otherwise occur. Measured reduction (AC5), via the second query
# above: all 563 of these currently carry a non-null diff today -- same prospective-only
# reasoning as Virginia's own comment above (merging alone does not retroactively touch them;
# a separate, explicitly-authorized `recompute-diff-order --commit` for Utah does).
# Utah, OPEN-350: two more families, on DIFFERENT evidence -- one is not proof of the other.
# Audited 2026-10-13 against the local database (notes/open349-utah-version-document-audit-
# 20261013.md in ddp-open-states, OPEN-349); all 762 distinct Utah notes fall into seven families.
#   (1) "Amended Excerpts <M/D/YYYY H:MM:N>" -- 368 documents (PDF only), each a short excerpt of
#       one floor amendment. This one meets the length standard above: max 10,114 chars among
#       bills and 1,902 among the other classifications, against full-text maxes near 1,000,000;
#       all 368 sit in a bill that also has a full "Amended <same timestamp>" version.
#   (2) "Comparison to Original Bill" / "Comparison to Sub #N" -- redline PDFs, 2,239 documents.
#       These are NOT short (median 21,731 chars against 23,035 for "Substitute #N"; max 593,304
#       against 1,018,572), so the length standard cannot justify excluding them and this entry
#       does not claim it does. It rests on content and ordering instead (Ramon, 2026-10-13):
#       in a 220-bill sample the extracted text holds a median 29% of the old-only wording, 43%
#       of the new-only wording and 58% of the unchanged wording of the two real drafts, with
#       nothing marking which is which, so it is neither version of the bill; and its sort key
#       collides with "Substitute #N" and "House Amendment N" (all (1, '', N)), which left its
#       place in the lineage to whatever order the database returned -- 471 of Utah's 1,021
#       bills had versions tied that way, none once both families are excluded. The sample
#       covered only "Comparison to Original Bill" (1,164 of the 2,239); "Comparison to Sub #N"
#       (1,075) and the 67 resolution documents rest on the shared naming and form and on the
#       collision, not on a content sample.
# The full-text "Amended <ts>" (736 documents, min 1,193 chars) shares a prefix with (1) and is
# deliberately NOT matched: the pattern requires the literal word "excerpts". "Introduced",
# "Substitute #N" and "Enrolled" are not matched either. Checked against every distinct Utah
# note: 7 comparison notes (2,239 documents) and 367 excerpt notes (368 documents) match, no
# note matches both, and none naming "comparison" or "excerpt" is missed. Measured reduction
# (AC5): all 2,239 + 368 = 2,607 of these carry a non-null diff today -- same prospective-only
# reasoning as above (a recompute for Utah, OPEN-351, is what changes stored diffs).
# Virginia (pattern match -- committee/subcommittee/legislator names, not a fixed list; OPEN-246):
# a floor amendment offered by a specific committee ("Courts of Justice Amendment"), subcommittee
# ("Subcommittee #2 Subcommittee Amendment"), or individual legislator ("Senator Sturtevant
# Amendments") is a short excerpt of just that one proposed change, not a re-typeset full bill --
# the same shape as Utah's numbered "House/Senate Amendment <N>" above, just named by committee or
# person instead of by number, and just as open-ended: 133 distinct committee/subcommittee/
# legislator names observed in the real archived data (2026-09-02), with a new one appearing every
# time committee assignments or membership change, so a fixed list would need re-auditing every
# General Assembly session. This is the exact family OPEN-224's own original problem description
# named as in-scope ("Commerce and Labor Amendment") but never actually added to the exclusion
# table -- SB 542's "Courts of Justice Amendment" (9,229 chars) diffed against the full prior bill
# into a 660,649-char degenerate diff, which directly triggered a real MLX worker wedge
# (AGENTS-95, surfaced by AGENTS-91's 1,000-bill dry run).
#
# The size evidence confirms this is a distinct, clean population from real full-text versions --
# not just an extrapolation from the naming convention alone: every one of the 133 real note values
# ending in "Amendment"/"Amendments" tops out at 11,098 chars at its single largest real instance --
# two full orders of magnitude below Virginia's own real full-text maxes (904,438-980,000+ chars,
# see the Substitute/Utah entries in this same table). This population's max (11,098) does sit
# inside the low end of Virginia's real full-text *median* range (7,700-13,300 chars) -- medians
# describe the typical case, not the floor, and a real full document routinely runs far larger
# (see below); the max-vs-max comparison is the one that cleanly separates the two populations,
# not max-vs-median. Critically, this module's own audit also found a lexically-adjacent but
# structurally different family that must NOT be excluded: "<Committee/Legislator> Substitute"
# (e.g. "Courts of Justice Substitute", "Commerce and Labor Substitute") IS real committee-
# substitute full text -- median 6,810 chars, max 904,438 chars across 5,968 real documents, the
# same shape as "Governor Substitute" and "Conference Report Substitute" above. The two families
# share a committee-name prefix but never overlap in the real archived data (nothing matches both
# "amendment" and "substitute" in the same note) -- this pattern only matches notes ending in the
# word "Amendment"/"Amendments" specifically, which is what keeps it from also catching
# "Substitute" or the ticket's own named regression case, "Amendment in the Nature of a
# Substitute" (ends in "Substitute", not "Amendment"). Also checked directly against the real
# archived data: no bare, non-committee/non-legislator-prefixed note ending in "Amendment"/
# "Amendments" (e.g. a hypothetical "Constitutional Amendment") exists anywhere in Virginia's
# real archive as of this audit -- every one of the 133 real values has a real committee,
# subcommittee, or legislator name as its prefix. Population: 1,447 real documents (1,436 {bill}
# + 11 {constitutional amendment} -- a bill *classification*, not a note value; their real notes
# are still committee/legislator-prefixed, e.g. "Senator Jordan, Emily M. Amendment"), all of
# which currently carry a non-null diff_from_previous_version today -- same prospective-only
# reasoning as the Virginia/Utah entries above (merging this fix does not retroactively touch
# them; a separate, explicitly-authorized `recompute-diff-order --commit` for Virginia does).
# North Carolina, OPEN-366: "A<n>: Amendment" (and the un-numbered "A: Amendment") is the stamp
# page of an adopted amendment, not the amendment text and not a bill: e.g. "ADOPTED A49 ADOPTED
# A49 ... The official copy of this document, with signatures and vote information, is available
# in the House Principal Clerk's Office". Audited 2026-10-15 against the local database (OPEN-352 /
# OPEN-359): 54 documents, 3 to 424 characters (52 numbered, 212 to 424; and 2 un-numbered "A:
# Amendment", whose whole text is "A" and "A A"), all 54 carry a non-null diff today, against North
# Carolina's real full texts ("Filed" 228 / 3,701 / 536,800 and "Edition 2" 320 / 7,495 /
# 1,519,891, min / median / max). The lexically adjacent "A<n>: <code>" family (e.g. "A1:
# ABR-9-V-1", 206 documents) is amendment text, not the bill; it matches no stage rule and so is
# already outside the lineage. OPEN-366 left it in; OPEN-594 (below) takes it out too, because a
# version with no stage now counts as full text. Same prospective-only reasoning as the entries
# above: a recompute for North Carolina is a separate, explicitly authorised step.
# Alabama, OPEN-367 (pattern match -- committee and legislator names, not a fixed list): a
# committee amendment ("Judiciary 1st Amendment"), a floor amendment offered by a member
# ("Robbins 2nd Amendment Offered") and a reported-out committee amendment ("Reported Out
# Committee House of Origin-- Committee 1st Amendment") are each a short excerpt of just that one
# change, the same shape as Virginia's OPEN-246 family just below. Audited 2026-10-15 against the
# local database (OPEN-352 / OPEN-360): the 25 distinct notes ending in "Amendment" or
# "Amendment Offered" are 45 documents, all {bill}, min 53 / median 332 / max 2,519 characters,
# against Alabama's real full texts of "Introduced" 360 / 3,841 / 176,574, "Enrolled" 768 / 3,978 /
# 183,904 and "Engrossed" 1,068 / 6,295 / 179,596 (min / median / max). Compared max against max, as
# OPEN-246 did. All 45 carry a non-null diff today (the AC5 query above), none a real diff: they
# were diffed against the whole bill. The lexically adjacent family that must stay in is
# "<Committee> 1st Substitute" / "... Substitute Offered" / "... Engrossed Substitute Offered":
# 14 distinct notes, 21 documents, 1,929 to 183,379 characters, real full replacement text. None
# of them ends in "Amendment" or "Amendment Offered", so the end anchor keeps them out. Same
# prospective-only reasoning as the entries above: a recompute for Alabama is a separate,
# explicitly authorised step.
# Alabama, OPEN-525: the floor-motion papers (_AL_FLOOR_MOTION_PAPER, "<Member> motion to Adopt -
# Adopted Roll Call N", "Motion to Table - Adopted Voice Vote") are not bill versions either. Read on
# the Mac replica (2026-11-10, 2026rs): 453 documents. 256 are amendments, short line-edit
# instructions ("Replace line 209 on page 8 with the following: ...", "Remove line 113 ...", "On page
# 18 after line 491, insert ..."; median 431, max 16,620 characters). 197 are substitutes, full bill
# texts ("A BILL TO BE ENTITLED AN ACT ...", "SYNOPSIS: ...", "Substitute for SB146 ..."; median
# 7,457, max 181,915). The note cannot tell the two apart (both kinds have every outcome shape), so
# the rule takes both out: 185 of the 197 substitutes were adopted and their text is carried by the
# bill's own real versions (after Alabama's pre-diff cleaner, OPEN-372, on both sides, 181 have at
# least 90% of their six-word phrases in one of them; the other 4 have 83 to 88%: three of those bills
# had a further floor amendment or substitute adopted, and HB 306 has no version after Introduced),
# and the other 12 were tabled, so never became the bill (the same call as Arizona's failed
# amendments, OPEN-379). The documents stay archived with their text. Before this, all 453 were
# versions with no stage (15.5% of Alabama's versions). Prospective only, as above.
# OPEN-379: Arizona publishes a document for every floor amendment offered, including ones the
# chamber rejected ("SENATE - Ortiz flr amend (ref Bill) failed"). A failed amendment never became
# part of the bill, so it is not a version of it (Ramon, 2026-10-15). Read on the production
# replica (2026-10-16): 332 distinct Arizona notes, exactly 11 contain "fail" and all 11 have this
# shape (16 versions); none of the adopted/passed notes, the engrossed or chaptered versions does.
# Prospective only: an Arizona recompute-diff-order applies it to stored diffs (OPEN-381). The
# document stays archived with its raw_text; only its diff, and its place as a baseline, go.
# OPEN-428: the scraper may append " (<document id>)" to a note that repeats inside one bill
# (upstream openstates-scrapers #5807: the second and later copies, the first stays plain), so the
# same sponsor's second failed amendment reads "... failed (541432)". The optional group lets that
# shape through; nothing else changes. On the 2026-10-22 live sample 213 of 1,480 version notes
# (430 bills) carried the suffix and it moved neither note_stage() nor version_sort_key() for any of
# them; this pattern was the only end-anchored matcher it broke.
# OPEN-554: each state's entry lives in its own file, openstates/ddp_states/<st>.py (`PROCEDURAL_DOCUMENT_NOTE_PATTERN`), keyed here by
# the state's name as before.
_PROCEDURAL_DOCUMENT_NOTE_PATTERNS: dict[str, "re.Pattern"] = state_values_by_name("PROCEDURAL_DOCUMENT_NOTE_PATTERN")


def is_procedural_document(jurisdiction_name: str, note: str) -> bool:
    """
    True if `note` (for `jurisdiction_name`) is a known short procedural document rather than
    a full bill text, per the real-data audit above (or, for Utah's redline comparisons, a
    document that is not a clean version of the bill, OPEN-350; see its comment) -- callers
    should treat this the same way they already treat `note_stage(note)[0] == STAGE_UNKNOWN`
    (OPEN-34): the version neither takes a diff nor becomes the baseline the next version
    diffs against, so the diff lineage closes over the gap and the next real version diffs
    against the previous real one.

    Deliberately separate from `note_stage()` rather than folded into it: `note_stage()` has
    other callers (this module's own docstring names api-v3's bill-detail endpoint) that want
    a jurisdiction-independent stage classification for display/ordering purposes regardless
    of diffability, and "Conference Report" genuinely IS a real STAGE_CHAMBER_PASSAGE document
    in Michigan (median ~1.5M chars there) while being a short stub in Virginia -- collapsing
    that into a single global function would force every caller to become jurisdiction-aware
    whether or not diff-lineage exclusion is what they actually need.

    `jurisdiction_name` is matched exactly against `Jurisdiction.name` (e.g. "Virginia"), the
    same value `archive_bill_versions`/`recomputed_diffs_for_documents` already read and pass
    to `apply_prediff_cleaning` -- no new lookup needed at any call site.
    """
    if note in _PROCEDURAL_DOCUMENT_NOTES.get(jurisdiction_name, ()):
        return True
    pattern = _PROCEDURAL_DOCUMENT_NOTE_PATTERNS.get(jurisdiction_name)
    if pattern is not None and pattern.match(note):
        return True
    return False


# OPEN-498: Massachusetts files agency reports, legislative orders, amendment papers and messages under
# the same bill numbers as real bills, and gives their document the same note ("Bill Text"), so the note
# rules above cannot see them. For such a state the BILL's title says what it is: a document is a real
# bill version only if its bill's title opens like a measure (An Act, a Resolve, Resolutions, a Joint
# Resolution, a Proposal for a constitutional amendment). Matched exactly against `Jurisdiction.name`,
# like the note tables. Measured on the Mac replica of the 194th session (2026-11-06): 3,267 of 11,872.
# OPEN-554: each state's entry lives in its own file, openstates/ddp_states/<st>.py (`REAL_MEASURE_TITLE_PATTERN`), keyed here by
# the state's name as before.
_REAL_MEASURE_TITLE_PATTERNS = state_values_by_name("REAL_MEASURE_TITLE_PATTERN")


# OPEN-564: a paper whose TEXT is a line edit to the bill, not the bill. Alabama names the paper behind a committee
# or floor action after the action ("Reported Out of Committee Second House"), so the note cannot tell a full
# substitute from a short amendment; the text can. An amendment opens with its instruction ("Replace line 54 on page
# 2 with the following: ...") and has no enacting clause; a full substitute is a whole bill ("A BILL TO BE ENTITLED
# AN ACT ... BE IT ENACTED BY THE LEGISLATURE OF ALABAMA"). Measured on the Mac replica (2026-11-15, all 3,379
# Alabama documents with text): 548 open with an instruction and have no enacting clause; 547 of them are already
# not-a-version by their note (the floor-motion papers above) and the 548th is HB 248's "Replace line 54", the one
# short amendment among the 200 papers the 11-15 archive re-saved as versions. The other 199 are full substitutes,
# which stay versions (Ramon, 2026-11-15). The only versions without an enacting clause, HB 511's Engrossed and
# Enrolled constitutional amendment, do not open with an instruction and stay versions.
# OPEN-554: each state's entry lives in its own file, openstates/ddp_states/<st>.py (`AMENDMENT_TEXT_OPENING`), keyed here by
# the state's name as before.
_AMENDMENT_TEXT_OPENING = state_values_by_name("AMENDMENT_TEXT_OPENING")
_ENACTING_CLAUSE_RE = re.compile(r"\bbe\s+it\s+(?:enacted|resolved)\b", re.IGNORECASE)


def is_amendment_text(jurisdiction_name: str, raw_text: typing.Optional[str]) -> bool:
    """
    True if a document's extracted text is a line edit to the bill rather than a bill text, by the per-state rule
    in `_AMENDMENT_TEXT_OPENING` (OPEN-564). Such a document is not a bill version (`is_bill_version`) and, like a
    procedural paper, neither takes a diff nor becomes the next version's baseline. No text, or a state without a
    rule, is never a reason to exclude a document.
    """
    pattern = _AMENDMENT_TEXT_OPENING.get(jurisdiction_name)
    return bool(
        pattern is not None
        and raw_text
        and pattern.match(raw_text)
        and not _ENACTING_CLAUSE_RE.search(raw_text)
    )


def is_bill_version(
    jurisdiction_name: str,
    note: str,
    bill_title: typing.Optional[str] = None,
    raw_text: typing.Optional[str] = None,
) -> bool:
    """
    The value the archiver stores in `BillVersionDocument.is_bill_version` (OPEN-492): False for a
    paper this module's per-state rules call procedural (`is_procedural_document()`), True for
    everything else. This is the ONE definition of "is it a real bill version"; readers (api-v3,
    OPEN-422) read the stored answer instead of holding a list of their own. It says nothing about
    the stage: a version `note_stage()` cannot place is still True here, and readers keep
    handling that on their own.

    OPEN-498: for a state in `_REAL_MEASURE_TITLE_PATTERNS` the bill's own title is checked too, because
    there the note cannot tell a report from a bill. An empty or missing `bill_title` is never a reason
    to exclude a document (a caller that has no title keeps the OPEN-492 answer).

    OPEN-564: for a state in `_AMENDMENT_TEXT_OPENING` the document's own text is checked too
    (`is_amendment_text`); a caller with no text keeps the answer from the note and title.
    """
    if is_procedural_document(jurisdiction_name, note):
        return False
    if is_amendment_text(jurisdiction_name, raw_text):
        return False
    pattern = _REAL_MEASURE_TITLE_PATTERNS.get(jurisdiction_name)
    if (
        pattern is not None
        and bill_title
        and bill_title.strip()
        and not pattern.match(bill_title)
    ):
        return False
    return True


# Where `relabel-non-bills` keeps the label a bill had before it changed it (so --undo can put it back).
# The bill import keeps this key on a re-scrape (OPEN-518): a scraper never sends it, and without that the
# import would replace extras with the scraper's and lose the saved label.
ORIGINAL_CLASSIFICATION_KEY = "ddp_open517_original_classification"

# OPEN-541: set on a bill by `reverse-action-order --commit` once its stored actions are oldest first, so a
# second run skips it (no double reversal) and `--undo` knows which bills to put back. Kept on a re-scrape for the
# same reason as the key above: the pass runs after the scraper already stores that state oldest first, so a
# re-scraped bill is still oldest first and must still be skipped.
ACTIONS_REVERSED_KEY = "ddp_open541_actions_reversed"


# OPEN-517: what a Massachusetts non-bill item IS, so `os-text-extract relabel-non-bills` can store a
# label that says so instead of "bill" / "proposed bill" (Ramon, 2026-11-09: "a label per kind"; relabel,
# never delete). Only items the OPEN-498 title rule above already calls not-a-bill are ever given a kind,
# so a real measure can never be relabelled by these rules. Checked in this order, first match wins;
# whatever matches nothing is a report (agency filings: inspection packages, audited statements,
# budgets, plans, legislative recommendations). Committee study orders whose title is only their subject
# ("Golf Carts", "rail and transit") are recognised by their action "Order reported favorably by
# committee". Measured on the Mac replica of the 194th session (2026-11-09): 3,297 items fail the title
# rule; see the OPEN-517 dry run for the counts per kind.
# NON_BILL_AMENDMENT ("amendment": amendment papers, not relabelled; OPEN-509 moves them) lives in
# openstates.ddp_states (OPEN-554) so a state's rules can name it; imported above.
# OPEN-554: each state's entry lives in its own file, openstates/ddp_states/<st>.py (`NON_BILL_KIND_RULES`), keyed here by
# the state's name as before.
_NON_BILL_KIND_RULES = state_values_by_name("NON_BILL_KIND_RULES")
# OPEN-554: each state's entry lives in its own file, openstates/ddp_states/<st>.py (`NON_BILL_STUDY_ORDER_ACTION`), keyed here by
# the state's name as before.
_NON_BILL_STUDY_ORDER_ACTION = state_values_by_name("NON_BILL_STUDY_ORDER_ACTION")
# OPEN-554: each state's entry lives in its own file, openstates/ddp_states/<st>.py (`NON_BILL_OTHER_ORDER`), keyed here by
# the state's name as before.
_NON_BILL_OTHER_ORDER = state_values_by_name("NON_BILL_OTHER_ORDER")


def non_bill_kind(
    jurisdiction_name: str,
    bill_title: typing.Optional[str],
    action_descriptions: typing.Iterable[str] = (),
) -> typing.Optional[str]:
    """
    OPEN-517: the label for a bill row that is not a bill: "communication", "study request", "order"
    or "report"; `NON_BILL_AMENDMENT` for an amendment paper; None for a real measure, a blank or
    missing title, or a state without a title rule (the same fail-open cases as `is_bill_version`).
    """
    kinds = _NON_BILL_KIND_RULES.get(jurisdiction_name)
    if kinds is None or not bill_title or not bill_title.strip():
        return None
    if is_bill_version(jurisdiction_name, "", bill_title):
        return None
    for kind, pattern in kinds:
        if pattern.search(bill_title):
            return kind
    action_rule = _NON_BILL_STUDY_ORDER_ACTION[jurisdiction_name]
    if any(action_rule.match(a or "") for a in action_descriptions):
        return "study request"
    if _NON_BILL_OTHER_ORDER[jurisdiction_name].search(bill_title):
        return "order"
    return "report"
