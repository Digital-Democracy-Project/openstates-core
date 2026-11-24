"""
Montana's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Pre-diff text cleaning (OPEN-827) ────────────────────────────────────────────────────────────────────────────────


# OPEN-827: every Montana text opens with the bill-number line and the sponsor line. The drafted bill ("Bill Draft",
# the LC file) has blanks ("_____________ BILL NO. _____________", "INTRODUCED BY ______..."); the introduced bill has
# them filled in ("SENATE BILL NO. 30", "INTRODUCED BY <sponsor>, <cosponsors>", the list wrapping onto up to 3 more
# lines), so every Bill Draft -> Introduced comparison showed both lines as changed. The second source of noise is the
# space after "Section N.", printed one space wider or narrower between the two PDFs. Read on the Mac replica
# (2026-11-24, all 594 Montana documents, 2027): the number line is line 1 and "INTRODUCED BY" line 2 in every one;
# the next line is "BY REQUEST OF ..." (485), "A BILL FOR AN ACT ..." (103) or "A JOINT RESOLUTION ..." (6); neither
# header line appears anywhere else in any text. Both header lines (and the sponsor list's wrapped lines) are dropped
# only in that place, and runs of spaces inside a line become one space. Letter case is left alone.
_BILL_NUMBER_LINE = re.compile(r"(?:_+|HOUSE|SENATE) (?:BILL|JOINT RESOLUTION|RESOLUTION) NO\. (?:_+|[1-9]\d*)", re.I)
_SPONSOR_LINE = re.compile(r"INTRODUCED BY\b.*", re.I)
_AFTER_SPONSORS = re.compile(r"(?:BY REQUEST OF|A BILL FOR AN ACT|A JOINT RESOLUTION|A RESOLUTION)\b", re.I)
# the sponsor list wrapped onto at most 3 more lines in all 594 texts; 5 leaves room for a longer list, and the lines
# go only when one of the lines above ends the list within them (otherwise only the two header lines go)
_SPONSOR_MAX_WRAPPED_LINES = 5
_SPACE_RUN = re.compile(r"[ \t]{2,}")


def _clean_montana_text(text: str) -> str:
    """Montana-only cleaner, see the block comment above: the header's bill-number and sponsor lines dropped, runs of
    spaces inside a line made one space."""
    lines = text.split("\n")
    if len(lines) > 1 and _BILL_NUMBER_LINE.fullmatch(lines[0].strip()) and _SPONSOR_LINE.fullmatch(lines[1].strip()):
        end = 2
        for i in range(2, min(len(lines), 2 + _SPONSOR_MAX_WRAPPED_LINES + 1)):
            if _AFTER_SPONSORS.match(lines[i].strip()):
                end = i
                break
        lines = lines[end:]
    return "\n".join(_SPACE_RUN.sub(" ", line) for line in lines)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Montana's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`)."""
    return _clean_montana_text(prior_text), _clean_montana_text(raw_text)
