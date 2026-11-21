"""
Michigan's action-label rule (OPEN-548, PLAN-state-specific-handling, epic OPEN-536).

Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
`_read_michigan`, BROKER-248), unchanged: the broker drops it in BROKER-490 once this rule is live. `_read_michigan`
only removes a label, so nothing else of it has to stay in the broker.

Each chamber's journal records the other chamber's passage as a message: the Senate's "PASSED BY HOUSE WITH IMMEDIATE
EFFECT" (and its variants: "WITH SUBSTITUTE (H-5)", "WITH AMENDMENT(S)", "BY 2/3 VOTE WITHOUT AMENDMENT") is stored
under the Senate, after the House's own roll call ("passed; given immediate effect Roll Call #4 ...", stored under
the House). The message is not a vote. Labelled `passage`, it reads as the receiving chamber passing the bill, so a
bill passed by one chamber reads as passed by both, and its passage date is the day the message was read.

The scraper has not labelled these lines since OPEN-344 (`mi/bills.py`, `categorize_action`), but stored history
still has them: on the Mac replica (2026-11-17, session 2025-2026) 610 such lines carry `passage` (dated 2025-02-04
to 2026-10-01) and 2 newer ones do not. The roll-call lines that start "PASSED BY 2/3 VOTE ROLL CALL # ..." (SB 304,
SB 602, SB 704) are a chamber's own vote and keep `passage`; the scraper keeps it on them too.
"""

import re
import typing
import textwrap

_CHAMBER_MESSAGE = re.compile(r"^PASSED BY (HOUSE|SENATE)\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on the other chamber's "PASSED BY HOUSE/SENATE ..." message, every other
    label as given."""
    if "passage" in classification and _CHAMBER_MESSAGE.match(description or ""):
        return [c for c in classification if c != "passage"]
    return list(classification)


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# OPEN-11: Michigan-only text cleaning applied to prior_text/raw_text immediately before the
# difflib.unified_diff() call in archive_bill_versions() (never touching the stored raw_text
# field, and never applied to any other jurisdiction).
#
# AC2 characterization, revised 2026-08-14 after a first submission (PR #20) was independently
# validated against the real archive and found to have near-zero real effect (see the ticket's
# comment thread for the full numbers). That submission's front-matter-only design was built on
# a stale premise: Michigan's application/pdf extractor was 100% broken when the ticket was last
# researched (2026-07-30), so every real diff at that time was necessarily text/html vs
# text/html. OPEN-49 (merged 2026-08-09) fixed the PDF extractor, and archive_bill_versions()'s
# real prior_text selection (text/xml or application/pdf or first-available) now prefers PDF for
# nearly every Michigan version -- which changes what actually needs cleaning:
#
# 1. Front matter/tracking-block boilerplate (the original finding) is still real for
#    text/html documents and the enacted "Public Act" stage's PDF (confirmed via a real diff
#    read, HB4493 As Passed by the House -> Public Act) -- see _MI_ENACTING_CLAUSE_RE. This is
#    stripped for every media type, safely, since resolutions simply never contain the anchor
#    and are returned unchanged by this step (AC5).
# 2. Two previously-undiscovered real noise patterns, found while independently re-validating
#    PR #20 against the full current archive (4569 real transitions) and tracing its 10 found
#    regressions to root cause:
#    (a) A per-page tracking-code/hash footer bleeds through MI's own numbered-PDF extractor at
#        every page break, not just the final page, e.g. "GSS   H05157'25_HB5314_INTR_1
#        y5icbv\x0c   2" mid-document and "Final Page\n    KHS    H00127'25_HB4010_INTR_1
#        ft61ok\x0c" at the true end -- a unique, unrepeated hash per file, so it always looks
#        like a real content change between any two versions. Enacted "Public Act" PDFs use a
#        different, plain "(N)\x0c" page-number-only footer instead (no tracking hash). Both are
#        stripped unconditionally (_MI_TRACKING_PAGE_BREAK_RE, _MI_PLAIN_PAGE_NUM_RE) -- neither
#        pattern can plausibly appear in real bill text.
#    (b) MI's numbered-PDF extraction keeps each printed margin line-number as literal leading
#        text on its own line (e.g. "1         Sec. 1. ..."), and the column-padding whitespace
#        after that number is not stable between two renderings of the *same* content (observed:
#        "1         Sec." in one PDF vs. "1          Sec." -- one extra space -- in another PDF
#        of the identical bill stage). On a short bill this single incidental whitespace/line-
#        number difference can dominate the whole diff. Both are collapsed for Bill-classified
#        notes only (_MI_LEADING_LINE_NUM_RE, then per-line inner-whitespace normalization) --
#        deliberately NOT applied to Resolutions, whose own indentation/whitespace conventions
#        this distorted when tested unconditionally (see point 4).
# 3. The real remaining noise source once (1) and (2) are handled is a genuine cross-pipeline
#    line-wrap mismatch: a fixed-width-wrapped numbered PDF (~56-65 char lines) diffed against a
#    same-version-but-different-media-type document (raw HTML, or an enacted-stage PDF that uses
#    a noticeably wider print column) shares no real line boundaries at all, so difflib's
#    line-based ratio can't reflect boilerplate removal -- this is the exact class of problem
#    WA's OPEN-7 ticket solved with a sentence-reflow step. Applying the same technique
#    (_reflow_michigan_text, width tuned to MI's own ~56-65 char PDF convention rather than
#    reusing WA's constant) only when the prior and current media types genuinely differ
#    collapses a real, confirmed example (SB 542, Substitute (H-2) - 4 PDF -> As Passed by the
#    Senate HTML) from ratio 0.970 to 0.019, and the ticket's own central target case (SB 542,
#    Senate Concurred Bill -> Public Act, both PDF but different print widths) from 0.990 to
#    0.103.
# 4. PR #20's own AC2 write-up said a reflow step was tested and "made the ratio uniformly worse
#    on every transition tested" -- reproduced here, but only for two specific cases, not as a
#    blanket verdict: (a) applying reflow to *same-media-type* pairs that were already
#    line-aligned coarsens line granularity for no benefit (confirmed: applying reflow
#    unconditionally introduced 243 real regressions across the full archive, vs. 1 when gated
#    to cross-media-type pairs only); (b) applying it to Resolutions specifically is actively
#    harmful (confirmed: gating on `bill.classification == ["bill"]` removed ~90 further
#    regressions, all "Senate/House Enrolled -> Adopted Resolution" text/html pairs) -- Michigan
#    resolutions have no enacting clause and their own distinct whitespace/indentation
#    conventions this cleaner was never designed for. Gating reflow on BOTH conditions (a
#    genuine media-type change AND a Bill, not a Resolution) keeps the real win from point 3
#    while avoiding both failure modes.
# 5. Validated against a real, representative 30-bill/107-transition sample (>=15 per AC3,
#    including both of this ticket's own named bills, SB 542/HB 4493) AND the entire current MI
#    archive (1465 bills, 4569 real consecutive transitions via _version_sort_key(), 216
#    tied-stage pairs excluded per point 6 below): among the 1455 transitions that actually
#    carried some form of the noise above, 64.8% improved (64.1% meaningfully, >=20% relative or
#    >=0.05 absolute) -- a clear majority, per AC4. The remaining 3025 transitions (already-clean
#    PDF-vs-PDF pairs with nothing to strip) are confirmed byte-for-byte ratio-unchanged, as
#    expected. Exactly 1 regression survives across the whole archive (0.964 -> 0.982, on an "As
#    Passed by the House" -> "Public Act" PDF pair that already sat at 0.964 raw -- i.e. an
#    already near-total rewrite with no real signal to preserve either way) -- a real, known,
#    accepted gap (enacted-stage PDFs occasionally use a wider print column than earlier
#    same-media-type stages, which this cleaner does not separately detect), left documented
#    rather than chased further, per AC6's "stop after 5 iterations, document the rest" bar (this
#    revision went through 7 rounds of real-data-driven refinement to get here).
# 6. Known, documented, OUT-OF-SCOPE gap (not this ticket's to fix, carried over unchanged):
#    _note_stage() maps both "Public Act" and "Senate|House Concurred Bill" to the same
#    _STAGE_ENACTED bucket with no ordinal distinguishing them (nor same-ordinal Senate vs. House
#    Substitute pairs) -- so _version_sort_key() cannot always tell which of a tied pair is truly
#    "prior". These pairs are excluded from this ticket's own AC3/AC4 validation (their true
#    chronological order isn't reliably known) -- this is OPEN-34-shaped work, not a
#    text-cleaning problem.
_MI_ENACTING_CLAUSE_RE = re.compile(
    r"the\s+people\s+of\s+the\s+state\s+of\s+michigan\s+enact\s*:", re.IGNORECASE
)


# Per-page tracking-code/hash footer, mid-document or at the true end, e.g.
#   "GSS   H05157'25_HB5314_INTR_1   y5icbv\x0c   2"
#   "Final Page\n    KHS    H00127'25_HB4010_INTR_1    ft61ok\x0c"
# A unique per-file hash, so it always looks like real content changed between any two versions.
_MI_TRACKING_PAGE_BREAK_RE = re.compile(
    r"\s*(?:Final Page\s*\n)?\s*[A-Za-z]{2,4}\s+\S*'\S*_\S+_\d+\s+[a-z0-9]{4,8}\s*\x0c\s*\d*",
    re.IGNORECASE,
)


# Enacted "Public Act" PDFs use a plain parenthesized page-number footer instead, e.g.
# "...(12)\x0c    (b) Add taxes..." -- no tracking hash, different shape from the above.
_MI_PLAIN_PAGE_NUM_RE = re.compile(r"\s*\(\d+\)\s*\x0c\s*")


# A printed margin line-number kept as literal leading text on its own line by MI's numbered-PDF
# extractor, e.g. "1         Sec. 1. ...". Bill-only (see point 2b/4 above).
_MI_LEADING_LINE_NUM_RE = re.compile(r"^\d{1,3}\s+(?=[A-Za-z(])")


_MI_INLINE_WS_RUN = re.compile(r"[ \t]+")


_MI_WHITESPACE_RUN = re.compile(r"\s+")


_MI_SENTENCE_BREAK = re.compile(r"(?<=[.;:])\s*(?=[A-Z(])")


_MI_WRAP_WIDTH = 65


def _strip_michigan_boilerplate(text: str) -> str:
    """
    Strip the boilerplate that's safe to remove regardless of note type (Bill or Resolution) --
    the enacting-clause front matter and the two page-break footer shapes. See point 1/2a above.
    """
    match = _MI_ENACTING_CLAUSE_RE.search(text)
    if match:
        text = text[match.end() :]
    text = _MI_TRACKING_PAGE_BREAK_RE.sub(" ", text)
    text = _MI_PLAIN_PAGE_NUM_RE.sub(" ", text)
    return text


def _reflow_michigan_text(text: str) -> str:
    """Collapse to one content-derived line shape per sentence (see point 3 above)."""
    text = _MI_WHITESPACE_RUN.sub(" ", text).strip()
    lines: typing.List[str] = []
    for sentence in _MI_SENTENCE_BREAK.split(text):
        lines.extend(textwrap.wrap(sentence, width=_MI_WRAP_WIDTH) or [""])
    return "\n".join(lines)


def _clean_michigan_text(
    prior_text: str,
    raw_text: str,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
    is_bill: bool,
) -> typing.Tuple[str, str]:
    """
    Clean a Michigan prior_text/raw_text pair immediately before diffing (see the AC2 comment
    above for the full real-data writeup behind this design). Boilerplate stripping is safe for
    every note type; the line-number/whitespace normalization and cross-media reflow are gated
    to Bill-classified notes only (point 2b/4 above) since Resolutions have their own, different
    conventions these steps would otherwise distort.
    """
    prior_text = _strip_michigan_boilerplate(prior_text)
    raw_text = _strip_michigan_boilerplate(raw_text)
    if is_bill:
        prior_text = "\n".join(
            _MI_INLINE_WS_RUN.sub(" ", _MI_LEADING_LINE_NUM_RE.sub("", line)).strip()
            for line in prior_text.splitlines()
        )
        raw_text = "\n".join(
            _MI_INLINE_WS_RUN.sub(" ", _MI_LEADING_LINE_NUM_RE.sub("", line)).strip()
            for line in raw_text.splitlines()
        )
        if prior_media_type != cur_media_type:
            prior_text = _reflow_michigan_text(prior_text)
            raw_text = _reflow_michigan_text(raw_text)
    return prior_text, raw_text


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Michigan's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _clean_michigan_text(prior_text, raw_text, prior_media_type, cur_media_type, is_bill)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Michigan's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_sometimes_numbered_pdf, extractor_for_element_by_xpath
    return {
        "text/html": extractor_for_element_by_xpath('.//*[@class="WordSection1"]'),
        # Found 2026-08-09 (OPEN-49): no application/pdf entry ever existed here, upstream
        # or in this fork -- every MI PDF (7,000+) silently extracted as empty/is_error=True.
        # Most MI PDF stages (Introduced, Substitute, etc.) print line numbers on body text,
        # matching extract_line_numbered_pdf's shape elsewhere (AL/FL/MA/MD/etc.) -- but the
        # enacted "Public Act" stage does NOT use that numbering convention at all, and
        # extract_line_numbered_pdf (which keeps ONLY numbered lines) silently returns empty
        # for it. extract_sometimes_numbered_pdf handles both: verified directly against a
        # real Public Act PDF (previously empty, now extracts correctly) and a real numbered
        # Introduced PDF (unchanged, still extracts correctly).
        "application/pdf": extract_sometimes_numbered_pdf,
    }
