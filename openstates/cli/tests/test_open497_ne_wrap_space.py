"""OPEN-497: Nebraska's cleaner must not report a changed line when two printings differ only by
where a line wrapped after a hyphen, a slash or a closing parenthesis (the space the join adds)."""

from openstates.cli.text_extract import _clean_ne_text, apply_prediff_cleaning


def _clean(prior, raw):
    return apply_prediff_cleaning(
        prior,
        raw,
        jurisdiction_name="Nebraska",
        is_bill=True,
        prior_media_type="application/pdf",
        cur_media_type="application/pdf",
    )


def test_a_wrap_after_a_hyphen_cleans_to_the_unwrapped_text():
    wrapped = "persons who are non-\nParticipating States and speech-\nlanguage pathologists."
    flat = "persons who are non-Participating States and speech-language pathologists."
    assert _clean_ne_text(wrapped) == _clean_ne_text(flat)


def test_a_wrap_after_a_slash_cleans_to_the_unwrapped_text():
    assert _clean_ne_text("an obstetrician/\ngynecologist") == _clean_ne_text(
        "an obstetrician/gynecologist"
    )


def test_a_wrap_between_parenthesised_labels_cleans_to_the_unwrapped_text():
    assert _clean_ne_text("divisions (3)(c)\n(i) and (ii)") == _clean_ne_text(
        "divisions (3)(c)(i) and (ii)"
    )


def test_two_printings_that_differ_only_by_that_space_have_no_changed_line():
    a, b = _clean(
        "the owner-\ngenerator is subject to subdivision (2)\n(a) of this section.",
        "the owner-generator is subject to subdivision (2)(a) of this\nsection.",
    )
    assert a == b


def test_a_real_word_change_next_to_a_wrap_still_shows():
    a, b = _clean(
        "persons who are non-\nParticipating States.",
        "persons who are non-Member States.",
    )
    assert a != b


def test_a_spaced_dash_and_ordinary_spaces_are_left_alone():
    # " - " has a space before the hyphen, and "(a) text" has no "(" after the ")".
    assert _clean_ne_text("one - two (a) three") == "one - two (a) three"


def test_the_rule_is_a_spacing_normalisation_not_a_wrap_detector():
    # Documented on purpose: the PDF text carries these spaces inside a line as well, so the
    # same space is dropped wherever it sits, on both sides of a comparison.
    assert _clean_ne_text("a word- word and a/ b and (a) (1)") == "a word-word and a/b and (a)(1)"
    a, b = _clean("see (2) (a) of this section", "see (2)(a) of this section")
    assert a == b


def test_a_page_number_shaped_token_is_kept_whole():
    assert "-4- of the statute" in _clean_ne_text("Before.\n-4- of the statute\nAfter.")
