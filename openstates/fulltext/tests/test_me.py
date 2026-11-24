"""OPEN-871: Maine PDFs are read (upstream's table said DoNotDownload, so the archive saved nothing).

The fixtures are real pages from mainelegislature.org (`getPDF.asp?paper=...`), each cut from the published PDF with
PyMuPDF (pdftotext reads the cut page exactly as it reads that page in the whole file):

* LD 108 (132R1), Introduced, both pages: an unnumbered cover page (title, sponsor), then the numbered bill.
* LD 769 (132R1), Introduced, page 2: numbered lines; a whole paragraph and some words printed struck through.
* LD 1797 (132S1), Chapter 186, page 1: a Public Law, no line numbers.
"""

from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS, DoNotDownload, get_extract_func
from openstates.fulltext.common import (
    extract_line_numbered_pdf,
    extract_simple_pdf,
)

TESTDATA = Path(__file__).parent / "testdata"
ME = "ocd-jurisdiction/country:us/state:me/government"


def _read(name: str) -> str:
    metadata = {
        "url": "http://www.mainelegislature.org/legis/bills/getPDF.asp?paper=HP0073&item=1&snum=132",
        "media_type": "application/pdf",
        "title": "",
        "jurisdiction_id": ME,
    }
    return get_extract_func(metadata)((TESTDATA / name).read_bytes(), metadata)


def test_maine_pdf_is_downloaded():
    # the bug: upstream's DoNotDownload, which the archiver skips without a count
    assert CONVERSION_FUNCTIONS["me"]["application/pdf"] is not DoNotDownload


def test_the_other_upstream_entries_are_kept():
    assert CONVERSION_FUNCTIONS["me"]["application/rtf"] is DoNotDownload
    assert "text/html" in CONVERSION_FUNCTIONS["me"]


def test_an_introduced_bill_keeps_its_numbered_lines():
    data = (TESTDATA / "me_ld108_introduced.pdf").read_bytes()
    text = _read("me_ld108_introduced.pdf")
    assert text == extract_line_numbered_pdf(data, {})
    lines = text.split("\n")
    assert lines[0] == "Be it enacted by the People of the State of Maine as follows:"
    assert "SUMMARY" in text
    assert "suicide" in text
    # no line number is left in front of the text, and the page footer is not text
    assert not any(line.strip()[:1].isdigit() and line.split()[0].isdigit() for line in lines)
    assert "132LR0138" not in text


def test_a_chapter_law_is_read_whole():
    data = (TESTDATA / "me_ld1797_chapter186_p1.pdf").read_bytes()
    text = _read("me_ld1797_chapter186_p1.pdf")
    # no numbered line, so the line-numbered reader keeps nothing
    assert extract_line_numbered_pdf(data, {}) == ""
    assert text == extract_simple_pdf(data, {})
    assert "Concerning Denials of Public Records Requests" in text
    assert "Be it enacted by the People of the State of Maine as follows:" in text


def test_struck_out_text_is_left_out():
    data = (TESTDATA / "me_ld769_introduced_p2.pdf").read_bytes()
    plain = extract_line_numbered_pdf(data, {})
    text = _read("me_ld769_introduced_p2.pdf")
    # the struck paragraph (2) and the struck words "For a child under 18 years of age, with" are in the plain
    # reading, mixed into the text that stays
    assert "For an adult 18 years of age or older" in plain
    assert "For a child under 18 years of age, with With the approval" in plain
    assert "For an adult 18 years of age or older" not in text
    assert "For a child under 18" not in text
    # the words that stay
    assert "With the approval, following a case-" in text
    assert "(1) On the recommendation of the person's personal planning team; and" in text
