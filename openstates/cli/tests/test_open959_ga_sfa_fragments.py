"""OPEN-959 (Ramon, 2026-11-28, option B): Georgia's adopted Senate floor amendment papers ("SFA to SB 170", 624
characters of replacement lines) are not versions, so the as-passed paper after one is compared with the last real
version, not with the fragment. Seven are line edits; SB 17's and SB 131's replace the whole bill, carry its enacting
clause and stay versions. The note cannot tell them apart ("SFA to SB 17" / "SFA to SB 170"), the text does
(`ddp_states/ga.py AMENDMENT_TEXT_OPENING`, with the enacting-clause exclusion of `is_amendment_text`, OPEN-564).

The documents are SB 170 of 2025_26 as stored on the Mac replica (2026-11-28), text unchanged; the stored
`diff_baseline_id` is what the replica held before this rule (the as-passed paper pointed at the fragment, 350816).
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

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ga_sb170_open959.json"

SFA, HSS, AP = 350816, 350813, 350817  # row ids on the replica


def _docs():
    return [
        types.SimpleNamespace(diff_from_previous_version="stored before the rule", **row)
        for row in json.loads(FIXTURE.read_text())
    ]


def _recompute():
    return recomputed_diffs_for_documents(
        _docs(), jurisdiction_name="Georgia", is_bill=True, bill_identifier="SB 170"
    )


def test_the_floor_amendment_fragment_takes_no_comparison():
    new_diff = dict((d.id, diff) for d, diff in _recompute()["changed"])
    assert new_diff[SFA] is None


def test_the_as_passed_paper_is_compared_with_the_house_substitute_not_the_fragment():
    baselines = {d.id: (b.id if b else None) for d, b in _recompute()["baselines"]}
    assert baselines[AP] == HSS  # the last real version before it on the legislature's list
    assert baselines[SFA] is None
    new_diff = dict((d.id, diff) for d, diff in _recompute()["changed"])
    # the old comparison was 3,565 characters against a 624-character baseline; against the 2,726-character
    # House substitute it is the as-passed text's real edits
    assert 0 < len(new_diff[AP]) < 3565


PAPERS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "ga_sfa_papers_open959.json").read_text())
FRAGMENTS = [p for p in PAPERS["sfa"] if p["identifier"] not in ("SB 17", "SB 131")]
WHOLE_BILL = [p for p in PAPERS["sfa"] if p["identifier"] in ("SB 17", "SB 131")]


def test_the_fixture_is_the_nine_real_sfa_papers_and_thirty_one_scsfa():
    assert len(PAPERS["sfa"]) == 9 and len(WHOLE_BILL) == 2 and len(FRAGMENTS) == 7
    assert len(PAPERS["scsfa"]) == 31


@pytest.mark.parametrize("paper", FRAGMENTS, ids=lambda p: p["identifier"])
def test_the_seven_line_edit_sfa_papers_are_not_versions(paper):
    # HB 1283, HB 295, HB 308, HB 54 ("HB 54/SFA"), SB 170, SB 220, SB 293
    assert is_amendment_text("Georgia", paper["text"]) is True
    assert is_bill_version("Georgia", paper["note"], raw_text=paper["text"]) is False


@pytest.mark.parametrize("paper", WHOLE_BILL, ids=lambda p: p["identifier"])
def test_the_two_whole_bill_sfa_papers_stay_versions(paper):
    # "replacing lines 1 through 90" / "through the end": the enacting clause is in the text
    assert paper["text"].lstrip().upper().startswith(paper["identifier"].upper() + "/SFA")
    assert is_amendment_text("Georgia", paper["text"]) is False
    assert is_bill_version("Georgia", paper["note"], raw_text=paper["text"]) is True


@pytest.mark.parametrize("paper", PAPERS["scsfa"], ids=lambda p: p["note"])
def test_scsfa_papers_stay_versions(paper):
    assert is_bill_version("Georgia", paper["note"], raw_text=paper["text"]) is True


def test_a_note_alone_never_marks_an_sfa_paper_and_other_states_are_unchanged():
    # no text: the note answer, which is "version" (the fragments are known by their text)
    assert is_bill_version("Georgia", "SFA to SB 170") is True
    assert is_bill_version("Georgia", "Sen Floor Amend 1") is False  # unchanged, by its note (OPEN-602)
    for state in ("Alabama", "Virginia", "Florida", "Maine"):
        for paper in FRAGMENTS:
            if state != "Alabama":  # Alabama's own opening rule is its own
                assert is_bill_version(state, paper["note"], raw_text=paper["text"]) is True


def test_other_texts_that_open_amend_are_not_marked_by_the_sfa_rule():
    assert is_amendment_text("Georgia", "Amend SB 3EX (LC 47 4392) by inserting after") is False
    assert is_amendment_text("Georgia", "SB 17/SCSFA\nTo amend Title 20") is False
    assert is_amendment_text("Georgia", "HB 54/SFA\nAmend the Senate substitute BE IT ENACTED") is False


# Latest version of the three bills whose latest "version" was an SFA paper (notes and Georgia document numbers as
# stored): HB 54 ends on its Senate committee substitute with the floor amendments, SB 17 and SB 131 on their SFA.
@pytest.mark.parametrize(
    "identifier, expected",
    [("HB 54", "SCSFA to HB 54"), ("SB 17", "SFA to SB 17"), ("SB 131", "SB 131/SFA")],
)
def test_the_latest_version_of_hb54_sb17_sb131(identifier, expected):
    docs = PAPERS["bills"][identifier]
    versions = [d for d in docs if is_bill_version("Georgia", d["note"], raw_text=d["text"] or None)]
    versions.sort(key=lambda d: version_sort_key(d["note"], "", [d["url"]], origin_chamber(identifier), "Georgia"))
    assert versions[-1]["note"] == expected
