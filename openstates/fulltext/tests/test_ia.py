"""OPEN-913: the governor's signed copy of an enacted Iowa bill ("Signed Enrolled Bill (PDF)") is a transmittal letter and
an unnumbered scan, so the numbered-lines reader kept almost nothing of it (364 of 370 under 300 characters in the active
sessions, 259 empty); `ddp_states/ia.py` reads such a paper as plain text and every other Iowa PDF as before.

The fixtures are real published PDFs:

* `ia_hf2329_signed_enrolled.pdf`: HF 2329's signed enrolled bill (2026), letter plus a two-page scan. Stored text: empty.
* `ia_sjr9_signed_enrolled.pdf`: SJR 9's signed copy (2025), the Secretary of the Senate's letter (OCR: "Secretary ofthe Senate").
* `ia_hf2329_introduced.pdf`: HF 2329's introduced bill, with margin line numbers (read as before).
* `ia_ssb3170_prefiled_p1.pdf`: page 1 of a study bill "BY (PROPOSED GOVERNOR BILL)": "GOVERNOR" in its opening, numbered.
* `ia_sf162_enrolled_with_governors_action.pdf`: a one-page enrolled bill with 2 numbered lines of 30 (6.7%, under 10%).

OPEN-930: the "Enrolled" papers of SJR 9 and SJR 11 are text with one numbered line each and no letter (60 characters read);
`is_unnumbered_enrolled_paper` reads a paper titled "- Enrolled" with at most 5% numbered lines in full:

* `ia_sjr9_enrolled.pdf`, `ia_sjr11_enrolled.pdf`: the real 2025 papers (1 numbered line of 44 and of 62 lines).
* `ia_sf615_amendment_s3054.pdf`: a real amendment, 2 numbered lines of 73 (2.7%) and no "Enrolled" title: read as before.
"""

from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS, DoNotDownload
from openstates.fulltext.common import extract_line_numbered_pdf
from openstates.fulltext.utils import pdfdata_to_text
from openstates.ddp_states.ia import is_signed_paper, is_unnumbered_enrolled_paper

TESTDATA = Path(__file__).parent / "testdata"
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:ia/government"}
READ = CONVERSION_FUNCTIONS["ia"]["application/pdf"]


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def test_a_signed_enrolled_bill_is_read_in_full():
    data = _read("ia_hf2329_signed_enrolled.pdf")
    assert (
        len(extract_line_numbered_pdf(data, META).strip()) < 300
    )  # what was stored before
    text = READ(data, META)
    assert text == pdfdata_to_text(data)
    assert "The above House File is hereby approved on this date." in text  # the letter
    assert "BE IT ENACTED BY THE GENERAL ASSEMBLY OF THE STATE OF IOWA" in text
    assert "Sec. 2. Section 536.13, subsections 4 and 5, Code" in text  # the scan, page 1
    assert "537.1301 in amount and may charge, contract for, and receive" in text  # page 2


def test_a_signed_joint_resolution_letter_with_ocr_noise_is_read():
    data = _read("ia_sjr9_signed_enrolled.pdf")
    assert extract_line_numbered_pdf(data, META).strip() == ""
    text = READ(data, META)
    assert "Dear Secretary Pate:" in text
    assert "PROPOSING AN AMENDMENT TO THE CONSTITUTION OF THE STATE OF IOWA" in text


def test_a_numbered_bill_is_read_as_before():
    data = _read("ia_hf2329_introduced.pdf")
    assert not is_signed_paper(pdfdata_to_text(data))
    assert READ(data, META) == extract_line_numbered_pdf(data, META)


def test_a_numbered_bill_that_mentions_the_governor_is_read_as_before():
    # "BY (PROPOSED GOVERNOR BILL)" is in the first 400 characters of 67 numbered papers: the letter test alone is wrong
    data = _read("ia_ssb3170_prefiled_p1.pdf")
    assert "GOVERNOR" in pdfdata_to_text(data)[:400]
    assert READ(data, META) == extract_line_numbered_pdf(data, META)


def test_a_sparsely_numbered_enrolled_bill_is_read_as_before():
    # 2 numbered lines of 30 (6.7%): under 10% by itself, but no governor's letter, so the numbered reader still reads it
    data = _read("ia_sf162_enrolled_with_governors_action.pdf")
    assert READ(data, META) == extract_line_numbered_pdf(data, META)


def test_the_html_entry_is_unchanged():
    assert CONVERSION_FUNCTIONS["ia"]["text/html"] is DoNotDownload


def test_an_unnumbered_enrolled_joint_resolution_is_read_in_full():
    for name, ending in [
        ("ia_sjr9_enrolled.pdf", "published as provided by law for three months previous to the"),
        ("ia_sjr11_enrolled.pdf", "section.\n   Each bill to which this section applies must include a"),
    ]:
        data = _read(name)
        assert len(extract_line_numbered_pdf(data, META).strip()) == 60  # what was stored before
        text = READ(data, META)
        assert text == pdfdata_to_text(data)
        assert "Joint Resolution" in text and "Secretary of the Senate" in text  # title and certificate
        assert "PROPOSING AN AMENDMENT TO THE CONSTITUTION OF THE STATE OF IOWA" in text
        assert ending.split("\n")[0] in text  # text from page 2
    assert "Sec. 10A. Protection of children and other witnesses." in READ(_read("ia_sjr9_enrolled.pdf"), META)
    assert "Two-thirds majority vote for state tax law" in READ(_read("ia_sjr11_enrolled.pdf"), META)


def test_the_enrolled_rule_does_not_catch_the_other_low_numbered_papers():
    # SF 162's enrolled text is 6.7% numbered (over 5%): same title, read as before; the amendment has no "Enrolled" title
    sf162 = pdfdata_to_text(_read("ia_sf162_enrolled_with_governors_action.pdf"))
    assert sf162.strip().endswith("Governor") and not is_unnumbered_enrolled_paper(sf162)
    amendment = pdfdata_to_text(_read("ia_sf615_amendment_s3054.pdf"))
    assert not is_unnumbered_enrolled_paper(amendment) and not is_signed_paper(amendment)
    data = _read("ia_sf615_amendment_s3054.pdf")
    assert READ(data, META) == extract_line_numbered_pdf(data, META)
    assert not is_unnumbered_enrolled_paper(pdfdata_to_text(_read("ia_hf2329_introduced.pdf")))
    assert not is_unnumbered_enrolled_paper("")
