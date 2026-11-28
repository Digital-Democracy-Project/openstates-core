"""
Kentucky's action-label rules (OPEN-845, PLAN-state-specific-handling §5.4, epic OPEN-146).

Found by the bill-stages check of Kentucky's go-live (OPEN-667) on the Mac replica (2026-11-24, session 2026RS, 12,393
stored actions on 1,737 bills). The scraper's categorizer (`scrapers/ky/bills.py`, `_action_classifiers`, first
`re.match` wins) gets two kinds of floor line wrong:

* "passed over and retained in the Orders of the Day" (67 lines), "... in the Consent Orders of the Day" (24) and
  "... retained for concurrence in House Committee Substitute (1) ..." (13) are tagged `passage`: the chamber skipped
  the bill on that day's calendar, and the rule ("passed", "passage") catches the word. They lose `passage`, as
  Nebraska's "Passed over" lines do; no label is added.
* "3rd reading, adopted 38-0 ..." (29 lines, joint and concurrent resolutions) is the chamber's roll-call vote on a
  resolution, but "3rd reading" matches first and the line gets only `reading-3`. It gets `passage`, as "3rd reading,
  passed 38-0 ..." has. SJR 62 (2026RS) shows both: passed over on 3/11, adopted by the Senate 38-0 on 3/12; with the
  first rule alone it would read as never passing the Senate.

Every other line, and every other label, is as given: the bills' real votes ("3rd reading, passed 38-0 ...", "passed
32-6", "adopted by voice vote") keep `passage`, and a simple resolution's bare "adopted 38-0" and the veto-override
"adopted 88-4" are left alone.

Presiding officers' signatures (OPEN-947): "enrolled, signed by President of the Senate" / "... Speaker of the House"
(240 lines each) also carried `passage` although they are signatures, not votes (the same check that rejects
Washington's "Speaker signed." and Oregon's signature lines). OPEN-845 kept them on purpose: the scraper then stored
every second-chamber floor line under the origin chamber, so the Senate President's signature was the only line under
the other chamber and the only thing that made a bill read as passed by both chambers. OpenStates scrapers' OPEN-852
now stores a second-chamber line under the chamber that voted (187 of 501 "3rd reading, passed ..." lines on the
replica, the rest correctly under the origin chamber), so the signatures lose `passage`. 203 of the 204 bills with
an "enrolled, signed" line already have real passage lines in both chambers; the one that does not, HB 642, is a
documented known miss (Ramon, 2026-11-24).
"""

import re
import typing

_PASSED_OVER = re.compile(r"^passed over and retained\b", re.I)
_RESOLUTION_ADOPTED = re.compile(r"^3rd reading, adopted\b", re.I)
_SIGNED = re.compile(r"^enrolled, signed by\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on a "passed over and retained ..." or an "enrolled, signed by ..." line,
    `passage` added on a "3rd reading, adopted ..." line, every other label as given."""
    text = description or ""
    labels = list(classification)
    if _PASSED_OVER.match(text) or _SIGNED.match(text):
        labels = [c for c in labels if c != "passage"]
    elif _RESOLUTION_ADOPTED.match(text) and "passage" not in labels:
        labels.append("passage")
    return labels


# ── Reader choice (OPEN-906; replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ──────

# Kentucky's bill PDFs print a margin line number on every line, and upstream reads them with
# `extract_line_numbered_pdf`, which keeps only the lines that start with a number. The chaptered acts ("Acts Chapter N",
# the version the legislature publishes once a bill is enacted, apps.legislature.ky.gov/law/acts/26RS/documents/NNNN.pdf)
# are a different layout: a Word document with no line numbers, so that reader kept only the page-header lines ("2
# ACTS OF THE GENERAL ASSEMBLY") or nothing. Read on the replica and the archive copy, 2026RS, 2026-11-26: 202 acts, 164
# of them 300 characters or fewer (23 empty), every one a normal text PDF that pdftotext reads in full. Every act opens
# with "CHAPTER <number>" and no bill PDF does (checked on all 202 acts and all 2,359 bill PDFs), so an act is read as
# plain text. Not by `extract_sometimes_numbered_pdf` alone: its "10% of lines start with a number" rule takes a claims
# act (HB 816, whose addresses begin "3139 B Canton Street") for a numbered one and keeps 1,108 of its 20,118 characters.
# The same reader (the rest of the bills) also fixes the long road-plan and budget papers (HB 502, HB 533, HJR 52, 75, 76),
# whose tables carry no line numbers and were cut to a few thousand characters; the 2,351 other bill PDFs read exactly
# as before.
_ACT = re.compile(r"\s*CHAPTER \d+\b")


def readers():
    """Kentucky's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    from openstates.fulltext.utils import pdfdata_to_text

    def extract_ky_pdf(data: bytes, metadata: typing.Any) -> str:
        text = pdfdata_to_text(data)
        if _ACT.match(text):
            return text
        return extract_sometimes_numbered_pdf(data, metadata)

    return {"application/pdf": extract_ky_pdf}


# ── Pre-diff cleaning (OPEN-946) ─────────────────────────────────────────────────────────────────────────────────────

# Kentucky's PDFs justify the margin numbers and the "(1)    text" / "SECTION 1.      A NEW SECTION" runs differently
# from one print to the next, so the Introduced and the Current/Final text differ in the number of spaces only, and
# the stored comparison lists those lines as removed and added (HB 508: both changed lines; HB 312: 4 of 4; HB 103:
# its whole 52-line hunk). Mac replica, 2026-11-27, all 854 stored comparisons (483 not empty): 388,827 changed lines,
# 105,300 (27.1%) a removed and an added line that are the same once spacing is collapsed (87,028 of them differ as
# printed; the rest are lines moved unchanged, which stay); 28 comparisons are 95% or more such lines.
# Each line is cut to its words, joined by one space, before the comparison. Only the text that goes
# into the comparison is changed, never the stored text. Words are untouched, so a "[deleted law]" bracket or a real
# word change shows as before.
def _clean_ky_text(text: str) -> str:
    """Kentucky-only cleaner: every line's runs of white space become one space and its ends are trimmed. Lines are cut where
    the comparison cuts them (`str.splitlines`, which also breaks at a form feed), so no line is joined or split."""
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Kentucky's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`)."""
    return _clean_ky_text(prior_text), _clean_ky_text(raw_text)
