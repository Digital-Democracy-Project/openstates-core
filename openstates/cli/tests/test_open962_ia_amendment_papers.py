"""OPEN-962: Iowa's amendment papers ("Amendment H-8118", "Amendment S-3004") that only edit the bill are not versions;
the amendments that replace the whole bill ("By striking everything after the enacting clause and inserting") stay.
The note is the same for every shape, so the text decides (`ddp_states/ia.py AMENDMENT_TEXT_OPENING`, with core's
`is_amendment_text`, OPEN-564), as for Georgia's SFA papers (OPEN-959).

The papers are real documents of the Mac replica (2026-11-28), opening 1,500 characters of each (the rule reads only the
opening and "be it enacted", which none of the 1,274 Iowa amendment papers holds); the bills are real, whole, with the
comparisons the replica stored before this rule (the Enrolled paper compared with the amendment, 339839).
"""

import json
import pathlib
import types

import pytest

from openstates.cli.text_extract import recomputed_diffs_for_documents
from openstates.utils.version_ordering import (
    is_amendment_text,
    is_bill_version,
    origin_chamber,
    version_sort_key,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PAPERS = json.loads((FIXTURES / "ia_amendment_papers_open962.json").read_text())
BILLS = json.loads((FIXTURES / "ia_bills_open962.json").read_text())
AMENDMENTS = PAPERS["amendment_papers"]
FRAGMENTS = [p for p in AMENDMENTS if p["shape"] != "striker"]
STRIKERS = [p for p in AMENDMENTS if p["shape"] == "striker"]


def _label(p):
    return f"{p['identifier']} {p['note']}"


def test_the_fixture_has_every_shape_in_both_chambers():
    assert len(AMENDMENTS) == 22 and len(STRIKERS) == 7 and len(FRAGMENTS) == 15
    for shape in ("line_edit", "striker", "to_amendment"):
        notes = {p["note"].split()[1][0] for p in AMENDMENTS if p["shape"] == shape}
        assert notes == {"H", "S"}, shape
    assert all(p["note"].startswith("Amendment ") and p["text"].lstrip().startswith("Amend ") for p in AMENDMENTS)


@pytest.mark.parametrize("paper", FRAGMENTS, ids=_label)
def test_papers_that_edit_lines_or_amend_an_amendment_are_not_versions(paper):
    assert is_amendment_text("Iowa", paper["text"]) is True
    assert is_bill_version("Iowa", paper["note"], raw_text=paper["text"]) is False


@pytest.mark.parametrize("paper", STRIKERS, ids=_label)
def test_papers_that_replace_the_whole_bill_stay_versions(paper):
    assert "1. By striking everything after the" in " ".join(paper["text"].split())
    assert is_amendment_text("Iowa", paper["text"]) is False
    assert is_bill_version("Iowa", paper["note"], raw_text=paper["text"]) is True


def test_a_joint_resolution_striker_names_the_resolving_clause():
    (paper,) = [p for p in STRIKERS if "resolving clause" in " ".join(p["text"].split())]
    assert paper["identifier"].startswith("SJR")
    assert is_bill_version("Iowa", paper["note"], raw_text=paper["text"]) is True


def test_an_amendment_to_an_amendment_that_carries_a_whole_striker_is_still_a_fragment():
    # S-3067 to SF 167: "Amend the House amendment, S-3004, ... 1. By striking page 1, line 1, through page 8, line 34,
    # and inserting: <Amend Senate File 167 ... 1. By striking everything after the enacting clause and inserting: ...>"
    (paper,) = [p for p in AMENDMENTS if p["note"] == "Amendment S-3067"]
    assert "By striking everything after the enacting clause" in " ".join(paper["text"].split())
    assert is_bill_version("Iowa", paper["note"], raw_text=paper["text"]) is False


@pytest.mark.parametrize("paper", PAPERS["other_versions"], ids=lambda p: f"{p['identifier']} {p['note']}")
def test_every_other_iowa_version_stays_a_version(paper):
    assert is_bill_version("Iowa", paper["note"], raw_text=paper["text"]) is True


def test_a_note_alone_never_marks_an_amendment_paper_and_other_states_are_unchanged():
    assert is_bill_version("Iowa", "Amendment H-8031") is True  # no text: the papers are known by their text
    assert is_bill_version("Iowa", "Amendment H-8031", raw_text="") is True
    for state in ("Virginia", "Florida", "Texas", "Georgia", "Colorado"):
        for paper in FRAGMENTS:
            assert is_bill_version(state, paper["note"], raw_text=paper["text"]) is True


def test_other_texts_that_open_amend_in_other_words_are_judged_by_the_same_opening():
    assert is_amendment_text("Iowa", "Amend House File 583 as follows:\n1. Page 4, by striking lines 23 and 24.") is True
    assert is_amendment_text("Iowa", "Amend the amendment, H-1153, to House File 872, as follows:\n1. Page 1") is True
    assert is_amendment_text("Iowa", "An Act relating to amend House File 583.") is False
    # (written for this test, none of the 1,274 does this) an amendment to an amendment that strikes everything is still not the bill
    striking = "Amend the amendment, H-1153, to House File 872, as follows:\n1. By striking everything after the enacting clause and inserting:\n<Section 1."
    assert is_amendment_text("Iowa", striking) is True
    # a striker is the same for every kind of measure and for the bill as passed by a chamber
    for target in ("House File 5", "Senate File 5, as passed by the Senate,", "Senate Study Bill 9", "House Joint Resolution 3"):
        text = f"Amend {target} as follows:\n1. By striking everything after the enacting clause and inserting:\n<Section 1. A."
        assert is_amendment_text("Iowa", text) is False


# ---- the comparisons and the latest version of real bills ----------------------------------------------------------


def _docs(identifier):
    return [types.SimpleNamespace(**row) for row in BILLS[identifier]]


def _recompute(identifier):
    return recomputed_diffs_for_documents(
        _docs(identifier), jurisdiction_name="Iowa", is_bill=True, bill_identifier=identifier
    )


def test_hf_2697_the_amendment_takes_no_comparison_and_is_not_a_baseline():
    result = _recompute("HF 2697")
    baselines = {d.version_note: (b.version_note if b else None) for d, b in result["baselines"]}
    new_diff = {d.version_note: diff for d, diff in result["changed"]}
    assert baselines["Amendment H-8118"] is None
    assert new_diff["Amendment H-8118"] is None  # stored: 1,415 characters against the Introduced text
    # the paper that was compared with the amendment is compared with the real version before it, on the site's order
    assert baselines["Reprinted"] == "Introduced"
    assert baselines["Enrolled with Governor's Action"] == "Reprinted"


def test_hf_2697_the_reprint_comparison_is_the_bills_own_edit_not_the_amendment_instructions():
    result = _recompute("HF 2697")
    diff = {d.version_note: x for d, x in result["changed"]}["Reprinted"]
    assert "or violation of an ordinance" in diff  # the amendment's inserted words, as the reprint prints them
    assert "Amend House File" not in diff and "Page 1, line 6" not in diff
    enrolled = {d.version_note: x for d, x in result["changed"]}["Enrolled with Governor's Action"]
    assert "Amend House File" not in enrolled  # stored: compared with the amendment's instruction text


def test_hf_872_two_amendment_papers_are_both_out_of_the_chain():
    result = _recompute("HF 872")
    baselines = {d.version_note: (b.version_note if b else None) for d, b in result["baselines"]}
    assert baselines["Amendment H-1153"] is None and baselines["Amendment H-1173"] is None
    assert baselines["Reprinted"] == "Introduced"  # the stored comparison chained Introduced, H-1153, H-1173


def test_a_striker_stays_in_the_chain():
    docs = _docs("HF 872")
    striker = STRIKERS[0]  # a real striker's text, filed as one more paper of this bill
    doc = types.SimpleNamespace(**{**vars(docs[0])})
    doc.id, doc.version_note = 999999, "Amendment H-9999"
    doc.raw_text = striker["text"]
    doc.diff_from_previous_version, doc.diff_baseline_id = None, None
    doc.source_url = "https://www.legis.iowa.gov/docs/publications/AMDI/91/H9999.pdf"
    result = recomputed_diffs_for_documents(
        docs + [doc], jurisdiction_name="Iowa", is_bill=True, bill_identifier="HF 872"
    )
    baselines = {d.version_note: (b.version_note if b else None) for d, b in result["baselines"]}
    assert baselines["Amendment H-9999"] == "Introduced"  # takes a comparison, like any bill text
    assert baselines["Amendment H-1153"] is None


def _latest(identifier, *, read_text=True):
    docs = BILLS[identifier]
    versions = [
        d
        for d in docs
        if is_bill_version("Iowa", d["version_note"], raw_text=(d["raw_text"] or None) if read_text else None)
    ]
    versions.sort(
        key=lambda d: version_sort_key(
            d["version_note"], d["version_date"], [d["source_url"]], origin_chamber(identifier), "Iowa"
        )
    )
    return versions[-1]["version_note"]


# HF 642, HCR 105 and SF 2342 ended on a line-edit amendment paper (121 Iowa bills did); each now ends on its Introduced text.
@pytest.mark.parametrize("identifier", ["HF 642", "HCR 105", "SF 2342"])
def test_a_bill_whose_latest_paper_was_an_amendment_ends_on_its_introduced_text(identifier):
    assert _latest(identifier, read_text=False).startswith("Amendment")  # what the note alone said before the rule
    assert _latest(identifier) == "Introduced"
