#!/usr/bin/env python
"""OPEN-560: compare every archived PDF of a state read the old way (struck words kept) and the
new way (Alabama's strike-line reader, `fulltext/al.py`). Read-only: it opens files and prints a
report; it never touches a database, S3 or the files themselves.

    python scripts/open560_compare_struck_text.py la [--root /Volumes/DDP-HOT/bills/raw]

`--root` is the local bill archive (`ARCHIVE_ROOT_DIR`); its `<state>/` folder holds the same
files as the S3 archive, under the same keys. Two texts are compared for each PDF:

* the PRINT: pdftotext's whole page text (`pdfdata_to_text`), of the original PDF and of the
  copy with the struck characters removed. This isolates the reader itself;
* the STORED text: what the state's extractor returns (old reader vs new reader), which is what
  `refresh-extraction` would write.

"Only loses characters" means: spacing and line breaks aside, the new text is the old text with
characters taken out. Per state it reports:

* documents, documents whose stored text changes;
* documents whose PRINT gains a character (must be 0: the reader only removes);
* documents whose STORED text gains a character over the old stored text, split into those where
  the new stored text is still the original print with characters only taken out (the old
  extractor had dropped a printed line that the new one keeps, see below) and the rest (must
  be 0). pdftotext sometimes prints a line holding struck text as two lines, the second with no
  line number, and `text_after_line_numbers` drops that second line, unstruck words included
  (WA HB 1197: "(($422,000)) $1,903,000 of the general fund—state"). With the struck words gone
  the line prints whole and those words come back;
* Washington: documents where a removed character of the print lies outside the printed
  `((...))` deletion marks (Washington prints every deletion inside them);
* documents where text was removed by a rule at 0.6 of the line height or higher (strikes sit at
  0.41 to 0.55; Louisiana's signature rule at 0.72 is outside the reader's 0.35-0.70 band, so a
  removal this high would mean a signature or fill-in line eating real text), and documents with
  any thin rule crossing a character at 0.6-0.8 (left alone by the reader; for context);
* a histogram of the line-height position of the rules that removed characters;
* errors from either reader, and the time both readers take on the largest file.

Exit status 1 when any document's print gains, or its stored text gains without that
explanation.
"""

import argparse
import collections
import difflib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import fitz  # PyMuPDF

from openstates.fulltext import CONVERSION_FUNCTIONS, al
from openstates.fulltext.utils import pdfdata_to_text

HIGH_RULE = 0.6
UNDERLINE = 0.8  # underlines sit at 0.85 of the line height or more (measured, all three states)
# Above this many characters in one changed block, the exact Washington check is skipped and
# the block is reported for reading (it is quadratic in the block's size).
MAX_EXACT_BLOCK = 4000


def _squeeze(line: str) -> str:
    return "".join(line.split())


def _is_subsequence(short: str, long: str) -> bool:
    rest = iter(long)
    return all(ch in rest for ch in short)


def gains_characters(old: str, new: str) -> bool:
    """True when the new text is not the old text with characters only taken out, spacing and
    line breaks aside. Over the whole document: pdftotext may move a line up or down a row when
    the struck words beside it are gone, which is not a gain."""
    return not _is_subsequence(_squeeze(new), _squeeze(old))


def deletion_mark_masks(lines: list) -> list:
    """For each line, its characters (spacing aside) and whether each lies between a printed
    `((` and its `))`. Marks may open and close on different lines; a single `(`...`)` inside
    the marks is part of the deleted text ("subsection((s (2)))", "(((g)))")."""
    masks = []
    depth = inner = 0
    for line in lines:
        chars = _squeeze(line)
        inside = []
        i = 0
        while i < len(chars):
            if not depth and chars.startswith("((", i):
                depth, inner = 1, 0
                inside += [False, False]
                i += 2
            elif depth and not inner and chars.startswith("))", i):
                depth = 0
                inside += [False, False]
                i += 2
            else:
                if depth and chars[i] == "(":
                    inner += 1
                elif depth and chars[i] == ")" and inner:
                    inner -= 1
                inside.append(depth > 0)
                i += 1
        masks.append((chars, inside))
    return masks


def only_marked_characters_removed(chars: str, inside: list, kept: str) -> bool:
    """Whether `kept` is `chars` with only characters flagged `inside` taken out: some way of
    reading `kept` out of `chars` keeps every character outside the marks."""
    n, m = len(chars), len(kept)
    # ok[j]: chars[i:] can become kept[j:]; filled from the end of `chars` backwards.
    ok = [False] * m + [True]
    for i in range(n - 1, -1, -1):
        row = [False] * (m + 1)
        for j in range(m, -1, -1):
            row[j] = (inside[i] and ok[j]) or (
                j < m and chars[i] == kept[j] and ok[j + 1]
            )
        ok = row
    return ok[0]


def removed_outside_deletion_marks(old: str, new: str) -> list:
    """Washington: the old lines holding a removed character (spacing aside) that is not
    between a printed `((` and its `))`. Empty when every removal is inside the marks. Lines
    are aligned first (a wholly struck line can drop out), then each changed block is checked
    exactly."""
    old_lines = old.split("\n")
    masks = deletion_mark_masks(old_lines)
    old_keys = [chars for chars, _ in masks]
    new_keys = [_squeeze(line) for line in new.split("\n")]
    bad = []
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        chars = "".join(old_keys[i1:i2])
        inside = [flag for _, mask in masks[i1:i2] for flag in mask]
        kept = "".join(new_keys[j1:j2])
        if len(chars) > MAX_EXACT_BLOCK or not only_marked_characters_removed(
            chars, inside, kept
        ):
            bad.append(" / ".join(old_lines[i1:i2])[:300])
    return bad


def rule_positions(data: bytes) -> tuple:
    """(positions of the rules that removed a character, positions of thin rules crossing a
    character at 0.6-0.8 of the line height). Same geometry as `al._struck_runs`."""
    removing, high = [], []
    doc = fitz.open(stream=data, filetype="pdf")
    for page in doc:
        rules = [
            d["rect"]
            for d in page.get_drawings()
            if d["rect"].height < al._RULE_MAX_HEIGHT
        ]
        if not rules:
            continue
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                dx, dy = line["dir"]
                if abs(dx - 1) > 1e-3 or abs(dy) > 1e-3:
                    continue  # the reader measures left-to-right text only
                for span in line["spans"]:
                    x0, top, x1, bottom = span["bbox"]
                    if bottom <= top:
                        continue
                    for rule in rules:
                        if not (rule.x1 > x0 and rule.x0 < x1):
                            continue
                        pos = ((rule.y0 + rule.y1) / 2 - top) / (bottom - top)
                        if not al._STRIKE_LOW < pos < UNDERLINE:
                            continue
                        if not any(
                            min(c["bbox"][2], rule.x1) - max(c["bbox"][0], rule.x0)
                            > 0.5 * (c["bbox"][2] - c["bbox"][0])
                            and c["c"].strip()  # a rule over a blank removes nothing
                            for c in span["chars"]
                        ):
                            continue
                        if pos < al._STRIKE_HIGH:
                            removing.append(pos)
                        if HIGH_RULE <= pos < UNDERLINE:
                            high.append(pos)
    return removing, high


def compare_one(args: tuple) -> dict:
    state, path = args
    data = Path(path).read_bytes()
    meta = {
        "url": path,
        "media_type": "application/pdf",
        "title": "",
        "jurisdiction_id": f"ocd-jurisdiction/country:us/state:{state}/government",
    }
    extract = CONVERSION_FUNCTIONS[state]["application/pdf"].__wrapped__
    out = {"path": path, "size": len(data)}
    try:
        t = time.monotonic()
        old = extract(data, meta)
        out["old_s"] = time.monotonic() - t
    except Exception as e:  # reported, not raised
        out["old_error"] = f"{type(e).__name__}: {e}"[:200]
        return out
    try:
        t = time.monotonic()
        copy = al.without_struck_text(data)
        new = extract(copy, meta)  # exactly what CONVERSION_FUNCTIONS[state] now returns
        out["new_s"] = time.monotonic() - t
    except Exception as e:
        out["new_error"] = f"{type(e).__name__}: {e}"[:200]
        return out
    out["changed"] = old != new
    if copy is not data:
        old_print, new_print = pdfdata_to_text(data), pdfdata_to_text(copy)
        out["print_gained"] = gains_characters(old_print, new_print)
        if out["changed"] and gains_characters(old, new):
            if _is_subsequence(_squeeze(new), _squeeze(old_print)):
                out["stored_gained_recovered_print"] = True
            else:
                out["stored_gained_unexplained"] = True
        if state == "wa":
            out["outside_marks"] = removed_outside_deletion_marks(old_print, new_print)[:3]
    elif out["changed"]:
        out["stored_gained_unexplained"] = True  # same bytes cannot read differently
    try:
        removing, high = rule_positions(data)
    except Exception as e:
        out["geometry_error"] = f"{type(e).__name__}: {e}"[:200]
        removing, high = [], []
    out["removing_buckets"] = dict(
        collections.Counter(f"{int(p * 20) / 20:.2f}" for p in removing)
    )
    out["removed_by_high_rule"] = any(p >= HIGH_RULE for p in removing)
    out["rule_0.6_0.8"] = bool(high)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("state", choices=["la", "ne", "wa"])
    parser.add_argument(
        "--root",
        default=os.environ.get("ARCHIVE_ROOT_DIR", "/Volumes/DDP-HOT/bills/raw"),
    )
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--details", default=None, help="write per-document JSON lines here")
    args = parser.parse_args()

    paths = sorted(
        str(p)
        for p in Path(args.root, args.state).rglob("*")
        if p.suffix.lower() == ".pdf"
    )
    if args.limit:
        paths = paths[: args.limit]
    started = time.monotonic()
    with ProcessPoolExecutor(args.jobs) as pool:
        results = list(
            pool.map(compare_one, [(args.state, p) for p in paths], chunksize=2)
        )
    elapsed = time.monotonic() - started

    if args.details:
        with open(args.details, "w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")

    def where(key):
        return [r for r in results if r.get(key)]

    buckets: collections.Counter = collections.Counter()
    for r in results:
        buckets.update(r.get("removing_buckets", {}))
    largest = max(results, key=lambda r: r["size"]) if results else {}
    slowest = max(results, key=lambda r: r.get("new_s", 0)) if results else {}
    summary = {
        "state": args.state,
        "root": args.root,
        "documents": len(results),
        "documents_with_struck_text_removed": sum(1 for r in results if "print_gained" in r),
        "documents_stored_text_changed": len(where("changed")),
        "documents_print_gains_characters": len(where("print_gained")),
        "documents_stored_text_gains_unexplained": len(where("stored_gained_unexplained")),
        "documents_stored_text_gains_recovered_dropped_print_line": len(
            where("stored_gained_recovered_print")
        ),
        "old_reader_errors": len(where("old_error")),
        "new_reader_errors": len(where("new_error")),
        "geometry_errors": len(where("geometry_error")),
        "documents_text_removed_by_rule_at_0.6_or_higher": len(where("removed_by_high_rule")),
        "documents_with_thin_rule_crossing_text_at_0.6_to_0.8": len(where("rule_0.6_0.8")),
        "removed_by_rule_position": dict(sorted(buckets.items())),
        "largest_file": {
            "path": largest.get("path"),
            "bytes": largest.get("size"),
            "old_seconds": round(largest.get("old_s", -1), 2),
            "new_seconds": round(largest.get("new_s", -1), 2),
        },
        "slowest_new_read": {
            "path": slowest.get("path"),
            "bytes": slowest.get("size"),
            "seconds": round(slowest.get("new_s", -1), 2),
        },
        "invalid_object_state_skips": "n/a: read from local disk, not S3",
        "elapsed_seconds": round(elapsed, 1),
        "examples": {
            "print_gained": [r["path"] for r in where("print_gained")][:10],
            "stored_gained_unexplained": [r["path"] for r in where("stored_gained_unexplained")][:10],
            "stored_gained_recovered_print": [
                r["path"] for r in where("stored_gained_recovered_print")
            ][:10],
            "new_errors": [(r["path"], r["new_error"]) for r in where("new_error")][:10],
            "removed_by_high_rule": [r["path"] for r in where("removed_by_high_rule")][:10],
        },
    }
    if args.state == "wa":
        outside = where("outside_marks")
        summary["wa_documents_with_removal_outside_marks"] = len(outside)
        summary["examples"]["wa_outside_marks"] = [
            (r["path"], r["outside_marks"]) for r in outside[:10]
        ]
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    failed = summary["documents_print_gains_characters"] or summary[
        "documents_stored_text_gains_unexplained"
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
