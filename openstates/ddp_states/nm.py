"""
New Mexico's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""


# ── Version rules (moved unchanged from openstates/utils/version_ordering.py, OPEN-554) ─────────────────────────────


# OPEN-637: New Mexico's "Amendments in Context" is the legislature's marked-up working copy
# (the bill with adopted amendments marked in place), not a version of the bill; Ramon,
# 2026-11-19: keep it as a document, out of the version list (option A, no OCR). Most of these
# PDFs are "Microsoft: Print To PDF" files whose text is drawn as shapes, so they extract to the
# page header only ("57TH LEGISLATURE - STATE OF NEW MEXICO - SECOND SESSION, 2026"). Mac
# replica, 2026 regular session (2026-11-18): n=85 min=64 median=72 max=183,360, 79 under 200
# characters, all 85 with a stored comparison. In the dev LegBot sample (OPEN-618) every
# changelog into one said the bill was emptied, and 9 summaries failed because one was the
# newest text. The official texts stay versions: "introduced version", the committee
# substitutes ("JC substitute", ...) and "Final Version" (OPEN-635).
PROCEDURAL_DOCUMENT_NOTES = frozenset({"Amendments in Context"})
