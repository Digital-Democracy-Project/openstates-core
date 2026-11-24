"""
Oregon's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""

import re
import typing


# ── Action labels (OPEN-841, PLAN-state-specific-handling §5.4) ──────────────────────────────────────────────────────


# Found by the bill-stages check of Oregon's go-live (OPEN-589) on the Mac replica (2026-11-24, session 2026R1, 3,912
# stored actions on 304 bills). The scraper's categorizer (`scrapers/or/bills.py`, `action_classifiers`) gets the
# floor's result wrong in two ways:
#
# * "Speaker signed." / "President signed." (153 lines each) are labelled `passage`. They are the presiding officers
#   signing the enrolled bill after both chambers passed it, not a vote, so they lose `passage` (as Nebraska's
#   "President/Speaker signed", ne.py). Without this, "Passed both chambers" is reached by the signatures.
# * The chamber's result is sometimes printed on its own line: "Third reading. Carried by <member>." and then "Passed."
#   (HB 4134, 4145, 4014, SB 1507, 1599: 5 lines, House, each after a third reading or a failed motion to re-refer),
#   and a special order is read with "Read as Special Order of Business. Carried by <members>. Adopted." (HCR 201),
#   which the categorizer's "Read. ... Adopted" shape does not match. Neither got `passage`, so the bill's first passage
#   was the other chamber's. They get it.
# * "House concurred in Senate amendments and repassed bill." (14 lines) / "Senate concurred in House amendments and
#   repassed bill." (9 lines, 23 bills in all) carry no label. That is the originating chamber's vote to accept the
#   other chamber's amendments and pass the amended bill again, its final vote on the bill (no later floor vote is
#   stored on any of the 23; the signatures follow), so it gets `passage` (Ramon, 2026-11-24, OPEN-841 comment 23171).
#   These two are the only concurrence lines stored; a concurrence without "repassed bill" gets nothing.
#
# The floor result is a reading line ("Third reading", "Final reading", "Read", each possibly "as Special Order of
# Business", possibly after "Rules suspended.") whose last sentence is "Passed." or "Adopted.", or a bare "Passed."
# line, matched to the end of the line. Every other line, and every other label, is as given; a reading line without a
# result ("Third reading. Carried by X.") or with "Failed." gets no `passage`.
_PRESIDING_OFFICER_SIGNED = re.compile(r"^(?:Speaker|President) signed\b", re.I)
_READING_LINE = (
    r"^(?:Rules suspended\.\s+)?(?:Third reading|Final reading|Read)(?: as Special Order of Business)?\..*\.\s+"
)
_FLOOR_RESULT = re.compile(_READING_LINE + r"(?:Passed|Adopted)\.\Z|^Passed\.\Z", re.I)
# A floor vote the bill lost carries no `failure`: "Third reading. Carried by <member>. Failed." (HB 4098, its
# only third reading: roll call 28-30) is stored with `reading-3` alone, and the bare "Failed." line OLIS adds when
# that failure becomes final (HB 4098, 03-07, the session's last day, after a member's notice of possible
# reconsideration lapsed; no roll call of its own) with no label. Both get `failure` (Ramon, 2026-11-24). They are
# the only two such lines stored. The "Motion to ... failed." lines (substitute the minority report, refer, re-refer,
# withdraw from committee, move on the calendar: 23 lines) decide a motion, not the bill, and get nothing.
_FLOOR_FAILED = re.compile(_READING_LINE + r"Failed\.\Z|^Failed\.\Z", re.I)
_CONCURRED_AND_REPASSED = re.compile(
    r"\A(?:House|Senate) concurred in (?:House|Senate) amendments and repassed bill\.\Z"
)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on a presiding officer's signature, `passage` on a floor result line and on
    a concurrence that repasses the bill, `failure` on a floor vote the bill lost, every other label as given."""
    text = description or ""
    labels = list(classification)
    if _PRESIDING_OFFICER_SIGNED.match(text):
        labels = [c for c in labels if c != "passage"]
    elif (_FLOOR_RESULT.match(text) or _CONCURRED_AND_REPASSED.match(text)) and "passage" not in labels:
        labels.append("passage")
    if _FLOOR_FAILED.match(text) and "failure" not in labels:
        labels.append("failure")
    return labels


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Oregon's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    from openstates.fulltext.oregon import without_bracketed_text
    # OPEN-600: the words a bill deletes from current law, printed [italic and bracketed], are
    # left out (see fulltext/oregon.py).
    return {"application/pdf": without_bracketed_text(extract_sometimes_numbered_pdf)}
