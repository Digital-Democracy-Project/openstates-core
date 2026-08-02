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
    data = (TEST_DATA_PATH / fixture).read_bytes()
    metadata = {**VA_METADATA, "media_type": "application/pdf"}

    text = CONVERSION_FUNCTIONS["va"]["application/pdf"](data, metadata)

    assert text
    # real VA PDFs prefix every real line with a running line number (pdftotext -layout
    # renders it as e.g. "     1                     HOUSE BILL NO. 1"); confirm
    # text_after_line_numbers stripped that prefix, leaving the bare content as its own line
    # rather than as a substring of a still-numbered line.
    assert expected in text.splitlines()
