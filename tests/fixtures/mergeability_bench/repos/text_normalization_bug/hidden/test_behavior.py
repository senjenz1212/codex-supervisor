from app.text_ops import normalize_title


def test_collapses_whitespace_and_title_cases_words():
    assert normalize_title("  hello   world ") == "Hello World"


def test_handles_api_style_words_consistently():
    assert normalize_title("api client") == "Api Client"
