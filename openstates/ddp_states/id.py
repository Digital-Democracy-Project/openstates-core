"""
Idaho's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re


# ── Version rules (OPEN-834) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-834: the bill page on legislature.idaho.gov lists the bill's documents, and the scraper stores every link whose
# name holds "Bill Text", "Engrossment" or "Amendment" as a version (scrapers/id/bills.py, "# documents"). The
# "Amendment" links (<bill>A<N>.pdf, or <bill>E<M>A<N>.pdf for an amendment to the engrossed bill) are the amendments
# a chamber adopted, written as line edits to the printed bill ("AMENDMENT TO SECTION 1 On page 3 of the printed bill,
# in line 8, delete ... and insert: ...", "AMENDMENT TO THE BILL On page 1 of the printed bill, delete lines 6 through
# 41 ..."): motions, not versions of the bill. Their result is the bill's next "Engrossment <N>", which stays a
# version, as "Bill Text" does. Read on the stored names and texts, Mac replica, 2026 session, 2026-11-24: 924
# documents (Bill Text 817, Amendment 56, Engrossment 1 50, Engrossment 2 1); every one of the 56 "Amendment" texts
# opens "AMENDMENT TO". Their stage is untouched (note_stage's amendment rule already places them); they now drop out
# of the comparison chain, and an engrossment compares with the bill's text instead of with the amendment paper.
# The optional " (<document id>)" is the scraper's suffix on a name repeated within one bill (OPEN-428): two bills
# (H 561, H 575) have two amendment papers each.
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(r"\Aamendment(?: \(\d+\))?\Z", re.IGNORECASE)


# ── Reader choice (replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ─────────────


def readers():
    """Idaho's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_line_numbered_pdf

    # OPEN-895: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_line_numbered_pdf)}
