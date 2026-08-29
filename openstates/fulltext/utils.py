import re
import typing
import tempfile
import functools
import subprocess
from lxml import html  # type: ignore


def pdfdata_to_text(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(delete=True) as tmpf:
        tmpf.write(data)
        tmpf.flush()
        try:
            pipe = subprocess.Popen(
                ["pdftotext", "-layout", tmpf.name, "-"],
                stdout=subprocess.PIPE,
                close_fds=True,
            ).stdout
        except OSError as e:
            raise EnvironmentError(
                f"error running pdftotext, missing executable? [{e}]"
            )
        if not pipe:
            raise EnvironmentError("could not open pipe")
        data = pipe.read()
        pipe.close()
        return data.decode("utf8", "ignore")


def clean(text: str) -> str:
    text = text.replace("\xa0", " ")  # nbsp -> sp
    text = text.replace("\r\n", "\n")  # replace carriage returns
    text = re.sub(r"[ \t]", " ", text)  # collapse spaces
    # collapse newlines too?
    return text


def _text_near_line_numbers(lines: str, regex: str) -> str:
    """ used for before & after line numbers """
    text = []
    for line in lines.splitlines():
        # real bill text starts with an optional space, line number,
        # more spaces, then real text
        match = re.match(regex, line)
        if match:
            text.append(match.group(1))

    # return all real bill text joined w/ newlines
    return "\n".join(text)


text_after_line_numbers = functools.partial(
    _text_near_line_numbers, regex=r"\s*\d+\s+(.*)"
)
text_before_line_numbers = functools.partial(
    _text_near_line_numbers, regex=r"(.*?)\s+\d+\s*"
)


def text_from_element_lxml(data: bytes, lxml_query: str) -> str:
    html_document = html.fromstring(data)
    matching_elements = html_document.findall(lxml_query)

    # To ensure that we exit non-zero if there are multiple matching elements
    # on the page, raise an exception: this means that the extraction
    # code needs to be updated.
    assert (
        len(matching_elements) == 1
    ), f"{len(matching_elements)} matches for {lxml_query}"

    text_inside_element = matching_elements[0].text_content()
    return text_inside_element


def text_from_element_xpath(
    data: bytes, lxml_xpath_query: str, preserve_element_boundaries: bool = False
) -> str:
    """
    OPEN-212: `preserve_element_boundaries=True` serializes the matched element with
    `xml_tree_to_lines()` instead of lxml's `text_content()`.

    `text_content()` concatenates every text node with NO separator, so wherever the source
    HTML puts adjacent blocks on one line, their text fuses. Real stored Washington output:
    "CERTIFICATION OF ENROLLMENTENGROSSED SUBSTITUTE SENATE BILL 516769TH LEGISLATURE" --
    Engrossed Substitute Senate Bill 5167 stored as bill "516769". That is a content bug, not
    only a diffing one: the bill number is unsearchable and any reader sees a number that was
    never in the document.

    Default stays `text_content()` deliberately. Eight other jurisdictions use this helper
    (ca/hi/mi/ms/nj/tx via this function, plus the sibling variants) and every one of them
    measures clean today -- zero whole-document diffs -- because their sources happen to ship
    HTML with literal newlines between elements, which `text_content()` preserves. They are one
    upstream reformat away from the same bug, so this is not a reason to leave them alone
    forever; it is a reason not to change eight jurisdictions' stored text in a ticket scoped to
    the one that is measurably broken. Adopting it later is one keyword per jurisdiction.
    """
    html_document = html.fromstring(data)
    matching_elements = html_document.xpath(lxml_xpath_query)

    # To ensure that we exit non-zero if there are multiple matching elements
    # on the page, raise an exception: this means that the extraction
    # code needs to be updated.
    assert (
        len(matching_elements) == 1
    ), f"{len(matching_elements)} matches for {lxml_xpath_query}"

    if preserve_element_boundaries:
        return xml_tree_to_lines(matching_elements[0], block_tags=HTML_BLOCK_TAGS)
    text_inside_element = matching_elements[0].text_content()
    return text_inside_element


def text_from_element_siblings_lxml(data: bytes, lxml_query: str) -> str:
    html_document = html.fromstring(data)
    matching_elements = html_document.findall(lxml_query)

    text_inside_elements = ""
    for element in matching_elements:
        text_inside_elements += element.text_content() + "\n"

    return text_inside_elements


def text_from_element_siblings_xpath(data: bytes, lxml_query: str) -> str:
    html_document = html.fromstring(data)
    matching_elements = html_document.xpath(lxml_query)

    text_inside_elements = ""
    for element in matching_elements:
        text_inside_elements += element.text_content() + "\n"

    return text_inside_elements


# OPEN-210: XML bill documents (UT's `<leg>` export, US govinfo's bill.dtd/USLM) used to be
# extracted by pulling every text node via `itertext()` and joining with single spaces. That
# parses the document correctly and then discards the parse: the result is one enormous line.
#
# That was an accepted trade-off while `raw_text` only fed search and display. It stopped being
# one once archive_bill_versions() started diffing raw_text with `difflib.unified_diff`, which
# is LINE-based -- a single-line document can only ever produce "replace the whole thing".
# Measured before this fix: all 2,074 Utah XML diffs and 6,683 of 6,692 US XML diffs were
# degenerate whole-document hunks, and every PDF diff computed after XML became the preferred
# diff baseline was ruined too (OPEN-209).
#
# The fix is to use the tree lxml already handed us, rather than a hand-maintained list of
# "block-level" tag names per schema -- those rot, and neither of these formats is stable
# enough across stages (bill.dtd vs USLM) to enumerate confidently. Instead block-vs-inline is
# read from the document's own MIXED CONTENT: an element that sits in a run of real text is
# inline by definition, because the source put it there. Everything else starts its own line.
#
# Concretely this keeps `<bold>81-4-502</bold>, as enacted by Laws of Utah 2024` on one line
# (the `<bold>` has a non-empty tail, so it is inline), while `<text>`, `<section>`, `<heading>`
# and friends each get their own line (their surrounding text is whitespace-only).


# OPEN-212: HTML block-level elements, per the HTML spec. Unlike the per-jurisdiction XML
# vocabularies this serializer deliberately refuses to enumerate, this set is a fixed,
# standardised constant -- it does not drift per source and cannot rot the way a bill-schema
# tag list would. It exists because HTML carries DISPLAY semantics that XML does not: a
# <div> is a block even when bare text follows it, whereas in the XML schemas adjacency to
# text reliably meant inline.
#
# Rare but real: across four sampled Washington documents, 2 of 29,525 block elements had
# text in their tail -- `<div>Secretary</div>Secretary`, which the mixed-content rule alone
# folded into "SecretarySecretary". That is the same token fusion this ticket exists to fix,
# so the rule needs HTML's own semantics here. Raised by /pm-review.
HTML_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "body", "center", "dd", "details", "dialog",
    "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "head", "header", "hgroup", "hr", "html", "li", "main", "nav", "ol", "p",
    "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "title", "tr", "ul",
})


def _is_inline(el: typing.Any, block_tags: typing.AbstractSet[str] = frozenset()) -> bool:
    """True when `el` sits inside a run of real text and so belongs on its neighbours' line.

    Read from the document rather than from a tag list: an element is inline if there is
    non-whitespace text immediately before it (its parent's own text when it is the first
    child, otherwise the previous sibling's tail) or immediately after it (its own tail).
    """
    parent = el.getparent()
    if parent is None:
        return False
    if block_tags and isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1].lower() in block_tags:
        return False
    previous = el.getprevious()
    before = previous.tail if previous is not None else parent.text
    if before and before.strip():
        return True
    return bool(el.tail and el.tail.strip())


def xml_tree_to_lines(
    root: typing.Any, block_tags: typing.AbstractSet[str] = frozenset()
) -> str:
    """Serialize an lxml tree to text, one line per block-level element.

    Inline elements (see `_is_inline`) are folded into the surrounding line so a sentence
    broken up by markup stays a single line -- which is what makes the output stable enough
    for a line-based diff to align two versions of the same bill.
    """
    lines: typing.List[str] = []
    buffer: typing.List[str] = []

    def flush() -> None:
        if buffer:
            # Join the fragments RAW and collapse whitespace once, rather than stripping each
            # fragment and joining with spaces. Inline markup butts directly against its
            # neighbours in the source -- `<bold>81-4-502</bold>, as enacted by ...` -- and
            # per-fragment stripping would render that "81-4-502 , as enacted by", inventing a
            # space before the comma. Harmless-looking, but it is text every downstream reader
            # sees, LegBot included.
            line = " ".join("".join(buffer).split())
            if line:
                lines.append(line)
            buffer.clear()

    def add(text: typing.Optional[str]) -> None:
        if text:
            buffer.append(text)

    def walk(el: typing.Any, within_inline: bool = False) -> None:
        # `within_inline` stops a nested wrapper from breaking a sentence apart:
        # `<p>foo <bold><italic>bar</italic></bold> baz</p>` would otherwise yield "foo" /
        # "bar" / "baz", because <italic> has no adjacent text of its own. Raised by
        # /pm-review.
        #
        # Inheritance is deliberately narrower than "inline elements have inline children",
        # which over-merges: in Utah's `<sa>Utah Code Sections Affected:<saamd><snhead>AMENDS:
        # </snhead>...</saamd></sa>` the <saamd> is adjacent to text and so inline by the rule
        # above, but it is a container of blocks, and inheriting into it swallowed the whole
        # section onto one line.
        #
        # The discriminator is the element's OWN tail, and it is measured rather than guessed.
        # Across the sampled UT/US corpus, 34 elements were inline-with-children whose tail
        # carried real text -- `<quote>` mid-sentence, genuinely embedded in a run -- and 18
        # were inline-with-children whose tail did not -- `<hl>`, a block container that merely
        # follows text. Text after the element means the run continues past it, so its subtree
        # is part of that run; no trailing text means it was appended after a line, not woven
        # into one.
        #
        # The tail check governs ENTERING a run, not continuing one: once inside, an
        # intermediate wrapper with no tail of its own (the <y> in `a <x><y><z>b</z></y></x> c`)
        # must not end it.
        inline = within_inline or _is_inline(el, block_tags)
        carries_run_onward = bool(el.tail and el.tail.strip())
        if not inline:
            flush()
        add(el.text)
        for child in el:
            if isinstance(child.tag, str):  # skip comments/processing instructions
                walk(child, within_inline=within_inline or (inline and carries_run_onward))
            else:
                add(child.tail)  # a comment's tail is still real document text
        if not inline:
            flush()
        add(el.tail)

    walk(root)
    flush()
    return "\n".join(lines)
