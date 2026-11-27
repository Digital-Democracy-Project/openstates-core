"""OPEN-907: Maine's amendment papers are not versions of the bill (openstates/ddp_states/me.py).

The notes are stored Maine version notes from the Mac replica (132nd Legislature, 5,131 documents, 2026-11-26): the 1,792
"Amendment ..." papers all match the rule, the 3,339 others (Introduced, Chapter, ACTPS Chapter) none does. The bills are
LD 1009, LD 1003 and LD 2233 as stored.
"""
import pytest  # type: ignore
from openstates.utils.version_ordering import (
    STAGE_UNKNOWN,
    is_bill_version,
    is_procedural_document,
    note_stage,
    version_sort_key,
)

ME_VERSION_NOTES = [
    "Introduced (LD 1009, HP 656)",
    "Introduced (LD 635, HP 403)",
    "Chapter 25",
    "Chapter 738",
    "ACTPS  Chapter 12",
]

# Every amendment kind in the stored notes: committee, floor, and floor-to-committee, with the longest letter runs.
ME_AMENDMENT_NOTES = [
    "Amendment C-A (H-1031)",
    "Amendment C-B (S-411)",
    "Amendment C-D (H-77)",
    "Amendment H-A (H-208)",
    "Amendment S-A (S-31)",
    "Amendment H-A to C-A (H-1048)",
    "Amendment S-A to C-A (S-659)",
    "Amendment S-F to C-A (S-902)",
    "Amendment H-U to C-A (H-1180)",
    "Amendment H-B to C-B (H-1170)",
    "Amendment S-A to C-B (S-640)",
]

# Stored texts' openings: an instruction sheet, never the bill (LD 1009's H-1048, LD 635's H-167).
ME_AMENDMENT_TEXTS = [
    'HOUSE AMENDMENT " " to COMMITTEE AMENDMENT "A" to H.P. 656, L.D. 1009 Amend the amendment by striking out all of section 4',
    "COMMITTEE AMENDMENT \" \" to H.P. 403, L.D. 635 Amend the resolve by incorporating the attached fiscal note.",
]


@pytest.mark.parametrize("note", ME_VERSION_NOTES)
def test_maine_texts_stay_versions_with_a_stage(note):
    assert is_bill_version("Maine", note) is True
    assert is_procedural_document("Maine", note) is False
    assert note_stage(note)[0] != STAGE_UNKNOWN


@pytest.mark.parametrize("note", ME_AMENDMENT_NOTES)
@pytest.mark.parametrize("raw_text", [None] + ME_AMENDMENT_TEXTS)
def test_maine_amendment_papers_are_not_versions(note, raw_text):
    assert is_bill_version("Maine", note, raw_text=raw_text) is False
    assert is_procedural_document("Maine", note) is True


@pytest.mark.parametrize(
    "note",
    [
        # Close shapes are not Maine's papers: the rule is its own name, nothing wider.
        "Amendments",
        "Amendment",
        "Amendment C-A",
        "Amendment C-A (H-1031) revised",
        "Introduced (LD 1009, HP 656) Amendment",
        "Chapter 25 Amendment C-A (H-1)",
    ],
)
def test_maine_paper_rule_does_not_widen(note):
    assert is_bill_version("Maine", note) is True


@pytest.mark.parametrize("note", ME_AMENDMENT_NOTES)
def test_maine_rule_does_not_reach_other_states(note):
    for state in ("Utah", "Idaho", "Nevada", "Massachusetts", "Montana", "Virginia", "Alabama"):
        assert is_bill_version(state, note) is True


def test_maine_real_versions_keep_their_order():
    # LD 2233 as stored: Introduced, "S-A to C-A (S-659)", "C-A (H-1025)", Chapter 738. The two amendment papers are no
    # longer versions, so the bill's versions are its introduced text then its chapter, whatever order they are stored in.
    stored = [
        "Introduced (LD 2233, HP 1509)",
        "Amendment S-A to C-A (S-659)",
        "Amendment C-A (H-1025)",
        "Chapter 738",
    ]
    for rows in (stored, list(reversed(stored))):
        versions = [n for n in rows if is_bill_version("Maine", n)]
        ranked = sorted(versions, key=lambda n: version_sort_key(n, "", [], None, "Maine"))
        assert ranked == ["Introduced (LD 2233, HP 1509)", "Chapter 738"]


def test_chapter_compares_with_the_introduced_bill_not_with_an_amendment_paper():
    # LD 1003 as stored (Introduced, C-A, S-A to C-A, Chapter 756): the chapter's comparison is taken against the
    # introduced text; the amendment papers take none and are not baselines.
    from types import SimpleNamespace

    from openstates.cli.text_extract import recomputed_diffs_for_documents

    notes = [
        "Introduced (LD 1003, HP 650)",
        "Amendment C-A (H-362)",
        "Amendment S-A to C-A (S-705)",
        "Chapter 756",
    ]
    texts = ["Sec. 1. line a\nSec. 2. line b", "Amend the bill by striking out line a", "Amend the amendment", "Sec. 1. line a2\nSec. 2. line b"]
    docs = [
        SimpleNamespace(
            id=i,
            version_note=n,
            version_date="",
            source_url=f"http://x/{i}",
            media_type="application/pdf",
            raw_text=t,
            is_error=False,
            diff_from_previous_version=None,
        )
        for i, (n, t) in enumerate(zip(notes, texts), start=1)
    ]
    result = recomputed_diffs_for_documents(
        docs, jurisdiction_name="Maine", is_bill=True, bill_identifier="LD 1003"
    )
    baseline_of = {doc.version_note: base.id if base else None for doc, base in result["baselines"]}
    assert baseline_of == {notes[0]: None, notes[1]: None, notes[2]: None, notes[3]: 1}
