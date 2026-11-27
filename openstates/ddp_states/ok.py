"""
Oklahoma's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Version rules (OPEN-788) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-788: the bill page on oklegislature.gov (BillInfo.aspx) has a "Versions" table and, beside it, the papers
# offered along the way under "Floor Amendments (House|Senate)", "Committee Amendments (House|Senate)" and "Sub
# Committee OR Policy Committee Recommendations (House)". The scraper stores all of them as versions
# (scrapers/ok/bills.py, scrape_amendments). These are motions, not versions of the bill (read on the site's pages and
# the stored texts, Mac replica, 2025 and 2026 sessions, 2026-11-23):
#   "<N> Floor Amendment by <MEMBER>" (FLOOR AMENDMENTS/House/... FA<N>), "<N>  Amendment to Amendment by <MEMBER>"
#   (... FA<N>-A<M>): a member's floor amendment or floor substitute in the House, adopted or not.
#   "HB1001 (4-29-25) (THOMPSON) RT FA1.PDF" (FLOOR AMENDMENTS/Senate/...): the same in the Senate, named after its
#   file ("FS" floor substitute, "RT" title amendment, "WD" withdrawn); the prefix is one of Oklahoma's measure types.
#   "Committee Amendment[ <N>]": in the Senate the committee's proposed substitute (Senate/... PCS<N>.PDF; 357 of the
#   422 bills with one also have the adopted "Committee Substitute" / "Senate Committee Substitute for House Bill",
#   which stays a version), in the House a member's amendment in committee (House/... FULLAMD<N>).
#   "Proposed Policy Committee Substitute <N>", "Proposed Committee Substitute (full committee) <N>", "(sub
#   committee) <N>", "Proposed ... Committee Amendment ... <N>" and their "- Amendment <M>", "Amendment to ...": the
#   substitutes and amendments proposed in a House committee ("PROPOSED COMMITTEE SUBSTITUTE"); the one the committee
#   adopts is its "... Recommendation" or "House Committee Substitute", which stay versions.
# The bill's texts stay versions: "Introduced", the recommendations and committee substitutes, "Floor (House|Senate)",
# "Engrossed", "Amended And Engrossed", the conference committee substitutes and "Enrolled (final version)".
_OK_SENATE_FLOOR_AMENDMENT_FILE = (
    r"(?:hb|sb|hjr|sjr|hcr|scr|hr|sr)\d+ \(\d{1,2}-\d{2}-\d{2}\) \([a-z][a-z' .-]*\) +(?:(?:fs|rt) )?(?:wd)?fa[1-9]\d*(?: \(\d+\))?\.pdf"
)

PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A(?:"
    r"[1-9]\d* +(?:floor amendment|amendment to amendment) by .+"
    r"|committee amendment(?: [1-9]\d*)?"
    r"|proposed (?:policy committee (?:substitute|amendment)|committee (?:substitute|amendment) \((?:full|sub) committee\))"
    r" [1-9]\d*(?: - amendment [1-9]\d*)?"
    r"|proposed policy committee recommendation - - amendment [1-9]\d*"
    r"|amendment to (?:proposed committee substitute \((?:full|sub) committee\) [1-9]\d*|sub committee recommendation)"
    r"|" + _OK_SENATE_FLOOR_AMENDMENT_FILE + r")\Z",
    re.IGNORECASE,
)


# OPEN-788: Oklahoma's versions carry no date and the same name comes at a different point for a House bill and a
# Senate bill: "Floor (House)" (FLR/HFLR, "HOUSE OF REPRESENTATIVES - FLOOR VERSION") is the text the House committee
# reported, placed on the House floor; for a House bill it comes before "Engrossed", for a Senate bill after it. So the
# order of a committee text depends on whether its chamber is the bill's own (origin_chamber of the identifier). Read
# on the bill pages' version dates and histories (HB 1002, HB 4478, 2026-11-23):
#   House: "Sub Committee Recommendation" / "Policy Committee Recommendation" (the text an Appropriations and Budget
#   subcommittee or a policy committee recommends to the full committee: "Recommendation to the full committee; Do
#   Pass, amended by committee substitute"), then the full committee's "House Committee Substitute" ("... for Senate
#   Bill" on a Senate bill), then "Floor (House)".
#   Senate: "Committee Substitute" ("Senate Committee Substitute for House Bill" on a House bill), then "Floor (Senate)".
# In its own chamber such a text is an amendment-stage text, in that order. In the second chamber it comes after
# "Engrossed" (chamber passage, 0) and before "Amended And Engrossed" (the second chamber's engrossed amendments) and
# the conference committee substitutes (0.25 and up, note_stage's "conference" rule), which come before "Enrolled".
_CHAMBER_TEXTS = {
    "sub committee recommendation": ("house", 0),
    "policy committee recommendation": ("house", 0),
    "house committee substitute": ("house", 1),
    "house committee substitute for senate bill": ("house", 1),
    "floor (house)": ("house", 2),
    "committee substitute": ("senate", 1),
    "senate committee substitute for house bill": ("senate", 1),
    "floor (senate)": ("senate", 2),
}


def version_rank(note: str, origin: typing.Optional[str]) -> typing.Optional[typing.Tuple[int, float]]:
    """Oklahoma's (stage, ordinal) for `version_ordering.version_sort_key`, given the version's note and the bill's
    origin chamber ("house" / "senate"), or None to keep `note_stage`'s. See the block comment above."""
    # imported at call time: version_ordering imports this module
    from openstates.utils.version_ordering import STAGE_AMENDMENT, STAGE_CHAMBER_PASSAGE

    lowered = note.lower()
    if lowered == "amended and engrossed":
        return (STAGE_CHAMBER_PASSAGE, 0.1)
    if lowered not in _CHAMBER_TEXTS or origin not in ("house", "senate"):
        return None
    chamber, step = _CHAMBER_TEXTS[lowered]
    if chamber == origin:
        return (STAGE_AMENDMENT, float(step))
    return (STAGE_CHAMBER_PASSAGE, 0.01 * (step + 1))


# ── Reader choice (OPEN-914; replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ─────


# OPEN-914: upstream reads Oklahoma's PDFs with `extract_sometimes_numbered_pdf`: a paper with margin line numbers on
# more than 10% of its lines is read with `extract_line_numbered_pdf`, which keeps only the lines that start with a
# number. Oklahoma prints margin numbers 1-24 on every page, but pdftotext does not put them beside every line of
# text: the title paragraph's numbers sit on every other line ("9   An Act relating to debtors and creditors; creating"
# / "    the Uniform Assignment for Benefit of Creditors Act;" / "10  defining terms; ..."), a number is sometimes
# printed on a line of its own above the text ("B. The Board is authorized and empowered to:" and the items 1 and 2
# under it, SB 439 introduced), and a few lines hold a heading or a short continuation ("and", "FOR"). Every such line,
# a whole paragraph in places, was dropped. Read on the Mac's archive copy, 251 sampled Oklahoma PDFs (2026-11-27):
# 97 lose 5% or more of their body lines and 35 lose 10% or more (SB 374 introduced 200 of 792).
# A line without a number between the first and the last numbered line of the paper is body text and is kept, as
# Missouri's reader keeps the House's line 1 (OPEN-908). What is not body, as before, stays out: a line that is only a
# number, and the running header or footer a page repeats ("Req. No. 1140  Page 1", "ENGR. H. B. NO. 3588  Page 2",
# "SENATE FLOOR VERSION - SB1 SFLR", "BOLD FACE denotes Committee Amendments."): an unnumbered line among the first
# two or last three text lines of a page whose text, digits aside, is among those lines on three or more pages (the
# footer is printed in pieces, so it is not always the very last line).
_NUMBERED = re.compile(r"\s*\d+\s+(.*)")
_BARE_NUMBER = re.compile(r"\s*\d+\s*\Z")
_PAGE_EDGES_TO_REPEAT = 3  # pages that must share an edge line for it to be a running header or footer
_HEAD_LINES, _FOOT_LINES = 2, 3


def _running_key(line: str) -> str:
    """A line's text with digits blanked and spacing collapsed, so "Page 1" and "Page 12" are one footer."""
    return " ".join(re.sub(r"\d+", "0", line).split())


def _running_lines(pages: typing.List[typing.List[str]]) -> typing.Set[str]:
    """The unnumbered lines' texts (digits blanked) that sit at the top or bottom of three or more pages."""
    seen: typing.Dict[str, typing.Set[int]] = {}
    for n, page in enumerate(pages):
        text = [line for line in page if line.strip() and not _BARE_NUMBER.match(line)]
        for line in text[:_HEAD_LINES] + text[-_FOOT_LINES:]:
            if not _NUMBERED.match(line):
                seen.setdefault(_running_key(line), set()).add(n)
    # a line that ends "Page <n>" is a page footer wherever it sits, even in a paper of two pages
    return {key for key, on in seen.items() if len(on) >= _PAGE_EDGES_TO_REPEAT or key.endswith(" Page 0")}


def extract_ok_pdf(data: bytes, metadata: typing.Any) -> str:
    # imported at call time: openstates.fulltext builds its reader table from this module
    from openstates.fulltext.utils import pdfdata_to_text

    text = pdfdata_to_text(data)
    lines = text.split("\n")
    # upstream's switch between the numbered and the plain reading, unchanged (`extract_sometimes_numbered_pdf`)
    # (counted over the whole text, as upstream does: its `\s+` can run across a line break)
    numbered = re.findall(r"^\s*\d+\s+(.*)", text, flags=re.MULTILINE)
    if len(numbered) / len(lines) <= 0.10:
        return text
    at = [i for i, line in enumerate(lines) if _NUMBERED.match(line)]
    if not at:
        return ""  # as `extract_line_numbered_pdf` returns
    first, last = at[0], at[-1]
    running = _running_lines([page.split("\n") for page in text.split("\f")])
    kept = []
    for i, line in enumerate(lines):
        match = _NUMBERED.match(line)
        if match:
            kept.append(match.group(1))
        elif (
            first < i < last
            and line.strip()
            and not _BARE_NUMBER.match(line)
            and _running_key(line) not in running
        ):
            kept.append(line.strip())
    return "\n".join(kept)


def readers():
    """Oklahoma's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    return {"application/pdf": extract_ok_pdf}
