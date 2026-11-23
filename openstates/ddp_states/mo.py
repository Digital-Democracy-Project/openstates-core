"""
Missouri's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Version rules (OPEN-806) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-806: the scraper (scrapers/mo/bills.py) stores two kinds of paper as versions beside the bill's texts. Neither
# is a text of the bill; each is an amendment or substitute offered on the floor, whose result the legislature prints
# as the bill's next text ("all amendments and substitutes are incorporated in any bill amended or substituted for
# when printed as perfected or truly agreed to", Senate Rule 21). Read on the sites and the stored links, Mac replica,
# 2026 session, 2026-11-23 (4,798 versions):
#   "Amendment <name> (<status>)": every House bill's amendment, adopted or not ("Amendment HA 1 (Adopted)",
#   "Amendment 2002H03.18H (Distributed)", "Amendment HPA 1 (Adopted)", "Amendment HSA 1 HA 2 (Adopted)", "Amendment
#   SENATE AMENDMENTS (Distributed)", "Amendment CCR (Distributed)": the conference committee's report). All 604 link
#   to documents.house.mo.gov/.../amendpdf/ ("House ____ Amendment NO.____ Offered By ..."); no bill text does.
#   "<date> - SA 1 to SS S offered & adopted (<member>)--(<file>)\r\n ... <date> - Adopted" and the like (SA, SS, SS#2,
#   "... for SCS", CCR, CCS): the cards of a Senate bill's "Amendments" tab on senate.mo.gov ("The following amendments
#   are available"), one line for the paper and one for its status. All 142 link to its AmendmentPdf handler; the
#   bill's texts are on its "BillText" tab ("The following versions of this bill are available"). One is the
#   conference committee substitute as offered ("CCS S Third Read and Passed"); its passed text is the bill's "Truly
#   Agreed to and Finally Passed" version, as on every other bill.
_MO_SENATE_AMENDMENT_CARD = (
    r"(?:\d{1,2}/\d{1,2}/\d{4}|\d{4}s\d*\.\d+[a-z]) - [^\r\n]+\r?\n\s*\d{1,2}/\d{1,2}/\d{4} - [a-z][a-z ,]*"
)

PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A(?:amendment .+|" + _MO_SENATE_AMENDMENT_CARD + r")\Z",
    re.IGNORECASE,
)

# The text-number prefix senate.mo.gov puts on a Senate bill's text names ("5607S.02P - Perfected", "5607H.07C - House
# Committee Substitute"); the House's names of the same texts have none ("Perfected", "House Committee Substitute").
TEXT_NUMBER_PREFIX = r"(?:\d{4}[hs][a-z]?\.\d+[a-z] - )?"


# OPEN-806: Missouri's versions carry no date, and a committee or floor substitute comes at a different point for a
# House bill and a Senate bill: a bill is introduced, may get a committee substitute and a floor substitute in its own
# chamber, and is then printed "Perfected" (Senate Rule 38: debated and amended "before a vote on the question to
# perfect and print it"; Rule 46's three printings, "upon introduction, after perfection, and upon final passage") and
# third read; the second chamber's committee and floor substitutes come after that, then the conference committee
# substitute, then "Truly Agreed to and Finally Passed". Read on the texts' own headers (HB 2002: "[PERFECTED] HOUSE
# COMMITTEE SUBSTITUTE FOR HOUSE BILL NO. 2002", then "SENATE SUBSTITUTE FOR SENATE COMMITTEE SUBSTITUTE FOR HOUSE
# COMMITTEE SUBSTITUTE FOR HOUSE BILL NO. 2002", then "[TRULY AGREED TO AND FINALLY PASSED] CONFERENCE COMMITTEE
# SUBSTITUTE FOR ..."). In its own chamber such a text is an amendment-stage text (committee, then floor); in the
# second it comes after "Perfected" / "Re-Perfected" (chamber passage 0 / 0.01, note_stage) and before the conference
# committee substitute (0.25, note_stage's "conference" rule).
#   "Senate Committee Substitute - Committee Version" (senate.mo.gov .../SCSfromResearch/SB1060.pdf: "4565S.03C SENATE
#   COMMITTEE SUBSTITUTE FOR SENATE BILL NO. 1060") is a Senate bill's committee substitute too. It is only ever on a
#   Senate bill, with no other committee text, so note_stage's amendment rank already places it and it needs no entry.
_CHAMBER_TEXTS = {
    "house committee substitute": ("house", 1),
    "house substitute (house floor substitute)": ("house", 2),
    "senate committee substitute": ("senate", 1),
    "senate substitute (senate floor substitute)": ("senate", 2),
}


def version_rank(note: str, origin: typing.Optional[str]) -> typing.Optional[typing.Tuple[int, float]]:
    """Missouri's (stage, ordinal) for `version_ordering.version_sort_key`, given the version's note and the bill's
    origin chamber ("house" / "senate"), or None to keep `note_stage`'s. See the block comment above."""
    # imported at call time: version_ordering imports this module
    from openstates.utils.version_ordering import STAGE_AMENDMENT, STAGE_CHAMBER_PASSAGE

    name = re.sub(r"\A" + TEXT_NUMBER_PREFIX, "", note.lower())
    if name not in _CHAMBER_TEXTS or origin not in ("house", "senate"):
        return None
    chamber, step = _CHAMBER_TEXTS[name]
    if chamber == origin:
        return (STAGE_AMENDMENT, float(step))
    return (STAGE_CHAMBER_PASSAGE, 0.1 * step)
