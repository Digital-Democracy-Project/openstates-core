"""OPEN-516: Pennsylvania's OpenDocument printings, read with struck-out text left out.

The fixtures are real printings from palegis.us (the "DOC" link, served as application/msword):
HB 2625 PN3586 (the first printing, nothing struck), HB 2625 PN3751 (the next printing, which
strikes the section 9301 "applicant" change and "for definitions and" from the title) and
HB 2450 PN3371 (struck "(b)(1) and (d)", [bracketed] existing-law amounts).
"""

import io
import zipfile
from pathlib import Path

from openstates.fulltext import CONVERSION_FUNCTIONS, DoNotDownload, get_extract_func
from openstates.fulltext.pa import (
    ODT_MEDIA_TYPE,
    handle_pennsylvania_odt,
    is_opendocument_text,
)

TESTDATA = Path(__file__).parent / "testdata"
PA = "ocd-jurisdiction/country:us/state:pa/government"


def _read(name: str) -> bytes:
    return (TESTDATA / name).read_bytes()


def _text(name: str) -> str:
    return handle_pennsylvania_odt(_read(name), {})


def _without_strike_styles(data: bytes) -> bytes:
    """The same file with the line-through property renamed, so no style counts as struck."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            body = src.read(info)
            if info.filename == "content.xml":
                body = body.replace(
                    b"style:text-line-through-style=", b"style:x-renamed="
                )
            dst.writestr(info, body)
    return out.getvalue()


def test_the_struck_section_9301_change_and_title_words_are_left_out():
    lines = _text("pa_hb2625_pn3751.odt").split("\n")
    text = "\n".join(lines)
    assert "9301" not in text
    assert '"Applicant." A person who' not in text
    assert "for definitions and" not in text
    assert (
        "Amending Title 3 (Agriculture) of the Pennsylvania Consolidated Statutes, in race horse "
        "industry reform, further providing for additional powers of commission."
        in lines
    )
    # The paragraph the printing adds stays, whole, on one line.
    assert (
        '(viii) For the purposes of this paragraph, the term "applicant" shall mean an individual '
        "applying for a license relating to:" in lines
    )
    # Underlined (inserted existing-law) language is kept and is not split off its sentence.
    assert any(
        line.startswith(
            "Section 1. Section 9312(2)(ii), (iii) and (iv) of Title 3 of the Pennsylvania"
        )
        for line in lines
    )


def test_the_struck_text_is_really_in_the_file():
    """Guards the test above: the words it expects gone are present when nothing counts as struck."""
    kept = handle_pennsylvania_odt(
        _without_strike_styles(_read("pa_hb2625_pn3751.odt")), {}
    )
    assert "§ 9301. Definitions." in kept
    assert "for definitions and" in kept
    assert len(kept.split()) == 613
    assert len(_text("pa_hb2625_pn3751.odt").split()) == 436


def test_a_printing_with_nothing_struck_loses_nothing():
    data = _read("pa_hb2625_pn3586.odt")
    text = handle_pennsylvania_odt(data, {})
    assert text == handle_pennsylvania_odt(_without_strike_styles(data), {})
    assert len(text.split()) == 463
    assert "§ 9301. Definitions." in text  # this printing still makes the 9301 change


def test_bracketed_existing_law_and_struck_words_in_hb2450():
    text = _text("pa_hb2450_pn3371.odt")
    for amount in ("[$650,000]", "[$500]", "[$1,000]", "[$2,500]", "[$4,500]"):
        assert amount in text
    kept = handle_pennsylvania_odt(
        _without_strike_styles(_read("pa_hb2450_pn3371.odt")), {}
    )
    assert "(b)(1) and (d)" in kept
    assert "(b)(1) and (d)" not in text


def test_change_bar_drawings_do_not_cut_a_paragraph_short():
    """Each change bar is a draw:frame with an empty paragraph inside, placed within the real
    paragraph; reading up to the inner paragraph's end lost the rest of the sentence."""
    lines = _text("pa_hb2625_pn3586.odt").split("\n")
    assert all("<--" not in line for line in lines)
    assert any(
        line.endswith(
            "further providing for definitions and for additional powers of commission."
        )
        for line in lines
    )


def test_one_line_per_paragraph_and_no_empty_lines():
    text = _text("pa_hb2625_pn3751.odt")
    assert "" not in text.split("\n")
    assert "  " not in text


def test_opendocument_is_recognised_by_its_bytes():
    assert is_opendocument_text(_read("pa_hb2625_pn3751.odt"))
    assert not is_opendocument_text(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    )  # a real OLE .doc
    assert not is_opendocument_text(b"%PDF-1.7")
    other_zip = io.BytesIO()
    with zipfile.ZipFile(other_zip, "w") as z:
        z.writestr("word/document.xml", "<w/>")
    assert not is_opendocument_text(other_zip.getvalue())  # a .docx is a zip too
    assert not is_opendocument_text(b"PK\x03\x04 not really a zip")


def test_pennsylvania_reads_the_doc_link_under_either_label_and_skips_the_pdf():
    for media_type in ("application/msword", ODT_MEDIA_TYPE):
        assert (
            get_extract_func({"jurisdiction_id": PA, "media_type": media_type})
            is handle_pennsylvania_odt
        )
    assert (
        get_extract_func({"jurisdiction_id": PA, "media_type": "application/pdf"})
        is DoNotDownload
    )
    assert (
        get_extract_func({"jurisdiction_id": PA, "media_type": "text/html"})
        is DoNotDownload
    )


def test_no_other_state_uses_the_pennsylvania_reader():
    for state, funcs in CONVERSION_FUNCTIONS.items():
        if state == "pa":
            continue
        assert ODT_MEDIA_TYPE not in funcs, state
        assert handle_pennsylvania_odt not in funcs.values(), state


def test_margin_notes_in_drawings_are_left_out():
    """HB 165 PN617 is a corrective reprint: its "◄Corrective Reprint" margin notes are text boxes
    in draw:frame drawings anchored mid-word, which landed inside the title ("Title ◄Corrective
    ReprintCorrective Reprint18 (Crimes ...") until drawings were removed."""
    lines = _text("pa_hb165_pn0617.odt").split("\n")
    assert (
        "Amending Title 18 (Crimes and Offenses) of the Pennsylvania Consolidated Statutes, in "
        "abortion, further providing for medical consultation and judgment." in lines
    )
    assert not any("◄" in line for line in lines)
    assert (
        lines[0] == "CORRECTIVE REPRINT"
    )  # the printing's own heading is real text and stays


_ODF_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
)


def _style(
    name: str, line_through: str = None, parent: str = None, family: str = None
) -> str:
    """A style; the family defaults from the name's first letter (P paragraph, else text)."""
    family = family or ("paragraph" if name.startswith("P") else "text")
    parent_attr = f' style:parent-style-name="{parent}"' if parent else ""
    props = (
        f'<style:text-properties style:text-line-through-style="{line_through}"/>'
        if line_through
        else ""
    )
    return f'<style:style style:name="{name}" style:family="{family}"{parent_attr}>{props}</style:style>'


def _odt(body: str, styles: str = "", shared_styles: str = "") -> bytes:
    """A minimal OpenDocument text file: `styles` go in content.xml, `shared_styles` in styles.xml."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", ODT_MEDIA_TYPE)
        z.writestr(
            "content.xml",
            f"<office:document-content {_ODF_NS}>"
            f"<office:automatic-styles>{styles}</office:automatic-styles>"
            f"<office:body><office:text>{body}</office:text></office:body></office:document-content>",
        )
        z.writestr(
            "styles.xml",
            f"<office:document-styles {_ODF_NS}><office:styles>{shared_styles}</office:styles>"
            "</office:document-styles>",
        )
    return out.getvalue()


def test_space_tab_and_line_break_elements_separate_words():
    data = _odt(
        '<text:p>one<text:s/>two<text:s text:c="3"/>three<text:tab/>four<text:line-break/>five</text:p>'
    )
    assert is_opendocument_text(data)
    assert handle_pennsylvania_odt(data, {}) == "one two three four five"


def test_new_words_inside_a_struck_paragraph_are_kept():
    """/pm-review, confirmed on a real printing: HB 2534 PN3968 strikes the paragraph "(2)
    Generative artificial intelligence." and inserts "AS FOLLOWS:" inside it, in a span whose
    style says line-through "none". Dropping the struck paragraph whole lost the new words."""
    data = _odt(
        '<text:p text:style-name="P27">(2) Generative artificial intelligence. '
        '<text:span text:style-name="T6">As follows:</text:span></text:p>',
        styles=_style("P27", "solid") + _style("T6", "none"),
    )
    assert handle_pennsylvania_odt(data, {}) == "As follows:"


def test_text_around_a_struck_span_is_kept_in_order():
    data = _odt(
        '<text:p><text:span text:style-name="S">60</text:span> 180 days'
        ' and <text:span text:style-name="S">not</text:span> more</text:p>',
        styles=_style("S", "solid"),
    )
    assert handle_pennsylvania_odt(data, {}) == "180 days and more"


def test_strike_through_inherited_from_a_shared_parent_style():
    data = _odt(
        '<text:p>kept <text:span text:style-name="T1">struck</text:span> kept</text:p>'
        '<text:p text:style-name="P1">a struck paragraph</text:p>',
        styles=_style("T1", parent="Struck") + _style("P1", parent="Struck"),
        shared_styles=_style("Struck", "solid", family="text")
        + _style("Struck", "solid", family="paragraph"),
    )
    assert handle_pennsylvania_odt(data, {}) == "kept kept"


def test_a_paragraph_style_and_a_text_style_with_the_same_name_are_kept_apart():
    """/pm-review round 2: style names are per family. None of 42 real printings reuses a name
    across families, but the format allows it."""
    data = _odt(
        '<text:p text:style-name="X">struck paragraph</text:p>'
        '<text:p>kept <text:span text:style-name="X">kept span</text:span></text:p>',
        styles=_style("X", "solid", family="paragraph")
        + _style("X", "none", family="text"),
    )
    assert handle_pennsylvania_odt(data, {}) == "kept kept span"
