import pytest  # type: ignore

from openstates.fulltext import CONVERSION_FUNCTIONS, DoNotDownload
from openstates.fulltext.us import handle_us_bill_xml

US_METADATA = {
    "url": "https://www.govinfo.gov/content/pkg/BILLS-118s3941is/xml/BILLS-118s3941is.xml",
    "title": "x",
    "jurisdiction_id": "ocd-jurisdiction/country:us/government",
}

# A minimal but structurally real fixture matching govinfo's legacy "bill.dtd" format (used
# for most stages -- Introduced, Engrossed, Reported, etc.), based on a real bill (118 S3941)
# fetched 2026-08-12.
LEGACY_DTD_XML = b"""<?xml version="1.0"?>
<!DOCTYPE bill PUBLIC "-//US Congress//DTDs/bill.dtd//EN" "bill.dtd">
<bill bill-stage="Introduced-in-Senate">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dublinCore>
<dc:title>118 S3941 IS: Test Act</dc:title>
<dc:date>2024-03-14</dc:date>
</dublinCore>
</metadata>
<form>
<congress>118th CONGRESS</congress><legis-num>S. 3941</legis-num>
<official-title>To do a test thing.</official-title>
</form>
<legis-body>
<section><enum>1.</enum><header>Short title</header>
<text>This Act may be cited as the Test Act.</text></section>
</legis-body>
</bill>
"""

# A minimal but structurally real fixture matching the newer USLM schema (used for Enrolled
# Bills), based on a real bill (119 HR7194 ENR) fetched 2026-08-12.
USLM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<bill xmlns="http://schemas.gpo.gov/xml/uslm">
<meta><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">119 HR7194 ENR</dc:title>
<processedDate>2026-07-11</processedDate></meta>
<main>
<longTitle><docTitle>An Act</docTitle>
<officialTitle>To do a test thing.</officialTitle></longTitle>
<section><num>1.</num><heading>Short title</heading>
<text>This Act may be cited as the Test Act.</text></section>
</main>
</bill>
"""


def test_us_xml_is_no_longer_donotdownload():
    """Regression pin for the 2026-08-12 fix: text/xml used to map to DoNotDownload
    unconditionally (inherited unchanged from public upstream, predates this fork)."""
    assert CONVERSION_FUNCTIONS["us"]["text/xml"] is not DoNotDownload
    assert CONVERSION_FUNCTIONS["us"]["text/xml"] is handle_us_bill_xml


def test_extracts_real_text_from_legacy_dtd_format():
    metadata = {**US_METADATA, "media_type": "text/xml"}
    text = handle_us_bill_xml(LEGACY_DTD_XML, metadata)

    assert "This Act may be cited as the Test Act." in text
    assert "S. 3941" in text
    assert "To do a test thing." in text
    # The <metadata>/<dublinCore> sidecar must be stripped -- its dc:date field changes on
    # every re-export even when the bill text hasn't, which would inject diff noise.
    assert "2024-03-14" not in text
    assert "118 S3941 IS: Test Act" not in text


def test_extracts_real_text_from_uslm_format():
    metadata = {**US_METADATA, "media_type": "text/xml"}
    text = handle_us_bill_xml(USLM_XML, metadata)

    assert "This Act may be cited as the Test Act." in text
    assert "An Act" in text
    assert "To do a test thing." in text
    # The USLM <meta> block must be stripped the same way -- processedDate changes on every
    # re-conversion regardless of content.
    assert "2026-07-11" not in text
    assert "119 HR7194 ENR" not in text


def test_tag_names_and_attributes_do_not_leak_into_extracted_text():
    metadata = {**US_METADATA, "media_type": "text/xml"}
    text = handle_us_bill_xml(LEGACY_DTD_XML, metadata)

    assert "bill-stage" not in text
    assert "<section>" not in text


def test_raises_on_genuinely_unparseable_data():
    metadata = {**US_METADATA, "media_type": "text/xml"}
    with pytest.raises(Exception):
        handle_us_bill_xml(b"this is not xml at all, not even close", metadata)


def test_output_has_real_line_structure():
    """OPEN-210: both schemas used to collapse onto a single line, which made 6,683 of the
    6,692 archived US XML diffs degenerate whole-document hunks."""
    metadata = {**US_METADATA, "media_type": "text/xml"}

    for fixture in (LEGACY_DTD_XML, USLM_XML):
        lines = handle_us_bill_xml(fixture, metadata).split("\n")
        assert len(lines) > 3
        assert "This Act may be cited as the Test Act." in lines
        assert "To do a test thing." in lines
        assert all(line == line.strip() and line for line in lines)


def test_a_version_transition_produces_a_targeted_diff():
    """The condition OPEN-209 measures: a real edit must diff to a specific hunk rather than
    replacing the whole document."""
    import difflib
    import re

    metadata = {**US_METADATA, "media_type": "text/xml"}
    amended = LEGACY_DTD_XML.replace(
        b"This Act may be cited as the Test Act.",
        b"This Act may be cited as the Revised Test Act.",
    )

    diff = "\n".join(
        difflib.unified_diff(
            handle_us_bill_xml(LEGACY_DTD_XML, metadata).splitlines(),
            handle_us_bill_xml(amended, metadata).splitlines(),
            lineterm="",
        )
    )
    hunks = re.findall(r"@@[^@]*@@", diff)

    assert len(hunks) == 1
    assert not re.fullmatch(r"@@ -1(,\d+)? \+1(,\d+)? @@", hunks[0]), hunks[0]
    assert "+This Act may be cited as the Revised Test Act." in diff
