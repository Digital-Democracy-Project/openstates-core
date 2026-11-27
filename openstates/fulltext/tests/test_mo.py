"""OPEN-908: Missouri's Senate committee substitutes and resolutions have no margin line numbers, so the numbered-lines
reader kept almost nothing of them (SB 1017's substitute: 53 characters); `ddp_states/mo.py` tells them from numbered
bills and reads them in full.

The fixtures are the legislature's whole published PDFs (2026 session, senate.mo.gov), the same files the archive holds
(the classification is a share of a whole document's lines, so a single page would not test it):

* `mo_sb1017_scs_committee_version.pdf`: SB 1017's "Senate Committee Substitute - Committee Version" (6 pages). Its stored
  text was 53 characters, the one line that starts with a number ("536 are nonseverable and if any of the powers vested
  with", from "section 536.010 ... chapter 536 are ...").
* `mo_scr21_introduced.pdf`: SCR 21's introduced text, a resolution (3 pages). Stored text: 52 characters ("250 Missouri
  Commission, and its forty-nine counterparts", from "the America / 250 Missouri Commission, ...").
* `mo_sb1017_introduced.pdf`: SB 1017's introduced text, a bill with margin line numbers (2 pages; stored text 2,019
  characters, read as before).
"""

from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.common import extract_line_numbered_pdf
from openstates.fulltext.utils import pdfdata_to_text

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:mo/government"}
READ = CONVERSION_FUNCTIONS["mo"]["application/pdf"]


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def test_a_committee_substitute_without_margin_numbers_is_read_in_full():
    data = _read("mo_sb1017_scs_committee_version.pdf")
    stored = extract_line_numbered_pdf(data, META)  # what was stored before
    assert stored == "are nonseverable and if any of the powers vested with"
    text = READ(data, META)
    assert text == pdfdata_to_text(data)
    assert len(text) > 9000
    assert "SENATE BILLS NOS. 1017 & 1239" in text  # the title, page 1
    assert "(d) The director of revenue may promulgate and enforce" in text  # page 4
    assert "provided in section 144.701.   The remaining portion of the" in text  # the last page


def test_a_resolution_without_margin_numbers_is_read_in_full():
    data = _read("mo_scr21_introduced.pdf")
    assert (
        extract_line_numbered_pdf(data, META).strip()
        == "Missouri Commission, and its forty-nine counterparts"
    )  # what was stored before
    text = READ(data, META)
    assert text == pdfdata_to_text(data)
    assert "SENATE CONCURRENT RESOLUTION NO. 21" in text
    assert "adopted the Declaration of" in text  # a line before the one the old reader kept
    assert "Whereas, comprised of a group from the private and" in text  # and one after it


def test_a_bill_with_margin_numbers_is_read_as_before():
    data = _read("mo_sb1017_introduced.pdf")
    text = READ(data, META)
    assert text == extract_line_numbered_pdf(data, META)
    assert len(text) == 2019  # the stored length
    assert "Section 144.014, RSMo, is repealed and one new" in text
    assert "INTRODUCED BY SENATOR" not in text  # the margin-numbered reader drops the unnumbered header
