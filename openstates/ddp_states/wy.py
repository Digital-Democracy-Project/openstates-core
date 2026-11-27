"""
Wyoming's action-label rules (OPEN-821, PLAN-state-specific-handling §5.4, epic OPEN-146).

Found by the bill-stages check of Wyoming's go-live (OPEN-662) on the Mac replica (2026-11-24, session 2026, 3,713
stored actions on 335 bills). Two of the scraper's labels (`scrapers/wy/actions.py`) are wrong:

* "H Failed Introduction 39-23-0-0-0" / "S Failed Introduction ..." (60 House and 32 Senate lines, 92 bills) carries
  no label. In the 2026 budget session a bill that is not the budget needs two thirds of the chamber to be introduced,
  and this vote ends it (HB 50: nothing follows). Without a label the bill reads as Introduced, so it gets `failure`.
  A chamber can still revive such a bill under its rules (HB 116 (2026): "H Pursuant to HR 12-11 (a) Passed by Roll
  Call", then introduced and passed by the House); the later lines carry their own labels, so it reads on from there.
* "Bill Number Assigned" (335 lines, one per bill, stored under the executive: the Legislative Service Office numbering
  the draft before a chamber receives it) is labelled `reading-1`. It is not a first reading, so it loses that label.

"H Did not Consider for Introduction" (48 lines, `deferral`) also ends a bill in a budget session, but no vote took
place; it is left as the scraper labels it (a judgement call listed in the OPEN-821 pull request). Every other line,
and every other label, is as given.
"""

import re
import typing

_FAILED_INTRODUCTION = re.compile(r"^[HS] Failed Introduction\b", re.I)
_NUMBER_ASSIGNED = re.compile(r"^Bill Number Assigned\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `failure` added on a chamber's "Failed Introduction" vote, no `reading-1` on "Bill Number
    Assigned", every other label as given."""
    text = description or ""
    labels = list(classification)
    if _FAILED_INTRODUCTION.match(text) and "failure" not in labels:
        labels.append("failure")
    if _NUMBER_ASSIGNED.match(text):
        labels = [c for c in labels if c != "reading-1"]
    return labels


# ── Version rules (OPEN-835) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-835: the bill page on wyoleg.gov lists, beside the bill's texts, every amendment offered on it and the
# governor's veto letters, and the scraper stores them as versions. These are motions and letters, not versions of the
# bill (read on the stored names and texts, Mac replica, session 2026, 1,295 documents on 335 bills, 2026-11-24):
#   "Amendment <BILL><CHAMBER/COMMITTEE CODE><NNN>[.NN] (<stage>)[ - <sponsor or committee>][ (<disposition>)]", 840:
#   a standing committee's, the Committee of the Whole's, a 2nd or 3rd reading, a conference committee's or a veto
#   override amendment, adopted, failed, withdrawn, divided, corrected or ruled out of order. Each is a line edit
#   ("Page 2-lines 12 through 23 Delete entirely and renumber as necessary"), 183 of them under 200 characters; they
#   took a comparison like a version, so 87 Enrolled texts were compared with an amendment paper, not the Introduced
#   text.
#   "Governor's Veto Message", "Governor's Line Item Veto Message", "Governor's Line Item Vetoes", 5: the governor's
#   letters to the presiding officer ("Re: Line-Item Veto of ..."), or empty.
# The bill's texts stay versions: "Introduced", "Enrolled", and SF 1's "Chaptered Law with Final Veto Actions" (the
# enacted budget with the vetoes marked in it).
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A(?:amendment .+|governor's (?:line item )?veto(?: message|es))\Z",
    re.IGNORECASE,
)


# ── Reader choice (replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ─────────────


def readers():
    """Wyoming's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_sometimes_numbered_pdf

    # OPEN-927: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_sometimes_numbered_pdf)}
