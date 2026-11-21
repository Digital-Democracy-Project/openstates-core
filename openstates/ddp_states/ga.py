"""
Georgia's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


# OPEN-602: Georgia's papers that are not versions of the bill. Georgia's web service lists
# every document of a bill as a version, so these arrive beside the real texts:
#   "Local Ad" / "SB 4EX Local Ad": the newspaper notice of intent to introduce a local bill
#   with the author's affidavit ("NOTICE OF INTENTION TO INTRODUCE LOCAL LEGISLATION ...
#   AFFIDAVIT ... O.C.G.A. § 28-1-14"), 98 of the 303 names measured.
#   "Floor Amend 1 AM 47 0219", "Sen Floor Amend 1A", "Floor Amendment AM 64 0009": one floor
#   amendment, a line edit ("Amend SB 3EX (LC 47 4392) by inserting after ... on line 8"),
#   adopted, lost or ruled out of order, the same call as the other states' amendment papers.
#   "Fiscal Note", "Local Government Fiscal Note": the fiscal note; "Auditor's Certification LC
#   56 0399S": the state auditor's certification of a retirement bill's actuarial investigation.
#   Neither is bill text (judged by the name: their PDFs are only on www.legis.ga.gov, which
#   does not answer from the Mac). They keep no stage, as Louisiana's bureau note does.
# Read on the 2026 special session's own PDFs (public govbot copy) and the names of a 2025-26
# sample from Georgia's web service (2026-11-16); no other state has a note of these shapes.
# The real versions stay: "/a", "As introduced", the substitutes, "As Passed", "/AP".
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A\s*(?:"
    r"(?:[hs][br]\s+\d+(?:ex)?\s+)?local\s+ad"
    r"|(?:sen\s+)?floor\s+amend(?:ment)?(?:\s+[1-9]\d*[a-z]?)?(?:\s+am\s+\d+\s+\d+)?"
    r"|(?:local\s+government\s+)?fiscal\s+note"
    r"|auditor's\s+certification\s+lc\s+\d+\s+\d+s?|lc\s+\d+\s+\d+s?\s+auditor's\s+certification"
    r")\s*\Z",
    re.IGNORECASE,
)

# OPEN-602: Georgia's versions carry no date and its names do not place them in time: a House
# substitute to a Senate bill ("LC 49 2362S/hss") comes after "As Passed Senate", and a bill can
# have two Senate committee substitutes with nothing in the names to order them (HB 61 of 2025-26).
# The site's own document number does: it rises as documents are published. Measured on the 123
# bills with two or more real versions in the 2026 special session (govbot copy) and a 313-bill
# 2025-26 sample from Georgia's web service (2026-11-16): ordered by it, 0 bills break the chamber
# sequence their status history shows (introduced, origin chamber's substitute, its passage, the
# other chamber's substitute, its passage, "/AP" last). Georgia's own "Version" field is not an
# order (SB 3EX: floor amendments 2 and 4, As introduced 6), so it is not used. For Georgia the
# document number leads and the note's stage breaks a tie; a version without a Georgia link
# number ranks first.
VERSION_ORDER = "document"  # version_ordering.version_sort_key: the document number leads


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Georgia's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    # OPEN-601: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_sometimes_numbered_pdf)}
