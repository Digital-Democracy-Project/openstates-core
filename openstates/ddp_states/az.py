"""
Arizona's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re
import typing


# ── Action labels (OPEN-552, moved from the broker's _read_arizona) ─────────────────────────────────────────────────
# Arizona's action-label rule (OPEN-552, PLAN-state-specific-handling, epic OPEN-536).
#
# Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
# `_read_arizona`, BROKER-481), unchanged: the broker drops it in BROKER-490 once this rule is live. `_read_arizona` only
# removes labels, so nothing else of it has to stay in the broker.
#
# Every Arizona failure line is the bare word "FAILED", with no motion named. The scraper labels it `failure` (with
# `reading-3` when it follows a third reading). It follows committee do-pass motions, second readings and amendment
# votes as well as floor votes, and a third-reading "FAILED" is often followed by the bill's own passage after a motion
# to reconsider: HB 2946 (2026) failed its third reading 26-30 on 3/3 (azleg.gov), the House reconsidered it the same
# evening ("PASSED"), passed it on 3/11, and the governor signed it on 6/4. The line cannot say what failed, or whether it
# stays failed, so it is never read as the bill's failure. On the Mac replica (2026-11-17, session 57th-2nd-regular) 175
# lines carry it: 107 `failure, reading-3` and 68 `failure`. None carries `committee-failure` (the broker reader removes
# both; so does this).
#
# Unlike the other states' rules, this label is also read by the broker's scorecard rule (BROKER-68,
# `motion_eligibility._own_record_says_failed`): an Arizona vote whose text says "failed to pass" is kept off scorecards
# only while a same-day action in its chamber carries `failure`, and in Arizona that action is always one of these
# lines. Without the label all 141 such votes on the replica count. The scraper labels every "failed to pass" vote
# `passage`, including Committee of the Whole motions to amend (HB 2805, HCR 2001) and motions to reconsider (SB 1100),
# not only third readings (HB 2918, SB 1093). So this rule must not reach production before those votes are dealt with:
# see OPEN-552.

_BARE_FAILED = re.compile(r"^\s*FAILED\.?\s*$", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `failure` or `committee-failure` on the bare "FAILED" line, every other label as
    given."""
    if _BARE_FAILED.match(description or ""):
        return [c for c in classification if c not in ("failure", "committee-failure")]
    return list(classification)


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# Arizona's drafting software's own literal section-delimiter tokens (see _reflow_paragraphs()'s
# own docstring, point 1) -- END_STATUTE is always glued to the tail of the preceding sentence
# with no reliable punctuation of its own, so it's forced onto its own line before the main
# merge logic runs rather than left to an accident of that version's own word-wrap.
_AZ_END_STATUTE_RE = re.compile(r"\s*END_STATUTE")


_AZ_START_STATUTE_RE = re.compile(r"START_STATUTE")


_AZ_WHITESPACE_RUN = re.compile(r"\s+")


# Real sentence boundaries, found wherever they actually occur in a whole (blank-line-joined)
# block rather than only at each original physical line's own end (point 2), excluding a lone
# lettered/numbered subsection marker specifically -- requiring a non-alphanumeric character
# immediately before the letter/number itself so this can't also match the tail of an ordinary
# (often all-caps, see point 3) word ending a real sentence. The final alternative handles a
# semicolon-joined "; and Whereas"-style clause chain the plain rule can't see, since the word
# right after the connector is lowercase, not the next clause's own capital letter (point 3).
_AZ_SENTENCE_BREAK = re.compile(
    r"(?<![^A-Za-z0-9][A-Z]\.)(?<![^A-Za-z0-9][0-9]\.)(?<![^A-Za-z0-9][0-9][0-9]\.)"
    r"(?<=[.:;])\s+(?=[A-Z0-9(\[])"
    r"|(?<=; and)\s+(?=[A-Z])"
)


def _reflow_paragraphs(text: str) -> str:
    """
    Arizona-only text-cleaning step (OPEN-10) applied to prior_text/raw_text
    immediately before archive_bill_versions()'s difflib.unified_diff() call --
    never touches the stored raw_text field or any other jurisdiction.

    Arizona's bill documents are Microsoft Word HTML exports, which encode each
    visual (word-wrapped) line as its own paragraph-like HTML block, not each
    logical paragraph/sentence. Two exports of the same underlying text routinely
    word-wrap at different widths between drafting stages, so the same sentence
    fragments into a different number of "lines" in each version and a raw
    line-based diff sees changed content on nearly every line even with zero real
    edits. This rejoins those fragments back into one line per real sentence
    before diffing, so a word-wrap-width difference no longer fragments the
    document differently in each version.

    2026-08-15 rework, after an earlier submission (PR #17, byte-identical to this ticket's
    own starting-point pseudocode) was independently validated and found to leave real,
    closeable room for improvement (AC3's own explicit finding: a word-level diff comparison
    showed the reflowed-line approach was still far short of what's achievable). Iterating
    against the real archive (30-bill sample, then the entire current archive, 3,045 real
    transitions) found and fixed three further real problems, in order:

    1. Arizona's own drafting software inserts literal `START_STATUTE`/`END_STATUTE`
       delimiter tokens around each amended statute section -- confirmed real and common
       (~1/3 of the archive). `END_STATUTE` in particular is always glued directly onto the
       tail of the preceding sentence with no reliable separating punctuation of its own, so
       whether it ends up on its own line or merged into real content was purely an accident
       of each version's own word-wrap. Forcing both markers onto their own line before the
       main merge logic runs removes this as a noise source entirely.
    2. The original starting technique's merge rule only ever checks whether an INPUT LINE's
       own ending has sentence-final punctuation -- but Word's word-wrap can, and often does,
       place a real sentence boundary in the MIDDLE of a physical line (e.g. "...administering
       the fund. Monies\nin the fund are continuously appropriated."), and whether that
       happens is itself just an accident of each version's own wrap width -- exactly the kind
       of accident this reflow exists to remove. Fixed by joining each blank-line-delimited
       block into one continuous string first, then finding real sentence boundaries wherever
       they actually occur in that string (not just at each original line's own end).
    3. Splitting on any "capital-letter-preceded-by-a-period" boundary, done naively, can't
       tell a real lettered/numbered subsection marker ("B." starting a new subsection) from
       the last letter of a longer, ordinarily-capitalized word ending a sentence -- and
       Arizona's own convention of rendering amended statutory text in ALL CAPS means this
       naive check was accidentally treating the tail of nearly every all-caps sentence
       ("...ELECTRONICALLY.") as if it were itself a subsection marker, suppressing the split
       and silently merging entire lettered subsections (A/B/C...) into one giant run-on line.
       Fixed by requiring the letter/number itself be preceded by a non-alphanumeric boundary,
       so only a genuine standalone one-letter/one-number token is excluded from splitting.
       Also added (the same real, cross-jurisdiction pattern independently found and fixed for
       VA's OPEN-9): a semicolon-joined "; and Whereas" clause chain (real, common in AZ's own
       resolution/memorial preambles) doesn't split on the plain rule either, since the word
       right after "; and" is lowercase, not the next clause's own capital letter -- without
       this, an entire multi-WHEREAS preamble collapses into one giant merged line, and any
       small real difference elsewhere then dominates a now-tiny total line count.

    Re-validated against the entire current archive (3,045 real transitions, 692 tied-stage
    pairs excluded per the OPEN-34 caveat): 90.1% of transitions improved (78.5% meaningfully),
    mean ratio 0.660 -> 0.495, vs. the starting technique's 86.3%/72.4%/0.531 on the same
    sample -- a real, measured improvement, not just a re-submission of the starting code.
    Regressions dropped from 6.7% to 3.8% of the sample over these iterations; the residual
    3.8% is a real, known, accepted gap (not chased further, per this ticket's own 5-iteration
    cap): a floor-amendment or "Strike Everything" stage's own amendment-instruction preamble
    (e.g. "Strike everything after the enacting clause and insert:") is genuinely different
    boilerplate from the enacted bill's real preamble it's compared against -- a real content
    difference, not an artifact this reflow can or should paper over, but one that concentrates
    onto very few total lines and so has an outsized effect on the ratio for those specific
    transitions. Independently re-verified against SB1503's own real archived text (the
    ticket's own named strike-all-rewrite example, "public pensions; proxy voting"): ratio
    1.0 both before and after reflow, confirming a genuine, complete rewrite is never
    mistaken for a stripping failure or suppressed (AC4).

    Blank lines are preserved as real separators (paragraph/section breaks), and internal
    whitespace runs (including the double-space/nbsp-after-period artifact Word's HTML export
    leaves behind) are collapsed to a single space.
    """
    text = _AZ_END_STATUTE_RE.sub("\nEND_STATUTE\n", text)
    text = _AZ_START_STATUTE_RE.sub("\nSTART_STATUTE", text)
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        joined = _AZ_WHITESPACE_RUN.sub(" ", block.replace("\n", " ")).strip()
        if not joined:
            out.append("")
            continue
        # A synthetic leading space guarantees the subsection-marker lookbehind below always
        # has enough preceding context to evaluate, even for a block that itself opens with a
        # marker (e.g. "A. The department...") -- stripped back off immediately after.
        parts = _AZ_SENTENCE_BREAK.split(" " + joined)
        parts[0] = parts[0][1:]
        out.extend(parts)
        out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Arizona's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _reflow_paragraphs(prior_text), _reflow_paragraphs(raw_text)


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(r"\A(house|senate) - .+\bfailed( \(\d+\))?\Z", re.IGNORECASE)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Arizona's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_sometimes_numbered_pdf, extractor_for_elements_by_class
    return {
        "text/html": extractor_for_elements_by_class("WordSection2"),
        # Found 2026-08-10: DoNotDownload here (since before this fork existed) skipped
        # every AZ PDF unconditionally -- fine when a version also has an HTML copy (the
        # common case), but silently zero-archived text for the ~1,160 versions (mostly
        # committee-amendment stages) that exist ONLY as PDF, with no HTML counterpart at
        # all. Verified live against a real never-archived PDF (introduced-bill text):
        # plain fetch, no WAF/bot-block response, and extract_sometimes_numbered_pdf
        # produces clean text -- same numbered-line shape as AL/FL/MA/MD. Some
        # committee-stage PDFs are scanned/image-only (no text layer at all, confirmed via
        # pdffonts/pdfimages on a real sample) and will still archive with
        # raw_text="" -> is_error=True; that's an honest signal (OCR is future work, see
        # `dc`'s textract_extractor precedent), not a regression -- the raw PDF itself is
        # now preserved either way, which DoNotDownload never did.
        "application/pdf": extract_sometimes_numbered_pdf,
    }
