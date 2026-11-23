"""
Mississippi's per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536).
"""

import re


# ── Version rules (OPEN-807) ─────────────────────────────────────────────────────────────────────────────────────────


# OPEN-807: the bill's history XML on billstatus.ls.state.ms.us (<session>/pdf/history/<HB|SB..>/<bill>.xml) lists,
# beside the bill's texts, the amendments adopted along the way under <AMENDMENTS>, and the scraper stores the adopted
# ones and the amendment reports as versions (scrapers/ms/bills.py, "# amendments"). These are motions, not versions
# of the bill (read on the site's pages and the stored names, Mac replica, 2026 and 20261E sessions, 2026-11-23):
#   "Amendment No <N>", "Committee Amendment No <N>", "Amendment No <N> (Cmte Sub)" (an amendment to the committee
#   substitute), "Amendment No <N> to [Committee ]Amendment No <M>", "Substitute No <N> for Committee Amendment No
#   <M>", each followed by its disposition and vote in parentheses ("(Adopted) (Voice Vote)"; "({Vote})" as printed):
#   one HAM / SAM entry, the amendment paper itself ("AMENDMENT NO 1 PROPOSED TO House Bill No. 1 BY: Representative
#   ... AMEND on lines 44 & 45 ..."; HB 1924's "COMMITTEE AMENDMENT NO 1 PROPOSED TO House Bill No. 1924 BY:
#   Committee Amend by striking all after the enacting clause ..."), 464 of the 6,542 versions.
#   "Amendment Report for <House|Senate> <Bill|Concurrent Resolution> No. <N>": the AMRPT entry, one chamber's
#   message to the other's clerk listing the amendments it adopted ("TO THE CLERK OF THE HOUSE: THIS IS TO INFORM YOU
#   THAT THE SENATE HAS ADOPTED THE AMENDMENTS SET OUT BELOW: AMENDMENT NO. 1 ..."), 263 of the 6,542.
# The bill's texts stay versions: "As Introduced", "Committee Substitute", "As Passed the lower|upper", "Approved by
# the Governor" (and, until the scraper fix of the same ticket is re-scraped, "Current version"). Their stage is
# untouched: the papers already have one (amendment, or for the reports note_stage's conference-or-report rule), so
# they never counted as versions with no stage; they now drop out of the comparison chain, and the next real text
# compares with the last real one (recompute dry run, Mac replica: 1,118 documents not a version, 356 comparisons
# corrected).
PROCEDURAL_DOCUMENT_NOTE_PATTERN = re.compile(
    r"\A(?:"
    r"(?:(?:committee )?amendment|substitute) no [1-9]\d*(?: (?:to|for) (?:committee )?amendment no [1-9]\d*)?"
    r"(?: \([^()]+\))*"
    r"|amendment report for (?:house|senate) (?:bill|concurrent resolution|resolution) no\. +[1-9]\d*"
    r")\Z",
    re.IGNORECASE,
)
