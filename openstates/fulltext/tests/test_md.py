"""OPEN-837: Maryland PDFs, read with the line-numbered reader only when the document has line numbers.

The fixtures are real pages, each cut from the published PDF with PyMuPDF (pdftotext reads the cut page exactly as
it reads that page in the whole file):

* Senate Bill 907 (2026RS), Chapter 28, page 1 (`2026RS/Chapters_noln/CH_28_sb0907t.pdf`): the signed law, printed
  without line numbers. The line-numbered reader read the whole file to 0 characters.
* Senate Bill 907 (2026RS), Third Reader, page 1 (`2026RS/bills/sb/sb0907T.pdf`): every line of the act numbered.
"""

from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS, get_extract_func
from openstates.fulltext.common import (
    extract_line_numbered_pdf,
    extract_sometimes_numbered_pdf,
)
from openstates.fulltext.utils import pdfdata_to_text

TESTDATA = Path(__file__).parent / "testdata"
MD = "ocd-jurisdiction/country:us/state:md/government"


def _read(name: str, url: str) -> str:
    metadata = {
        "url": url,
        "media_type": "application/pdf",
        "title": "",
        "jurisdiction_id": MD,
    }
    return get_extract_func(metadata)((TESTDATA / name).read_bytes(), metadata)


def test_a_chapter_law_is_read_whole():
    data = (TESTDATA / "md_sb907_chapter28_p1.pdf").read_bytes()
    text = _read(
        "md_sb907_chapter28_p1.pdf",
        "http://mgaleg.maryland.gov/2026RS/Chapters_noln/CH_28_sb0907t.pdf",
    )
    # the bug: no numbered line, so nothing was kept
    assert extract_line_numbered_pdf(data, {}) == ""
    assert text == pdfdata_to_text(data)
    # past the cover lines (the governor's signature line, "Ch. 28", "Chapter 28"): the act itself
    assert "Public Health – Female Genital Mutilation" in text
    assert (
        "FOR the purpose of altering the definition of “abuse” to include female genital mutilation"
        in text
    )


def test_a_third_reader_keeps_the_line_numbered_reading():
    data = (TESTDATA / "md_sb907_third_p1.pdf").read_bytes()
    text = _read(
        "md_sb907_third_p1.pdf",
        "http://mgaleg.maryland.gov/2026RS/bills/sb/sb0907T.pdf",
    )
    assert text == extract_line_numbered_pdf(data, {})
    lines = text.split("\n")
    assert "AN ACT concerning" in lines
    # the unnumbered header (committee, dates, "CHAPTER ______") is still left out, as before
    assert "Introduced and read first time" not in text
    assert "CHAPTER ______" not in text


def test_maryland_reads_pdfs_with_the_shared_reader():
    assert CONVERSION_FUNCTIONS["md"] == {
        "application/pdf": extract_sometimes_numbered_pdf
    }
