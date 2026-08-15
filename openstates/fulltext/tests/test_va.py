from pathlib import Path

import pytest  # type: ignore

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.va import handle_virginia_html

TEST_DATA_PATH = Path(__file__).parent / "testdata"

# Real 2026 Regular Session documents fetched from lis.blob.core.windows.net during OPEN-15's
# diagnosis: a bill (HB1) and a joint resolution (HJ1), covering both a section-sign citation
# and a plain resolution with no such citation.
VA_METADATA = {
    "url": "https://lis.blob.core.windows.net/files/x",
    "title": "x",
    "jurisdiction_id": "ocd-jurisdiction/country:us/state:va/government",
}


@pytest.mark.parametrize("fixture", ["va_hb1_2026.html", "va_hj1_2026.html"])
def test_handle_virginia_html_extracts_real_documents(fixture):
    data = (TEST_DATA_PATH / fixture).read_bytes()
    metadata = {**VA_METADATA, "media_type": "text/html"}

    text = handle_virginia_html(data, metadata)

    assert text
    assert "2026 SESSION" in text


def test_handle_virginia_html_decodes_utf8_correctly():
    # HB1 has a real "§" citation (Code of Virginia § 40.1-28.10) -- confirmed during
    # diagnosis that parsing this bare, charset-less <p>-tag fragment as raw bytes makes
    # lxml guess an 8-bit encoding and mangle it into mojibake (e.g. "Â§"). Decoding as
    # UTF-8 before parsing is what OPEN-15 fixed; this pins that fix against a real document.
    data = (TEST_DATA_PATH / "va_hb1_2026.html").read_bytes()
    metadata = {**VA_METADATA, "media_type": "text/html"}

    text = handle_virginia_html(data, metadata)

    assert "§ 40.1-28.10" in text
    assert "Â§" not in text


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("va_hb1_2026.pdf", "HOUSE BILL NO. 1"),
        ("va_hj1_2026.pdf", "HOUSE JOINT RESOLUTION NO. 1"),
    ],
)
def test_va_pdf_extraction_strips_line_numbers(fixture, expected):
    # Regression coverage for OPEN-76: both fixtures use VA's numbered-draft PDF template
    # (Introduced stage), the convention extract_sometimes_numbered_pdf must keep routing
    # through its numbered-line path exactly as extract_line_numbered_pdf always did.
    data = (TEST_DATA_PATH / fixture).read_bytes()
    metadata = {**VA_METADATA, "media_type": "application/pdf"}

    text = CONVERSION_FUNCTIONS["va"]["application/pdf"](data, metadata)

    assert text
    # real VA PDFs prefix every real line with a running line number (pdftotext -layout
    # renders it as e.g. "     1                     HOUSE BILL NO. 1"); confirm
    # text_after_line_numbers stripped that prefix, leaving the bare content as its own line
    # rather than as a substring of a still-numbered line.
    assert expected in text.splitlines()


# OPEN-76: real 2026 Regular Session documents fetched from lis.virginia.gov confirming the
# root cause -- VA's LIS uses a second, genuinely different PDF layout template (the
# unnumbered "Acts of Assembly" final-typeset convention, no body line numbers, just a
# "Page X of N" footer) for the enacted "Chaptered" bill stage and every resolution's
# "Enrolled" stage. The old extractor (extract_line_numbered_pdf, which keeps only
# digit-prefixed lines) dropped nearly all real content for these and kept only that
# repeated "of N" footer plus a handful of accidental fragments:
#   va_hb1_chaptered_2026.pdf  (1pg):  3,093 real chars -> 0 chars
#   va_hb19_chaptered_2026.pdf (5pg): 25,032 real chars -> 219 chars of "of 5\nof 5..."
#   va_hj1_enrolled_2026.pdf   (1pg):  4,004 real chars -> 109 chars of fragments
@pytest.mark.parametrize(
    "fixture,expected_substrings",
    [
        ("va_hb1_chaptered_2026.pdf", ["CHAPTER 350", "40.1-28.10"]),
        ("va_hb19_chaptered_2026.pdf", ["CHAPTER 527"]),
        (
            "va_hj1_enrolled_2026.pdf",
            ["RESOLVED by the House of Delegates", "reproductive freedom"],
        ),
    ],
)
def test_va_pdf_extraction_recovers_real_content_for_chaptered_and_enrolled_resolution(
    fixture, expected_substrings
):
    data = (TEST_DATA_PATH / fixture).read_bytes()
    metadata = {**VA_METADATA, "media_type": "application/pdf"}

    text = CONVERSION_FUNCTIONS["va"]["application/pdf"](data, metadata)

    # No other real, successfully-extracted VA application/pdf document anywhere in the
    # archive ever falls under ~290 chars (per the ticket) -- well above the old garbage
    # ceiling (0-219 chars for these exact fixtures), confirming this is real substantial
    # content and not just non-empty.
    assert len(text) > 1000
    for substring in expected_substrings:
        assert substring in text
    # the defining old-garbage signature: near-total domination by the repeated page-footer
    # artifact. Real content should reduce this to a tiny fraction of the extracted text.
    assert text.count("of ") < len(text) / 100
