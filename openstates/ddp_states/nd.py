"""
North Dakota's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536): the text reader (OPEN-836) and the
action-label rules (OPEN-833, PLAN-state-specific-handling §5.4, epic OPEN-146).

Action labels (OPEN-833). Found by the bill-stages check of North Dakota's go-live (OPEN-663) on the Mac replica
(2026-11-24, sessions 69, 69S1 and 69S2, 15,495 stored actions on 1,106 bills). In North Dakota a chamber votes on a
bill at Second Reading: "Second reading, passed, yeas 46 nays 0" carries `passage, reading-2`. Its defeat carries
`reading-2` alone, so a bill killed on the floor never reads as failed. Three forms are stored, all only in this shape:

* "Second reading, failed to pass, yeas 10 nays 36" (400 lines: bills)
* "Second reading, failed to adopt" / "... failed to adopt yeas 15 nays 77" (30 lines: resolutions)
* "Second reading, failed, lacks constitutional majority yeas 47 nays 37" (8 lines: more yeas than nays, but fewer
  than the constitution requires)

Each gains `failure`; `reading-2` and any other label stay. A chamber that reconsiders a failed bill and passes it
prints a later "Second reading, passed ..." line that carries its own `passage`, so the bill reads on from there.
OPEN-858: a resolution's adoption ("Second reading, adopted", "Second reading, adopted yeas 24 nays 23", "Second
reading, adopted as amended ...", 82 lines) is that resolution's passage, as "Second reading, passed ..." is a bill's,
so it gains `passage`. The scraper gave it `reading-2` alone, so SCR 4008 (69), the one stored resolution that failed
(House, 04-01-25, "failed, lacks constitutional majority yeas 46 nays 43") and was then reconsidered and adopted
(04-02-25, "adopted yeas 53 nays 39"; filed with the Secretary of State 04-08-25), read as failed.
The other failed motions are left as the scraper labels them: "Amendment failed" (an amendment, `amendment-failure`),
"Motion to reconsider failed" (the bill had already passed or failed), "Emergency clause failed" (the bill can still
pass without it) and "Division A lost" (one part of a divided question).

Pre-diff text cleaning (OPEN-909). North Dakota's "Enrollment" version (the latest version of every enacted bill) is
the enrolled printing, and it carries what no earlier printing does: a running page header on every page and, after
the last page of the bill, the certification pages (signatures, the chambers' vote, "Received by the Governor",
"Approved at", "Filed in this office", and for a vetoed measure the Governor's objections and the failed override
vote), so the changelog against the engrossment spent its lines on them. See `clean_text` below.
"""

import re
import typing

# ── Reader choice (replaces upstream's entry in openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS) ──────────────


def readers():
    """North Dakota's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_sometimes_numbered_pdf

    # OPEN-836: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_sometimes_numbered_pdf)}


# ── Action labels (OPEN-833) ─────────────────────────────────────────────────────────────────────────────────────────

# The three stored forms, whole line, with or without the roll call; any other wording is looked at before it is
# labelled.
_FLOOR_DEFEAT = re.compile(
    r"^\s*Second reading, failed(?: to (?:pass|adopt)|, lacks constitutional majority)(?:,? yeas \d+ nays \d+)?\s*$",
    re.I,
)

# OPEN-858: the stored forms, whole line, with or without "as amended" and the roll call.
_FLOOR_ADOPTION = re.compile(r"^\s*Second reading, adopted(?: as amended)?(?:,? yeas \d+ nays \d+)?\s*$", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `failure` added on a Second Reading defeat, `passage` on a resolution's adoption, every
    other label as given."""
    labels = list(classification)
    if _FLOOR_DEFEAT.match(description or "") and "failure" not in labels:
        labels.append("failure")
    if _FLOOR_ADOPTION.match(description or "") and "passage" not in labels:
        labels.append("passage")
    return labels


# ── Version rules (OPEN-858) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-858: ndlegis.gov lists, among a measure's versions, an engrossment that its chamber did not adopt, named
# "<N> ENGROSSMENT - NOT ADOPTED" (Mac replica, 2026-11-24, all 2,654 stored North Dakota versions: 3, on SB 2308 and
# SB 2400 "SECOND ENGROSSMENT - NOT ADOPTED", SCR 4022 "FIRST ENGROSSMENT - NOT ADOPTED"). It is a proposed text that
# never became the measure's, so it is not a version (`version_ordering.is_procedural_document`), as other states'
# failed amendment papers are not.
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(r"\A[a-z]+ engrossment - not adopted\Z", re.IGNORECASE)


# ── Pre-diff text cleaning (OPEN-909) ────────────────────────────────────────────────────────────────────────────────

# Measured on the Mac replica (all 648 stored North Dakota Enrollment documents, 2026-11-27; no other version carries
# either shape, 2,042 documents checked):
# * The running page header, alone on its line, as the PDF reader gives it, with the page break's form feed in front:
#   "H. B. NO. 1540 - PAGE 8" (970 lines), "S. B. NO. 2032 - PAGE 3" (857), "H.C.R. NO. 3027 - PAGE 2" (27),
#   "S.C.R. NO. 4005 - PAGE 2" (19), "H.M.R. NO. ..." (2), "H. R. NO. 3027 - PAGE 2" (1, spacing as printed: "H.   R."):
#   chamber letter, one or two letters for the measure type, "NO.", number, "- PAGE", number.
# * The certification pages: every one of the 648 has a page header whose next printed line is a signature rule
#   ("____________________________", or two of them side by side), and the pages from there to the end of the text hold
#   only the signature rules and titles, "This certifies that the within bill originated in ...", the vote lines
#   ("House Vote:       Yeas 49 ..."), "Received by the Governor ...", "Approved at ...", "Filed in this office ...",
#   and for a vetoed measure the Governor's objections ("Honorable Michael Howe / Secretary of State ... I certify this
#   Act ... together with the objections of Governor ...") and the failed override vote (HB 1540, 45 to 48). A signature
#   rule is also printed inside the body of some bills (a form that is to be signed: SB 2269, HB 1424), but never right
#   after a page header, so the block starts at the page header and not at the first rule.
# Everything else is left exactly as it was: the cover lines and sponsor list, the title, the body. A line that only
# looks like a header ("NO. 1540" mid-sentence, a header with other words on its line) is body text and stays.
_ND_PAGE_HEADER_RE = re.compile(r"\f?\s*[HS]\.\s*(?:[A-Z]\.\s*){1,2}NO\.\s*\d+\s*-\s*PAGE\s*\d+\s*")
_ND_SIGNATURE_RULE_RE = re.compile(r"\s*_{8,}(?:\s+_{8,})*\s*")


def _clean_nd_text(text: str) -> str:
    """North Dakota-only cleaner, see the block comment above: the certification pages after the last page of the bill
    and every page header line are dropped, every other line is kept unchanged."""
    lines = text.split("\n")
    end = len(lines)
    for i, line in enumerate(lines):
        if _ND_PAGE_HEADER_RE.fullmatch(line):
            following = next((x for x in lines[i + 1 :] if x.strip()), "")
            if _ND_SIGNATURE_RULE_RE.fullmatch(following):
                end = i
                break
    return "\n".join(line for line in lines[:end] if not _ND_PAGE_HEADER_RE.fullmatch(line))


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """North Dakota's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`)."""
    return _clean_nd_text(prior_text), _clean_nd_text(raw_text)
