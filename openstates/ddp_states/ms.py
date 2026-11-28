"""
Mississippi's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Pre-diff text cleaning ──────────────────────────────────────────────────────────────────────────────────────────


# OPEN-949: Mississippi's comparisons were mostly noise. Measured on the Mac replica (every stored comparison, 3,602 of
# them, 1,801 HTML and 1,801 PDF, 2026-11-27): the HTML text opens with a header block ("MISSISSIPPI LEGISLATURE",
# the session, the committee, the author, the bill number, the stage in parentheses) whose stage line and bill number
# line change with every version, so "(As Passed the Senate)" appearing after the bill number was a changed line of
# its own; 850 of the 1,801 HTML comparisons changed nothing else. Three shapes, each only as a whole line, as printed
# (no padded or indented one was seen):
#   the stage line, one of "(As Passed the House)", "(As Passed the Senate)", "(As Sent to Governor)", "(As Adopted
#   by House)", "(As Adopted by Senate)", "(As Adopted by House and Senate)", "(As Adopted by Senate and House)" and
#   "(COMMITTEE SUBSTITUTE)" (all 1,795 occurrences sit in lines 10 to 19 of an HTML document);
#   the bill number line, "House Bill 1606", "Senate Concurrent Resolution 5" (the chamber, "Concurrent" for a
#   concurrent resolution, "Bill" or "Resolution", the number; 1,795 in HTML, none in PDF), no "No." and nothing else on
#   the line, so a sentence that cites a bill ("... established in House Bill No. 1, 2026") is body text and stays;
#   the "* * *" mark that stands for text left out between two sections (385 in the PDFs, none in the HTML), which
#   moves from one version to the next.
# Blank lines go as well: 31,962 of the short changed lines were empty, and a blank line added or removed is not a word.
# Every other line is left exactly as it was. A bare year or number line ("1985.", "5") is body text (the tail of a
# wrapped sentence, a table cell) and stays; so do the budget lines ("........ $") and the list markers.
_MS_HEADER_LINE_RE = re.compile(
    r"\((?:As Passed the (?:House|Senate)|As Sent to Governor"
    r"|As Adopted by (?:House|Senate)(?: and (?:House|Senate))?|COMMITTEE SUBSTITUTE)\)"
    r"|(?:House|Senate)(?: Concurrent)? (?:Bill|Resolution) [1-9]\d*"
    r"|\* \* \*"
)


def _clean_ms_text(text: str) -> str:
    """Mississippi-only cleaner, see the block comment above: blank lines and the whole-line stage, bill number and
    "* * *" lines are dropped, every other line is kept unchanged."""
    return "\n".join(line for line in text.split("\n") if line.strip() and not _MS_HEADER_LINE_RE.fullmatch(line))


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Mississippi's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`)."""
    return _clean_ms_text(prior_text), _clean_ms_text(raw_text)


# ── Version rules (OPEN-807) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-807: the bill's history XML on billstatus.ls.state.ms.us (<session>/pdf/history/<HB|SB..>/<bill>.xml) lists,
# beside the bill's texts, the amendments adopted along the way under <AMENDMENTS>, and the scraper stores the adopted
# ones and the amendment reports as versions (scrapers/ms/bills.py, "# amendments"). These are motions, not versions
# of the bill (read on the site's pages and the stored names, Mac replica, 2026 and 20261E sessions, 2026-11-23):
#   "Amendment No <N>", "Committee Amendment No <N>", "Amendment No <N> (Cmte Sub)" (an amendment to the committee
#   substitute), "Amendment No <N> to [Committee ]Amendment No <M>", "Substitute No <N> for Committee Amendment No
#   <M>", each followed by its disposition and vote in parentheses ("(Adopted) (Voice Vote)"; "({Vote})" as printed):
#   one HAM / SAM entry, the amendment paper itself ("AMENDMENT NO 1 PROPOSED TO House Bill No. 1 BY: Representative
#   ... AMEND on lines 44 & 45 ..."; HB 1924's "COMMITTEE AMENDMENT NO 1 PROPOSED TO House Bill No. 1924 BY:
#   Committee Amend by striking all after the enacting clause ..."), 464 of the 6,542 versions.
#   "Amendment Report for <House|Senate> <Bill|Concurrent Resolution> No. <N>": the AMRPT entry, one chamber's
#   message to the other's clerk listing the amendments it adopted ("TO THE CLERK OF THE HOUSE: THIS IS TO INFORM YOU
#   THAT THE SENATE HAS ADOPTED THE AMENDMENTS SET OUT BELOW: AMENDMENT NO. 1 ..."), 263 of the 6,542.
# The bill's texts stay versions: "As Introduced", "Committee Substitute", "As Passed the lower|upper", "Approved by
# the Governor" (and, until the scraper fix of the same ticket is re-scraped, "Current version"). Their stage is
# untouched: the papers already have one (amendment, or for the reports note_stage's conference-or-report rule), so
# they never counted as versions with no stage; they now drop out of the comparison chain, and the next real text
# compares with the last real one (recompute dry run, Mac replica: 1,118 documents not a version, 356 comparisons
# corrected).
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A(?:"
    r"(?:(?:committee )?amendment|substitute) no [1-9]\d*(?: (?:to|for) (?:committee )?amendment no [1-9]\d*)?"
    r"(?: \([^()]+\))*"
    r"|amendment report for (?:house|senate) (?:bill|concurrent resolution|resolution) no\. +[1-9]\d*"
    r")\Z",
    re.IGNORECASE,
)
