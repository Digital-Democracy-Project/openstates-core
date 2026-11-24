"""OPEN-849: Nevada's "Reprint <N>" is an amended text with a stage, and its "Amendment <N>" papers are not versions.

The notes and links are real (Mac replica, 36th (2025) Special Session, 2026-11-24): NELIS prints a bill as introduced,
then each adopted amendment ("Amendment 5", file A_AB4_5.pdf) produces a reprint ("Reprint 1", AB4_R1.pdf), the next
amendment is to that reprint (A_AB4_R1_15.pdf) and produces "Reprint 2", and so on to "As Enrolled". The texts in the
recompute test are short stand-ins: which document a comparison is taken against does not depend on the text.
"""

import types

import pytest

from openstates.cli.text_extract import recomputed_diffs_for_documents
from openstates.utils.version_ordering import (
    STAGE_AMENDMENT,
    STAGE_FINAL_PASSAGE,
    STAGE_INTRODUCED,
    STAGE_UNKNOWN,
    is_bill_version,
    is_procedural_document,
    note_stage,
    version_sort_key,
)

NV_SITE = "https://www.leg.state.nv.us/Session/36th2025Special/Bills"

# AB 4, every stored version, in NELIS's order
AB4 = [
    ("As Introduced", f"{NV_SITE}/AB/AB4.pdf"),
    ("Amendment 5", f"{NV_SITE}/Amendments/A_AB4_5.pdf"),
    ("Reprint 1", f"{NV_SITE}/AB/AB4_R1.pdf"),
    ("Amendment 13", f"{NV_SITE}/Amendments/A_AB4_13.pdf"),
    ("Amendment 15", f"{NV_SITE}/Amendments/A_AB4_R1_15.pdf"),
    ("Reprint 2", f"{NV_SITE}/AB/AB4_R2.pdf"),
    ("Amendment 31", f"{NV_SITE}/Amendments/A_AB4_R2_31.pdf"),
    ("Reprint 3", f"{NV_SITE}/AB/AB4_R3.pdf"),
    ("Amendment 34", f"{NV_SITE}/Amendments/A_AB4_R3_34.pdf"),
    ("Reprint 4", f"{NV_SITE}/AB/AB4_R4.pdf"),
    ("As Enrolled", f"{NV_SITE}/AB/AB4_EN.pdf"),
]
AB4_VERSIONS = ["As Introduced", "Reprint 1", "Reprint 2", "Reprint 3", "Reprint 4", "As Enrolled"]


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("Reprint 1", (STAGE_AMENDMENT, 1.0)),
        ("Reprint 2", (STAGE_AMENDMENT, 2.0)),
        ("Reprint 4", (STAGE_AMENDMENT, 4.0)),
        # unchanged: already had a stage
        ("As Introduced", (STAGE_INTRODUCED, 0.0)),
        ("As Enrolled", (STAGE_FINAL_PASSAGE, 0.0)),
        ("Amendment 5", (STAGE_AMENDMENT, 5.0)),
    ],
)
def test_note_stage_nevada(note, expected):
    assert note_stage(note) == expected


@pytest.mark.parametrize("note", ["Reprint", "Reprint 0", "Reprint 1A", "1st Reprint", "Reprinted 1", "Reprint 01"])
def test_note_stage_nevada_reprint_rule_does_not_widen(note):
    assert note_stage(note)[0] == STAGE_UNKNOWN


@pytest.mark.parametrize("note", ["Amendment 5", "Amendment 34", "amendment 1"])
def test_nevada_amendment_papers_are_not_versions(note):
    assert is_procedural_document("Nevada", note) is True
    assert is_bill_version("Nevada", note) is False


@pytest.mark.parametrize("note", ["As Introduced", "Reprint 1", "Reprint 4", "As Enrolled"])
def test_nevada_real_versions_stay_versions(note):
    assert is_procedural_document("Nevada", note) is False
    assert is_bill_version("Nevada", note) is True


@pytest.mark.parametrize("note", ["Amendment", "Amendment 0", "Amendment 5 to Reprint 1", "Proposed Amendment 5"])
def test_nevada_amendment_rule_does_not_widen(note):
    assert is_procedural_document("Nevada", note) is False


@pytest.mark.parametrize("jurisdiction", ["Hawaii", "Utah", "Florida", "Washington"])
def test_the_amendment_rule_is_nevadas_only(jurisdiction):
    assert is_procedural_document(jurisdiction, "Amendment 5") is False


def test_nevada_versions_rank_as_nelis_lists_them():
    for stored in (AB4_VERSIONS, AB4_VERSIONS[::-1]):
        ranked = sorted(stored, key=lambda n: version_sort_key(n, "", [], "house", "Nevada"))
        assert ranked == AB4_VERSIONS


def _doc(doc_id, note, url, stored_diff=None):
    return types.SimpleNamespace(
        id=doc_id,
        version_note=note,
        version_date="",
        source_url=url,
        media_type="application/pdf",
        raw_text=f"AB 4\nSECTION 1. text of {note}.\n",
        is_error=False,
        diff_from_previous_version=stored_diff,
    )


def test_recompute_takes_the_amendment_papers_out_and_enrolled_compares_with_the_last_reprint():
    # reversed ids, so the database's row order cannot be what puts them right
    docs = [_doc(100 - i, note, url, stored_diff="stored") for i, (note, url) in enumerate(AB4)]
    by_note = {d.version_note: d for d in docs}

    result = recomputed_diffs_for_documents(docs, jurisdiction_name="Nevada", is_bill=True, bill_identifier="AB 4")
    baseline = {doc.version_note: (base.version_note if base else None) for doc, base in result["baselines"]}
    changed = {doc.version_note: diff for doc, diff in result["changed"]}

    assert baseline == {
        "As Introduced": None,
        "Amendment 5": None,
        "Reprint 1": "As Introduced",
        "Amendment 13": None,
        "Amendment 15": None,
        "Reprint 2": "Reprint 1",
        "Amendment 31": None,
        "Reprint 3": "Reprint 2",
        "Amendment 34": None,
        "Reprint 4": "Reprint 3",
        "As Enrolled": "Reprint 4",
    }
    for note in ("Amendment 5", "Amendment 13", "Amendment 15", "Amendment 31", "Amendment 34"):
        assert changed[note] is None  # their stored comparison is removed
    assert "Reprint 4" in changed["As Enrolled"] and "Amendment" not in changed["As Enrolled"]
    assert by_note["As Enrolled"] in [d for d, _ in result["changed"]]
