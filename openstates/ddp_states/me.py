"""
Maine's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re
import typing


# ── Action labels (OPEN-866, PLAN-state-specific-handling §5.4) ──────────────────────────────────────────────────────


# Ramon, 2026-11-24: in Maine "passed one chamber" is the originating chamber's "PASSED TO BE ENGROSSED" vote, not
# its enactment vote. A Maine bill is passed to be engrossed by each chamber in turn (the second "in concurrence"),
# and only then enacted by both, the House first even on a Senate bill. The scraper's categorizer
# (`scrapers/me/actions.py`) gives `passage` to the enactment lines ("PASSED TO BE ENACTED", and "FINALLY PASSED" for
# a resolve or a constitutional resolution) and none to the engrossment lines, so a Senate bill's first `passage` was
# the House's enactment: the Mac replica (2026-11-24, 132nd Legislature, 2,450 bills, 44,826 actions) had 512 bills
# whose first passage was not by their own chamber, each reaching "Passed both chambers" without "Passed one chamber"
# (OPEN-672).
#
# So `passage` moves to the engrossment votes. The first chamber's engrossment is "Passed one chamber"; the second
# chamber's ("READ A SECOND TIME and PASSED TO BE ENGROSSED, in concurrence", or the House's engrossment of a Senate
# bill) is "Passed both chambers", because the stage rule reads a `passage` by the other chamber that way. The
# enactment lines lose `passage` and get no label in its place: no stage needs one (both chambers have passed the bill
# by then, and the Governor's signature or "became-law" is the next stage), and a failed enactment keeps the
# `failure` the categorizer gives it.
#
# A chamber's engrossment vote is printed two ways:
#
# * "PASSED TO BE ENGROSSED", in any of its forms ("The Bill was ...", "Under suspension of the Rules, READ A SECOND
#   TIME and ...", "Subsequently, ...");
# * "PASSAGE TO BE ENGROSSED" after the chamber agreed to it: it "RECEDED and CONCURRED to" it, "the Bill was" it, or
#   the line ends with the vote "PREVAILED". When the other chamber engrossed the bill first (often after this chamber
#   had accepted an Ought Not to Pass report), receding and concurring is this chamber's engrossment.
#
# A line that is not the chamber's vote to pass gets nothing: a motion not yet decided ("moved to INSIST on ...",
# "MOVED to RECEDE and CONCUR to ...", which the shapes above do not match), a failed one ("Motion ... to RECEDE and
# CONCUR to ... PASSAGE TO BE ENGROSSED FAILED", "FAILED PASSAGE TO BE ENGROSSED", "INSISTED to FAILURE OF PASSAGE
# ..."), a reconsideration or a line referring back to the vote ("RECONSIDER ... whereby the Bill was PASSED TO BE
# ENGROSSED", "RECEDED whereby ..."), the chamber receding from it ("RECEDE from PASSAGE TO BE ENGROSSED ...
# PREVAILED"), and a line that puts the bill aside ("TABLED ... pending PASSAGE TO BE ENGROSSED", not matched either).
# A chamber that "INSISTED" on (or "ADHERED" to) its engrossment keeps the vote it already took, so that line is not
# a new passage either: labelling it made 25 bills read "Passed one chamber" again after the other chamber's Ought Not
# to Pass (stored as `committee-failure`), each a "Failed" stage the bill then moved on from.
#
# A joint order or joint resolution has no engrossment: each chamber's single vote, "READ and PASSED" or "READ and
# ADOPTED", is its passage, and the categorizer labels it only when the line ends there with a period. The Senate
# prints its own as "On motion by Senator <name> READ and PASSED" (no period; 50 lines, each the first vote on a
# Senate order, so the House's concurrence was its first passage), with a trailing "PREVAILED", or "READ and ADOPTED in
# Joint Convention"; after a roll call it is "Subsequently, PASSED in concurrence." or "the Joint Order was PASSED."
# (10 lines in all). Those get `passage` too, but not when the words go on to an engrossment or an enactment
# ("Subsequently, PASSED TO BE ENACTED in concurrence." is the Senate's enactment). A line naming an
# amendment ("Committee Amendment "A" ... READ and ADOPTED") is the amendment's adoption, not the paper's, and gets
# nothing. Every other line and label is as given.
_ENGROSSMENT_VOTE = re.compile(
    r"\bPASSED TO BE ENGROSSED\b"
    r"|\bRECEDED and CONCURRED\b.*\bPASSAGE TO BE ENGROSSED\b"
    r"|\bwas PASSAGE TO BE ENGROSSED\b"
    r"|\bPASSAGE TO BE ENGROSSED\b.*\bPREVAIL",
    re.I,
)
_NOT_A_VOTE_TO_PASS = re.compile(r"\bwhereby\b|\bRECONSIDER|\bFAIL|\bRECEDED? from\b|\bINSIST", re.I)
_ENACTMENT = re.compile(r"\bPASSED TO BE ENACTED\b|\bFINALLY PASSED\b", re.I)
_SINGLE_VOTE = re.compile(
    r"(?:\bREAD and |\bthe (?:Joint )?(?:Order|Resolution) was |\ASubsequently,? )(?:PASSED|ADOPTED)\b(?! TO BE)", re.I
)
_AMENDMENT = re.compile(r"\bAmendment\b", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `passage` on a chamber's vote to pass the bill to be engrossed (or a joint order's or
    resolution's single vote), none on its enactment, every other label as given."""
    text = description or ""
    labels = list(classification)
    if not _NOT_A_VOTE_TO_PASS.search(text) and (
        _ENGROSSMENT_VOTE.search(text) or (_SINGLE_VOTE.search(text) and not _AMENDMENT.search(text))
    ):
        if "passage" not in labels:
            labels.append("passage")
    elif _ENACTMENT.search(text):
        labels = [c for c in labels if c != "passage"]
    return labels
