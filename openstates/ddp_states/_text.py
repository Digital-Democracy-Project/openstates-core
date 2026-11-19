"""
Text helpers shared by more than one state's pre-diff cleaner (OPEN-554: moved unchanged from
openstates/cli/text_extract.py). Not a state: the leading underscore keeps it out of the per-state lookup.
"""

import re


# OPEN-399: the tail of a printed PDF page footer ("Page 3 of 12" -> the extractor keeps only
# "of 12") left as a bare line, often in the middle of a paragraph. When two versions have a
# different page count, difflib shows it as a removed or added line and a reflow-only change
# reads as a real edit: on HB 979 a stray "-of 12" between "...a possible bone marrow" and
# "donor, if ..." made the changelog model report a donor-screening fee as removed.
# Measured 2026-10-19 on the production replica: 2,654 of 11,871 Virginia PDF documents contain
# one (10,813 lines, a single N per document, the largest N 159, so one to three digits) and none
# of 13,669 Virginia HTML documents does.
# Only this exact shape: "Page N of M" and "Page N" lines were not seen as separate lines, and a
# broader pattern would only add false-positive risk (real text can wrap onto an "of 17" line, as
# one Michigan HTML document does, which is why this runs only for the jurisdictions that need it).
_PAGE_FOOTER_TAIL_RE = re.compile(r"^\s*of\s+\d{1,3}\s*$", re.IGNORECASE)


def _drop_page_footer_lines(text: str, footer_re: "re.Pattern[str]") -> str:
    """The shared page-footer routine (OPEN-400, reused by OPEN-496): drop every whole line that
    matches `footer_re` and leave every other line exactly as it was."""
    return "\n".join(line for line in text.split("\n") if not footer_re.match(line))
