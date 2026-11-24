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
# * The chamber's result is sometimes printed on its own line: "Third reading. Carried by Helm." and then "Passed."
#   (HB 4134, 4145, 4014, SB 1507, 1599: 5 lines, House, each after a third reading or a failed motion to re-refer),
#   and a special order is read with "Read as Special Order of Business. Carried by Boice, Grayber. Adopted." (HCR 201),
#   which the categorizer's "Read. ... Adopted" shape does not match. Neither got `passage`, so the bill's first passage
#   was the other chamber's. They get it.
#
# The floor result is a reading line ("Third reading", "Final reading", "Read", each possibly "as Special Order of
# Business", possibly after "Rules suspended.") that ends "Passed." or "Adopted.", or a bare "Passed." line. Every
# other line, and every other label, is as given; a reading line without a result ("Third reading. Carried by X.")
# or with "Failed." gets no `passage`.
_PRESIDING_OFFICER_SIGNED = re.compile(r"^(?:Speaker|President) signed\b", re.I)
_FLOOR_RESULT = re.compile(
    r"^(?:Rules suspended\.\s+)?(?:Third reading|Final reading|Read)(?: as Special Order of Business)?\..*"
    r"\b(?:Passed|Adopted)\.\s*$"
    r"|^Passed\.\s*$",
    re.I,
)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: no `passage` on a presiding officer's signature, `passage` on a floor result line, every
    other label as given."""
    text = description or ""
    labels = list(classification)
    if _PRESIDING_OFFICER_SIGNED.match(text):
        labels = [c for c in labels if c != "passage"]
    elif _FLOOR_RESULT.match(text) and "passage" not in labels:
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
