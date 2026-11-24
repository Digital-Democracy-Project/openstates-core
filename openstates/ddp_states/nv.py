"""
Nevada's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re


# ── Version rules ─────────────────────────────────────────────────────────────────────────────────────────────────────


# OPEN-849: Nevada's amendment papers are not versions of the bill. The scraper listed each "Amendment <N>" (the
# site's Bills/Amendments/A_<bill>[_R<N>]_<N>.pdf: the proposed amendment to the introduced text or to a reprint) as a
# version beside the real texts ("As Introduced", "Reprint <N>", "As Enrolled"). On the Mac replica (2026-11-24) the
# 26 such rows were stored as versions with a comparison each, and 10 "As Enrolled" texts were compared against one.
# OPEN-830 (openstates-scrapers #269) makes the scraper list them as documents; this keeps the rows already stored,
# and any re-listed one, out of the lineage, as Missouri's and Alabama's amendment papers are. Their stage (an amendment,
# note_stage) is unchanged, so they do not count as versions with no stage. "Reprint <N>", the bill reprinted with
# an adopted amendment, is a real version (note_stage, OPEN-849).
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(r"\Aamendment [1-9]\d*\Z", re.IGNORECASE)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Nevada's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    # OPEN-591: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_sometimes_numbered_pdf)}
