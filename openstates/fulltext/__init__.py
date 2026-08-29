from openstates.utils import jid_to_abbr
from .common import (
    extract_simple_pdf,
    extract_line_numbered_pdf,
    extract_line_post_numbered_pdf,
    extract_pre_tag_html,
    extract_sometimes_numbered_pdf,
    extract_from_p_tags_html,
    extractor_for_elements_by_class,
    extractor_for_element_by_id,
    extractor_for_element_by_xpath,
    extract_from_code_tags_html,
    extractor_for_elements_by_xpath,
    textract_extractor,
    Metadata,
    ExtractorFunc,
)
from .de import handle_delaware
from .va import handle_virginia_html
from .ut import handle_utah_xml
from .us import handle_us_bill_xml


class DoNotDownload:
    """ Sentinel to indicate that nothing should be downloaded """


DOCX_MIMETYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

CONVERSION_FUNCTIONS = {
    "al": {"application/pdf": extract_line_numbered_pdf},
    "ak": {"text/html": extractor_for_element_by_id("draftOverlay")},
    "az": {
        "text/html": extractor_for_elements_by_class("WordSection2"),
        # Found 2026-08-10: DoNotDownload here (since before this fork existed) skipped
        # every AZ PDF unconditionally -- fine when a version also has an HTML copy (the
        # common case), but silently zero-archived text for the ~1,160 versions (mostly
        # committee-amendment stages) that exist ONLY as PDF, with no HTML counterpart at
        # all. Verified live against a real never-archived PDF (introduced-bill text):
        # plain fetch, no WAF/bot-block response, and extract_sometimes_numbered_pdf
        # produces clean text -- same numbered-line shape as AL/FL/MA/MD. Some
        # committee-stage PDFs are scanned/image-only (no text layer at all, confirmed via
        # pdffonts/pdfimages on a real sample) and will still archive with
        # raw_text="" -> is_error=True; that's an honest signal (OCR is future work, see
        # `dc`'s textract_extractor precedent), not a regression -- the raw PDF itself is
        # now preserved either way, which DoNotDownload never did.
        "application/pdf": extract_sometimes_numbered_pdf,
    },
    "ar": {"application/pdf": extract_sometimes_numbered_pdf},
    "ca": {
        "text/html": extractor_for_element_by_xpath(
            './/div[@id="bill"] | .//span[@class="Resolution"]'
        ),
        "application/pdf": extract_sometimes_numbered_pdf,
    },
    "co": {"application/pdf": extract_sometimes_numbered_pdf},
    "ct": {"text/html": extract_from_p_tags_html, "application/pdf": DoNotDownload},
    "dc": {"application/pdf": textract_extractor(extension="pdf", method="tesseract")},
    "de": {
        "text/html": handle_delaware,
        "application/pdf": handle_delaware,
        "application/msword": DoNotDownload,
    },
    "fl": {
        "text/html": extract_pre_tag_html,
        "application/pdf": extract_line_numbered_pdf,
    },
    "ga": {"application/pdf": extract_sometimes_numbered_pdf},
    "hi": {
        "text/html": extractor_for_element_by_xpath(
            './/*[@class="Section2"] | .//*[@class="WordSection2"]'
        ),
        "application/pdf": DoNotDownload,
    },
    "ia": {"application/pdf": extract_line_numbered_pdf, "text/html": DoNotDownload},
    "id": {"application/pdf": extract_line_numbered_pdf},
    "il": {"text/html": extract_from_code_tags_html},
    "in": {"application/pdf": extract_sometimes_numbered_pdf},
    "ks": {"application/pdf": extract_sometimes_numbered_pdf},
    "ky": {"application/pdf": extract_line_numbered_pdf},
    "la": {"application/pdf": extract_sometimes_numbered_pdf},
    "ma": {
        "application/pdf": extract_line_numbered_pdf,
        # Added 2026-08-14 (OPEN-37): the enacted Chapter-of-the-Acts page
        # (scrapers/ma/bills.py's "Chapter Law Text (Enacted)" version) is
        # plain HTML with no PDF variant. The chapter title and body text
        # live in two sibling <div class="col-xs-12"> elements -- the first
        # holding <h2 class="h3 chapterTitle">, the second the actual
        # <p>-tag body ending in "Approved, <date>." -- verified directly
        # against 5 real chapter-law pages (Chapter139/2024, Chapter15/3/18
        # of 2025 -- the exact bills this ticket names, H972/H4100/H4004 --
        # and Chapter163/2026).
        "text/html": extractor_for_elements_by_xpath(
            "//div[h2[contains(@class, 'chapterTitle')]] | "
            "//div[h2[contains(@class, 'chapterTitle')]]/following-sibling::div[1]"
        ),
    },
    "md": {"application/pdf": extract_line_numbered_pdf},
    "me": {
        "text/html": extractor_for_elements_by_class("billtextbody"),
        "application/rtf": DoNotDownload,
        "application/pdf": DoNotDownload,
    },
    "mi": {
        "text/html": extractor_for_element_by_xpath('.//*[@class="WordSection1"]'),
        # Found 2026-08-09 (OPEN-49): no application/pdf entry ever existed here, upstream
        # or in this fork -- every MI PDF (7,000+) silently extracted as empty/is_error=True.
        # Most MI PDF stages (Introduced, Substitute, etc.) print line numbers on body text,
        # matching extract_line_numbered_pdf's shape elsewhere (AL/FL/MA/MD/etc.) -- but the
        # enacted "Public Act" stage does NOT use that numbering convention at all, and
        # extract_line_numbered_pdf (which keeps ONLY numbered lines) silently returns empty
        # for it. extract_sometimes_numbered_pdf handles both: verified directly against a
        # real Public Act PDF (previously empty, now extracts correctly) and a real numbered
        # Introduced PDF (unchanged, still extracts correctly).
        "application/pdf": extract_sometimes_numbered_pdf,
    },
    "mo": {"application/pdf": extract_line_numbered_pdf},
    "mn": {"text/html": extractor_for_element_by_id("document")},
    "ms": {
        "text/html": extractor_for_element_by_xpath('.//*[@class="WordSection1"]'),
        "application/pdf": extract_line_numbered_pdf,
    },
    "mt": {"application/pdf": extract_sometimes_numbered_pdf},
    "nc": {"application/pdf": extract_sometimes_numbered_pdf},
    "nd": {"application/pdf": extract_sometimes_numbered_pdf},
    "ne": {"application/pdf": extract_sometimes_numbered_pdf},
    "nh": {
        "application/pdf": extract_sometimes_numbered_pdf,
        "text/html": extract_from_p_tags_html,
    },
    "nj": {"text/html": extractor_for_element_by_xpath('.//*[@class="WordSection3"]')},
    # NY HTML is just summaries
    "ny": {
        "text/html": DoNotDownload,
        "application/pdf": extract_sometimes_numbered_pdf,
    },
    "nm": {
        "application/pdf": extract_sometimes_numbered_pdf,
        "text/html": DoNotDownload,
    },
    "nv": {"application/pdf": extract_sometimes_numbered_pdf},
    "oh": {"application/pdf": extract_line_post_numbered_pdf},
    "or": {"application/pdf": extract_sometimes_numbered_pdf},
    "ok": {"application/pdf": extract_sometimes_numbered_pdf},
    "sc": {"text/html": extract_from_p_tags_html},
    "sd": {"text/html": extractor_for_elements_by_class("fullContent")},
    "tn": {"application/pdf": extract_simple_pdf},
    "ut": {
        "application/pdf": extract_line_numbered_pdf,
        # Found 2026-08-09 (OPEN-49): no text/xml entry ever existed here, upstream or in
        # this fork -- every UT bill served as XML (3,000+) silently extracted as
        # empty/is_error=True. See fulltext/ut.py for the actual defect (a mismatched
        # encoding declaration in Utah's own export, not a missing-content problem).
        "text/xml": handle_utah_xml,
    },
    "pr": {
        "application/msword": textract_extractor(extension="doc"),
        DOCX_MIMETYPE: textract_extractor(extension="docx"),
    },
    "pa": {
        "application/msword": DoNotDownload,
        "text/html": DoNotDownload,
        "application/pdf": extract_line_numbered_pdf,
    },
    "ri": {"application/pdf": extract_sometimes_numbered_pdf},
    # aggressive, but the Washington & Texas HTML are both basically bare
    "tx": {"text/html": extractor_for_element_by_xpath("//html")},
    "va": {
        "text/html": handle_virginia_html,
        # Found 2026-08-15 (OPEN-76): extract_line_numbered_pdf (keep-only-numbered-lines)
        # was mapped unconditionally for every VA PDF stage, but VA's LIS actually generates
        # two genuinely different PDF layout templates. Introduced/Substitute/bill-Enrolled
        # stages print a running line number on every body line (the numbered-draft
        # convention extract_line_numbered_pdf assumes) and extract correctly. The enacted
        # "Chaptered" stage and every resolution's (HJ/HR/SJ/SR) "Enrolled" stage instead use
        # VA's unnumbered "Acts of Assembly" final-typeset template -- no body line numbers
        # at all, just a "Page X of N" footer -- so extract_line_numbered_pdf drops nearly
        # all real content and keeps only that repeated "of N" footer plus a few accidental
        # digit-adjacent fragments. Confirmed directly against real 2026 Regular Session
        # documents fetched from lis.virginia.gov (HB1's Chaptered PDF, HB19's 5-page
        # Chaptered PDF, HJ1's Enrolled PDF): all are genuine embedded-text PDFs (verified via
        # pdffonts, not scanned images -- rules out an OCR gap), and extract_line_numbered_pdf
        # produced 0-219 chars of garbage from thousands of real chars per document.
        # extract_sometimes_numbered_pdf (already used by 13+ other states for exactly this
        # numbered-vs-plain situation) correctly detects which template each real document
        # uses and extracts full real content for both. OPEN-9's existing
        # _VA_LINE_PATTERNS/_strip_virginia_boilerplate (cli/text_extract.py) already strips
        # the plain-template path's decorative "+" margin-artifact lines during diff
        # cleaning, so no new VA-specific cleanup was needed here.
        "application/pdf": extract_sometimes_numbered_pdf,
    },
    "vt": {"application/pdf": extract_sometimes_numbered_pdf},
    "wa": {
        # OPEN-212: structured=True. WA serves its bill HTML with no newlines between
        # elements, so lxml's separator-free text_content() fused tokens across block
        # boundaries -- 5,750 of 5,818 documents stored as a single line, and Engrossed
        # Substitute Senate Bill 5167 stored as bill "516769". Every other jurisdiction using
        # this helper measures clean today and is deliberately left on the old path here; see
        # text_from_element_xpath's docstring.
        "text/html": extractor_for_element_by_xpath("//html", preserve_element_boundaries=True),
        # Found 2026-08-09 (OPEN-49): no application/pdf entry ever existed here, upstream
        # or in this fork -- every WA PDF (5,800+) silently extracted as empty/is_error=True.
        # Verified directly against real WA bills: same printed-line-number PDF shape already
        # handled by extract_line_numbered_pdf for AL/FL/MA/MD/etc.
        "application/pdf": extract_line_numbered_pdf,
    },
    "wi": {
        "application/pdf": extract_sometimes_numbered_pdf,
        "text/html": DoNotDownload,
    },
    "wv": {"text/html": extractor_for_element_by_xpath('.//*[@class="textcontainer"]')},
    "wy": {"application/pdf": extract_sometimes_numbered_pdf},
    "us": {
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
    },
}


def get_extract_func(metadata: Metadata) -> ExtractorFunc:
    try:
        state = jid_to_abbr(metadata["jurisdiction_id"])
        # ignore type here because DoNotDownload sentinels were in the way
        func = CONVERSION_FUNCTIONS[state][metadata["media_type"]]  # type: ignore
    except KeyError:
        print(f"no function for {state}, {metadata['media_type']}")
        return lambda data, metadata: ""
    return func
