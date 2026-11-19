"""
Alabama's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re
import typing


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# OPEN-372: Alabama-only text cleaning applied to prior_text/raw_text immediately before
# difflib.unified_diff() (never touching the stored raw_text, and never applied to any other
# jurisdiction). Measured 2026-10-15 on the production replica (OPEN-352 / OPEN-360), over every
# Introduced -> Engrossed (232) and Engrossed -> Enrolled (155) pair:
#
# 1. "Introduced" carries a "SYNOPSIS:" block (916 of 1,507 documents, always before the "A BILL"
#    line) that "Engrossed" and "Enrolled" do not, so the synopsis showed up as removed text.
# 2. The second line of every version is a tracking code that changes per version ("JPKGJNN-1",
#    "DGC49NN-2", "DGC49NN-3"), and the title block changes shape between versions ("A BILL / TO BE
#    ENTITLED / AN ACT" becomes "Enrolled, An Act,"). Each shape occurs at most once per document.
# 3. Every Engrossed document (232 of 232), and no other kind, ends with a legislative-history
#    block: a "Senate" or "House of Representatives" line, the "Read for the first time and
#    referred ..." readings, "Yeas / Nays / Abstains", and the Clerk's or Secretary's name. It
#    added about 14 lines to every Introduced -> Engrossed diff and removed them again in every
#    Engrossed -> Enrolled diff, more noise than the synopsis.
#
# Result over the same pairs: changed lines 30,232 -> 22,658 (Introduced -> Engrossed; median per
# pair 60 -> 26) and 10,322 -> 7,991 (Engrossed -> Enrolled; median 29 -> 13), never larger for
# any pair. The remainder is real amendment text, so no whitespace or re-wrapping step is done.
_AL_TRACKING_LINE_RE = re.compile(r"[A-Z0-9]{4,}-\d+")


_AL_TITLE_LINES = frozenset(
    {"A BILL", "TO BE ENTITLED", "AN ACT", "Enrolled, An Act,"}
)


# The title lines were never found past line 20 once the synopsis is removed (4,501 occurrences).
_AL_TITLE_REGION_LINES = 25


_AL_HISTORY_MARKER_RE = re.compile(r"Read for the first time")


_AL_CHAMBER_LINES = frozenset({"Senate", "House of Representatives"})


# All 232 history blocks run 10 to 14 non-blank lines from the marker to the end.
_AL_HISTORY_MAX_TAIL_LINES = 20


# OPEN-529: three more kinds of noise, measured 2026-11-10 on the Mac replica over all 917 stored
# Alabama comparisons (every one a real bill version; the recompute path reproduces all 917 stored
# diffs exactly before this change):
#
# 4. Every Enrolled text (616 of 616, and no other version) ends with the signature and
#    certification block: two "____" lines for the Speaker and the President of the Senate, the
#    bill number, the Clerk's or Secretary's certificate ("I hereby certify that the within Act
#    originated in ..."), dated chamber actions ("Senate 09-Apr-26 Adopted") and "By: Senator
#    ...", 10 to 18 non-blank lines. It was 5,933 changed lines, and it made 364 Enrolled
#    comparisons 95% or more noise. Cut from the first "____" line immediately followed by an
#    exact signature title, only within the last lines.
# 5. The front header ("HB503", the tracking code, "By Representative ...", "RFD: Judiciary",
#    "First Read: 24-Feb-26", "PFD: ..."): committee substitutes and "Reported Out of Committee"
#    versions do not print it, and "RFD:" changes between versions, so it showed up as removed or
#    added text. Cut only when the first line is a bare bill number and an "RFD:", "First Read:"
#    or "PFD:" line follows within the first lines; everything up to the last of those is header
#    (the sponsor list wraps onto up to 15 lines; checked over all 2,355 versions that start with
#    a bill number).
# 6. The same table line printed with a different number of spaces (the budget bills, SB 146 and
#    HB 238), so each line is stripped and runs of spaces inside it become one space.
_AL_SIGNATURE_RULE_RE = re.compile(r"_{10,}")


_AL_SIGNATURE_TITLES = frozenset(
    {
        "Speaker of the House of Representatives",
        "President and Presiding Officer of the Senate",
    }
)


# All 616 signature blocks run 10 to 18 non-blank lines from the first "____" line to the end.
_AL_SIGNATURE_MAX_TAIL_LINES = 25


_AL_BILL_NUMBER_LINE_RE = re.compile(r"(?:HB|SB|HJR|SJR|HR|SR)\d+")


_AL_HEADER_MARKER_RE = re.compile(r"(?:RFD|First Read|PFD):.*")


# The last header marker was never past line 20.
_AL_HEADER_REGION_LINES = 40


def _clean_al_text(text: str) -> str:
    """Alabama-only cleaner, see the block comment above: the front header, the synopsis block,
    the per-version tracking code, the title block, the legislative-history block and the
    Enrolled signature block are removed, and spacing inside a line is ignored.

    Every cut is anchored on the structure measured in the corpus, so text that merely resembles
    one is kept: the synopsis only when an "A BILL" line follows it; the title lines only in the
    first lines of the document; the history block only when the marker is immediately preceded
    by an exact chamber line and sits within the last few lines (a line that starts "Read for the
    first time" anywhere else is bill text and is left alone); the header only when the first
    line is a bare bill number and a header marker line follows it closely; the signature block
    only when a "____" line is followed by an exact signature title within the last few lines."""
    lines = [" ".join(ln.split()) for ln in text.split("\n")]

    # The header and signature anchors are judged on the document as printed, before any other
    # rule removes a line; `top` keeps the positions the older rules below are measured in.
    top = 0
    if lines and _AL_BILL_NUMBER_LINE_RE.fullmatch(lines[0]):
        markers = [
            i
            for i, ln in enumerate(lines[:_AL_HEADER_REGION_LINES])
            if _AL_HEADER_MARKER_RE.fullmatch(ln)
        ]
        if markers:
            top = markers[-1] + 1
    for i in range(top, len(lines) - 1):
        if (
            _AL_SIGNATURE_RULE_RE.fullmatch(lines[i])
            and lines[i + 1] in _AL_SIGNATURE_TITLES
            and sum(1 for rest in lines[i:] if rest) <= _AL_SIGNATURE_MAX_TAIL_LINES
        ):
            lines = lines[:i]
            break
    lines = lines[top:]

    synopsis = [i for i, ln in enumerate(lines) if ln.strip() == "SYNOPSIS:"]
    bill = [i for i, ln in enumerate(lines) if ln.strip() == "A BILL"]
    if synopsis and bill and synopsis[0] < bill[0]:
        lines = lines[: synopsis[0]] + lines[bill[0] :]

    for i, ln in enumerate(lines):
        if (
            i > 0
            and _AL_HISTORY_MARKER_RE.match(ln.strip())
            and lines[i - 1].strip() in _AL_CHAMBER_LINES
            and sum(1 for rest in lines[i - 1 :] if rest.strip())
            <= _AL_HISTORY_MAX_TAIL_LINES
        ):
            lines = lines[: i - 1]
            break

    kept = []
    for i, ln in enumerate(lines, start=top):
        stripped = ln.strip()
        if i == 1 and _AL_TRACKING_LINE_RE.fullmatch(stripped):
            continue
        if i < _AL_TITLE_REGION_LINES and stripped in _AL_TITLE_LINES:
            continue
        kept.append(ln)
    return "\n".join(kept)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Alabama's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _clean_al_text(prior_text), _clean_al_text(raw_text)


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


# OPEN-525: Alabama's floor-motion papers. The scraper adds the paper behind each floor motion on an
# amendment or substitute (the action's amdSubFileUrl) as a version named after the action, e.g.
# "Orr motion to Table - Adopted Voice Vote", "Motion to Adopt - Adopted Roll Call 407". All 453 such
# notes on the Mac replica (2026-11-10, session 2026rs) have this shape and no other state has a note
# of it. Used twice: version_ordering.note_stage() ranks them as amendment papers, and this file's
# PROCEDURAL_DOCUMENT_NOTE_PATTERN below says they are not bill versions (see the evidence there).
_AL_FLOOR_MOTION_PAPER = (
    r"(?:.+ )?motion to (?:adopt|table) ?- ?(?:adopted|lost) (?:roll call [1-9]\d*|voice vote)"
)

PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A(?:.+\bamendment( offered)?|" + _AL_FLOOR_MOTION_PAPER + r")\Z", re.IGNORECASE
)

AMENDMENT_TEXT_OPENING = re.compile(r"\A\s*(?:replace|delete|insert|strike|add|amend)\b", re.IGNORECASE)

# OPEN-569: Alabama names an amendment or substitute version after the action that first carries its
# file ("Tillman 1st Substitute Offered", "Jefferson County Legislation 1st Substitute", "Reported Out
# of Committee Second House"), so the note cannot place it in time: HB 315's Senate committee
# substitute (2026-04-02) and the House floor substitute (2026-03-17) share a stage and an ordinal,
# and both rank before the House's Engrossed bill. From OPEN-569 the Alabama scraper dates every
# version from the action that carries it, so for Alabama the date leads and the note's stage only
# breaks a same-day tie (a floor substitute adopted, then the bill engrossed, the same day). An
# Alabama version without a date ranks first, as every Alabama version did before (all undated).
VERSION_ORDER = "date"  # version_ordering.version_sort_key: the date leads, then the stage


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Alabama's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import handle_alabama_pdf
    # OPEN-530: struck-out words removed, see fulltext/al.py.
    return {"application/pdf": handle_alabama_pdf}
