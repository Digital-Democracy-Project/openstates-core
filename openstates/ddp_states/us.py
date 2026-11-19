"""
the United States (Congress)'s per-state rules (PLAN-state-specific-handling §4.2, epic OPEN-536; OPEN-554 moved them here
unchanged from the generic code that used to hold them).
"""


# ── Reader choice (moved unchanged from openstates/fulltext/__init__.py's CONVERSION_FUNCTIONS, OPEN-554) ────────────


def readers():
    """the United States (Congress)'s text reader per media type; replaces upstream's entry in `fulltext.CONVERSION_FUNCTIONS`."""
    # imported at call time: openstates.fulltext builds its reader table from this function
    from openstates.fulltext.common import extract_sometimes_numbered_pdf
    from openstates.fulltext.us import handle_us_bill_xml
    return {
        # Found 2026-08-12: DoNotDownload here (inherited unchanged from public upstream,
        # predates this fork entirely) skipped every US bill's XML unconditionally. Unlike
        # AZ's PDF bug, this cost nothing in practice -- verified directly that all 44,401 US
        # versions with an XML link also have a PDF link, and PDF extraction already succeeds
        # 100% of the time -- but XML (govinfo's bill.dtd format for most stages, USLM for
        # Enrolled Bills) is cleanly structured with no page-break/line-wrap artifacts, making
        # it a meaningfully better diffing source than PDF's line-numbered extraction. See
        # `handle_us_bill_xml` and archive_bill_versions()'s prior_text preference order below.
        "text/xml": handle_us_bill_xml,
        "application/pdf": extract_sometimes_numbered_pdf,
    }
