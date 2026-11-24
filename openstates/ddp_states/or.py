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
_FLOOR_RESULT = re.compile(
    r"^(?:Rules suspended\.\s+)?(?:Third reading|Final reading|Read)(?: as Special Order of Business)?\..*"
    r"\.\s+(?:Passed|Adopted)\.\Z"
    r"|^Passed\.\Z",
    re.I,
)
_CONCURRED_AND_REPASSED = re.compile(r"\A(?:House|Senate) concurred in (?:House|Senate) amendments and repassed bill\.\Z")


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on a presiding officer's signature, `passage` on a floor result line and on
    a concurrence that repasses the bill, every other label as given."""
    text = description or ""
    labels = list(classification)
    if _PRESIDING_OFFICER_SIGNED.match(text):
        labels = [c for c in labels if c != "passage"]
    elif (_FLOOR_RESULT.match(text) or _CONCURRED_AND_REPASSED.match(text)) and "passage" not in labels:
        labels.append("passage")
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
