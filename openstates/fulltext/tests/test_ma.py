from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS

TEST_DATA_PATH = Path(__file__).parent / "testdata"

# OPEN-37: the enacted Chapter-of-the-Acts page (scrapers/ma/bills.py's
# "Chapter Law Text (Enacted)" version) is plain HTML with no PDF variant. Fixtures below are
# real content fetched 2026-08-14 from the exact bills OPEN-37 names as verification examples
# (H972 -> Chapter 15 of the Acts of 2025) plus a second real chapter (H4889 -> Chapter 139 of
# the Acts of 2024) with its real surrounding page chrome included, to pin that the extractor
# doesn't sweep in the toolbar/sidebar that borders the wanted content on the live page.
MA_METADATA = {
    "url": "https://malegislature.gov/Laws/SessionLaws/Acts/2025/Chapter15",
    "title": "x",
    "jurisdiction_id": "ocd-jurisdiction/country:us/state:ma/government",
}


def test_ma_chapter_law_html_extracts_real_document():
    data = (TEST_DATA_PATH / "ma_chapter15_2025.html").read_bytes()
    metadata = {**MA_METADATA, "media_type": "text/html"}

    text = CONVERSION_FUNCTIONS["ma"]["text/html"](data, metadata)

    assert text
    assert "AN ACT AUTHORIZING THE MASSACHUSETTS WATER RESOURCES AUTHORITY" in text
    assert "SECTION 1." in text
    assert "SECTION 2." in text
    assert "Approved, August 5, 2025." in text


def test_ma_chapter_law_html_excludes_surrounding_page_chrome():
    data = (TEST_DATA_PATH / "ma_chapter139_2024_with_page_chrome.html").read_bytes()
    metadata = {
        **MA_METADATA,
        "url": "https://malegislature.gov/Laws/SessionLaws/Acts/2024/Chapter139",
        "media_type": "text/html",
    }

    text = CONVERSION_FUNCTIONS["ma"]["text/html"](data, metadata)

    assert "AN ACT TO PROVIDE FOR THE FUTURE INFORMATION TECHNOLOGY NEEDS" in text
    assert "Approved, July 29, 2024." in text
    # The preceding toolbar (Print/Prev/Next) and the following sidebar are both real
    # "col-xs-12"-classed siblings on the live page -- must not be swept in.
    assert "Print Page" not in text
    assert "Prev" not in text
    assert "Go Directly to a Session Law" not in text
    assert "This sidebar text must never appear in extracted bill text." not in text
