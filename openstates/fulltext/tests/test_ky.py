"""OPEN-906: Kentucky's chaptered acts ("Acts Chapter N") have no margin line numbers, so the numbered-lines reader kept
almost nothing of them (164 of 202 under 300 characters in 2026RS); `ddp_states/ky.py` reads an act as plain text.

The fixtures are real published PDFs (2026RS):

* `ky_hb1_acts_chapter4.pdf`: HB 1's whole act, one page. The stored text of this version was empty.
* `ky_hb10_acts_chapter178_p1_p3.pdf`: pages 1 to 3 of HB 10's act (19 pages). Stored text of the whole act: 260
  characters, nine "ACTS OF THE GENERAL ASSEMBLY" header lines.
* `ky_hb816_acts_chapter119_p1_p2.pdf`: pages 1 and 2 of HB 816's act (a list of claims; the addresses start with numbers).
* `ky_hb10_current_final_p1.pdf`: page 1 of HB 10's Current/Final bill PDF, with its margin line numbers (read as before).
"""

from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.common import (
    extract_line_numbered_pdf,
    extract_sometimes_numbered_pdf,
)
from openstates.fulltext.utils import pdfdata_to_text

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:ky/government"}
READ = CONVERSION_FUNCTIONS["ky"]["application/pdf"]


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def test_a_short_act_is_read_in_full():
    data = _read("ky_hb1_acts_chapter4.pdf")
    assert extract_line_numbered_pdf(data, META).strip() == ""  # what was stored before
    text = READ(data, META)
    assert (
        "AN ACT implementing the federal education opportunity program in Kentucky."
        in text
    )
    assert (
        "The Secretary of State may promulgate administrative regulations in accordance with KRS Chapter 13A"
        in text
    )
    assert len(text) > 2000


def test_a_longer_act_is_read_in_full():
    data = _read("ky_hb10_acts_chapter178_p1_p3.pdf")
    assert (
        len(extract_line_numbered_pdf(data, META).strip()) < 300
    )  # what was stored before
    text = READ(data, META)
    assert (
        "SECTION 1. A NEW SECTION OF KRS 12.210 TO 12.230 IS CREATED TO READ AS FOLLOWS:"
        in text
    )
    assert "Section 3. KRS 12.255 is amended to read as follows:" in text  # page 2
    assert (
        "Section 4. KRS 18A.111 is amended to read as follows:" in text
    )  # end of page 2
    assert (
        "if they are hired in accordance with KRS 171.311;" in text
    )  # last line of page 3


def test_a_numbered_bill_is_read_as_before():
    data = _read("ky_hb10_current_final_p1.pdf")
    text = READ(data, META)
    assert text == extract_line_numbered_pdf(data, META)
    assert (
        "AN ACT relating to executive branch operations and declaring an emergency."
        in text
    )
    assert (
        "UNOFFICIAL COPY" not in text
    )  # the margin-numbered reader drops the unnumbered header


def test_a_claims_act_whose_addresses_start_with_numbers_is_read_in_full():
    # HB 816's act lists claimants ("3139 B Canton Street ..."): the "10% of lines start with a number" rule of
    # extract_sometimes_numbered_pdf takes it for a numbered PDF and kept 1,108 of 20,118 characters.
    data = _read("ky_hb816_acts_chapter119_p1_p2.pdf")
    assert len(extract_sometimes_numbered_pdf(data, META)) < 0.5 * len(
        pdfdata_to_text(data)
    )  # the trap
    text = READ(data, META)
    assert text == pdfdata_to_text(data)
    assert "Hopkinsville, KY 42240-1315" in text
