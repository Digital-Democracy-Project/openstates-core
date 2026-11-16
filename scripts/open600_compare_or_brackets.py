#!/usr/bin/env python
"""OPEN-600: compare every Oregon PDF read the old way (bracketed deleted words kept) and the new
way (`fulltext/oregon.py`). Read-only: it opens files and prints a report; it never touches a
database, S3 or the files themselves.

    python scripts/open600_compare_or_brackets.py PDF [PDF ...]

A file with "Amendment" in its name is counted as an amendment paper, any other as a bill
version (govbot's file names). For each file:

* the new text must be the old text with characters only taken out (a subsequence, spacing
  aside: nothing added or reordered). The reader's one added character is a space where a removed
  span sat between two letters with no space printed ("agencies[.]and"); those are counted;
* outside the "[...]" spans, the two texts must hold the same characters in the same order,
  spacing aside (the brackets the new text keeps, and their contents, are set aside on both
  sides). So every character removed, other than spacing, was a bracket or inside one;
* the documents where the same bracket contents print both upright and italic (the reader then
  drops both; listed for reading, upright page numbers aside, as their header line is gone);
* the brackets left in the new text are counted and listed by contents (an upright span the PDF
  prints in a chemical name or a quotation, or a fill-in blank).

Exit status 1 when a text gains or reorders a character, or changes outside brackets.
"""

import collections
import re
import sys

from openstates.fulltext import CONVERSION_FUNCTIONS
from openstates.fulltext.oregon import bracket_fonts

NEW = CONVERSION_FUNCTIONS["or"]["application/pdf"]
OLD = NEW.__wrapped__
META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:or/government"}


def outside(text: str) -> str:
    """`text` without its "[...]" spans and without spacing."""
    out, depth = [], 0
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
        elif not depth and not ch.isspace():
            out.append(ch)
    return "".join(out)


def is_subsequence(new: str, old: str) -> bool:
    """Spacing aside."""
    new, old = re.sub(r"\s", "", new), re.sub(r"\s", "", old)
    rest = iter(old)
    return all(ch in rest for ch in new)


def main(paths: list) -> int:
    c = collections.Counter()
    left = collections.Counter()
    both = collections.Counter()
    bad = []
    for path in paths:
        kind = "amendment" if "Amendment" in path else "version"
        data = open(path, "rb").read()
        try:
            old = OLD(data, META)
        except Exception:
            c[kind + " old reader error"] += 1
            continue
        try:
            new = NEW(data, META)
        except Exception as e:
            c[kind + " NEW READER ERROR"] += 1
            bad.append((path, f"new reader error {e!r}"))
            continue
        c[kind] += 1
        ok = is_subsequence(new, old)
        rest = iter(old)
        if not all(ch in rest for ch in new):
            c[kind + " not a strict subsequence (an added space)"] += 1
        same_outside = outside(old) == outside(new)
        if old != new:
            c[kind + " changed"] += 1
        c[kind + " words removed"] += len(old.split()) - len(new.split())
        removed = len(old) - len(new)
        spacing = sum(ch.isspace() for ch in old) - sum(ch.isspace() for ch in new)
        c[kind + " characters removed"] += removed
        c[kind + " of which spacing"] += spacing
        if not ok or not same_outside:
            bad.append((path, f"subsequence={ok} same outside brackets={same_outside}"))
            c[kind + " BAD"] += 1
        glued = r"(?<=\w)\[[^\[\]]*\](?=\w)"
        c[kind + " spaces added between letters"] += len(re.findall(glued, old)) - len(
            re.findall(glued, new)
        )
        upright, italic = bracket_fonts(data)
        for text in upright & italic:
            if not text.strip().isdigit():
                c[kind + " contents both upright and italic (not a page number)"] += 1
                both[(kind, text[:40])] += 1
        c[kind + " brackets in old text"] += old.count("[") + old.count("]")
        c[kind + " brackets left"] += new.count("[") + new.count("]")
        if "[" in new or "]" in new:
            c[kind + " documents with a bracket left"] += 1
            for piece in new.split("[")[1:]:
                left[(kind, piece.split("]")[0][:40])] += 1
    for key in sorted(c):
        print(f"{key}: {c[key]}")
    print("brackets left, by contents:")
    for (kind, text), n in sorted(left.items()):
        print(f"  {kind}: [{text}] x{n}")
    print("contents both upright and italic in one document:")
    for (kind, text), n in sorted(both.items()):
        print(f"  {kind}: [{text}] x{n}")
    for path, why in bad:
        print("BAD", path, why)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
