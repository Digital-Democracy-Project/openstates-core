#!/usr/bin/env python
"""OPEN-580: compare every archived Washington HTML printing read the old way (struck words and
their "((" "))" marks kept) and the new way (`fulltext/wa.py`). Read-only: it opens files and
prints a report; it never touches a database, S3 or the files themselves.

    python scripts/open580_compare_wa_html.py [--root /Volumes/DDP-HOT/bills/raw]

`--root` is the local bill archive (`ARCHIVE_ROOT_DIR`); its `wa/` folder holds the same files
as the S3 archive, under the same keys. It reports:

* documents, documents with struck text, documents whose text changes;
* documents whose new text gains a character over the old one (spacing and line breaks aside;
  `gains_characters` from OPEN-560's script). Must be 0;
* documents where the characters lost are not exactly the struck characters plus the "((" "))"
  marks (and leaders inside them) taken out, counted from the page itself rather than from the
  reader. Must be 0;
* "((" left in the new text, with examples, and placeholders left (must be 0).

Exit status 1 when any "must be 0" count is not.
"""

import argparse
import collections
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from lxml import html

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from open560_compare_struck_text import gains_characters  # noqa: E402

from openstates.fulltext import CONVERSION_FUNCTIONS  # noqa: E402
from openstates.fulltext.common import extractor_for_element_by_xpath  # noqa: E402
from openstates.fulltext.wa import STRUCK, _is_struck  # noqa: E402

META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:wa/government"}
# The reader before OPEN-580 (OPEN-212's), kept here to compare against.
OLD = extractor_for_element_by_xpath("//html", preserve_element_boundaries=True)
NEW = CONVERSION_FUNCTIONS["wa"]["text/html"]
LEADERS = ".-\u2013\u2014"


def _squeezed_len(text: str) -> int:
    return len("".join(text.split()))


def _struck_text(data: bytes) -> str:
    """The text of the outermost struck elements of the page."""
    [root] = html.fromstring(data).xpath("//html")
    return "\n".join(
        el.text_content()
        for el in root.iter()
        if _is_struck(el) and not any(_is_struck(p) for p in el.iterancestors())
    )


def compare(path: str) -> dict:
    data = Path(path).read_bytes()
    old, new = OLD(data, META), NEW(data, META)
    struck_text = _struck_text(data)
    struck = _squeezed_len(struck_text)
    # Marks taken out, and leaders taken out with them: whatever "((" "))" "." "-" or dash the
    # new text lost beyond the struck text's own ("RCW 9.41.040(1))" can be struck text).
    marks_taken = sum(
        (old.count(mark) - struck_text.count(mark) - new.count(mark)) * len(mark)
        for mark in ("((", "))")
    ) + sum(old.count(ch) - struck_text.count(ch) - new.count(ch) for ch in LEADERS)
    lost = _squeezed_len(old) - _squeezed_len(new)
    left = []
    start = new.find("((")
    while start != -1:
        left.append(new[max(0, start - 40):start + 60].replace("\n", " "))
        start = new.find("((", start + 2)
    return {
        "path": path,
        "struck": struck > 0,
        "changed": old != new,
        "gains": gains_characters(old, new),
        "lost_other": lost != struck + marks_taken,
        "lost": lost,
        "expected": struck + marks_taken,
        "pairs_left": left,
        "placeholders": new.count(STRUCK),
        "old_chars": len(old),
        "new_chars": len(new),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/Volumes/DDP-HOT/bills/raw")
    parser.add_argument("--examples", type=int, default=10)
    args = parser.parse_args()
    paths = sorted(str(p) for p in (Path(args.root) / "wa").rglob("*.html"))
    counts: collections.Counter = collections.Counter()
    bad = []
    left_examples = []
    with ProcessPoolExecutor() as pool:
        for r in pool.map(compare, paths, chunksize=16):
            counts["documents"] += 1
            counts["with struck text"] += r["struck"]
            counts["text changes"] += r["changed"]
            counts["gains a character (must be 0)"] += r["gains"]
            counts["loses other than struck text, marks and leaders (must be 0)"] += r["lost_other"]
            counts["placeholders left (must be 0)"] += r["placeholders"]
            counts["documents with a (( left"] += bool(r["pairs_left"])
            counts["(( left"] += len(r["pairs_left"])
            counts["characters before"] += r["old_chars"]
            counts["characters after"] += r["new_chars"]
            if r["gains"] or r["lost_other"] or r["placeholders"]:
                bad.append(r)
            for example in r["pairs_left"]:
                left_examples.append((r["path"], example))
    for key, value in counts.items():
        print(f"{key}: {value:,}")
    for path, example in left_examples[:args.examples]:
        print(f"  (( left: {path}: ...{example}...")
    for r in bad[:20]:
        print(f"  CHECK: {r['path']} lost {r['lost']} expected {r['expected']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
