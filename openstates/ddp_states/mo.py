"""
Missouri's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Action labels (OPEN-831) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-831: in Missouri a bill passes a chamber on "Third Read and Passed" (and finally on "Truly Agreed To and Finally
# Passed"); a resolution passes the Senate on a bare "S adopted". The scraper's categorizer (scrapers/mo/bills.py) tags
# `passage` on every line that contains "S adopted", and the Senate writes that for everything it adopts on the floor
# before Third Reading: a substitute or committee substitute ("SS for SCS, as amended, S adopted", "SCS S adopted"),
# an amendment to one ("SA 1 to SS, as amended, S adopted"), the conference committee's report ("S adopted CCR
# (...)", followed by "CCS S Third Read and Passed", the bill's final vote) and a motion ("Motion to table SR 668 S
# adopted (<member>)", "Motion to reconsider Perfection Vote S adopted (<member>)", "Motion to allow S conferees to
# exceed differences S adopted"). On the Mac replica (2026-11-24, all 21,849 stored Missouri actions, session 2026)
# 44 such lines carry `passage`, and SB 1534, SB 838, SB 841 (Senate) and SR 668 (tabled) read as passed through them
# alone. So a line with "S adopted" or "H adopted" loses `passage` unless it is the bare "S adopted" / "H adopted" of
# a resolution, or the line says "Passed" (none stored says both; it keeps a Third Read and Passed line that also
# names an adoption a passage); other labels stay, and nothing is added. The House's same lines
# ("HCS, as amended, H adopted", "H adopts CCR", "House conferees allowed to exceed the differences H adopted") are
# stored with no label, and Missouri stores no `amendment-passage` or `failure` anywhere, so a tabled measure is left
# untagged too: the Senate can take it from the table. Case is kept, as in the scraper's own rule. The rule reads only
# the text (a state rule is not given the bill's identifier); on the replica a bare "S adopted" is stored only on SR
# and SCR and a bare "H adopted" only on SCR, the resolutions the Senate and House adopt in one vote.
_FLOOR_ADOPTED = re.compile(r"\b[SH] adopted\b")
_RESOLUTION_ADOPTED = re.compile(r"^\s*[SH] adopted\.?\s*$")
_PASSED = re.compile(r"\bPassed\b")

# OPEN-857: the House's adoption of a resolution is that resolution's passage in the House, but the categorizer has no
# rule for the House's wording, so it is stored with no label (Mac replica, 2026-11-24, every stored Missouri action):
# "Adopted (H)" on HR 1, HR 2, HR 3872, HR 3873, HCR 21 and HCR 22 (house.mo.gov's action list), "Adopted (H) - AYES:
# 31 NOES: 0 PRESENT: 0" on HCR 21 and HCR 22, and the bare "H adopted" on SCR 21 (senate.mo.gov's list: the House
# adopted the Senate's resolution on 05-14-26 after its committees reported it Do Pass). These gain `passage`, as the
# Senate's bare "S adopted" already has it. (The two "AYES: 31" lines of 01-08-26 are the Senate's adoption, "On motion
# of Senator ..., HCR 21 was adopted" in its stored roll call, which house.mo.gov prints with "(H)" and the scraper
# stores under the House; the rule reads only the text, so they gain it too.) The House's adoption of a substitute ("HCS Adopted (H)", on HJRs) or of an
# amendment ("... H adopted") is not a passage and is left as it is.
_HOUSE_RESOLUTION_ADOPTED = re.compile(r"^\s*(?:Adopted \(H\)(?:\s+-\s+AYES:.*)?|H adopted\.?)\s*$")


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on a substitute, amendment, conference report or motion "S/H adopted" line,
    `passage` on the House's adoption of a resolution, every other label as given."""
    text = description or ""
    if _HOUSE_RESOLUTION_ADOPTED.match(text):
        return list(classification) if "passage" in classification else list(classification) + ["passage"]
    if _FLOOR_ADOPTED.search(text) and not _RESOLUTION_ADOPTED.match(text) and not _PASSED.search(text):
        return [c for c in classification if c != "passage"]
    return list(classification)


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


# ── Reader choice (OPEN-908; replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ────


# OPEN-908: upstream reads every Missouri PDF with `extract_line_numbered_pdf`, which keeps only the lines that start
# with a margin line number. A bill's introduced and perfected printing has one ("1  Section A. Section 144.014,
# RSMo, ..."), but two kinds of paper do not: the Senate's committee substitutes (senate.mo.gov .../SCSfromResearch/
# SB1017.pdf: "SENATE COMMITTEE SUBSTITUTE FOR SENATE BILLS NOS. 1017 & 1239", body text unnumbered) and the Senate's
# resolutions (SCR 21: "Whereas, to secure the blessings of liberty ..."). For those the reader kept only the lines that
# happened to start with a digit, 53 characters of SB 1017's substitute ("are nonseverable and if any of the powers
# vested with") and 52 of SCR 21 ("Missouri Commission, and its forty-nine counterparts", from the line "250 Missouri
# Commission, ..."), or nothing. Read on the Mac's archive copy of all 4,856 Missouri 2026 PDFs and on the replica,
# 2026-11-26: 4,814 are text PDFs that pdftotext reads in full (the other 42, 34 of them amendment papers, have no text layer
# and read empty before and after). `extract_sometimes_numbered_pdf` (la, ne, nc, ga,
# nv) tells the two kinds apart by "more than 10% of the lines start with a number"; `extract_mo_pdf` below does the
# same with the share counted over the non-blank lines. On the first rule alone, of the 4,856 PDFs 132 read
# differently (63 Senate committee substitutes, 66 introduced papers, 3 others; 66 of them were empty, 130 had 300
# characters or fewer, none gets shorter) and the other 4,724 exactly as before; the 132 gain 1.99 million characters.
# OPEN-908 (second fix): the House prints no margin number "1" on the first line of a section, so even a numbered House
# bill lost those lines: "Section A. Chapter 167, RSMo, is amended by adding thereto one new section, to be" and
# "167.209. No school district or charter school ..." (HB 1880, whose stored text was 229 of 1,285 characters) both sit
# right above a line numbered "2". Read on the same archive copy (2026-11-26): 2,622 PDFs (2,586 on House bills) have such a
# line, 14,159 lines in all, each the opening line of a section or of a "That at the next general election ..." question.
# A line directly above a line numbered "2" that has no number of its own and is more than three characters (a bare
# "1" is the appropriations bills' number printed on a line of its own, 1,218 times, and a petition bill's "D" and "HD"
# marks are not text either) is that missing line 1, and is kept. Senate papers print the "1" (the 31 PDFs on
# Senate bills that gain such a line are the House's committee substitutes of them, typeset by the House).
# The 10% rule of `extract_sometimes_numbered_pdf` counts blank lines, which sends a one-section House bill (HB 1651:
# two numbered lines of sixteen) to the plain-text branch; here the share is of the non-blank lines.
_MARGIN_NUMBER = re.compile(r"\s*\d+\s+(.*)")
_MARGIN_TWO = re.compile(r"\s*2\s+\S")
_NUMBERED_SHARE = 0.10


def extract_mo_pdf(data: bytes, metadata: typing.Any) -> str:
    # imported at call time: openstates.fulltext builds its reader table from this module
    from openstates.fulltext.utils import pdfdata_to_text

    text = pdfdata_to_text(data)
    lines = text.split("\n")
    numbered = sum(1 for line in lines if _MARGIN_NUMBER.match(line))
    if numbered <= _NUMBERED_SHARE * sum(1 for line in lines if line.strip()):
        return text  # no margin numbers: a committee substitute or a resolution, read as it is printed
    kept = []
    for i, line in enumerate(lines):
        match = _MARGIN_NUMBER.match(line)
        if match:
            kept.append(match.group(1))
        elif (
            i + 1 < len(lines)
            and len(line.strip()) > 3
            and _MARGIN_TWO.match(lines[i + 1])
        ):
            kept.append(line.strip())  # the House's line 1, printed without its number
    return "\n".join(kept)


def readers():
    """Missouri's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    return {"application/pdf": extract_mo_pdf}
