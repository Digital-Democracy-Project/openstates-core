"""
Utah's action-label rules (OPEN-547, PLAN-state-specific-handling, epic OPEN-536).

Moved from the broker's read-time reader (ddp-broker-py `apps/ddp-broker/fetch/interfaces/OpenStates/bill_stage.py`,
`_read_utah`, BROKER-248), the label part unchanged: the broker can drop its label corrections in BROKER-490 once this
rule is live, but not its chamber correction (below).

Utah's action text starts with the chamber that acted ("House/ ", "Senate/ "), and the scraper's categorizer tags
`passage` on lines that pass nothing. On the Mac replica (2026-11-17, sessions 2026 and 2025S2):

* `passage` is the chamber's floor vote only on "<chamber>/ passed 3rd reading" (1,030 lines, all tagged),
  "<chamber>/ passed 2nd & 3rd readings/ suspension" (230 lines, none tagged: the categorizer's rule for it is
  anchored at the start of the line and never matches) and "<chamber> Conference Committee - Final Passage" (26
  lines, all tagged: each chamber's floor vote on the conference report, e.g. HB 136 (2026), House 57-13-5 and Senate
  20-6-3, OPEN-622 comment 21427). The presiding officers' "signed by Speaker" and "signed by President" lines
  (1,088) carry `passage` and lose it.
* `failure` is the bill's failure only on "<chamber>/ failed" (51 lines). Failed floor amendments, substitutes and
  committee "Motion to Recommend Failed" lines (58) lose it; their other labels (`amendment-failure`) stay.
* "to Lieutenant Governor" (36 lines, resolutions sent for filing) is not the governor's receipt of a bill, so it loses
  `executive-receipt`.

Not moved, because a label rule cannot hold it: the broker also reads a passage line's chamber from the text, since
the line's organization is often the chamber the bill goes to next ("House/ passed 3rd reading" is stored under the
Senate 522 times of 659). That stays in the broker until the action's organization is fixed at the source: with these
labels and no broker reader at all, 696 of Utah's 1,021 bills would read as passed by both chambers without passing
the first (`validate_bill_stages`, 2026-11-17); with the broker keeping only the chamber correction, 0 bills change.
"""

import re
import typing

_PASSAGE = re.compile(
    r"^(Senate|House)/ passed (2nd & )?3rd readings?\b|^(Senate|House) Conference Committee - Final Passage\b", re.I
)
_FAILURE = re.compile(r"^(Senate|House)/ failed$", re.I)
_NOT_TO_GOVERNOR = re.compile(r"to Lieutenant Governor", re.I)


def classify_action(
    *,
    description: typing.Optional[str],
    classification: typing.Sequence[str],
    chamber: typing.Optional[str],
    date: typing.Optional[str],
) -> typing.List[str]:
    """An action's labels: `passage` on a floor passage line and nowhere else, `failure` only on "<chamber>/ failed",
    no `executive-receipt` on a line to the Lieutenant Governor, every other label as given."""
    text = description or ""
    labels = list(classification)
    if _PASSAGE.match(text):
        if "passage" not in labels:
            labels.append("passage")
    else:
        labels = [c for c in labels if c != "passage"]
    if not _FAILURE.match(text):
        labels = [c for c in labels if c != "failure"]
    if _NOT_TO_GOVERNOR.search(text):
        labels = [c for c in labels if c != "executive-receipt"]
    return labels


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A("
    r"(house|senate) amendment [1-9]\d*"
    r"|comparison to (original bill|sub #[1-9]\d*)"
    r"|amended excerpts \d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d+"
    r")\Z",
    re.IGNORECASE,
)


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """Utah's text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_line_numbered_pdf
    from openstates.fulltext.ut import handle_utah_xml
    return {
        "application/pdf": extract_line_numbered_pdf,
        # Found 2026-08-09 (OPEN-49): no text/xml entry ever existed here, upstream or in
        # this fork -- every UT bill served as XML (3,000+) silently extracted as
        # empty/is_error=True. See fulltext/ut.py for the actual defect (a mismatched
        # encoding declaration in Utah's own export, not a missing-content problem).
        "text/xml": handle_utah_xml,
    }
