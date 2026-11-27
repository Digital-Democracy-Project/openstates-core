"""OPEN-914: Oklahoma's numbered PDFs lost every line pdftotext did not put a margin number beside (SB 374 introduced
200 of 792 body lines); `ddp_states/ok.py` keeps them and still leaves out page numbers and running footers.

Fixtures are the legislature's own published PDFs (oklegislature.gov), the files the archive holds:

* `ok_sb439_introduced.pdf`: SB 439 introduced (6 pages). Its "B. The Board is authorized and empowered to:" paragraph
  and the items 1 and 2 under it sit under a margin number printed on a line of its own, so the old reader dropped
  them (6,068 characters stored of 8,066).
* `ok_hb3588_engrossed_p1_p3.pdf`: pages 1 to 3 of HB 3588 engrossed. The title paragraph is numbered on every other
  line, so half of its lines were dropped (3,150 characters stored of 5,182); each page ends "ENGR. H. B. NO. 3588  Page N".
"""

import re
from pathlib import Path

import pytest

from openstates.fulltext import CONVERSION_FUNCTIONS, utils
from openstates.fulltext.common import extract_sometimes_numbered_pdf
from openstates.ddp_states import ok

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:ok/government"}
READ = CONVERSION_FUNCTIONS["ok"]["application/pdf"]


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _is_subsequence(old: str, new: str) -> bool:
    it = iter(new.split("\n"))
    return all(any(line == other for other in it) for line in old.split("\n"))


def test_the_reader_is_oklahomas_own():
    assert READ is ok.extract_ok_pdf


def test_a_paragraph_under_a_margin_number_on_its_own_line_is_kept():
    data = _read("ok_sb439_introduced.pdf")
    old = extract_sometimes_numbered_pdf(data, META)
    assert "B.   The Board is authorized and empowered to:" not in old
    assert len(old) == 6068
    text = READ(data, META)
    assert len(text) == 8066
    lines = text.splitlines()
    assert "B.   The Board is authorized and empowered to:" in lines
    assert "1.   Examine and test the qualifications of applicants for a" in lines
    assert "2.   Affiliate by contract or cooperative agreement with another" in lines
    assert _is_subsequence(old, text)  # nothing the old reader kept is lost, in the same order


def test_the_title_paragraph_numbered_on_every_other_line_is_kept_whole():
    data = _read("ok_hb3588_engrossed_p1_p3.pdf")
    old = extract_sometimes_numbered_pdf(data, META)
    assert len(old) == 3150
    text = READ(data, META)
    assert len(text) == 5182
    lines = text.splitlines()
    assert "the Uniform Assignment for Benefit of Creditors Act;" in lines  # a continuation line
    assert "an assignee shall be; requiring assignment agreement" in lines
    assert _is_subsequence(old, text)


@pytest.mark.parametrize("name", ["ok_sb439_introduced.pdf", "ok_hb3588_engrossed_p1_p3.pdf"])
def test_page_numbers_and_running_footers_stay_out(name):
    text = READ(_read(name), META)
    for line in text.splitlines():
        assert not re.search(r"Req\. No\.|\bPage \d+\s*$|ENGR\. H\. B\. NO\.", line), line
        assert not re.fullmatch(r"\s*\d+\s*", line), line  # a bare margin number


def _fake(monkeypatch, text: str) -> None:
    monkeypatch.setattr(utils, "pdfdata_to_text", lambda data: text)


def _page(n, body, footer="Req. No. 99"):
    """a page the way pdftotext -layout prints one: margin numbers on 1-24, a footer"""
    return "\n".join(body) + f"\n\n     {footer}   Page {n}\n\f"


def test_a_paper_without_margin_numbers_is_read_as_it_is_printed(monkeypatch):
    text = "AN ACT\nrelating to something\nand more words\n" * 5
    _fake(monkeypatch, text)
    assert READ(b"", META) == text


def test_only_lines_between_the_first_and_the_last_numbered_line_are_added(monkeypatch):
    body = [
        "STATE OF OKLAHOMA",  # cover, before the first numbered line
        " 1   first numbered line",
        "     an unnumbered continuation",
        " 2   second numbered line",
        " 2",
        " 3   third numbered line",
        "Reading Clerk",  # after the last numbered line
    ]
    _fake(monkeypatch, "\n".join(body))
    assert READ(b"", META).split("\n") == [
        "first numbered line",
        "an unnumbered continuation",
        "second numbered line",
        "third numbered line",
    ]


def test_a_two_page_paper_loses_its_page_footers(monkeypatch):
    p1 = _page(1, [" 1   alpha", "     beta", " 2   gamma"])
    p2 = _page(2, [" 1   delta", "     epsilon", " 2   zeta"], footer="Req. No. 99")
    _fake(monkeypatch, p1 + p2)
    assert READ(b"", META).split("\n") == ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]


def test_a_footer_repeated_on_three_pages_stays_out_but_mid_page_text_that_repeats_stays_in(monkeypatch):
    pages = "".join(
        _page(n, [" 1   text one", " 2   text two", "     and", " 3   text three", " 4   text four", " 5   text five", "     ENGR. H. B. NO. 5"], footer="Req. No. 9")
        for n in (1, 2, 3)
    )
    _fake(monkeypatch, pages)
    out = READ(b"", META).split("\n")
    assert out.count("and") == 3  # a body word on a line of its own is not a footer: it is mid-page
    assert not any("Req. No." in line or "ENGR." in line for line in out)
