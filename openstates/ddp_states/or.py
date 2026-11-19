"""
Oregon's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Oregon's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    from openstates.fulltext.oregon import without_bracketed_text
    # OPEN-600: the words a bill deletes from current law, printed [italic and bracketed], are
    # left out (see fulltext/oregon.py).
    return {"application/pdf": without_bracketed_text(extract_sometimes_numbered_pdf)}
