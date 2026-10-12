from morning_edition.textutil import clean_text, no_em_dash


def test_clean_text_strips_tags_and_entities():
    assert clean_text("<p>Fish &amp; <b>chips</b></p>") == "Fish & chips"


def test_clean_text_truncates_on_word_boundary():
    out = clean_text("alpha beta gamma delta", limit=12)
    assert out == "alpha beta..."


def test_no_em_dash_replaces_with_comma():
    assert no_em_dash("Harbor\u2014then the wall") == "Harbor, then the wall"
    assert no_em_dash("a \u2014 b") == "a, b"
