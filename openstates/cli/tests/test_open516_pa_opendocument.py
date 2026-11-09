"""OPEN-516: the archiver takes Pennsylvania's text from its OpenDocument ("DOC") printing.

Every Pennsylvania version has three links (PDF, HTM, DOC). The DOC is served as
application/msword but is an OpenDocument file, where struck-out words are marked. These tests run
`archive_bill_versions` on real printings (HB 2625 PN3586 then PN3751) with only the network fetch
and the S3 upload faked: the real extractor, the real magic-byte check and the real Pennsylvania
cleaner all run.
"""

from pathlib import Path
from unittest import mock

import pytest

from openstates.cli.text_extract import (
    _block_page_reason,
    _clean_pa_text,
    _sniffed_media_type,
    archive_bill_versions,
)
from openstates.cli.tests.test_text_extract import _make_bill
from openstates.data.models import BillVersionDocument
from openstates.fulltext.pa import ODT_MEDIA_TYPE

PA = "ocd-jurisdiction/country:us/state:pa/government"
TESTDATA = Path(__file__).parents[2] / "fulltext" / "tests" / "testdata"
PN3586 = (TESTDATA / "pa_hb2625_pn3586.odt").read_bytes()
PN3751 = (TESTDATA / "pa_hb2625_pn3751.odt").read_bytes()
BASE = "https://www.palegis.us/legislation/bills/text"


def _make_pa_bill():
    bill = _make_bill(jid=PA, jurisdiction_name="Pennsylvania")
    bill.classification = ["bill"]
    bill.save()
    for pn, date in (("PN3586", "2026-06-10"), ("PN3751", "2026-07-01")):
        version = bill.versions.create(note=f"Printer's No. {pn}", date=date)
        for kind, media_type in (
            ("PDF", "application/pdf"),
            ("HTM", "text/html"),
            ("DOC", "application/msword"),
        ):
            version.links.create(
                url=f"{BASE}/{kind}/2025/0/HB2625/{pn}", media_type=media_type
            )
    return bill


def _archive(bill, bytes_by_url, uploaded_to=None):
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return bytes_by_url[url]

    with mock.patch(
        "openstates.cli.text_extract._fetch_bytes", side_effect=fake_fetch
    ), mock.patch(
        "openstates.cli.text_extract._upload_and_verify", return_value=uploaded_to
    ), mock.patch(
        "os.makedirs"
    ), mock.patch(
        "builtins.open", mock.mock_open()
    ):
        counters = archive_bill_versions(bill)
    return counters, fetched


@pytest.mark.django_db
def test_pennsylvania_archives_the_opendocument_printing_under_its_real_type():
    bill = _make_pa_bill()
    counters, fetched = _archive(
        bill,
        {
            f"{BASE}/DOC/2025/0/HB2625/PN3586": PN3586,
            f"{BASE}/DOC/2025/0/HB2625/PN3751": PN3751,
        },
    )
    # Only the DOC links are fetched: the PDF and HTM are not downloaded at all.
    assert fetched == [
        f"{BASE}/DOC/2025/0/HB2625/PN3586",
        f"{BASE}/DOC/2025/0/HB2625/PN3751",
    ]
    assert (
        counters["archived"] == 2
        and counters["blocked"] == 0
        and counters["extract_errors"] == 0
    )

    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}
    assert {d.media_type for d in docs.values()} == {ODT_MEDIA_TYPE}
    first, second = docs["Printer's No. PN3586"], docs["Printer's No. PN3751"]
    assert not first.is_error and not second.is_error
    assert first.diff_from_previous_version is None
    assert "§ 9301. Definitions." in first.raw_text
    assert "9301" not in second.raw_text  # struck in PN3751

    diff = second.diff_from_previous_version.splitlines()
    removed = [
        line[1:] for line in diff if line.startswith("-") and not line.startswith("---")
    ]
    added = [
        line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")
    ]
    # The comparison tells the true story: the 9301 change is removed, the title loses
    # "for definitions and", and the (viii) paragraph is added.
    assert "§ 9301. definitions." in removed
    assert any(
        "further providing for definitions and for additional powers" in line
        for line in removed
    )
    assert any(
        line.startswith("(viii) for the purposes of this paragraph") for line in added
    )
    # The OPEN-495 cleaner still applies: lower-cased, and no printer's-number line in it.
    assert not any("printer's no" in line for line in removed + added)


@pytest.mark.django_db
def test_already_archived_pdf_documents_keep_their_own_lineage():
    """A PDF archived before the change is skipped as before and is not re-downloaded; the
    OpenDocument printing starts its own lineage beside it (one baseline per media type)."""
    bill = _make_pa_bill()
    BillVersionDocument.objects.create(
        bill=bill,
        version_note="Printer's No. PN3586",
        version_date="2026-06-10",
        source_url=f"{BASE}/PDF/2025/0/HB2625/PN3586",
        media_type="application/pdf",
        raw_text="SECTION 1. OLD PDF TEXT",
        is_error=False,
        archive_location="s3://ddp-bill-archive/x.pdf",
    )
    counters, fetched = _archive(
        bill,
        {
            f"{BASE}/DOC/2025/0/HB2625/PN3586": PN3586,
            f"{BASE}/DOC/2025/0/HB2625/PN3751": PN3751,
        },
    )
    assert counters["skipped"] == 1 and counters["archived"] == 2
    assert not any("/PDF/" in url for url in fetched)
    pdf = BillVersionDocument.objects.get(bill=bill, media_type="application/pdf")
    assert pdf.raw_text == "SECTION 1. OLD PDF TEXT"
    second = BillVersionDocument.objects.get(
        bill=bill, version_note="Printer's No. PN3751"
    )
    assert "old pdf text" not in second.diff_from_previous_version


@pytest.mark.django_db
def test_a_second_run_finds_the_stored_opendocument_rows_and_fetches_nothing():
    """/pm-review: the link is still labelled application/msword while the stored row says
    OpenDocument. The already-archived check matches on bill, version and URL, not media type,
    so the next nightly run skips both printings: no fetch, no second row, lineage unchanged."""
    bill = _make_pa_bill()
    doc_bytes = {
        f"{BASE}/DOC/2025/0/HB2625/PN3586": PN3586,
        f"{BASE}/DOC/2025/0/HB2625/PN3751": PN3751,
    }
    _archive(bill, doc_bytes, uploaded_to="s3://ddp-bill-archive/pa/x.odt")
    before = {
        d.source_url: (d.id, d.diff_from_previous_version)
        for d in BillVersionDocument.objects.filter(bill=bill)
    }

    counters, fetched = _archive(
        bill, doc_bytes, uploaded_to="s3://ddp-bill-archive/pa/x.odt"
    )

    assert fetched == []
    assert counters["skipped"] == 2 and counters["archived"] == 0
    after = BillVersionDocument.objects.filter(bill=bill)
    assert {d.media_type for d in after} == {ODT_MEDIA_TYPE}
    assert {d.source_url: (d.id, d.diff_from_previous_version) for d in after} == before


def test_the_msword_label_is_corrected_only_for_opendocument_bytes():
    assert _sniffed_media_type(PN3751, "application/msword") == ODT_MEDIA_TYPE
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64
    assert _sniffed_media_type(ole, "application/msword") == "application/msword"
    assert _sniffed_media_type(PN3751, "application/pdf") == "application/pdf"
    # Read by the label, Pennsylvania's file failed the magic-byte check as a block page.
    assert _block_page_reason(PN3751, "application/msword") is not None
    assert _block_page_reason(PN3751, ODT_MEDIA_TYPE) is None
    assert (
        _block_page_reason(b"<html>Validation request</html>", ODT_MEDIA_TYPE)
        is not None
    )


@pytest.mark.parametrize(
    "line",
    [
        "PRINTER'S NO. 3751",
        "PRIOR PRINTER'S NO. 3586",
        "PRIOR PRINTER'S NOS. 912, 934, 1427",
        "PRIOR PRINTER'S NOS. 63, 1279",
    ],
)
def test_printer_number_lines_are_dropped(line):
    assert _clean_pa_text(f"{line}\nSection 1. Text.") == "section 1. text."


def test_a_sentence_mentioning_a_printer_number_is_kept():
    text = "Section 2. As in Printer's No. 3586, the fee is $5."
    assert _clean_pa_text(text) == text.lower()
