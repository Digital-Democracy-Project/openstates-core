#!/usr/bin/env python
"""OPEN-950: compare every archived Mississippi HTML document read the old way (struck words kept) and
the new way (`fulltext/ms.py`), and each against the PDF copy of the same version. Read-only: it opens
files and prints a report; it never touches a database, S3 or the files themselves.

    python scripts/open950_compare_ms_html.py [--root /Volumes/DDP-HOT/bills/raw]

`--root` is the local bill archive (`ARCHIVE_ROOT_DIR`); its `ms/` folder holds the same files as the
S3 archive, under the same keys. It reports:

* documents, documents with struck or hidden text, documents whose text changes, documents either
  reader cannot read (no single `WordSection1`);
* documents whose new text gains a character over the old one (spacing aside; must be 0);
* documents where the characters lost are not exactly the characters of the struck and hidden
  elements, counted from the page itself rather than from the reader (must be 0);
* the pairs: each HTML document with the PDF of the same bill and version note (same folder, same
  file-name stem). The HTML text is taken from where the PDF's text starts (its cover block of
  legislature, session and sponsors is not in the PDF). For each pair the words of the HTML text
  with no counterpart in the PDF text, and the other way round, before and after: a pair "agrees"
  when each side has at most `--header` such words (the printings' headers and footers differ).

Exit status 1 when any "must be 0" count is not.
"""

import argparse
import collections
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from lxml import html

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from open560_compare_struck_text import gains_characters  # noqa: E402

from openstates.fulltext import CONVERSION_FUNCTIONS  # noqa: E402
from openstates.fulltext.common import extractor_for_element_by_xpath  # noqa: E402
from openstates.fulltext.ms import _WORD_SECTION, _is_struck  # noqa: E402

META = {"jurisdiction_id": "ocd-jurisdiction/country:us/state:ms/government"}
# The reader before OPEN-950 (upstream's).
OLD = extractor_for_element_by_xpath(_WORD_SECTION)
NEW = CONVERSION_FUNCTIONS["ms"]["text/html"]
PDF = CONVERSION_FUNCTIONS["ms"]["application/pdf"]
HEADER = 30


def _squeezed(text: str) -> str:
    return "".join(text.split())


def _words(text: str) -> collections.Counter:
    return collections.Counter(re.findall(r"\w+", text.lower()))


def _unmatched(a: collections.Counter, b: collections.Counter) -> int:
    return sum((a - b).values())


def _from_body(text: str, pdf: str) -> str:
    """`text` from where the PDF's own text starts: the HTML printing opens with a cover block (the
    legislature, session, sponsors, bill number) that the PDF's text does not carry."""
    first = pdf.split()[:5]
    match = re.search(r"\s+".join(re.escape(w) for w in first), text) if len(first) == 5 else None
    return text[match.start():] if match else text


def _struck_text(data: bytes) -> str:
    """The text of the outermost struck or hidden elements of the page's section, from the page."""
    [section] = html.fromstring(data).xpath(_WORD_SECTION)
    return "".join(
        el.text_content()
        for el in section.iter()
        if _is_struck(el) and not any(_is_struck(p) for p in el.iterancestors())
    )


def read_pair(args) -> dict:
    html_path, pdf_path = args
    data = Path(html_path).read_bytes()
    out = {"path": html_path, "error": False, "paired": pdf_path is not None}
    try:
        old, new = OLD(data, META), NEW(data, META)
    except Exception:  # noqa: BLE001 (counted and reported)
        out["error"] = True
        return out
    struck_text = _struck_text(data)
    struck = len(_squeezed(struck_text))
    out.update(
        struck=struck > 0,
        changed=old != new,
        gains=gains_characters(old, new),
        lost=len(_squeezed(old)) - len(_squeezed(new)),
        expected=struck,
        old_chars=len(old),
        new_chars=len(new),
    )
    if pdf_path:
        try:
            pdf = PDF(Path(pdf_path).read_bytes(), META)
        except Exception:  # noqa: BLE001
            pdf = ""
        wp = _words(pdf)
        old_body, new_body = _from_body(old, pdf), _from_body(new, pdf)
        out["pdf_empty"] = not pdf.strip()
        out["old_html_extra"] = _unmatched(_words(old_body), wp)
        out["new_html_extra"] = _unmatched(_words(new_body), wp)
        out["old_pdf_extra"] = _unmatched(wp, _words(old_body))
        out["new_pdf_extra"] = _unmatched(wp, _words(new_body))
        out["old_len_ratio"] = len(_squeezed(old_body)) / max(1, len(_squeezed(pdf)))
        out["new_len_ratio"] = len(_squeezed(new_body)) / max(1, len(_squeezed(pdf)))
    return out


def pairs(root: Path):
    """(html path, pdf path or None): the PDF in the same folder with the same file-name stem."""
    for folder in sorted(p for p in root.rglob("*") if p.is_dir()):
        htmls, pdfs = {}, {}
        for f in folder.iterdir():
            stem = f.name.rsplit("-", 1)[0]
            if f.suffix == ".html":
                htmls.setdefault(stem, []).append(f)
            elif f.suffix == ".pdf":
                pdfs.setdefault(stem, []).append(f)
        for stem, files in htmls.items():
            for f in files:
                twin = pdfs.get(stem)
                yield str(f), str(twin[0]) if twin and len(twin) == 1 and len(files) == 1 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/Volumes/DDP-HOT/bills/raw")
    parser.add_argument("--header", type=int, default=HEADER)
    parser.add_argument("--examples", type=int, default=10)
    args = parser.parse_args()
    todo = list(pairs(Path(args.root) / "ms"))
    counts: collections.Counter = collections.Counter()
    bad, worst = [], []
    with ProcessPoolExecutor() as pool:
        for r in pool.map(read_pair, todo, chunksize=8):
            counts["documents"] += 1
            if r["error"]:
                counts["cannot be read (no single WordSection1)"] += 1
                continue
            counts["with struck or hidden text"] += r["struck"]
            counts["text changes"] += r["changed"]
            counts["gains a character (must be 0)"] += r["gains"]
            wrong = r["lost"] != r["expected"]
            counts["loses other than struck text (must be 0)"] += wrong
            counts["characters before"] += r["old_chars"]
            counts["characters after"] += r["new_chars"]
            if r["gains"] or wrong:
                bad.append(r)
            if r["paired"]:
                counts["pairs"] += 1
                counts["pairs with an empty PDF text"] += r["pdf_empty"]
                if r["pdf_empty"]:
                    continue
                counts["pairs HTML longer than PDF by over 5%, before"] += r["old_len_ratio"] > 1.05
                counts["pairs HTML longer than PDF by over 5%, after"] += r["new_len_ratio"] > 1.05
                counts["pairs HTML longer than PDF by over 25%, before"] += r["old_len_ratio"] > 1.25
                counts["pairs HTML longer than PDF by over 25%, after"] += r["new_len_ratio"] > 1.25
                counts["pairs HTML shorter than PDF by over 5%, before"] += r["old_len_ratio"] < 0.95
                counts["pairs HTML shorter than PDF by over 5%, after"] += r["new_len_ratio"] < 0.95
                for when, label in (("old", "before"), ("new", "after")):
                    html_extra, pdf_extra = r[f"{when}_html_extra"], r[f"{when}_pdf_extra"]
                    agrees = html_extra <= args.header and pdf_extra <= args.header
                    counts[f"pairs that agree within {args.header} words each way, {label}"] += agrees
                    counts[f"pairs with more than {args.header} HTML-only words, {label}"] += html_extra > args.header
                worst.append((r["new_html_extra"], r["new_pdf_extra"], r["path"]))
    for key, value in counts.items():
        print(f"{key}: {value:,}")
    for r in bad[:20]:
        print(f"  CHECK: {r['path']} lost {r['lost']} expected {r['expected']} gains {r['gains']}")
    print("pairs with the most HTML-only words after (html-only, pdf-only, path):")
    for item in sorted(worst, reverse=True)[: args.examples]:
        print("  ", item)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
