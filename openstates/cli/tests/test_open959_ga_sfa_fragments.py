"""OPEN-959: Georgia's "SFA to SB 170" (the adopted Senate floor amendment, 624 characters of replacement lines)
is not a version, so the as-passed paper after it is compared with the last real version, not with the fragment.

The documents are SB 170 of 2025_26 as stored on the Mac replica (2026-11-28), text unchanged; the stored
`diff_baseline_id` is what the replica held before this rule (the as-passed paper pointed at the fragment, 350816).
"""

import json
import pathlib
import types

from openstates.cli.text_extract import recomputed_diffs_for_documents

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
