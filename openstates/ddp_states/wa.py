"""
Washington's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re
import textwrap
import typing


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# OPEN-7: archive_bill_versions()'s diff_from_previous_version, for Washington specifically,
# is dominated by repeated administrative text (tracking code, title, sponsor line, procedural
# "Read first time/Referred to Committee" line) printed at the top of every version and never
# stripped by CONVERSION_FUNCTIONS["wa"]["text/html"] (extractor_for_element_by_xpath("//html"),
# which only runs clean() -- whitespace normalization, no header/footer filtering).
#
# Two things confirmed directly against the real archive (not assumed) reshape this well beyond
# the ticket's own starting point:
#
# 1. CONVERSION_FUNCTIONS["wa"]["application/pdf"] (OPEN-49, landed 2026-08-09) means WA PDF
#    text is no longer always-empty -- and extract_line_numbered_pdf's text_after_line_numbers()
#    already drops every one of these header lines as a side effect (same mechanism that closed
#    Florida's equivalent ticket, OPEN-8), confirmed against every real stage sampled. PDF text
#    needs no boilerplate stripping at all.
# 2. WA's real HTML raw_text has ZERO internal newlines (confirmed via SQL against the live
#    archive: length(text) - length(replace(text, chr(10), '')) = 0 on every sampled row) --
#    extractor_for_element_by_xpath("//html")'s text_content() never reintroduces them, and
#    inter-element joins are sometimes glued with no whitespace at all ("ByRepresentatives...").
#    A single-line string always diffs as "the whole line changed" no matter what's stripped
#    from it, so boilerplate removal alone cannot reduce line-level diff noise -- reconstructing
#    real line boundaries is what actually does. Since prior_text now usually comes from PDF
#    (see the "Prefer text/xml over application/pdf" comment below) while a version's own HTML
#    document still diffs its raw, un-reflowed text against that PDF-sourced prior_text, the two
#    pipelines' wildly different native line-wrapping (print-width columns vs none at all) also
#    needs a common, content-derived line shape before they can align at all -- not just the
#    HTML side.
#
# _clean_wa_text() therefore: normalizes whitespace (so "RCW  64.32.250" from HTML and
# "RCW 64.32.250" from PDF become the same string), strips the known noise substrings
# (non-line-anchored -- matching requires no assumption of surrounding whitespace or an intact
# line, since real data has neither reliably), then rebuilds a stable, content-derived line
# shape (split on sentence-ending punctuation, word-wrapped within each sentence so one
# mid-sentence edit doesn't cascade wrap points through the rest of the document). Deliberately
# omits the ticket's suggested _LEADING_LINE_NUMBER pattern -- verified inert against real WA
# data: PDF text never has it (already stripped upstream by extract_line_numbered_pdf) and HTML
# text never had it to begin with (WA's HTML page doesn't print per-line margin numbers).
#
# Validated against 20 real Washington bills (126 real consecutive version-transition x
# media-type pairs, incl. the 3 largest bills by file size, spanning Introduced/Substitute*/
# Engrossed*/Passed_Legislature stages): mean noise ratio (fraction of the old version's lines
# appearing as a +/- change) drops from 1.000 -> 0.174 for text/html documents and from 0.127 ->
# 0.099 for application/pdf documents; 120/126 pairs improve (106 "meaningfully," >=20% relative
# or >=0.05 absolute), 3 are unchanged (already-identical text), and 3 show a negligible
# regression (<=0.0012 absolute, i.e. <=0.12%) -- each traced to a genuine, tiny, real edit
# (confirmed by reading the actual raw diff) where this reflow's canonical, content-derived line
# boundaries happen to span slightly more physical lines than the original PDF's arbitrary
# print-width wrap did for that specific edit. A stricter variant that skips reflowing any
# text that already has short natural lines (e.g. PDF's own wrap) eliminates those 3
# regressions entirely, but was rejected: it also skips reflowing away the PDF/HTML
# cross-pipeline mismatch, leaving the mean text/html ratio at 0.975 -- effectively no
# improvement at all for the actual problem this ticket exists to fix.
_WA_GLOBAL_NOISE_PATTERNS = [
    # Tracking code, e.g. "Z-0056.2", "H-0530.1", "S-2500.1" -- generalized from the ticket's
    # own [ZH] class, which misses the real "S-" prefix (1,795 occurrences in the live archive).
    # Safe to strip anywhere: this shape never occurs in real bill body text, and a tracking
    # code can legitimately recur on a later page of a multi-page document.
    re.compile(r"[A-Z]-\d{4}\.\d+"),
    # Page-number + bill-ID watermark, e.g. "p. 1    HB 1337" -- per the ticket's watermark
    # Known Gap. Requires a trailing bill-ID so it can't collide with a real legal citation
    # like "F. Supp. 312" (confirmed: 13 real "p. N" occurrences in the live archive, all
    # citations, none matching this stricter shape). Also global: a watermark repeats on every
    # page, not just the first.
    re.compile(r"p\.\s*\d+\s+[HS]B\s*\d+", re.IGNORECASE),
]


# Everything below is boilerplate that only ever legitimately appears in the document's leading
# header block (title/legislature/session/vote-tally/sponsor/procedural lines), never restated
# in the substantive body -- unlike the patterns above, several of these are ordinary English
# phrase shapes ("Committee on <x>.", "<year> regular session", "House Bill <n>") that real bill
# TEXT can and does legitimately contain when it creates a committee, cites a session, or amends
# another numbered bill. Applying them across the whole document (as an earlier version of this
# cleaner did) silently deleted that real content -- confirmed against constructed body text
# such as "The joint committee on veterans affairs shall report..." and "amends Senate Bill 5129"
# both losing their real subject matter. _clean_wa_text() therefore only ever runs this list
# against the text preceding the bill's own enacting clause ("AN ACT ..."), which every real and
# fixture WA document places immediately after this boilerplate -- see _WA_ENACTING_CLAUSE.
_WA_HEADER_ONLY_NOISE_PATTERNS = [
    # Bill title with any optional prefix phrase (SUBSTITUTE, SECOND SUBSTITUTE, ENGROSSED
    # SUBSTITUTE, "CERTIFICATION OF ENROLLMENT", etc.) before "(SENATE|HOUSE) BILL <n>" -- the
    # general shape, not an enumerated prefix list, per the ticket's title-prefix Known Gap.
    # "\d{3,4}" (not "\d+"): a real enrolled-bill header glues the bill number directly onto
    # the following ordinal-legislature number with no separator ("...BILL 101469TH
    # LEGISLATURE...") -- an unbounded "\d+" swallows the legislature's ordinal digits too.
    re.compile(r"(?:[A-Z][A-Z ]*\s+)?(?:SENATE|HOUSE) BILL \d{3,4}", re.IGNORECASE),
    # "\d{1,3}(?:st|nd|rd|th)" (not "\d+\w*"): the ordinal-suffix form is not just more
    # specific, it avoids catastrophic backtracking -- an unanchored "\d+\w*" tries every
    # digit run in the whole document (statute citations, dollar amounts, dates, ...) at every
    # possible split between its two quantifiers before failing, which measured >120s on a
    # single real ~90KB "Passed Legislature" bill. The ordinal-suffix form has no such
    # ambiguous split and resolves in milliseconds on the same real document.
    re.compile(
        r"State of Washington\s*\d{1,3}(?:st|nd|rd|th)\s*Legislature\s*\d{4}[^.]{0,40}?Session",
        re.IGNORECASE,
    ),
    # An enrolled bill's own ordinal-legislature/session line has no "State of Washington"
    # prefix at all, e.g. "69TH LEGISLATURE2025 REGULAR SESSION" -- found on the same real
    # "Passed Legislature" document as the digit-boundary bug above. Known minor imprecision:
    # when the bill number is glued directly onto this with no separator at all (e.g. "...BILL
    # 101469TH LEGISLATURE..."), there is no reliable way to tell where a variable-length bill
    # number ends and a variable-length ordinal begins from the digits alone -- this can nibble
    # the bill number's own last digit along with the real ordinal-legislature match. Accepted
    # as a documented, narrow gap (see the PR description) rather than chased further: it only
    # affects the cosmetic bill-number digits inside boilerplate that's being removed anyway.
    re.compile(
        r"\d{1,3}(?:st|nd|rd|th)\s*Legislature\s*\d{4}[^.]{0,40}?Session", re.IGNORECASE
    ),
    # The enrolled-bill signing/vote-tally block, e.g. "Passed by the House March 11, 2025
    # Yeas 93 Nays 3Speaker of the House of RepresentativesPassed by the Senate April 16,
    # 2025 Yeas 48 Nays 1President of the Senate" -- printed once per chamber on every real
    # "Passed Legislature"/enrolled document regardless of the actual vote count or date.
    re.compile(
        r"Passed by the (?:House|Senate)[^.]*?Yeas\s*\d+\s*Nays\s*\d+"
        r"(?:Speaker|President) of the (?:House(?: of Representatives)?|Senate)",
        re.IGNORECASE,
    ),
    # Found against a real "Substitute Passed Legislature" PDF during AC6 refinement: a
    # page-break can split the session line across pages, leaking a bare "<year> Regular
    # Session" (or a tail-only "Regular Session") fragment onto a numbered line that survives
    # extract_line_numbered_pdf's own line-number-anchored filtering upstream -- the full
    # pattern above only matches when the whole line lands intact on one page.
    # No trailing "\b": real data (e.g. a "Passed Legislature" enrolled-bill header) glues
    # "SESSION" directly onto the next word with no separator at all ("SESSIONPassed"), and
    # "\b" can never match between two word characters.
    re.compile(r"(?:\d{4}\s+)?(?:Regular|Special)\s+Session", re.IGNORECASE),
    # Alternate committee sponsor line for substitute stages, e.g. "By House Finance
    # (originally sponsored by Representatives ...)" -- generalized to any committee text, per
    # the ticket's alternate-sponsor-line Known Gap (never enumerate committee names).
    re.compile(r"By\s*[^()]{0,80}?\(originally sponsored by[^)]*\)", re.IGNORECASE),
    # Plain introduced-version sponsor line. Stops at the first period (real sponsor-name
    # lists never contain one), then optionally consumes exactly that one period -- real WA
    # data sometimes glues the sponsor line directly onto the following procedural line with
    # no separating space at all (e.g. "...Ramel.Prefiled 02/11/25."), so the match must be
    # able to cross that single period to reach the lookahead. Deliberately has no "$"
    # fallback in the lookahead: an open-ended ".*?" (or a lookahead that can fall back to
    # end-of-string) would swallow the entire rest of the document whenever a version simply
    # doesn't restate "Prefiled"/"Read first time" -- under-matching here is far safer than
    # that.
    re.compile(
        r"By\s*(?:Senators?|Representatives?)[^.]*?\.?"
        r"(?=\s*(?:Prefiled|Read first time|READ FIRST TIME))",
        re.IGNORECASE,
    ),
    re.compile(r"(?:Prefiled[^.]*\.)?\s*(?:READ FIRST TIME|Read first time)[^.]*\.", re.IGNORECASE),
    re.compile(r"(?:Referred to )?Committee on [^.]*\.", re.IGNORECASE),
]


_WA_WHITESPACE_RUN = re.compile(r"\s+")


# Splits on a sentence-ending ./;/: followed by (optionally, real WA data often has none at
# all) whitespace and a capital letter or open-paren -- e.g. "...Committee on Finance.AN ACT
# Relating..." (no space) must split as reliably as "...Finance. AN ACT...". A decimal-style
# citation like "RCW 64.32.250" is never split: the digit right after the period fails the
# "capital letter or open-paren" lookahead on its own, with no extra lookbehind needed.
_WA_SENTENCE_BREAK = re.compile(r"(?<=[.;:])\s*(?=[A-Z(])")


_WA_WRAP_WIDTH = 55


# Marks the end of the boilerplate header and the start of a WA bill's own substantive text --
# every real and fixture document in this ticket's research places its enacting clause
# ("AN ACT Relating to ...") immediately after the title/legislature/sponsor/procedural block.
# If it's ever absent (e.g. a resolution with no enacting clause), header-only patterns run
# against the whole text, same as before this boundary existed -- no worse than the prior
# global behavior for that case.
_WA_ENACTING_CLAUSE = re.compile(r"\bAN ACT\b", re.IGNORECASE)


def _clean_wa_text(text: str) -> str:
    """
    Washington-only text cleaner applied to prior_text/raw_text immediately before the
    difflib.unified_diff() call in archive_bill_versions() -- see the block comment above for
    the real-data findings this encodes and the AC3/AC4 measurements behind the design.
    """
    text = _WA_WHITESPACE_RUN.sub(" ", text).strip()

    boundary_match = _WA_ENACTING_CLAUSE.search(text)
    boundary = boundary_match.start() if boundary_match else len(text)
    header, body = text[:boundary], text[boundary:]
    for pattern in _WA_HEADER_ONLY_NOISE_PATTERNS:
        header = pattern.sub("", header)
    text = header + body

    for pattern in _WA_GLOBAL_NOISE_PATTERNS:
        text = pattern.sub("", text)
    text = _WA_WHITESPACE_RUN.sub(" ", text).strip()
    lines = []
    for sentence in _WA_SENTENCE_BREAK.split(text):
        lines.extend(textwrap.wrap(sentence, width=_WA_WRAP_WIDTH) or [""])
    return "\n".join(lines)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Washington's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _clean_wa_text(prior_text), _clean_wa_text(raw_text)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Washington's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_line_numbered_pdf
    from openstates.fulltext.wa import handle_washington_html, without_deletion_marks
    return {
        # OPEN-212: structured=True. WA serves its bill HTML with no newlines between
        # elements, so lxml's separator-free text_content() fused tokens across block
        # boundaries -- 5,750 of 5,818 documents stored as a single line, and Engrossed
        # Substitute Senate Bill 5167 stored as bill "516769". Every other jurisdiction using
        # this helper measures clean today and is deliberately left on the old path here; see
        # text_from_element_xpath's docstring.
        # OPEN-580: read the same way, with struck-out words and their "((" "))" marks left out
        # (see fulltext/wa.py).
        "text/html": handle_washington_html,
        # Found 2026-08-09 (OPEN-49): no application/pdf entry ever existed here, upstream
        # or in this fork -- every WA PDF (5,800+) silently extracted as empty/is_error=True.
        # Verified directly against real WA bills: same printed-line-number PDF shape already
        # handled by extract_line_numbered_pdf for AL/FL/MA/MD/etc.
        # OPEN-560: struck-out words removed by Alabama's reader, see fulltext/al.py. OPEN-580:
        # the empty "((" "))" marks the reader leaves are taken out, as in the HTML printing.
        "application/pdf": without_deletion_marks(
            read_without_struck_text(extract_line_numbered_pdf)
        ),
    }
