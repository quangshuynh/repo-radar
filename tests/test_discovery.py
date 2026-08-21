from repo_radar.discovery import deduplicate_candidates, discover_candidates, filter_candidates
from repo_radar.models import PreferenceProfile, Repository


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
