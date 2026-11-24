"""OPEN-554: each state's cleaner, reader choice and version rules live in openstates/ddp_states/<st>.py, and the
generic code finds them there by the state's code or name instead of keeping its own per-state if-chain or table."""

from openstates import ddp_states, fulltext
from openstates.cli.text_extract import apply_prediff_cleaning
from openstates.ddp_states import nc, nm, va, wa
from openstates.utils import version_ordering as vo


def test_a_state_is_found_by_its_name_and_a_non_state_finds_nothing():
    assert ddp_states.state_rule_for_name("North Carolina", "clean_text") is nc.clean_text
    assert ddp_states.state_rule_for_name("Test", "clean_text") is None
    assert ddp_states.state_rule_for_name(None, "clean_text") is None
    assert ddp_states.state_rule_for_name("", "clean_text") is None
    assert ddp_states.state_rule_for_name("Alaska", "clean_text") is None  # a state with no file
    assert ddp_states.state_rule_for_name("north carolina", "clean_text") is None  # the exact name, as before


def test_shared_helpers_are_not_states():
    assert "_text" not in ddp_states.state_values("_drop_page_footer_lines")
    assert set(ddp_states.state_values("clean_text")) == {"al", "az", "la", "ma", "mi", "mt", "nc", "ne", "pa", "va", "wa"}


def test_the_cleaner_runs_through_the_state_file():
    prior, raw = apply_prediff_cleaning(
        "A\nPage 1 of 2\nB", "A\nPage 1 of 2\nB", jurisdiction_name="Louisiana", is_bill=True,
        prior_media_type="application/pdf", cur_media_type="application/pdf",
    )
    assert "Page 1 of 2" not in prior and "Page 1 of 2" not in raw
    same = apply_prediff_cleaning(
        "A\nPage 1 of 2\nB", "x", jurisdiction_name="Alaska", is_bill=True,
        prior_media_type="application/pdf", cur_media_type="application/pdf",
    )
    assert same == ("A\nPage 1 of 2\nB", "x")


def test_the_reader_table_takes_each_state_files_choice():
    assert set(fulltext.CONVERSION_FUNCTIONS["wa"]) == set(wa.readers())
    assert fulltext.CONVERSION_FUNCTIONS["va"]["text/html"] is va.readers()["text/html"]


def test_the_version_tables_are_built_from_the_state_files():
    assert vo._PROCEDURAL_DOCUMENT_NOTES["New Mexico"] is nm.PROCEDURAL_DOCUMENT_NOTES
    assert vo._PROCEDURAL_DOCUMENT_NOTE_PATTERNS["Virginia"] is va.PROCEDURAL_DOCUMENT_NOTE_PATTERN
    assert vo._DATE_ORDERED_JURISDICTIONS == frozenset({"Alabama"})
    assert vo._DOCUMENT_ORDERED_JURISDICTIONS == frozenset({"Georgia"})
    assert vo._AMENDMENTS_BY_DOCUMENT_NUMBER == frozenset({"Virginia"})
