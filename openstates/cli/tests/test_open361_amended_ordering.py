"""OPEN-361: Utah's stamped "Amended <M/D/YYYY H:MM:N>" versions are diffed in the order of the
date and hour in the note, not of its last number.

Unit coverage for the ranking itself lives in openstates/utils/tests/test_version_ordering.py.
This is the integration half: does the real archive-time diff chain follow that order, however
the versions happen to come back from the database?
"""

import pytest

from openstates.data.models import BillVersionDocument

from openstates.cli.tests.test_open224_procedural_documents import _archive
from openstates.cli.tests.test_text_extract import _make_bill

# A real Utah bill's three versions: by their file timestamps the order is 1/27, 2/9, 3/3, but
# their last numbers (108, 713, 587) used to rank them 1/27, 3/3, 2/9.
JAN_NOTE = "Amended 1/27/2026 11:01:108"
FEB_NOTE = "Amended 2/9/2026 11:02:713"
MAR_NOTE = "Amended 3/3/2026 19:03:587"
JAN_URL = "https://x.test/jan.pdf"
FEB_URL = "https://x.test/feb.pdf"
MAR_URL = "https://x.test/mar.pdf"
JAN_TEXT = "Section 1. The fee is ten dollars.\n"
FEB_TEXT = "Section 1. The fee is twenty dollars.\n"
MAR_TEXT = "Section 1. The fee is thirty dollars.\n"


@pytest.mark.django_db
def test_amended_versions_are_diffed_in_timestamp_order_whatever_the_database_returns():
    bill = _make_bill(
        jid="ocd-jurisdiction/country:us/state:ut/government", jurisdiction_name="Utah"
    )
    bill.classification = ["bill"]
    bill.save()
    # Created out of order on purpose: the archiver must not depend on row order.
    for note, url in ((MAR_NOTE, MAR_URL), (JAN_NOTE, JAN_URL), (FEB_NOTE, FEB_URL)):
        bill.versions.create(note=note, date="").links.create(
            url=url, media_type="application/pdf"
        )

    _archive(bill, {JAN_URL: JAN_TEXT, FEB_URL: FEB_TEXT, MAR_URL: MAR_TEXT})
    docs = {d.version_note: d for d in BillVersionDocument.objects.filter(bill=bill)}

    # The lineage's first document has no diff.
    assert docs[JAN_NOTE].diff_from_previous_version is None
    feb_diff = docs[FEB_NOTE].diff_from_previous_version
    assert "-Section 1. The fee is ten dollars." in feb_diff
    assert "+Section 1. The fee is twenty dollars." in feb_diff
    mar_diff = docs[MAR_NOTE].diff_from_previous_version
    assert "-Section 1. The fee is twenty dollars." in mar_diff
    assert "+Section 1. The fee is thirty dollars." in mar_diff
