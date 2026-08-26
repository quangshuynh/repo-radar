import pytest

from repo_radar.discovery import (
    build_search_queries,
    deduplicate_candidates,
    discover_candidates,
    filter_by_primary_language,
    filter_candidates,
    generate_recommendations,
)
from repo_radar.models import PreferenceProfile, Repository
from repo_radar.search import parse_search_query

# a mixed language pool including a Python repository that merely talks about JavaScript
POOL = [
    Repository("py/runner", "python task runner", "Python", ["python", "cli"], 900, owner="py"),
    Repository("py/bots", "python automation bots", "Python", ["automation"], 400, owner="py"),
    Repository("js/jest", "javascript testing framework", "JavaScript", ["testing"], 800, owner="js"),
    Repository("js/bundle", "javascript build tooling", "JavaScript", ["build"], 300, owner="js"),
    Repository("java/maven", "java build tool", "Java", ["build"], 700, owner="java"),
    Repository("ts/devkit", "typescript developer tools", "TypeScript", ["developer-tools"], 600, owner="ts"),
    Repository("rs/ripgrep", "rust cli search", "Rust", ["cli"], 500, owner="rs"),
    Repository("go/gin", "go api framework", "Go", ["api"], 550, owner="go"),
    # decoy: the word javascript appears in the name and description but the top language is Python
    Repository("decoy/javascript-helpers", "javascript helpers written in python", "Python", [], 950, owner="decoy"),
]


class LenientSearchClient:
    """
    GitHub search stand in that is deliberately looser than the language qualifier promises

    It answers a ``language:`` query with primary language matches plus repositories that
    merely mention the language in their name or description, which is the behavior
    ``filter_by_primary_language`` has to defend against.
    """

    def __init__(self, pool: list[Repository] | None = None) -> None:
        """
        initialize the fake search backend
        :param pool: repositories the fake backend can return
        :returns: nothing
        """
        self.pool = list(POOL if pool is None else pool)
        self.queries: list[str] = []

    def search_repositories(self, query: str, limit: int) -> list[Repository]:
        """
        return pool repositories loosely matching one generated query
        :param query: generated search query
        :param limit: requested result limit
        :returns: fake search results
        """
        self.queries.append(query)
        requested = ""
        for part in query.split():
            if part.startswith("language:"):
                requested = part.split(":", maxsplit=1)[1].strip('"').casefold()
        matches = [
            repository
            for repository in self.pool
            if not requested
            or (repository.language or "").casefold() == requested
            or requested in f"{repository.full_name} {repository.description or ''}".casefold()
        ]
        return matches[:limit]


def python_profile() -> PreferenceProfile:
    """
    build a strongly Python oriented profile
    :returns: preference profile dominated by Python signals
    """
    return PreferenceProfile(
        languages={"Python": 1.0},
        topics={"python": 1.0, "cli": 0.5, "automation": 0.4},
        keywords={"python": 1.0, "task": 0.6},
        median_stars=500.0,
    )


def recommend(query: str) -> list[str]:
    """
    run the full search pipeline against the fake backend
    :param query: raw user search query
    :returns: recommended repository names in ranked order
    """
    client = LenientSearchClient()
    recommendations = generate_recommendations(
        client, python_profile(), [], "me", {}, 20, None, parse_search_query(query)
    )
    return [recommendation.repository.full_name for recommendation in recommendations]


def languages_of(names: list[str]) -> set[str]:
    """
    collect the top languages of pool repositories by name
    :param names: repository full names
    :returns: distinct top languages
    """
    return {repository.language for repository in POOL if repository.full_name in names}


class FakeClient:
    """deterministic GitHub search replacement"""

    def __init__(self) -> None:
        """
        initialize request tracking
        :returns: nothing
        """
        self.queries: list[str] = []

    def search_repositories(self, query: str, limit: int) -> list[Repository]:
        """
        return one repeated fake repository
        :param query: generated search query
        :param limit: requested result limit
        :returns: fake search results
        """
        self.queries.append(query)
        return [Repository("new/repo", owner="new")]


def test_deduplicate_candidates_is_case_insensitive() -> None:
    """
    duplicate candidate names collapse while preserving order
    :returns: nothing
    """
    first = Repository("Owner/Repo")
    assert deduplicate_candidates([first, Repository("owner/repo")]) == [first]


def test_filter_candidates_excludes_ineligible_results() -> None:
    """
    candidate filtering applies ownership archive starred and feedback rules
    :returns: nothing
    """
    candidates = [
        Repository("good/repo", owner="good"),
        Repository("me/mine", owner="me"),
        Repository("old/archive", archived=True, owner="old"),
        Repository("seen/star", owner="seen"),
        Repository("no/thanks", owner="no"),
        Repository("saved/later", owner="saved"),
    ]
    result = filter_candidates(
        candidates,
        {"seen/star"},
        "ME",
        {"no/thanks": "not interested", "saved/later": "interested"},
    )
    assert [item.full_name for item in result] == ["good/repo"]


def test_filter_candidates_excludes_imported_owner() -> None:
    """
    an imported profile owner is excluded before token identity is available
    :returns: nothing
    """
    candidates = [Repository("portfolio/project", owner="portfolio"), Repository("other/repo", owner="other")]
    result = filter_candidates(candidates, set(), "token-user", {}, {"portfolio"})
    assert [item.full_name for item in result] == ["other/repo"]


def test_discovery_uses_multiple_queries_and_deduplicates() -> None:
    """
    discovery combines focused API searches without duplicates
    :returns: nothing
    """
    client = FakeClient()
    profile = PreferenceProfile(languages={"Python": 1.0}, topics={"automation": 1.0})
    assert len(discover_candidates(client, profile)) == 1
    assert len(client.queries) >= 2


def test_profile_discovery_remains_for_the_command_line_path() -> None:
    """
    the command line recommend path passes no search and keeps profile driven generation
    :returns: nothing
    """
    profile = python_profile()
    assert build_search_queries(profile) == [
        "language:Python topic:python archived:false",
        "language:Python stars:10..50000 archived:false",
        "topic:python stars:5..50000 archived:false",
        "topic:cli stars:5..50000 archived:false",
        "topic:automation stars:5..50000 archived:false",
    ]


@pytest.mark.parametrize(
    ("query", "language"),
    [
        ("", "Python"),
        ("automation", "Python"),
        ("python", "Python"),
        ("javascript", "JavaScript"),
        ("java", "Java"),
        ("typescript developer tools", "TypeScript"),
    ],
)
def test_every_generated_query_constrains_the_requested_language(query: str, language: str) -> None:
    """
    a search query constrains every generated GitHub search to one primary language
    :param query: raw user search query
    :param language: expected canonical GitHub language
    :returns: nothing
    """
    queries = build_search_queries(python_profile(), parse_search_query(query))
    assert queries
    assert all(f"language:{language}" in generated for generated in queries)


def test_topical_terms_reach_the_github_query_without_the_language_word() -> None:
    """
    the remaining topical terms drive a search alongside the language qualifier
    :returns: nothing
    """
    queries = build_search_queries(python_profile(), parse_search_query("typescript developer tools"))
    assert queries[0] == "developer tools language:TypeScript archived:false"
    # the only free text any generated query carries is the topical remainder
    assert {generated.split("language:")[0].strip() for generated in queries} == {"", "developer tools"}


def test_language_names_github_cannot_read_bare_are_quoted() -> None:
    """
    multi word and punctuated language names stay intact in the generated qualifier
    :returns: nothing
    """
    assert all(
        'language:"Jupyter Notebook"' in generated
        for generated in build_search_queries(python_profile(), parse_search_query("jupyter"))
    )
    assert all(
        'language:"C#"' in generated
        for generated in build_search_queries(python_profile(), parse_search_query("c# game engine"))
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", "py/runner"),
        ("automation", "py/bots"),
        ("python", "py/runner"),
        ("javascript testing", "js/jest"),
        ("java", "java/maven"),
        ("typescript developer tools", "ts/devkit"),
        ("rust cli", "rs/ripgrep"),
        ("go api", "go/gin"),
    ],
)
def test_search_returns_repositories_in_the_requested_language(query: str, expected: str) -> None:
    """
    each searched language survives candidate generation, filtering, and ranking
    :param query: raw user search query
    :param expected: repository expected in the results
    :returns: nothing
    """
    results = recommend(query)
    assert expected in results


def test_explicit_language_overrides_a_python_preference() -> None:
    """
    a Python dominated profile never suppresses an explicit non Python search
    :returns: nothing
    """
    results = recommend("javascript")
    assert set(results) == {"js/jest", "js/bundle"}
    assert languages_of(results) == {"JavaScript"}


@pytest.mark.parametrize("query", ["", "   ", "automation", "developer productivity"])
def test_a_search_without_a_language_yields_only_python(query: str) -> None:
    """
    an empty or purely topical search constrains candidates to primary language Python
    :param query: raw user search query
    :returns: nothing
    """
    results = recommend(query)
    assert results
    assert languages_of(results) == {"Python"}


def test_profile_languages_cannot_override_the_python_default() -> None:
    """
    a Rust dominated profile still gets Python candidates when no language is requested
    :returns: nothing
    """
    rust_profile = PreferenceProfile(languages={"Rust": 1.0}, topics={"cli": 1.0}, keywords={"search": 1.0})
    client = LenientSearchClient()
    recommendations = generate_recommendations(client, rust_profile, [], "me", {}, 20, None, parse_search_query(""))
    assert recommendations
    assert {item.repository.language for item in recommendations} == {"Python"}
    assert all("language:Python" in query for query in client.queries)


def test_profile_languages_cannot_override_an_explicit_language() -> None:
    """
    a Rust dominated profile never redirects an explicit JavaScript search
    :returns: nothing
    """
    rust_profile = PreferenceProfile(languages={"Rust": 1.0}, topics={"cli": 1.0}, keywords={"search": 1.0})
    recommendations = generate_recommendations(
        LenientSearchClient(), rust_profile, [], "me", {}, 20, None, parse_search_query("javascript")
    )
    assert {item.repository.language for item in recommendations} == {"JavaScript"}


def test_personalization_still_orders_eligible_candidates() -> None:
    """
    the profile reorders same language candidates without changing which ones qualify
    :returns: nothing
    """
    testing_profile = PreferenceProfile(languages={"Python": 1.0}, topics={"testing": 1.0}, keywords={"testing": 1.0})
    building_profile = PreferenceProfile(languages={"Python": 1.0}, topics={"build": 1.0}, keywords={"build": 1.0})
    pool = [
        Repository("py/pytest", "python testing framework", "Python", ["testing"], 500, owner="a"),
        Repository("py/builder", "python build framework", "Python", ["build"], 500, owner="b"),
    ]

    def ranked(profile: PreferenceProfile) -> list[str]:
        """
        rank the shared pool under one profile
        :param profile: preference profile used for ranking
        :returns: recommended repository names in ranked order
        """
        recommendations = generate_recommendations(
            LenientSearchClient(pool), profile, [], "me", {}, 20, None, parse_search_query("")
        )
        return [item.repository.full_name for item in recommendations]

    assert ranked(testing_profile) == ["py/pytest", "py/builder"]
    assert ranked(building_profile) == ["py/builder", "py/pytest"]


def test_language_match_ignores_repository_names_and_descriptions() -> None:
    """
    a Python repository mentioning JavaScript never qualifies as a JavaScript result
    :returns: nothing
    """
    javascript_results = recommend("javascript")
    assert "decoy/javascript-helpers" not in javascript_results
    assert "decoy/javascript-helpers" in recommend("python")


def test_filter_by_primary_language_uses_the_top_language_only() -> None:
    """
    primary language filtering is case insensitive and ignores missing languages
    :returns: nothing
    """
    candidates = [
        Repository("a/one", language="JavaScript"),
        Repository("b/two", "a javascript helper", language="Python"),
        Repository("c/three", language=None),
    ]
    assert [item.full_name for item in filter_by_primary_language(candidates, "javascript")] == ["a/one"]
    assert [item.full_name for item in filter_by_primary_language(candidates, "Python")] == ["b/two"]
