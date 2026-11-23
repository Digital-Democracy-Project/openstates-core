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
