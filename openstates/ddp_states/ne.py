"""
Nebraska's label rules (OPEN-550, PLAN-state-specific-handling, epic OPEN-536).

Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
`_read_nebraska`, BROKER-456/477), unchanged: the broker drops it in BROKER-490 once this rule is live.

Nebraska has one chamber, and the scraper's categorizer tags `passage` on lines that are not the Legislature's vote
on the bill: "President/Speaker signed" (the presiding officer signing the enrolled bill, 627 stored lines on the Mac
replica, 2026-11-17) and "Passed over" (the bill skipped on that day's agenda, 23 lines; LB 376, 109th, carries one
two months before its real vote). Only "Passed on Final Reading ..." (with or without "with Emergency Clause") is the
bill's passage, so `passage` stays on that line alone. Nothing is added: every stored "Passed on Final Reading" line
already carries `passage`.
"""

import re
import typing

_FINAL_PASSAGE = re.compile(r"^Passed on Final Reading\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `passage` only on "Passed on Final Reading" lines, every other label as given."""
    if "passage" in classification and not _FINAL_PASSAGE.match(description or ""):
        return [c for c in classification if c != "passage"]
    return list(classification)


# ── Pre-diff text cleaning (moved unchanged from openstates/cli/text_extract.py, OPEN-554) ──────────────────────────


# OPEN-486: Nebraska-only text cleaning applied to prior_text/raw_text immediately before
# difflib.unified_diff() (never touching the stored raw_text, never applied to another
# jurisdiction). Measured 2026-11-04 on the production replica (OPEN-479 Stage 4 trial, 3,382
# documents): the "Slip Law" printing is typeset differently from "Introduced" and "Final Reading":
# a running header on every page ("LB1237                LB1237", the year line "2026  2026"), the
# "LEGISLATIVE BILL 1237" title, a "Became law ..." or "Approved by the Governor ..." line, the
# "Introduced by ..." sponsor line, page numbers ("-4-") and a form feed after each page, and every
# line wrapped at a different width. The Introduced and Final Reading printings carry a bare year
# line "2026" on every page. A raw line diff therefore reported the whole document as changed: 360
# of 365 Final Reading -> Slip Law pairs and 359 of 365 Introduced -> Slip Law pairs were 90% or
# more changed.
#
# What it does: drop each whole line that is one of those header shapes, then put one sentence on
# each line (split after a period, semicolon or colon). A fixed-width re-wrap was tried and
# rejected (168 pairs got worse); splitting at sentences does not depend on where a printing wraps.
# Result over every real pair (changed-lines share, median; whole-document = 90% or more):
#   Final Reading -> Slip Law   365 pairs  0.97 -> 0.01   whole-document 360 -> 2   worse 0
#   Introduced -> Slip Law      365 pairs  0.97 -> 0.16   whole-document 359 -> 17  worse 0
#   Introduced -> Final Reading 380 pairs  0.20 -> 0.11   whole-document  31 -> 17  worse 10 (slightly)
# "Worse" is a pair whose changed-lines share rose by more than 0.05; the ten are named in the
# OPEN-486 pull request. A line is dropped only if the WHOLE line is one of the shapes.
_NE_HEADER_LINE_RES = tuple(
    re.compile(pattern)
    for pattern in (
        r"([A-Z]{1,3}\d+[A-Z]?)\s+\1",  # running "LB1237      LB1237", "LB380A  LB380A"
        r"\d{4}(?:\s+\d{4})?",  # the year line, alone or doubled
        r"LEGISLATIVE BILL \d+[A-Z]?",
        r"Became law\b.*",
        r"Approved by the Governor\b.*",
        r"Introduced by\b.*",
        r"-\d+-",  # Slip Law page number
    )
)


_NE_SENTENCE_BREAK = re.compile(r"(?<=[.;:])\s+")


# OPEN-497: a line wrap can fall right after a hyphen or slash ("non-" / "Participating",
# "time/" / "temperature") or between two parenthesised labels ("(3)(c)" / "(i)"). Joining the
# lines with a space then reads "non- Participating" or "(3)(c) (i)", while a printing wrapped
# elsewhere reads "non-Participating" or "(3)(c)(i)". The words are the same and only that space
# differs, so the comparison showed changed lines for nothing: 158 of the 494 stored Nebraska
# comparisons (2026-11-06, replica) were nothing else. The space is dropped on both sides, wherever it
# sits, because the PDF text also carries it inside a line (joining only at wraps measured worse:
# 7 pairs worse, 20 whitespace-only pairs left). It is a spacing normalisation of the comparison text
# only; the stored text is untouched.
# A page-number-shaped "-4-" token is matched first so it is kept whole.
_NE_HYPHEN_WRAP_SPACE = re.compile(r"(?:(?<!\S)-\d+-(?= ))|(?<=\S)([-/]) (?=\S)")


_NE_PAREN_WRAP_SPACE = re.compile(r"\) \(")


def _clean_ne_text(text: str) -> str:
    """Nebraska-only cleaner, see the block comment above: header lines dropped, then one
    sentence per line."""
    kept = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or any(p.fullmatch(line) for p in _NE_HEADER_LINE_RES):
            continue
        kept.append(line)
    words = " ".join(" ".join(kept).split())
    words = _NE_HYPHEN_WRAP_SPACE.sub(lambda m: m.group(1) or m.group(0), words)
    words = _NE_PAREN_WRAP_SPACE.sub(")(", words)
    return "\n".join(part for part in _NE_SENTENCE_BREAK.split(words) if part)


def clean_text(
    prior_text: str,
    raw_text: str,
    *,
    is_bill: bool,
    prior_media_type: typing.Optional[str],
    cur_media_type: str,
) -> typing.Tuple[str, str]:
    """Nebraska's pre-diff cleaning of both sides (`text_extract.apply_prediff_cleaning`, OPEN-554: moved unchanged
    from that function's per-state if-chain)."""
    return _clean_ne_text(prior_text), _clean_ne_text(raw_text)


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


# OPEN-486: Nebraska's amendment papers -- "<introducer or committee> AM<N>" (committee and
# introducer amendments), "<introducer> FA<N>" (floor amendments) and "Enrollment and Review
# ER<N>" (the enrollment-and-review amendment) -- are not versions of the bill (Ramon,
# 2026-11-04: "Leave them out"). The real versions stay: Introduced, Final Reading, Slip Law.
# Read on the production replica (2026-11-04): all 790 of Nebraska's non-version documents
# (AM 513, FA 103, ER 174 by these shapes) match, none of the 2,592 real versions does, and no
# other state has a note of any of these shapes. 76% (AM), 69% (FA) and 93% (ER) of their
# stored comparisons replaced the whole document (OPEN-479 Stage 4 trial). Prospective only: a
# Nebraska recompute-diff-order applies it to stored diffs, a separate, authorised step.
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A(?:.+ (?:am|fa)[1-9]\d*|enrollment and review er[1-9]\d*)\Z", re.IGNORECASE
)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Nebraska's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.al import read_without_struck_text
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    # OPEN-560: struck-out words removed by Alabama's reader, see fulltext/al.py.
    return {"application/pdf": read_without_struck_text(extract_sometimes_numbered_pdf)}
