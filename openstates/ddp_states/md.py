"""
Maryland's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""


# ── Reader choice (replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ──────────────


def readers():
    """Maryland's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_sometimes_numbered_pdf

    # OPEN-837: Maryland's chapter laws ("Chapters_noln/CH_<n>_<bill>.pdf", "noln" = no line numbers) are printed
    # without line numbers, and upstream's line-numbered reader keeps only numbered lines, so a chapter read to
    # nothing or to its cover line alone ("Ch. 560 / Chapter 560"). The bill texts (First, Third, Enrolled) number
    # every line. The shared reader decides from the document itself, not from the folder name: more than 10%
    # numbered lines -> the line-numbered reader, as before; otherwise the whole text. Measured 2026-11-24 on every
    # chapter PDF of 2026RS and 2026S1 (886), every Enrolled (238) and 306 First and 302 Third texts: a chapter
    # has at most 2% numbered lines and a bill text at least 29%, so every chapter reads whole and every bill text
    # reads exactly as before. The Racial Equity Impact Notes (69, no line numbers, at most 3%) read whole too.
    return {"application/pdf": extract_sometimes_numbered_pdf}
