"""OPEN-917: Congress's numbered PDFs lost every line a margin number is not printed beside: a bill's table-of-contents and
heading lines ("Sec. 1. Short title; table of contents.", "TITLE I"), and a simple resolution's "Whereas" paragraphs.
`ddp_states/us.py` keeps them, and still leaves out page numbers and the printer's page furniture.

Fixtures are the papers govinfo.gov publishes, the files the archive holds:

* `us_sres54_introduced.pdf`: S.Res. 54 (119th), 3 pages. Only its "Resolved" clause is numbered, so the old reader kept
  17 lines (738 characters) and dropped every "Whereas".
* `us_s241_introduced_p1_p3.pdf`: pages 1 to 3 of S. 241 (119th) introduced. The table-of-contents entries carry no
  margin number.
"""

import re
from pathlib import Path

import pytest

from openstates.fulltext import CONVERSION_FUNCTIONS, utils
from openstates.fulltext.common import extract_sometimes_numbered_pdf
from openstates.ddp_states import us

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/government"}
READ = CONVERSION_FUNCTIONS["us"]["application/pdf"]
FURNITURE = r"VerDate|\bon [A-Z0-9]+ with (?:BILLS|\$\$_JOB)|^[•†]?\s*[HS][A-Z]* \d+ [A-Z]{2,4}$"


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _is_subsequence(old: str, new: str) -> bool:
    it = iter(new.split("\n"))
    return all(any(line == other for other in it) for line in old.split("\n"))


def test_the_reader_is_congresss_own():
    assert READ is us.extract_us_pdf


def test_a_simple_resolutions_whereas_paragraphs_are_kept():
    data = _read("us_sres54_introduced.pdf")
    old = extract_sometimes_numbered_pdf(data, META)
    assert "Whereas" not in old
    assert len(old) == 738
    text = READ(data, META)
    assert len(text) == 4197
    lines = text.splitlines()
    assert lines[0].startswith("Whereas early efforts of the Colombian Government")  # the first one, before any number
    assert text.count("Whereas") == 18  # all of them: `pdftotext` on the whole file finds 18
    assert "Resolved, That the Senate—" in lines
    assert _is_subsequence(old, text)  # nothing the old reader kept is lost, in the same order
    assert not any("S. RES. 54" in line or "IN THE SENATE" in line for line in lines)  # the cover stays out


def test_a_bills_section_headings_and_contents_entries_are_kept():
    data = _read("us_s241_introduced_p1_p3.pdf")
    old = extract_sometimes_numbered_pdf(data, META)
    assert "Sec. 101. Short title." not in old.splitlines()
    text = READ(data, META)
    lines = text.splitlines()
    for heading in ("Sec. 1. Short title; table of contents.", "Sec. 101. Short title.", "Sec. 116. Antideficiency.",
                    "TITLE II—BLACKFEET TRIBE WASTEWATER FACILITIES"):
        assert heading in lines
    assert len(lines) == 66 and len(old.splitlines()) == 44
    assert _is_subsequence(old, text)
    assert lines[0].startswith("Be it enacted")  # the cover and the "A BILL" title block stay out


@pytest.mark.parametrize("name", ["us_sres54_introduced.pdf", "us_s241_introduced_p1_p3.pdf"])
def test_page_numbers_and_printer_furniture_stay_out(name):
    for line in READ(_read(name), META).splitlines():
        assert not re.search(FURNITURE, line), line
        assert not re.fullmatch(r"\s*\d+\s*", line), line  # a page number


def _fake(monkeypatch, text: str) -> None:
    monkeypatch.setattr(utils, "pdfdata_to_text", lambda data: text)


def test_a_paper_without_margin_numbers_is_read_as_it_is_printed(monkeypatch):
    text = "RESOLUTION\nrecognizing something\nWhereas a thing;\nWhereas another;\n" * 4
    _fake(monkeypatch, text)
    assert READ(b"", META) == text


def test_the_body_starts_after_the_title_block_and_stops_at_the_last_numbered_line(monkeypatch):
    page = "\n".join([
        "119TH CONGRESS", "S. RES. 9", "IN THE SENATE", "RESOLUTION", "Honoring a thing", "  or other.", "",
        "Whereas one;", "Whereas two;", "  continued;",
        " 1   Resolved, That the Senate—", " 2   (1) honors it.", "Attest: Secretary", "\f",
    ])
    _fake(monkeypatch, page)
    assert READ(b"", META).split("\n") == [
        "Whereas one;", "Whereas two;", "continued;", "Resolved, That the Senate—", "(1) honors it.",
    ]


def test_with_no_title_heading_the_body_starts_at_the_first_numbered_line(monkeypatch):
    _fake(monkeypatch, "\n".join(["cover line", "More cover", " 1   alpha", "     Sec. 2. Beta.", " 2   gamma"]))
    assert READ(b"", META).split("\n") == ["alpha", "Sec. 2. Beta.", "gamma"]


def test_printer_furniture_in_the_body_goes_whether_on_its_own_line_or_sharing_one(monkeypatch):
    text = "\n".join([
        "A BILL", "To do a thing.", "",
        " 1   alpha", " 2   beta",
        "kjohnson on DSK7ZCZBW3PROD with $$_JOB",
        "VerDate Sep 11 2014   22:13 Jan 04, 2025   Jkt 059200   PO 00000   Frm 00001",
        "                                  2",
        "lotter on DSK11XQN23PROD with BILLS                  •HR 6283 IH",
        "HR 33 RFS",
        "•SRES 312 IS1S",
        "\f 1   gamma", "     Sec. 3. Heading.", " 2   delta",
    ])
    _fake(monkeypatch, text)
    assert READ(b"", META).split("\n") == ["alpha", "beta", "gamma", "Sec. 3. Heading.", "delta"]


def test_a_body_line_that_only_looks_like_furniture_is_kept(monkeypatch):
    text = "\n".join(["A BILL", "To do a thing.", "", " 1   alpha", "     Sec. 7 in the VerDate Act.", "     HR 33 hearings", " 2   beta"])
    _fake(monkeypatch, text)
    assert READ(b"", META).split("\n") == ["alpha", "Sec. 7 in the VerDate Act.", "HR 33 hearings", "beta"]


def test_the_other_media_type_and_no_other_states_entry_changes():
    assert CONVERSION_FUNCTIONS["us"]["text/xml"].__name__ == "handle_us_bill_xml"
    assert set(CONVERSION_FUNCTIONS["us"]) == {"text/xml", "application/pdf"}


def test_a_concurrent_resolutions_first_whereas_follows_the_heading_directly(monkeypatch):
    # no title under the heading (H. Con. Res. 106): the Whereas paragraph must not be taken for the title block
    text = "\n".join([
        "H. CON. RES. 9", "", "CONCURRENT RESOLUTION",
        "Whereas one is true", "  and goes on;", "Whereas two;",
        " 1   Resolved by the House of Representatives", " 2   (the Senate concurring), That it is so.",
    ])
    _fake(monkeypatch, text)
    assert READ(b"", META).split("\n") == [
        "Whereas one is true", "and goes on;", "Whereas two;",
        "Resolved by the House of Representatives", "(the Senate concurring), That it is so.",
    ]
