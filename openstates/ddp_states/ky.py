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

Not changed here, on purpose: "enrolled, signed by President of the Senate" / "... Speaker of the House" (240 lines
each) also carry `passage` although they are the presiding officers' signatures, not votes. The scraper stores every
other floor line under the bill's own chamber (the actor is read from the end of the text, and "3rd reading, passed
38-0" names none), so on the replica all 973 real passage lines sit under the origin chamber, including the 407 cast
in the second chamber. The Senate President's signature is the only line under the other chamber, so it is the only
thing that makes a Kentucky bill read as passed by both chambers: without it all 204 bills that reach "Passed both
chambers" (`validate_bill_stages`) would lose that stage. The signatures can lose `passage` once the second chamber's
lines are stored under that chamber (OPEN-845 pull request).
"""

import re
import typing

_PASSED_OVER = re.compile(r"^passed over and retained\b", re.I)
_RESOLUTION_ADOPTED = re.compile(r"^3rd reading, adopted\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on a "passed over and retained ..." line, `passage` added on a "3rd reading,
    adopted ..." line, every other label as given."""
    text = description or ""
    labels = list(classification)
    if _PASSED_OVER.match(text):
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
