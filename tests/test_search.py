import pytest

from repo_radar.search import DEFAULT_LANGUAGE, parse_search_query


def test_missing_language_defaults_to_python() -> None:
    """
    a query without a language keeps every word topical and defaults to Python
    :returns: nothing
    """
    parsed = parse_search_query("developer productivity")
    assert parsed.language == DEFAULT_LANGUAGE == "Python"
    assert parsed.explicit_language is False
    assert parsed.terms == ("developer", "productivity")


def test_empty_query_defaults_to_python_without_terms() -> None:
    """
    an empty query carries the default language and no topical terms
    :returns: nothing
    """
    parsed = parse_search_query("   ")
    assert parsed.language == "Python"
    assert parsed.terms == ()
    assert parsed.explicit_language is False


@pytest.mark.parametrize(
    ("query", "language"),
    [
        ("python", "Python"),
        ("javascript", "JavaScript"),
        ("java", "Java"),
        ("typescript", "TypeScript"),
        ("rust", "Rust"),
        ("go", "Go"),
        ("golang", "Go"),
        ("C#", "C#"),
        ("cpp", "C++"),
        ("objective-c", "Objective-C"),
        ("jupyter", "Jupyter Notebook"),
        ("r", "R"),
    ],
)
def test_explicit_language_is_recognized(query: str, language: str) -> None:
    """
    an explicitly typed language name resolves to its canonical GitHub language
    :param query: raw search query
    :param language: expected canonical GitHub language
    :returns: nothing
    """
    parsed = parse_search_query(query)
    assert parsed.language == language
    assert parsed.explicit_language is True
    assert parsed.terms == ()


def test_java_is_not_confused_with_javascript() -> None:
    """
    the shared prefix between java and javascript never collapses the two languages
    :returns: nothing
    """
    assert parse_search_query("java").language == "Java"
    assert parse_search_query("javascript").language == "JavaScript"


@pytest.mark.parametrize(
    ("query", "language", "terms"),
    [
        ("javascript testing", "JavaScript", ("testing",)),
        ("python automation", "Python", ("automation",)),
        ("typescript developer tools", "TypeScript", ("developer", "tools")),
        ("rust cli", "Rust", ("cli",)),
        ("go api", "Go", ("api",)),
        ("testing frameworks for ruby", "Ruby", ("testing", "frameworks", "for")),
    ],
)
def test_language_is_separated_from_the_topical_query(query: str, language: str, terms: tuple[str, ...]) -> None:
    """
    the language constraint is removed from the terms used for topical search
    :param query: raw search query
    :param language: expected canonical GitHub language
    :param terms: expected remaining topical terms
    :returns: nothing
    """
    parsed = parse_search_query(query)
    assert parsed.language == language
    assert parsed.terms == terms
    assert parsed.explicit_language is True


def test_ambiguous_language_words_only_count_in_the_leading_position() -> None:
    """
    everyday words that are also language names stay topical away from the leading token
    :returns: nothing
    """
    trailing = parse_search_query("libraries that go fast")
    leading = parse_search_query("go concurrency")
    assert trailing.language == "Python"
    assert trailing.explicit_language is False
    assert "go" in trailing.terms
    assert leading.language == "Go"


def test_the_first_named_language_wins_deterministically() -> None:
    """
    a query naming two languages resolves to the first one every time
    :returns: nothing
    """
    parsed = parse_search_query("python javascript interop")
    assert parsed.language == "Python"
    assert parsed.terms == ("javascript", "interop")


def test_terms_cannot_inject_extra_github_qualifiers() -> None:
    """
    tokenization drops the separators a user would need to forge a search qualifier
    :returns: nothing
    """
    parsed = parse_search_query("rust stars:>50000 language:Python")
    assert parsed.language == "Rust"
    assert not any(":" in term for term in parsed.terms)


def test_parsing_is_case_insensitive() -> None:
    """
    capitalization does not change the resolved language or terms
    :returns: nothing
    """
    assert parse_search_query("JavaScript Testing") == parse_search_query("javascript testing")
