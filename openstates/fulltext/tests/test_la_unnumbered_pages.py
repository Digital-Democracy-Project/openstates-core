"""OPEN-915: Louisiana's PDFs with margin line numbers keep the pages the legislature attaches after the instrument that
carry no margin numbers (the redistricting bills' "Plan Statistics" tables), and nothing else changes (`ddp_states/la.py`
`extract_la_pdf`). The digest is not recovered; it stays out of the text, as before.

The fixtures are real pages of the published PDFs (2026 Regular Session, legis.la.gov), cut with PyMuPDF and their fonts
subset; pdftotext reads a cut page as it reads that page in the whole file, and each page keeps its own "Page N of M"
footer:

* `la_sb121_enrolled_p6_9.pdf`: SB 121 (congressional districts) Enrolled, pages 6 to 9 of 20: the end of the law text,
  the signature page ("Page 7 of 7"), then "Plan Statistics" and "District Summary" tables with no margin numbers. The
  law text is complete before this change; the tables were not stored.
* `la_sb136_original_p11_15_39.pdf`: SB 136 (Senate districts) Original, pages 11 to 15 and 39 of 51: law text, the page
  where the digest starts (13), the rest of the digest (14, "Page 14 of 14", no margin numbers), the first page of
  "Plan Statistics" (15), and a map page (39, upright precinct labels, some of them numbers).
* `la_sb117_original_p7_10_16.pdf`: SB 117 (congressional districts) Original, pages 7, 8, 9 (of 9), 10 and 16 (attachments):
  law text, the digest ("Page 9 of 9"), the first "Plan Statistics" page (10), and a map page (16) whose only
  number-like line is a bare "4", a precinct label standing alone on its line.
* `la_hb10_original.pdf`: HB 10 Original, whole (2 pages): a numbered bill and its digest.
* `la_sb460_act.pdf`: SB 460 Act 218, whole (2 pages): a numbered Act with a cover block and signature lines.
* `la_hcr104_enrolled.pdf`: HCR 104 Enrolled, whole (2 pages): a resolution with no margin numbers, read in full.
"""

from pathlib import Path

import pytest

from openstates.ddp_states.la import extract_la_pdf
from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.al import read_without_struck_text
from openstates.fulltext.common import extract_sometimes_numbered_pdf
from openstates.fulltext.utils import pdfdata_to_text

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:la/government"}
READ = CONVERSION_FUNCTIONS["la"]["application/pdf"]
OLD = read_without_struck_text(extract_sometimes_numbered_pdf)  # what Louisiana used before


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _lines(text: str) -> list:
    return [line for line in text.split("\n") if line.strip()]


def _is_subsequence(small: list, big: list) -> bool:
    rest = iter(big)
    return all(line in rest for line in small)


def test_louisiana_reads_through_extract_la_pdf_under_the_struck_text_reader():
    assert READ.__wrapped__ is extract_la_pdf


def test_a_redistricting_bills_plan_statistics_are_kept_after_the_law_text():
    data = _read("la_sb121_enrolled_p6_9.pdf")
    old, new = OLD(data, META), READ(data, META)
    assert new.startswith(old + "\n")  # every line the old reader kept, first, in order
    tables = new[len(old) + 1 :]
    assert tables.startswith("Plan Statistics\nPlan: Senate Bill 121 Enrolled\n")
    assert "District 1                       1           776,249            776,292" in tables
    assert "Grand Total:                      6        4,657,757          4,657,752" in tables
    # the law text before them is the old text, complete: its last sentence, then the tables
    assert old.endswith("Sections 3 and 4 of this Act shall become effective on the day following such approval.")
    # the signature page (page 7, numbered lines, then the unnumbered signature block) adds nothing
    assert "PRESIDENT OF THE SENATE" not in new


def test_the_digest_is_not_recovered_and_a_map_page_is_read_as_before():
    data = _read("la_sb136_original_p11_15_39.pdf")
    old, new = OLD(data, META), READ(data, META)
    assert _is_subsequence(_lines(old), _lines(new))  # nothing the old reader kept is lost
    pages = pdfdata_to_text(data).split("\f")
    assert len(pages) == 7  # six pages and the empty tail
    statistics = [line.strip() for line in pages[4].split("\n") if line.strip()]  # the "Plan Statistics" page
    assert statistics[0] == "Plan Statistics"
    extra = _lines(new)
    for line in _lines(old):
        extra.remove(line)
    assert extra == statistics  # the only lines gained are that whole page, none of the digest's page, none of the map's
    assert "The digest printed below" not in new
    assert "It constitutes no part" not in new


def test_a_map_page_with_a_bare_number_is_not_kept():
    data = _read("la_sb117_original_p7_10_16.pdf")
    old, new = OLD(data, META), READ(data, META)
    pages = pdfdata_to_text(data).split("\f")
    statistics = [line.strip() for line in pages[3].split("\n") if line.strip()]
    assert statistics[0] == "Plan Statistics"
    extra = _lines(new)
    for line in _lines(old):
        extra.remove(line)
    assert extra == statistics  # the statistics page only; the map page (pages[4]) reads as before


def test_a_numbered_bill_with_its_digest_reads_as_before():
    data = _read("la_hb10_original.pdf")
    text = READ(data, META)
    assert text == OLD(data, META)
    assert "Section 1. R.S. 1:55(E)(1)(j) is hereby enacted to read as follows:" in text
    assert "Abstract" not in text  # the digest stays out
    assert "constitutes no part" not in text


def test_a_numbered_act_reads_as_before():
    data = _read("la_sb460_act.pdf")
    text = READ(data, META)
    assert text == OLD(data, META)
    assert text.startswith("Regular Session")
    assert "Section 1. Notwithstanding any other provision of law to the contrary, Louisiana" in text
    assert "ACT No. 218" not in text  # the cover block is not kept, as before


def test_a_paper_without_margin_numbers_is_read_in_full():
    data = _read("la_hcr104_enrolled.pdf")
    assert READ(data, META) == OLD(data, META) == pdfdata_to_text(data)


# The tests below use made-up page text (no real paper has these footers), to pin the end-of-instrument rule's edges and
# the branch choice. `pdfdata_to_text` is replaced so no PDF is needed.


def _page(numbered: list, footer: str = "", extra: list = ()) -> str:
    return "\n".join([f" {i}   {text}" for i, text in enumerate(numbered, 1)] + list(extra) + ([footer] if footer else []))


def _read_text(monkeypatch, pages: list, reader=extract_la_pdf) -> str:
    text = "\f".join(pages) + "\f"
    monkeypatch.setattr("openstates.fulltext.utils.pdfdata_to_text", lambda data: text)
    monkeypatch.setattr("openstates.fulltext.common.pdfdata_to_text", lambda data: text)
    return reader(b"", META)


def test_a_paper_without_a_last_page_footer_keeps_the_old_reading(monkeypatch):
    pages = [
        _page(["law one", "law two"], "      Page 1 of 3"),
        _page(["law three"], "      Page 2 of 3"),
        "Plan Statistics\nDistrict 1    776,249",  # no footer says the instrument ended
    ]
    assert _read_text(monkeypatch, pages) == _read_text(monkeypatch, pages, extract_sometimes_numbered_pdf)
    assert "Plan Statistics" not in _read_text(monkeypatch, pages)


def test_a_footer_that_does_not_follow_the_one_before_does_not_end_the_instrument(monkeypatch):
    pages = [
        _page(["law one"], "      Page 1 of 3"),
        _page(["law two"], "      Page 2 of 3", extra=["      Page 2 of 2"]),  # a quoted footer, M differs
        "DIGEST\nthe digest has no margin numbers\n      Page 3 of 3",
        "Plan Statistics\nDistrict 1    776,249",
    ]
    text = _read_text(monkeypatch, pages)
    assert "the digest has no margin numbers" not in text
    assert text.endswith("Plan Statistics\nDistrict 1    776,249")


@pytest.mark.parametrize("numbered", [9, 10, 11])
def test_the_branch_is_upstreams_at_the_ten_percent_line(monkeypatch, numbered):
    lines = [f" {i}   numbered {i}" for i in range(1, numbered + 1)] + [f"plain {i}" for i in range(100 - numbered)]
    assert len(lines) == 100
    text = "\n".join(lines)
    monkeypatch.setattr("openstates.fulltext.utils.pdfdata_to_text", lambda data: text)
    monkeypatch.setattr("openstates.fulltext.common.pdfdata_to_text", lambda data: text)
    assert extract_la_pdf(b"", META) == extract_sometimes_numbered_pdf(b"", META)
