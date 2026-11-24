"""
Montana's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Action labels (OPEN-826) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-826: Montana's action text is the status name from api.legmt.gov (scrapers/mt/bills.py, `scrape_actions`), with
# the chamber in front ("(H) ", "(S) ", "(LC) " for the drafting office) and, for a committee's statuses, " - " and the
# committee's name after it. The scraper's categorizer (scrapers/mt/actions.py) was written for the older archive API's
# names, which carried the result ("3rd Reading Passed"). Read against every status name Montana used in 2025 (141,
# api.legmt.gov bills/v1/bills/search, session 2) and the 2027 actions on the Mac replica (13 names, 2026-11-24):
#   "(H) Introduced" / "(S) Introduced" carry no label: its pattern is "^introduced$", which the prefix defeats (1,764
#   lines in 2025; all 62 stored 2027 lines). "1st Reading" and the real "3rd Reading" carry none either, while
#   "Scheduled for 2nd / 3rd Reading" and the motions that move a bill onto a reading's calendar carry a reading.
#   "Signed by Speaker" and "Signed by President" carry `passage` (1,895 lines in 2025): the presiding officers sign the
#   enrolled bill after both chambers passed it, which is not a passage (Washington's "Speaker signed." trap).
#   "Fiscal Note Received" and the like carry `receipt`; "Tabled in Committee", the bill tabled, carries
#   `amendment-deferral`; "Died in Committee" and "Became Law Without Governor's Signature" carry nothing.
#   `failure` comes from any line containing "failed" ("2nd Reading Pass Motion Failed; 3rd Reading Vote Required",
#   "Veto Override Motion Failed"), a lost motion or amendment, not the bill. No 2025 or 2027 name contains "failed";
#   the archive API's do.
# The status names carry no result: "(H) 3rd Reading" is the same name whether the bill passed or failed. Since OPEN-851
# (scrapers) the scraper writes the status row's result after the name, in the archive API's words ("(H) 3rd Reading
# Passed", "(H) 3rd Reading Failed", "(S) 2nd Reading Not Adopted", "(H) Committee Executive Action--Bill Passed as
# Amended - (H) Judiciary"), and labels the outcome itself (`passage`, `failure`, `committee-passage`, ...). So the
# outcome words are set aside before the name is looked up below, and the scraper's outcome labels are kept.
#
# A name below gets exactly its labels (plus the scraper's outcome labels when the line has outcome words); any other
# name keeps the scraper's labels, without `failure` unless it is the bill's own loss ("3rd Reading Failed", "Died in
# Process", a simple resolution's "2nd Reading Not Adopted").
_PREFIX = re.compile(r"\A\((?:H|S|LC)\) ")

_LABELS = (
    (re.compile(r"introduced", re.I), ["introduction"]),
    (re.compile(r"1st reading", re.I), ["reading-1"]),
    (
        re.compile(
            r"3rd reading(?: conference committee report| free conference committee report| governor's amendments"
            r"| house amendments| senate amendments| pass consideration|; 2nd chamber vote required)?",
            re.I,
        ),
        ["reading-3"],
    ),
    # placed on a reading's calendar, not read
    (re.compile(r"scheduled for (?:2nd|3rd) reading", re.I), []),
    (re.compile(r"take from committee; place on (?:2nd|3rd) reading", re.I), []),
    (re.compile(r"taken from 3rd reading; placed on 2nd reading", re.I), []),
    (re.compile(r"taken from 2nd reading; rereferred to committee", re.I), ["referral-committee"]),
    (re.compile(r"signed by (?:speaker|president)", re.I), []),
    (re.compile(r"(?:revised )?(?:sponsor rebuttal to )?fiscal note received", re.I), []),
    (re.compile(r"tabled in committee", re.I), ["deferral"]),
    (re.compile(r"died in committee", re.I), ["committee-failure"]),
    (re.compile(r"became law without governor's signature", re.I), ["became-law"]),
)

_BILL_LOST = re.compile(r"\A(?:3rd reading failed|2nd reading not adopted|(?:draft )?died in process)\Z", re.I)

# OPEN-851's outcome words after a status name (" Passed", " Not Concurred as Amended", " Carried", ...). A committee's
# "--Bill <words>" needs no rule: no committee action or report is in the table above.
_OUTCOME = re.compile(r"(?: (?:Not )?(?:Passed|Concurred|Adopted)(?: as Amended)?| Rejected| Carried| Failed)\Z", re.I)
_OUTCOME_LABELS = frozenset(
    {
        "passage",
        "failure",
        "committee-passage",
        "committee-failure",
        "veto-override-passage",
        "veto-override-failure",
    }
)


def _status_name(description: str) -> str:
    """The status name with its outcome words: no chamber in front, no committee after."""
    return _PREFIX.sub("", description.strip()).split(" - ", 1)[0].strip()


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels, read from its status name: see the block comment above."""
    name = _status_name(description or "")
    bare = _OUTCOME.sub("", name)
    for pattern, labels in _LABELS:
        if pattern.fullmatch(bare):
            if bare == name:
                return list(labels)
            return list(labels) + [c for c in classification if c in _OUTCOME_LABELS and c not in labels]
    if _BILL_LOST.match(name):
        return list(classification)
    return [c for c in classification if c != "failure"]


# ── Pre-diff text cleaning (OPEN-827) ────────────────────────────────────────────────────────────────────────────────


# OPEN-827: every Montana text opens with the bill-number line and the sponsor line. The drafted bill ("Bill Draft",
# the LC file) has blanks ("_____________ BILL NO. _____________", "INTRODUCED BY ______..."); the introduced bill has
# them filled in ("SENATE BILL NO. 30", "INTRODUCED BY <sponsor>, <cosponsors>", the list wrapping onto up to 3 more
# lines), so every Bill Draft -> Introduced comparison showed both lines as changed. The second source of noise is the
# space after "Section N.", printed one space wider or narrower between the two PDFs. Read on the Mac replica
# (2026-11-24, all 594 Montana documents, 2027): the number line is line 1 and "INTRODUCED BY" line 2 in every one;
# the next line is "BY REQUEST OF ..." (485), "A BILL FOR AN ACT ..." (103) or "A JOINT RESOLUTION ..." (6); neither
# header line appears anywhere else in any text. Both header lines (and the sponsor list's wrapped lines) are dropped
# only in that place, and runs of spaces inside a line become one space. Letter case is left alone.
_BILL_NUMBER_LINE = re.compile(r"(?:_+|HOUSE|SENATE) (?:BILL|JOINT RESOLUTION|RESOLUTION) NO\. (?:_+|[1-9]\d*)", re.I)
_SPONSOR_LINE = re.compile(r"INTRODUCED BY\b.*", re.I)
_AFTER_SPONSORS = re.compile(r"(?:BY REQUEST OF|A BILL FOR AN ACT|A JOINT RESOLUTION|A RESOLUTION)\b", re.I)
# the sponsor list wrapped onto at most 3 more lines in all 594 texts; 5 leaves room for a longer list, and the lines
# go only when one of the lines above ends the list within them (otherwise only the two header lines go)
_SPONSOR_MAX_WRAPPED_LINES = 5
_SPACE_RUN = re.compile(r"[ \t]{2,}")


def _clean_montana_text(text: str) -> str:
    """Montana-only cleaner, see the block comment above: the header's bill-number and sponsor lines dropped, runs of
    spaces inside a line made one space."""
    lines = text.split("\n")
    if len(lines) > 1 and _BILL_NUMBER_LINE.fullmatch(lines[0].strip()) and _SPONSOR_LINE.fullmatch(lines[1].strip()):
        end = 2
        for i in range(2, min(len(lines), 2 + _SPONSOR_MAX_WRAPPED_LINES + 1)):
            if _AFTER_SPONSORS.match(lines[i].strip()):
                end = i
                break
        lines = lines[end:]
    return "\n".join(_SPACE_RUN.sub(" ", line) for line in lines)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Montana's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`)."""
    return _clean_montana_text(prior_text), _clean_montana_text(raw_text)
