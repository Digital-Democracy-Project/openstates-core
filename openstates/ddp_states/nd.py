"""
North Dakota's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

# ── Reader choice (replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ──────────────


def readers():
    """North Dakota's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_sometimes_numbered_pdf

    # OPEN-836: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_sometimes_numbered_pdf)}
