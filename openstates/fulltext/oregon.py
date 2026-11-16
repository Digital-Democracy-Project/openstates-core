"""OPEN-600: Oregon's bill text, read without the words a bill deletes from current law.

Oregon prints a change to current law with new words in bold and the words being removed in
italic and inside square brackets (each bill's own legend: "matter [italic and bracketed] is
existing law to be omitted"). There is no drawn strike line, so pdftotext keeps the removed words
beside the new ones: HB 4010 (2026R1) Introduced read "Payments [shall] must be based upon
estimates" and "due the contractor[. The interest shall commence] beginning 30 days after [receipt
of the] receiving an invoice". Measured on every 2026R1 PDF in govbot's Oregon copy (2026-11-16):
360 of 664 bill versions carry bracketed deleted text, 63,589 words in all.

Each "[...]" span is taken out of the extracted text, after the margin line numbers and headers
are gone (a span can run over line breaks, and over a page break once the header is gone). The
spacing either side is tidied as Washington's "((" "))" marks are (fulltext/wa.py): one space
between words, none at either end of a line or before punctuation, and a line left empty is
dropped. The one character ever added is that space, where Oregon prints new words hard against
the closing bracket with no space ("agencies[.]and of", SB 1534).

Not every bracket is a deletion: a chemical name ("6H-benzo[c]chromen-1-ol", HB 4139) or a
quotation in an amendment paper ("in the course of his employment[,]'", SB 1563) prints its
brackets and contents upright. The PDF's fonts say which: a span whose contents are all upright
on one line of the PDF is kept, unless the same contents also appear italic in that document. The
page number ("[2]", upright) is in that list too, but the header line holding it is already gone
from the text. The legend's own "[italic and bracketed]" is kept so the legend still reads.

Amendment papers are read the same way: their bracketed words quote the bill's own deletions
inside the text the amendment inserts ("ORS 308A.706, [and except as provided in section 2 of this
2026 Act,] additional taxes"), so dropping them gives the inserted text as it would read.
"""

import re

import fitz  # PyMuPDF

from .common import ExtractorFunc, Metadata

# Stands in for a removed span until spacing is tidied. A private-use character: in no bill.
_GONE = "\ue000"
_SPAN = re.compile(r"\[([^\[\]]*)\]")
_LEGEND = "italic and bracketed"
_ITALIC = 2  # PyMuPDF span flag


def bracket_fonts(data: bytes) -> tuple:
    """The contents of the bracket pairs on one line of the PDF, as two sets: those with no
    italic character inside (upright) and the rest (italic)."""
    upright, italic = set(), set()
    for page in fitz.open(stream=data, filetype="pdf"):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                inside = None
                for span in line["spans"]:
                    is_italic = bool(span["flags"] & _ITALIC)
                    for char in span["text"]:
                        if char == "[":
                            inside = []
                        elif char == "]" and inside is not None:
                            text = "".join(c for c, _ in inside)
                            if any(i for c, i in inside if not c.isspace()):
                                italic.add(text)
                            else:
                                upright.add(text)
                            inside = None
                        elif inside is not None:
                            inside.append((char, is_italic))
    return upright, italic


def upright_brackets(data: bytes) -> set:
    """The contents of the bracket pairs that print upright, and never also italic in the same
    document."""
    upright, italic = bracket_fonts(data)
    return upright - italic


def _tidy(match: "re.Match") -> str:
    text, start, end = match.string, match.start(), match.end()
    preceding = text[start - 1] if start else "\n"
    following = text[end] if end < len(text) else "\n"
    if preceding == "\n" or following == "\n" or following in ".,;:)]}":
        return ""
    if match.group(1) or match.group(2):
        return " "
    # Oregon sets a few new words hard against the brackets, with no space printed:
    # "agencies[.]and of" (SB 1534 A-Engrossed, "and" in bold). One space keeps the words apart.
    return " " if preceding.isalnum() and following.isalnum() else ""


def drop_bracketed(text: str, keep: set = frozenset()) -> str:
    """`text` without its "[...]" spans, except a span whose contents are in `keep` or are the
    legend's "italic and bracketed"."""
    removed = False

    def gone(match: "re.Match") -> str:
        nonlocal removed
        if match.group(1) in keep or match.group(1) == _LEGEND:
            return match.group(0)
        removed = True
        return _GONE

    text = _SPAN.sub(gone, text)
    if not removed:
        return text
    lines = []
    for line in text.split("\n"):
        if _GONE in line:
            line = re.sub(r"([ \t]*)" + _GONE + r"(?:[ \t]*" + _GONE + r")*([ \t]*)", _tidy, line)
            if not line.strip():
                continue  # a line that held only removed words
        lines.append(line)
    return "\n".join(lines)


def without_bracketed_text(extract: ExtractorFunc) -> ExtractorFunc:
    """`extract`, with the bracketed words Oregon deletes from current law taken out of its
    text."""

    def read(data: bytes, metadata: Metadata) -> str:
        text = extract(data, metadata)
        if "[" not in text:
            return text
        return drop_bracketed(text, keep=upright_brackets(data))

    read.__wrapped__ = extract  # type: ignore[attr-defined]
    return read
