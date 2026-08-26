from datetime import datetime, timezone

from repo_radar.contribution import (
    MAX_ISSUE_CANDIDATES,
    MAX_SOURCE_REPOSITORIES,
    REPOSITORY_BATCH_SIZE,
    build_issue_queries,
    collect_issue_candidates,
    generate_contribution_recommendations,
    select_source_repositories,
    source_labels,
)
from repo_radar.github_client import GitHubError
from repo_radar.models import ImportedProfile, ImportedRepository, Issue, PreferenceProfile, Repository

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROFILE = PreferenceProfile(
    languages={"Python": 1.0},
    topics={"backend": 1.0, "cli": 0.6},
    keywords={"retry": 1.0, "postgresql": 0.9},
)


def _repository(full_name: str, **overrides) -> Repository:
    """
    build a source repository with useful defaults
    :param full_name: repository full name
    :param overrides: repository field overrides
    :returns: source repository
    """
    owner = full_name.split("/", maxsplit=1)[0]
    fields = {
        "description": "backend service",
        "language": "Python",
        "topics": ["backend"],
        "stars": 100,
        "owner": owner,
        "pushed_at": "2025-12-01T00:00:00Z",
    }
    fields.update(overrides)
    return Repository(full_name, **fields)


def _issue(repository: str, number: int, **overrides) -> Issue:
    """
    build an issue candidate with useful defaults
    :param repository: repository full name
    :param number: issue number
    :param overrides: issue field overrides
    :returns: issue candidate
    """
    fields = {
        "title": "Improve retry handling",
        "url": f"https://github.com/{repository}/issues/{number}",
        "updated_at": "2025-12-28T00:00:00Z",
    }
    fields.update(overrides)
    return Issue(repository=repository, number=number, **fields)


class FakeIssueClient:
    """deterministic GitHub issue search replacement"""

    def __init__(self, results: list[list[Issue]] | None = None, failure_after: int | None = None) -> None:
        """
        initialize issue search behavior
        :param results: issues returned for each successive query
        :param failure_after: number of successful queries before raising
        :returns: nothing
        """
        self.results = results or []
        self.failure_after = failure_after
        self.queries: list[str] = []
        self.limits: list[int] = []

    def get_authenticated_user(self) -> str:
        """
        return the mocked authenticated login
        :returns: authenticated login
        """
        return "example"

    def search_issues(self, query: str, limit: int = 50) -> list[Issue]:
        """
        return the prepared issues for one query
        :param query: generated issue search query
        :param limit: requested result limit
        :returns: mocked issue candidates
        """
        if self.failure_after is not None and len(self.queries) >= self.failure_after:
            raise GitHubError("GitHub API rate limit exceeded. Reset timestamp: 1")
        index = len(self.queries)
        self.queries.append(query)
        self.limits.append(limit)
        return self.results[index] if index < len(self.results) else []


def test_saved_repositories_are_preferred_over_starred_ones() -> None:
    """
    explicit saves outrank the star cache regardless of relevance
    :returns: nothing
    """
    saved = [_repository("saved/plain", topics=[], language="Go", description="unrelated")]
    starred = [_repository("starred/relevant")]
    selected = select_source_repositories(saved, starred, PROFILE, now=NOW)
    assert [repository.full_name for repository in selected] == ["saved/plain", "starred/relevant"]


def test_source_repositories_are_ordered_by_relevance_within_a_source() -> None:
    """
    the most relevant starred repositories are searched first
    :returns: nothing
    """
    starred = [
        _repository("starred/unrelated", language="Ruby", topics=["recipes"], description="recipes"),
        _repository("starred/relevant"),
    ]
    selected = select_source_repositories([], starred, PROFILE, now=NOW)
    assert [repository.full_name for repository in selected] == ["starred/relevant", "starred/unrelated"]


def test_source_selection_excludes_ineligible_repositories() -> None:
    """
    archived, owned, imported, rejected, and duplicate repositories are never searched
    :returns: nothing
    """
    starred = [
        _repository("good/repo"),
        _repository("old/archive", archived=True),
        _repository("example/mine"),
        _repository("portfolio/project"),
        _repository("no/thanks"),
        _repository("never/again"),
        _repository("Good/Repo"),
    ]
    selected = select_source_repositories(
        [],
        starred,
        PROFILE,
        "EXAMPLE",
        {"no/thanks": "not interested", "never/again": "blocked"},
        {"portfolio"},
        now=NOW,
    )
    assert [repository.full_name for repository in selected] == ["good/repo"]


def test_source_selection_keeps_starred_and_saved_feedback() -> None:
    """
    the interested and starred classifications this feature depends on are never excluded
    :returns: nothing
    """
    saved = [_repository("saved/tool")]
    starred = [_repository("starred/tool")]
    feedback = {"saved/tool": "interested", "starred/tool": "starred"}
    selected = select_source_repositories(saved, starred, PROFILE, "example", feedback, now=NOW)
    assert [repository.full_name for repository in selected] == ["saved/tool", "starred/tool"]


def test_source_selection_is_bounded() -> None:
    """
    a large star library still produces a bounded source set
    :returns: nothing
    """
    starred = [_repository(f"owner/repository-{index}", stars=index) for index in range(60)]
    selected = select_source_repositories([], starred, PROFILE, now=NOW)
    assert len(selected) == MAX_SOURCE_REPOSITORIES


def test_issue_queries_are_grouped_open_issue_searches() -> None:
    """
    repositories are batched into grouped open issue queries
    :returns: nothing
    """
    repositories = [_repository(f"owner/repository-{index}") for index in range(MAX_SOURCE_REPOSITORIES)]
    queries = build_issue_queries(repositories)
    assert len(queries) == MAX_SOURCE_REPOSITORIES // REPOSITORY_BATCH_SIZE
    assert queries[0].startswith("is:issue is:open archived:false (")
    assert queries[0].count("repo:") == REPOSITORY_BATCH_SIZE
    assert all(len(query) <= 256 for query in queries)


def test_issue_candidates_are_deduplicated_and_bounded() -> None:
    """
    repeated search results collapse and the combined candidate set stays bounded
    :returns: nothing
    """
    repositories = [_repository(f"owner/repository-{index}") for index in range(MAX_SOURCE_REPOSITORIES)]
    flood = [_issue("owner/repository-0", number) for number in range(200)]
    client = FakeIssueClient([flood, [_issue("owner/repository-0", 0)]])
    issues, warning = collect_issue_candidates(client, repositories)
    assert warning is None
    assert len(issues) == MAX_ISSUE_CANDIDATES
    assert len({(issue.repository, issue.number) for issue in issues}) == MAX_ISSUE_CANDIDATES
    assert len(client.queries) == 1


def test_issue_search_failure_returns_partial_results_with_a_warning() -> None:
    """
    a rate limited second query keeps the first batch and reports the degradation
    :returns: nothing
    """
    repositories = [_repository(f"owner/repository-{index}") for index in range(MAX_SOURCE_REPOSITORIES)]
    client = FakeIssueClient([[_issue("owner/repository-0", 1)]], failure_after=1)
    issues, warning = collect_issue_candidates(client, repositories)
    assert [issue.number for issue in issues] == [1]
    assert warning is not None
    assert "rate limit" in warning
    assert len(client.queries) == 1


def test_immediate_issue_search_failure_reports_no_results() -> None:
    """
    a failure on the first query degrades to an empty warned result rather than an exception
    :returns: nothing
    """
    client = FakeIssueClient(failure_after=0)
    issues, warning = collect_issue_candidates(client, [_repository("owner/repository")])
    assert issues == []
    assert warning is not None


def test_recommendations_without_local_repositories_never_contact_github() -> None:
    """
    an empty saved and starred state performs no issue search
    :returns: nothing
    """
    client = FakeIssueClient()
    recommendations, warning = generate_contribution_recommendations(client, PROFILE, [], [], now=NOW)
    assert recommendations == []
    assert warning is None
    assert client.queries == []


def test_unassigned_only_filters_assigned_issues() -> None:
    """
    the optional filter removes assigned issues before ranking
    :returns: nothing
    """
    starred = [_repository("owner/repository")]
    issues = [
        _issue("owner/repository", 1, assignee_count=2),
        _issue("owner/repository", 2),
    ]
    client = FakeIssueClient([issues])
    kept, _ = generate_contribution_recommendations(client, PROFILE, [], starred, now=NOW)
    filtered, _ = generate_contribution_recommendations(
        FakeIssueClient([issues]), PROFILE, [], starred, unassigned_only=True, now=NOW
    )
    assert {item.issue.number for item in kept} == {1, 2}
    assert [item.issue.number for item in filtered] == [2]


def test_recommendations_exclude_the_imported_profile_owner() -> None:
    """
    repositories owned by the imported portfolio are not contribution targets
    :returns: nothing
    """
    starred = [_repository("portfolio/project"), _repository("owner/repository")]
    client = FakeIssueClient([[_issue("owner/repository", 1)]])
    imported = ImportedProfile("portfolio", repositories=[ImportedRepository("project")])
    recommendations, _ = generate_contribution_recommendations(
        client, PROFILE, [], starred, "example", {}, 10, imported, now=NOW
    )
    assert [item.issue.repository for item in recommendations] == ["owner/repository"]
    assert "portfolio/project" not in client.queries[0]


def test_recommendation_pipeline_is_deterministic() -> None:
    """
    repeating a run over the same inputs produces the same ordered results
    :returns: nothing
    """
    starred = [_repository("owner/one"), _repository("owner/two")]
    issues = [
        _issue("owner/one", 3, title="Improve postgresql retry handling"),
        _issue("owner/two", 1, labels=["help wanted"]),
        _issue("owner/one", 1),
    ]
    first, _ = generate_contribution_recommendations(FakeIssueClient([issues]), PROFILE, [], starred, now=NOW)
    second, _ = generate_contribution_recommendations(
        FakeIssueClient([list(reversed(issues))]), PROFILE, [], starred, now=NOW
    )
    identity = [(item.issue.repository, item.issue.number) for item in first]
    assert identity == [(item.issue.repository, item.issue.number) for item in second]
    assert identity[0] == ("owner/one", 3)


def test_source_labels_prefer_the_saved_classification() -> None:
    """
    a repository that is both saved and starred reports the explicit save
    :returns: nothing
    """
    labels = source_labels([_repository("owner/both")], [_repository("owner/both"), _repository("owner/starred")])
    assert labels == {"owner/both": "saved", "owner/starred": "starred"}
