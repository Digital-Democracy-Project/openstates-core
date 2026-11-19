"""
Pennsylvania's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re
import typing


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# OPEN-495: Pennsylvania-only text cleaning applied to prior_text/raw_text immediately before
# difflib.unified_diff() (never touching the stored raw_text, never applied to another
# jurisdiction). Measured 2026-11-06 on the production replica (Pennsylvania 2025-2026, 6,188
# documents, 921 stored comparisons): the General Assembly's printings carry four kinds of
# noise that a line diff reports as changes.
#   1. A change bar: new or reprinted lines end in the right-margin marker "<--" (909 of the 921
#      comparisons contain it). It is on the added line and not on the unchanged line it replaces.
#   2. Capital letters: new language is printed in CAPITALS and the next printing folds it back to
#      normal case (HB 1667 PN3745: 95% of the removed lines came back identical but for case).
#   3. Spacing: the same line is printed with a different number of spaces between words.
#   4. A "PRINTER'S NO. 3745" line at the top of 12 documents.
# What it does, line by line (the line structure is kept: Pennsylvania wraps both printings at the
# same width, so there is no reflow): drop the printer's-number line, remove the trailing change
# bar, collapse runs of spaces, and lower-case the line so a case-only difference is no change.
# Struck-out words ("60 180 days") cannot be told from the PDF's text, because the PDF extraction
# has no strike-through; "[bracketed]" words are real text and are kept.
# OPEN-516: Pennsylvania's text now comes from its OpenDocument printing (fulltext/pa.py), which
# leaves struck words out and gives one line per paragraph instead of one per printed line. The
# same cleaner applies to it unchanged (it has no change bars, and its capitals are already in
# normal case); the PDF documents archived before it keep their own diff lineage (OPEN-217).
# OPEN-516: also the "PRIOR PRINTER'S NO. 3586" / "PRIOR PRINTER'S NOS. 912, 934, 1427" line above
# it in every amended printing. The OpenDocument text keeps it, and the list grows by one number
# with each printing, so it was a changed line in every comparison.
# OPEN-499: some printings draw the change bar as an arrow glyph "\u25c4" (U+25C4) instead of
# "<--" (12 PDF documents, 449 lines, replica 2026-11-14; every one at the end of its line, so
# only a trailing glyph is a change bar and one inside a line is kept as text).
_PA_PRINTER_NO_LINE_RE = re.compile(
    r"(?:PRIOR\s+)?PRINTER'S NOS?\.\s*\d+(?:\s*,\s*\d+)*", re.IGNORECASE
)


_PA_CHANGE_BAR_RE = re.compile(r"\s*(?:<--|\u25c4)\s*$")


def _clean_pa_text(text: str) -> str:
    """Pennsylvania-only cleaner, see the block comment above: printer's-number line dropped,
    change bar removed, spaces collapsed and case ignored."""
    kept = []
    for line in text.split("\n"):
        line = " ".join(_PA_CHANGE_BAR_RE.sub("", line).split())
        if _PA_PRINTER_NO_LINE_RE.fullmatch(line):
            continue
        kept.append(line.lower())
    return "\n".join(kept)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Pennsylvania's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _clean_pa_text(prior_text), _clean_pa_text(raw_text)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Pennsylvania's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext import DoNotDownload
    from openstates.fulltext.pa import ODT_MEDIA_TYPE, handle_pennsylvania_odt
    return {
        # OPEN-516: Pennsylvania's text comes from its "DOC" printing, which is an OpenDocument
        # file (see fulltext/pa.py): struck-out words are marked there and left out, where the
        # PDF printed them struck through and the extraction kept them as if still in the bill.
        # The site labels it application/msword, and the archiver stores it under its real type
        # (`_sniffed_media_type` in cli/text_extract.py), so both keys read it. Every
        # Pennsylvania version has a DOC link (6,204 of 6,204 on the replica, 2026-11-09), so
        # the PDF is no longer archived; PDF documents already archived stay as they are.
        "application/msword": handle_pennsylvania_odt,
        ODT_MEDIA_TYPE: handle_pennsylvania_odt,
        "text/html": DoNotDownload,
        "application/pdf": DoNotDownload,
    }
