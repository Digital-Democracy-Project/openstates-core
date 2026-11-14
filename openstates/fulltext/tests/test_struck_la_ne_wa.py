"""OPEN-560: Louisiana, Nebraska and Washington PDFs, read with struck-out words left out by
Alabama's reader (fulltext/al.py).

The fixtures are real pages, each cut from the published PDF with PyMuPDF (pdftotext reads the
cut page exactly as it reads that page in the whole file):

* Louisiana SB 495 (2026), Engrossed, page 6: "fifty" is struck and "two hundred" underlined
  ("in excess of fifty two hundred dollars", the figure LegBot got wrong).
* Nebraska LB 778 (109th), Final Reading, page 2: "2023" and "2024" are struck after the
  underlined "2027" and "2028"; page 1 has nothing struck.
* Washington SB 5681 (2025-26), Substitute Bill, page 1: "21" and "access" are struck inside
  Washington's "((...))" deletion marks, with "20" and "inclusion" underlined.
"""

from pathlib import Path

import pytest

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.al import handle_alabama_pdf, without_struck_text
from openstates.fulltext.common import (
    extract_line_numbered_pdf,
    extract_sometimes_numbered_pdf,
)

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:la/government"}


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _lines(text: str) -> list:
    return [" ".join(line.split()) for line in text.split("\n")]


def _pdf(state: str):
    return CONVERSION_FUNCTIONS[state]["application/pdf"]


def test_louisiana_sb495_reads_two_hundred_not_fifty_two_hundred():
    data = _read("la_sb495_engrossed_p6.pdf")
    old = "\n".join(_lines(extract_sometimes_numbered_pdf(data, META)))
    new = _lines(_pdf("la")(data, META))
    assert "fifty two hundred" in old  # the defect the ticket reports
    assert "fifty" not in "\n".join(new)
    assert (
        "Paragraph, having an attributable monetary value in excess of two hundred"
        in new
    )


def test_nebraska_lb778_reads_the_new_years_only():
    data = _read("ne_lb778_final_reading_p2.pdf")
    old = _lines(extract_sometimes_numbered_pdf(data, META))
    new = _lines(_pdf("ne")(data, META))
    assert "(4) From July 1, 2027 2023, to June 30, 2028 2024, a municipality" in old
    assert "(4) From July 1, 2027 , to June 30, 2028 , a municipality" in new


def test_washington_sb5681_reads_the_new_words_only():
    data = _read("wa_sb5681_s_p1.pdf")
    old = "\n".join(_lines(extract_line_numbered_pdf(data, META)))
    new = "\n".join(_lines(_pdf("wa")(data, META)))
    assert "Clients age ((21)) 20 and older" in old
    assert "community ((access)) inclusion" in old.replace("\n", " ")
    # Washington's "((" "))" marks are printed, not struck, so they stay around the gap.
    assert "Clients age (( )) 20 and older" in new
    assert "access" not in new
    assert "inclusion" in new


@pytest.mark.parametrize(
    "state,name,extract",
    [
        ("la", "la_sb495_engrossed_p6.pdf", extract_sometimes_numbered_pdf),
        ("ne", "ne_lb778_final_reading_p2.pdf", extract_sometimes_numbered_pdf),
        ("wa", "wa_sb5681_s_p1.pdf", extract_line_numbered_pdf),
    ],
)
def test_only_characters_are_taken_out(state, name, extract):
    # Each line read now is its line read the old way with characters taken out, never added
    # (spacing aside), and at least one line lost something.
    data = _read(name)
    old = _lines(extract(data, META))
    new = _lines(_pdf(state)(data, META))
    assert len(new) == len(old)
    for before, after in zip(old, new):
        rest = iter(before.replace(" ", ""))
        assert all(ch in rest for ch in after.replace(" ", "")), (before, after)
    assert old != new


def test_a_page_with_nothing_struck_is_read_exactly_as_before():
    data = _read("ne_lb778_final_reading_p1.pdf")
    assert without_struck_text(data) is data
    assert _pdf("ne")(data, META) == extract_sometimes_numbered_pdf(data, META)


def test_a_file_pymupdf_cannot_open_is_an_extraction_error():
    # As for Alabama: no quiet fallback that would bring the struck words back.
    for state in ("la", "ne", "wa"):
        with pytest.raises(Exception):
            _pdf(state)(b"%PDF-1.7\n" + b"\x00" * 64, META)


def test_only_these_four_states_read_pdfs_without_struck_text():
    # Each of the three keeps its own text extractor underneath, and no other state's
    # reader or media type changes.
    wrapped = {
        state: funcs["application/pdf"].__wrapped__
        for state, funcs in CONVERSION_FUNCTIONS.items()
        if hasattr(funcs.get("application/pdf"), "__wrapped__")
    }
    assert wrapped == {
        "la": extract_sometimes_numbered_pdf,
        "ne": extract_sometimes_numbered_pdf,
        "wa": extract_line_numbered_pdf,
    }
    assert CONVERSION_FUNCTIONS["al"]["application/pdf"] is handle_alabama_pdf
    assert set(CONVERSION_FUNCTIONS["la"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["ne"]) == {"application/pdf"}
    assert set(CONVERSION_FUNCTIONS["wa"]) == {"text/html", "application/pdf"}
