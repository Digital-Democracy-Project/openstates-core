"""
Virginia's bill-type rule (OPEN-545, PLAN-state-specific-handling contract C3, epic OPEN-536) and its action-label
rule (OPEN-549).

Moved from the broker's read-time reading (ddp-broker-py `apps/ddp-broker/events/bill_events.py`,
`read_classification`, BROKER-478), with the scraper's title check first (below): the broker drops its copy once this
rule is live and Virginia's stored history is relabelled.

Until OPEN-545 Virginia's scraper labelled every item `bill`, resolutions included: 1,579 stored items on the Mac
replica (2026-11-17; HR 692, SR 271, HJ 322, SJ 294, across 2026, 2026S1 and 2027). The bill number says what it is:
HB/SB stay `bill`, HR/SR are `resolution`, HJ/SJ `joint resolution`. Only the `bill` label is rewritten, and a title
naming a constitutional amendment comes first, as in the scraper: such an item is a `constitutional amendment` whatever
its number (20 stored, 18 in 2026 and 2 in 2027, all already labelled so).

The scraper (openstates-scrapers `scrapers/va/bills.py`, `classify_bill`, upstream's rule since #5909) labels new
scrapes the same way; this module is what `os-text-extract relabel va` applies to the stored history, so a re-scrape
of a relabelled bill keeps its label. Keep the two equal.

Virginia's action-label rule (OPEN-549) is `classify_action` below: the broker's read-time reader (ddp-broker-py
`apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`, `_read_virginia`, BROKER-248 and BROKER-481), unchanged.
It only takes a label off, so nothing of `_read_virginia` has to stay in the broker (BROKER-490) once this rule is live
and the stored history is relabelled. On the Mac replica (2026-11-17, sessions 2026, 2026S1 and 2027):

* "Governor's recommendation adopted" (138 lines, all stored under the executive with `executive-signature`) is the
  legislature accepting the governor's amendments after both chambers concurred ("House concurred in Governor's
  recommendation (65-Y 35-N 0-A)"); the bill is signed later, by "Approved by Governor-Chapter ..." (HB 891, 2026).
  It loses `executive-signature`, or the bill reads as signed into law before it is.
* "Subcommittee failed to recommend ..." (11 lines with `committee-failure`, all House) is a subcommittee's
  recommendation to its committee, not the committee's decision: HB 625 and HB 871 (2026) were reconsidered the same
  day and reported. It loses `committee-failure`. The committee's own "Failed to report (defeated) in ..." keeps it.

Not touched here: the other Virginia lines the broker reads as they are (for example "Signed by President" and "Bill
text as passed House and Senate", both stored with `passage`).
"""

import re
import typing
import textwrap
from openstates.ddp_states._text import _PAGE_FOOTER_TAIL_RE

_TYPE_BY_PREFIX = {
    "HR": "resolution",
    "SR": "resolution",
    "HJ": "joint resolution",
    "SJ": "joint resolution",
}


def classify_bill(
    *,
    identifier: typing.Optional[str],
    title: typing.Optional[str],
    classification: typing.Sequence[str],
) -> typing.List[str]:
    """A bill's labels: `bill` read from the title, then the bill number ("HR 1" -> `resolution`, "HJ 10" -> `joint
    resolution`), every other label as given. The scraper's order, so the two give the same label on any input."""
    if "constitutional amendment" in (title or "").lower():
        read = "constitutional amendment"
    else:
        read = _TYPE_BY_PREFIX.get("".join((identifier or "").split())[:2].upper())
    if read is None:
        return list(classification)
    return [read if c == "bill" else c for c in classification]


# The broker's `_read_virginia` tests: `text.lower().startswith("governor's recommendation adopted")`, and
# `^Subcommittee failed to recommend\b`, case-insensitive.
_GOVERNOR_RECOMMENDATION_ADOPTED = re.compile(r"^Governor's recommendation adopted", re.I)
_SUBCOMMITTEE_FAILED = re.compile(r"^Subcommittee failed to recommend\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `executive-signature` on "Governor's recommendation adopted", no `committee-failure` on
    a subcommittee's "failed to recommend ...", every other label as given. Reads the text only, as the broker did."""
    text = description or ""
    labels = list(classification)
    if _GOVERNOR_RECOMMENDATION_ADOPTED.match(text):
        labels = [c for c in labels if c != "executive-signature"]
    if _SUBCOMMITTEE_FAILED.match(text):
        labels = [c for c in labels if c != "committee-failure"]
    return labels


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


_VA_LINE_PATTERNS = [
    re.compile(r"^\s*\+\s*$"),  # decorative vertical-rule margin artifact -- indent shifts by
    # document stage but is identical across bills at the same stage; match on content only.
    # Decorative em-dash divider line separating a bill's summary/patron block from its body
    # (confirmed real -- always exactly 5 em-dashes in every sample checked, 8,404 real rows
    # contain it); found reading a real raw diff during AC6 refinement (HB1011), not guessed.
    re.compile(r"^\s*—{2,}\s*$"),
    re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2}\s+\d{1,2}:\d{2}\s*$"),  # generation timestamp footer
    # "2026 SESSION" (HTML) or bare "SESSION" (PDF -- confirmed real: pdftotext -layout drops
    # the leading year into a separate layout column that never makes it into the text stream).
    re.compile(r"^\s*(\d{4}\s+)?SESSION\s*$", re.IGNORECASE),
    re.compile(r"^\s*(INTRODUCED|ENROLLED|REPRINT)\s*$", re.IGNORECASE),
    # "SENATE SUBSTITUTE"/"HOUSE SUBSTITUTE" -- the same bare stage-marker line as
    # INTRODUCED/ENROLLED above, just for the substitute stage (confirmed real, e.g. SB622).
    re.compile(r"^\s*(SENATE|HOUSE)\s+SUBSTITUTE\s*$", re.IGNORECASE),
    re.compile(r"^\s*AMENDMENT IN THE NATURE OF A SUBSTITUTE\s*$", re.IGNORECASE),
    # Committee-routing line -- confirmed real and near-exclusively Introduced-stage (4,909
    # rows), essentially never carried forward to a Substitute/Enrolled version, so it always
    # shows as pure removed-line noise on any Introduced->later transition. Found reading a
    # real raw diff during AC6 refinement (HB1006), not guessed.
    re.compile(r"^\s*Referred to Committee on .*$", re.IGNORECASE),
    # Ticket's original guess for the offer-date line -- never observed in real 2026/2026S1
    # archived VA text (checked directly against the full archive), kept as a harmless no-op
    # safety net in case an older session used this phrasing.
    re.compile(r"^\s*OFFERED FOR CONSIDERATION\s+\d{1,2}/\d{1,2}/\d{4}\s*$", re.IGNORECASE),
    # Real shape confirmed against archived text: "Offered January 14, 2026" / "Prefiled
    # January 14, 2026" -- the ticket's guess above never actually appears.
    re.compile(r"^\s*(Offered|Prefiled)\s+\w+ \d{1,2},\s*\d{4}\s*$", re.IGNORECASE),
    # Patron line -- generalized beyond the ticket's `Patron[—-].*` to also match real
    # committee-substitute-stage forms confirmed in the archive: "(Patron Prior to
    # Substitute—Senator Marsden)" and "(Patrons Prior to Substitute—Senators A, B, and C)",
    # which lead with "(" and don't put the dash immediately after "Patron".
    re.compile(r"^\s*\(?Patrons?\b.*$", re.IGNORECASE),
    re.compile(r"^\s*\d+[A-Z]?\s*$"),  # lone bill-tracking numeric code, e.g. "26101118D"
    # Generalized beyond the ticket's "(HOUSE|SENATE) BILL NO." to also cover resolutions
    # (confirmed real: "SENATE JOINT RESOLUTION NO. 58" on SJ58, a resolution not a bill).
    re.compile(
        r"^\s*(HOUSE|SENATE)\s+(BILL|JOINT RESOLUTION|RESOLUTION) NO\.\s*\d+\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*VIRGINIA ACTS OF ASSEMBLY.*$", re.IGNORECASE),
    # Chaptered stage's own act-header line -- confirmed real (e.g. "CHAPTER 350") and a
    # genuinely different template from Enrolled's "VIRGINIA ACTS OF ASSEMBLY -- CHAPTER"
    # above, not just more trailing text after it as the ticket's "known gap" note guessed.
    # All-caps-anchored so it can't collide with inline mixed-case content references like
    # "Chapter 780 of the Acts of Assembly of 2024".
    re.compile(r"^\s*CHAPTER\s+\d+\s*$"),
    # Committee/Governor/Conference-substitute preamble, e.g. "(Proposed by the Senate
    # Committee on Finance and Appropriations" / "(Proposed by the Governor" / "(Proposed by
    # the Joint Conference Committee" -- confirmed real across multiple real committees plus
    # the Governor and Conference Committee. Matches the general shape rather than enumerating
    # entities, same rationale as the ticket's own guidance for committee names.
    re.compile(r"^\s*\(Proposed by .*$", re.IGNORECASE),
    # The date/placeholder line closing the "(Proposed by ...)" preamble when it wraps onto a
    # second physical line, e.g. "on February 12, 2026)" or the placeholder "on ________________)".
    re.compile(r"^\s*on\s+(_+|\w+ \d{1,2},\s*\d{4})\)\s*$", re.IGNORECASE),
    # Bracketed chamber+number cross-reference tag on its own line, e.g. "[H 1244]" (confirmed
    # real on both Enrolled and Chaptered documents).
    re.compile(r"^\s*\[[A-Z]{1,3}\s*\d+\]\s*$"),
    # OPEN-399: the page footer's tail, "of 12" (see _PAGE_FOOTER_TAIL_RE above).
    _PAGE_FOOTER_TAIL_RE,
]
# Deliberately NOT implementing the ticket's originally-proposed `_VA_COMMITTEE_SUBSTITUTE`
# ("any line containing the word Substitute", meant to strip a supposed committee-name-plus-
# "Substitute" heading line): checked directly against the real archive and no such standalone
# heading line exists anywhere -- committee names only ever appear embedded inside the
# "(Proposed by the ... Committee on <name>..." preamble already matched above. Worse, the
# ticket's proposed pattern would have been actively unsafe: its bare `\bSubstitute\b` match
# would also strip real amendment-instruction content that uses "substitute" as an ordinary
# verb -- confirmed real example found in a Conference Report: "1. After line 23, substitute".


_VA_TRAILING_WATERMARK = re.compile(
    r"\s{3,}[SH][JRB]?\d+\s*$"
)  # inline bill-ID watermark appended to the end of real content lines, e.g.


# "...where every young person has access to              SJ58"
_LEADING_LINE_NUMBER = re.compile(r"^\s*\d+(\s{2,}|\t)")


def _strip_virginia_boilerplate(text: str) -> str:
    """
    Strip Virginia-specific administrative boilerplate from one bill version's text before
    it's diffed against another (OPEN-9). Never applied to the stored raw_text itself (only
    the text fed into the diff call differs) -- see _clean_virginia_text() below for the
    jurisdiction gate and the rest of the pipeline (degenerate-extraction guard, reflow).

    Per line: strip the inline trailing bill-ID watermark first (real content can have this
    appended to an otherwise-real line, so only the watermark should go, not the whole line);
    drop the line entirely if what's left matches one of _VA_LINE_PATTERNS; otherwise strip a
    leading printed margin line-number if present and keep the rest. See _VA_LINE_PATTERNS
    above for what's covered and why, including a deliberate deviation from the ticket's
    starting patterns found unsafe against real archived text.
    """
    cleaned_lines = []
    for line in text.splitlines():
        line = _VA_TRAILING_WATERMARK.sub("", line)
        if any(pattern.match(line) for pattern in _VA_LINE_PATTERNS):
            continue
        line = _LEADING_LINE_NUMBER.sub("", line)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


# 2026-08-15 rework, after independently re-validating PR #18's evaluation-time rejection
# (see the ticket's comment thread): the line-pattern stripper above is real, solid work for
# same-media-type transitions, but the ticket's own revised AC2/AC3 (Chaptered-stage and
# resolution coverage, required after the first submission failed both) exposed two further,
# previously-uncharacterized real problems once validated with archive_bill_versions()'s ACTUAL
# per-link diff construction (every version with 2+ links gets a SEPARATE stored diff per link,
# always against the same prior_text -- not the single "hold media_type consistent" comparison
# AC2 itself suggests, which silently discards the real, commonly-stored HTML-target diffs).
#
# 1. Degenerate extraction, not a cleaning problem. VA's `application/pdf` extractor produces
#    near-empty garbage (dominated by a repeated "of N" page-footer artifact, or a handful of
#    disconnected sentence fragments) for two specific real categories: the enacted "Chaptered"
#    stage (~95% of real rows are under 250 chars; the ~5% that work run 1,400+ chars -- a clean
#    bimodal split with no ambiguous middle) and EVERY resolution's (HJ/HR/SJ/SR) "Enrolled"
#    stage (100% of 1,521 real rows are under 211 chars). archive_bill_versions() still prefers
#    this garbage PDF as prior_text/raw_text whenever it "succeeds" (non-empty, is_error=False),
#    so no amount of boilerplate-stripping can fix these diffs -- there's no real content in one
#    side to align against. No other VA note/media_type combination in the real archive ever
#    falls under this same length band (confirmed directly: the shortest real, non-degenerate
#    application/pdf document anywhere else in the archive is 290 chars). A plain length guard
#    (_VA_DEGENERATE_LEN) skips cleaning entirely for these pairs -- cleaned reduces to raw
#    exactly, satisfying AC2's "never worse" trivially, which is the correct outcome: this is a
#    genuine extraction bug, not something a diffing-time text transform can or should paper
#    over. (Filed separately as its own bug ticket, matching the OPEN-15 precedent for VA's
#    original 100%-broken extraction -- out of scope for this cleaning-only ticket to fix.)
# 2. Cross-pipeline line-wrap mismatch -- the dominant real problem, not Enrolled->Chaptered
#    specifically. Once every version's OWN per-link diffs are actually validated (not just a
#    single media-type-consistent comparison), the real failure mode turns out to be much
#    broader: ANY transition whose current version's document is `text/html` while prior_text
#    came from `application/pdf` (the common case, since prior_text prefers PDF and most VA
#    bills archive both media types at every stage) is a cross-pipeline comparison -- VA's PDF
#    text is fixed-width-wrapped at print time (~85-95 char lines) while its HTML text has no
#    internal wrapping at all (each paragraph is one physical line), so line-based diffing sees
#    almost no real alignment regardless of boilerplate stripped. This is the same class of
#    problem WA's OPEN-7 ticket solved with a reflow step. Reflowing both sides onto a common,
#    content-derived line shape (only across a genuine media-type change, matching WA/MI's own
#    gating -- confirmed unconditional reflow regresses already-aligned same-media-type pairs)
#    turns this into the single largest source of real improvement in the whole ticket: across
#    the entire archive, 60.7% of all real transitions improve (54.2% meaningfully), including
#    100% of the previously-failing Chaptered-stage and resolution transitions once the
#    degenerate PDF-vs-PDF pairing for each is correctly set aside by (1) above and the real
#    PDF-vs-HTML pairing is reflowed.
# 3. Resolutions need a sentence-boundary fix, not exclusion from reflow. An initial attempt
#    gated reflow off entirely for resolutions (matching MI's OPEN-11 precedent, since MI's own
#    resolutions genuinely have no enacting clause and different conventions) -- but that didn't
#    fix VA's resolution regressions at all, because the regression wasn't caused by reflow: it
#    reproduced identically with reflow on or off. Root cause, found by reading a real raw diff
#    (SR 159, "Commending Project PEACE"): VA resolutions structure their real content as
#    "WHEREAS, ...; and\nWHEREAS, ...; and, be it\nRESOLVED ..." clauses -- real clause
#    boundaries that _VA_SENTENCE_BREAK's plain ".;:"-followed-by-a-capital-letter rule doesn't
#    recognize (the letter immediately after "; and" is lowercase "and", not the next clause's
#    capital), so the sentence-splitter merged every WHEREAS clause into one enormous run-on
#    "sentence" for wrapping purposes -- and a single inserted real line ("Agreed to by the
#    Senate, ...") then shifted every wrap boundary for the entire rest of the document. Adding
#    the two VA-specific connector patterns below (mirroring the real "; and" / "; and, be it"
#    shapes, confirmed against multiple real resolutions) fixes this directly: SJ 58's own named
#    example (this ticket's own resolution reference) goes from a claimed-then-disputed 54%
#    reduction to a real, reproducible 0.941 -> 0.029 (97% reduction) once reflowed correctly.
_VA_DEGENERATE_LEN = 300


_VA_WHITESPACE_RUN = re.compile(r"\s+")


_VA_SENTENCE_BREAK = re.compile(
    r"(?<=[.;:])\s*(?=[A-Z(])"
    r"|(?<=; and)\s+(?=[A-Z])"
    r"|(?<=; and, be it)\s+(?=[A-Z])"
)


_VA_WRAP_WIDTH = 90


def _reflow_virginia_text(text: str) -> str:
    """Collapse to one content-derived line shape per clause (see point 2/3 above)."""
    text = _VA_WHITESPACE_RUN.sub(" ", text).strip()
    lines: typing.List[str] = []
    for clause in _VA_SENTENCE_BREAK.split(text):
        lines.extend(textwrap.wrap(clause, width=_VA_WRAP_WIDTH) or [""])
    return "\n".join(lines)


def _clean_virginia_text(
    prior_text: str, raw_text: str, prior_media_type: typing.Optional[str], cur_media_type: str
) -> typing.Tuple[str, str]:
    """
    Clean a Virginia prior_text/raw_text pair immediately before diffing (OPEN-9). Called only
    for jurisdiction.name == "Virginia", immediately before the difflib.unified_diff() call in
    archive_bill_versions() -- never applied to any other jurisdiction, and never applied to
    the stored raw_text itself. See the block comment above for the full real-data findings
    behind this design (degenerate-extraction guard, cross-pipeline reflow).
    """
    if len(prior_text.strip()) < _VA_DEGENERATE_LEN or len(raw_text.strip()) < _VA_DEGENERATE_LEN:
        # Known-broken PDF extraction (Chaptered stage, resolution Enrolled stage) produces
        # near-empty garbage that no boilerplate stripping can meaningfully clean -- leave both
        # sides untouched rather than risk a misleading ratio (point 1 above).
        return prior_text, raw_text
    prior_text = _strip_virginia_boilerplate(prior_text)
    raw_text = _strip_virginia_boilerplate(raw_text)
    if prior_media_type != cur_media_type:
        prior_text = _reflow_virginia_text(prior_text)
        raw_text = _reflow_virginia_text(raw_text)
    return prior_text, raw_text


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Virginia's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _clean_virginia_text(prior_text, raw_text, prior_media_type, cur_media_type)


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


PROCEDURAL_DOCUMENT_NOTES = frozenset(
    {
        "Conference Report",
        "Governor's Recommendation",
        "Governor's Recommendations",
        "Governor's Veto Explanation",
    }
)

PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(r"\A.+\bamendments?\Z", re.IGNORECASE)

# OPEN-593: Virginia's amendment-stage versions (substitutes) are ordered by their LIS file number alone
# (version_ordering.version_sort_key gives them ordinal 0).
AMENDMENTS_BY_DOCUMENT_NUMBER = True


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Virginia's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    from openstates.fulltext.va import handle_virginia_html
    return {
        "text/html": handle_virginia_html,
        # Found 2026-08-15 (OPEN-76): extract_line_numbered_pdf (keep-only-numbered-lines)
        # was mapped unconditionally for every VA PDF stage, but VA's LIS actually generates
        # two genuinely different PDF layout templates. Introduced/Substitute/bill-Enrolled
        # stages print a running line number on every body line (the numbered-draft
        # convention extract_line_numbered_pdf assumes) and extract correctly. The enacted
        # "Chaptered" stage and every resolution's (HJ/HR/SJ/SR) "Enrolled" stage instead use
        # VA's unnumbered "Acts of Assembly" final-typeset template -- no body line numbers
        # at all, just a "Page X of N" footer -- so extract_line_numbered_pdf drops nearly
        # all real content and keeps only that repeated "of N" footer plus a few accidental
        # digit-adjacent fragments. Confirmed directly against real 2026 Regular Session
        # documents fetched from lis.virginia.gov (HB1's Chaptered PDF, HB19's 5-page
        # Chaptered PDF, HJ1's Enrolled PDF): all are genuine embedded-text PDFs (verified via
        # pdffonts, not scanned images -- rules out an OCR gap), and extract_line_numbered_pdf
        # produced 0-219 chars of garbage from thousands of real chars per document.
        # extract_sometimes_numbered_pdf (already used by 13+ other states for exactly this
        # numbered-vs-plain situation) correctly detects which template each real document
        # uses and extracts full real content for both. OPEN-9's existing
        # _VA_LINE_PATTERNS/_strip_virginia_boilerplate (cli/text_extract.py) already strips
        # the plain-template path's decorative "+" margin-artifact lines during diff
        # cleaning, so no new VA-specific cleanup was needed here.
        "application/pdf": extract_sometimes_numbered_pdf,
    }
