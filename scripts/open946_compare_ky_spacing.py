#!/usr/bin/env python
"""OPEN-946: replay every stored Kentucky comparison without and with the spacing cleaner (`ddp_states/ky.py`).
Read-only: it reads a file and prints a report; it touches no database.

    python scripts/open946_compare_ky_spacing.py PAIRS.jsonl

PAIRS.jsonl has one JSON object per stored comparison (read on the Mac replica, read only): "bill", "session", "note",
"stored" (the stored comparison), "prior" (the baseline's raw_text) and "raw" (the document's raw_text). Prints, before
and after: comparisons not empty, changed lines, changed lines that are a removed and an added line that differ only
in runs of white space ("spacing noise"), comparisons that are 95% or more such lines, and the comparisons
that become empty. Checks that the "before" replay equals the stored comparison, that the cleaned text holds the same
words in the same order as the raw text, and that no spacing noise is left. A real word change is not lost when the
words each comparison removes and adds on balance (a re-spaced or moved line cancels out) are the same before and after
(listed when they differ).
Exit status 1 when any check fails.
"""

import collections
import difflib
import json
import sys

from openstates.cli.text_extract import _compare_texts
from openstates.ddp_states.ky import clean_text


def changed(diff: str) -> tuple:
    minus, plus = [], []
    for line in diff.split("\n"):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            minus.append(line[1:])
        elif line.startswith("+"):
            plus.append(line[1:])
    return minus, plus


def noise_lines(minus: list, plus: list) -> int:
    """Changed lines in a removed/added pair that differ as printed but are identical once white space is collapsed
    (counted on both sides). A removed and an added line that are identical as printed are a moved line, not spacing."""
    norm = lambda s: " ".join(s.split())  # noqa: E731
    added = collections.defaultdict(list)
    for line in plus:
        added[norm(line)].append(line)
    n = 0
    for line in minus:
        pool = added[norm(line)]
        if line in pool:  # a moved line takes its identical twin first
            pool.remove(line)
        elif pool:
            pool.pop()
            n += 2
    return n


def moved_lines(minus: list, plus: list) -> int:
    """Changed lines in a removed/added pair that are identical as printed (difflib shows a moved line this way)."""
    return 2 * sum((collections.Counter(minus) & collections.Counter(plus)).values())


def net_words(minus: list, plus: list) -> tuple:
    """The words the comparison removes and adds on balance (a line that only moved or was re-spaced cancels out)."""
    out = collections.Counter(w for line in minus for w in line.split())
    inn = collections.Counter(w for line in plus for w in line.split())
    return out - inn, inn - out


def main(path: str) -> int:
    before = collections.Counter()
    after = collections.Counter()
    became_empty, bad, lost = [], [], []
    for raw_line in open(path):
        row = json.loads(raw_line)
        who = f"{row['bill']} ({row['session']}, {row['note']})"
        old = "\n".join(difflib.unified_diff(row["prior"].splitlines(), row["raw"].splitlines(), lineterm=""))
        if old != row["stored"]:
            bad.append(f"{who}: the before replay differs from the stored comparison")
        new = _compare_texts(
            row["prior"],
            row["raw"],
            jurisdiction_name="Kentucky",
            is_bill=True,
            prior_media_type=row["prior_media"],
            cur_media_type=row["media"],
        )
        for text, cleaned in zip(
            (row["prior"], row["raw"]),
            clean_text(row["prior"], row["raw"], is_bill=True, prior_media_type=None, cur_media_type=""),
        ):
            if text.split() != cleaned.split():
                bad.append(f"{who}: the cleaner changed the words")
        stats = {}
        for label, diff, counter in (("before", old, before), ("after", new, after)):
            minus, plus = changed(diff)
            total = len(minus) + len(plus)
            noise = noise_lines(minus, plus)
            stats[label] = (total, noise)
            if total:
                counter["comparisons not empty"] += 1
                counter["changed lines"] += total
                counter["spacing-noise lines"] += noise
                counter["moved verbatim (same as printed, not spacing)"] += moved_lines(minus, plus)
                counter["real changed lines (not noise)"] += total - noise
                if noise / total >= 0.95:
                    counter["comparisons 95%+ noise"] += 1
        if stats["after"][1]:
            bad.append(f"{who}: {stats['after'][1]} spacing-noise lines are left")
        if stats["before"][0] and not stats["after"][0]:
            became_empty.append(who)
        if net_words(*changed(old)) != net_words(*changed(new)):
            lost.append(who)
    for key in sorted(before):
        print(f"{key}: before {before[key]}, after {after[key]}")
    print(f"comparisons that become empty: {len(became_empty)}")
    for who in became_empty:
        print("  empty:", who)
    print(f"comparisons whose net removed/added words differ: {len(lost)}")
    for line in lost:
        print("  differs:", line)
    for line in bad:
        print("BAD", line)
    return 1 if bad or lost else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
