from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS, DoNotDownload

TEST_DATA_PATH = Path(__file__).parent / "testdata"

# Real 57th Legislature, 2nd Regular Session (2026) introduced-bill PDF, fetched from
# azleg.gov during the 2026-08-10 diagnosis of why AZ's archiver was stuck ~40% archived.
AZ_METADATA = {
    "url": "https://www.azleg.gov/legtext/57leg/2r/bills/sb1717p.pdf",
    "title": "x",
    "jurisdiction_id": "ocd-jurisdiction/country:us/state:az/government",
}


def test_az_pdf_is_no_longer_donotdownload():
    """Regression pin for the 2026-08-10 fix: `application/pdf` used to map to
    DoNotDownload unconditionally, which silently zero-archived the ~1,160 AZ
    versions (mostly committee-amendment stages) that exist only as PDF, with
    no HTML counterpart to fall back on."""
    assert CONVERSION_FUNCTIONS["az"]["application/pdf"] is not DoNotDownload


def test_az_pdf_extraction_strips_line_numbers():
    data = (TEST_DATA_PATH / "az_sb1717_2026.pdf").read_bytes()
    metadata = {**AZ_METADATA, "media_type": "application/pdf"}

    text = CONVERSION_FUNCTIONS["az"]["application/pdf"](data, metadata)

    assert text
    # Real AZ PDFs prefix every body line with a running line number (pdftotext -layout
    # renders it as e.g. " 1   Be it enacted by the Legislature of the State of Arizona:");
    # confirm the numbered-line stripping left the bare content as its own line rather than
    # as a substring of a still-numbered line.
    assert "Be it enacted by the Legislature of the State of Arizona:" in text.splitlines()
    assert "ARTICLE 27. CONSUMER BIOMETRIC DATA" in text
