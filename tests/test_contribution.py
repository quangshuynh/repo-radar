from datetime import datetime, timezone

import pytest

from repo_radar.contribution import (
    CONTRIBUTION_SCOPES,
    DEFAULT_SCOPE,
    MAX_DISCOVERY_QUERIES,
    MAX_ISSUE_CANDIDATES,
    MAX_REPOSITORY_HYDRATIONS,
    MAX_SOURCE_REPOSITORIES,
    QUERY_CHARACTER_LIMIT,
    REPOSITORY_BATCH_SIZE,
    SCOPE_DISCOVER,
    SCOPE_SAVED_STARRED,
    SEARCH_REQUEST_BUDGET,
    build_discovery_queries,
    build_issue_queries,
    collect_issue_candidates,
    discovery_terms,
    generate_contribution_recommendations,
    hydrate_repositories,
    hydration_targets,
    normalize_candidates,
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


class FakeDiscoveryClient(FakeIssueClient):
    """deterministic issue search and repository hydration replacement"""

    def __init__(
        self,
        results: list[list[Issue]] | None = None,
        failure_after: int | None = None,
        repositories: dict[str, Repository] | None = None,
        lookup_errors: dict[str, GitHubError] | None = None,
    ) -> None:
        """
        initialize issue search and repository lookup behavior
        :param results: issues returned for each successive query
        :param failure_after: number of successful queries before raising
        :param repositories: explicit repository metadata by full name
        :param lookup_errors: failures raised for specific repository lookups
        :returns: nothing
        """
        super().__init__(results, failure_after)
        self.repositories = repositories or {}
        self.lookup_errors = lookup_errors or {}
        self.lookups: list[str] = []

    def get_repository(self, full_name: str) -> Repository:
        """
        return prepared metadata for one hydrated repository
        :param full_name: repository full name
        :returns: repository metadata
        """
        self.lookups.append(full_name)
        error = self.lookup_errors.get(full_name)
        if error is not None:
            raise error
        return self.repositories.get(full_name) or _repository(full_name)


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
    the narrower scope with an empty saved and starred state performs no issue search
    :returns: nothing
    """
    client = FakeIssueClient()
    recommendations, warning = generate_contribution_recommendations(
        client, PROFILE, [], [], scope=SCOPE_SAVED_STARRED, now=NOW
    )
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
    kept, _ = generate_contribution_recommendations(client, PROFILE, [], starred, scope=SCOPE_SAVED_STARRED, now=NOW)
    filtered, _ = generate_contribution_recommendations(
        FakeIssueClient([issues]), PROFILE, [], starred, unassigned_only=True, scope=SCOPE_SAVED_STARRED, now=NOW
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
        client, PROFILE, [], starred, "example", {}, 10, imported, scope=SCOPE_SAVED_STARRED, now=NOW
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
    first, _ = generate_contribution_recommendations(
        FakeIssueClient([issues]), PROFILE, [], starred, scope=SCOPE_SAVED_STARRED, now=NOW
    )
    second, _ = generate_contribution_recommendations(
        FakeIssueClient([list(reversed(issues))]), PROFILE, [], starred, scope=SCOPE_SAVED_STARRED, now=NOW
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


# ---------------------------------------------------------------------------
# default discovery scope
# ---------------------------------------------------------------------------


def test_discovery_is_the_default_scope() -> None:
    """
    an omitted scope discovers opportunities instead of reading saved and starred state
    :returns: nothing
    """
    assert DEFAULT_SCOPE == SCOPE_DISCOVER
    client = FakeDiscoveryClient([[_issue("unknown/project", 1)]])
    recommendations, _ = generate_contribution_recommendations(client, PROFILE, [], [], now=NOW)
    assert [item.issue.repository for item in recommendations] == ["unknown/project"]
    assert client.queries
    assert all("repo:" not in query for query in client.queries)


def test_discovery_queries_are_deterministic_and_bounded() -> None:
    """
    the same profile always produces the same bounded searches within GitHub's query limit
    :returns: nothing
    """
    profile = PreferenceProfile(
        languages={"Python": 1.0, "Rust": 0.8, "Go": 0.4},
        topics={"backend": 1.0, "cli": 0.9, "api": 0.8, "async": 0.7, "parsing": 0.6},
        keywords={"postgresql": 1.0, "retry": 0.5},
    )
    queries = build_discovery_queries(profile)
    assert queries == build_discovery_queries(profile)
    assert len(queries) <= MAX_DISCOVERY_QUERIES
    assert len(queries) == len(set(queries))
    assert all(len(query) <= QUERY_CHARACTER_LIMIT for query in queries)
    assert all(query.startswith("is:issue is:open archived:false") for query in queries)
    # only the two strongest languages are searched, and the third never appears
    assert all('language:"Go"' not in query for query in queries)


def test_discovery_uses_both_a_relevance_and_an_invitation_strategy() -> None:
    """
    discovery is not reduced to beginner labels and is not reduced to free text either
    :returns: nothing
    """
    queries = build_discovery_queries(PROFILE)
    relevance = [query for query in queries if "label:" not in query]
    invitation = [query for query in queries if "label:" in query]
    assert relevance and invitation
    # the label free strategy is emitted first so a reduced budget keeps it
    assert queries.index(relevance[0]) < queries.index(invitation[0])
    assert '"backend"' in relevance[0]
    assert "good first issue" in invitation[0]


def test_discovery_query_terms_are_sanitized() -> None:
    """
    a stored preference cannot break out of its quoted term and restructure the query
    :returns: nothing
    """
    profile = PreferenceProfile(languages={"Python": 1.0}, topics={'evil") OR repo:attacker/pwn ("': 1.0})
    queries = build_discovery_queries(profile)
    assert all("repo:attacker/pwn" not in query for query in queries)
    assert all(query.count('"') % 2 == 0 for query in queries)


def test_discovery_without_profile_signals_never_contacts_github() -> None:
    """
    an empty profile produces no searches at all rather than a GitHub-wide crawl
    :returns: nothing
    """
    client = FakeDiscoveryClient()
    recommendations, warning = generate_contribution_recommendations(client, PreferenceProfile(), [], [], now=NOW)
    assert build_discovery_queries(PreferenceProfile()) == []
    assert recommendations == []
    assert warning is None
    assert client.queries == []
    assert client.lookups == []


def test_discovery_search_and_hydration_requests_are_bounded() -> None:
    """
    a flood of results still costs a bounded number of search and repository requests
    :returns: nothing
    """
    profile = PreferenceProfile(
        languages={"Python": 1.0, "Rust": 0.8},
        topics={"backend": 1.0, "cli": 0.9},
        keywords={"retry": 1.0},
    )
    flood = [[_issue(f"owner/repository-{index}", index) for index in range(80)] for _ in range(4)]
    client = FakeDiscoveryClient(flood)
    generate_contribution_recommendations(client, profile, [], [], now=NOW)
    assert len(client.queries) <= MAX_DISCOVERY_QUERIES
    assert len(client.queries) <= SEARCH_REQUEST_BUDGET[SCOPE_DISCOVER]
    assert len(client.lookups) <= MAX_REPOSITORY_HYDRATIONS
    assert len(client.lookups) == len(set(client.lookups))


def test_hydration_targets_follow_issue_strength_not_search_order() -> None:
    """
    the bounded hydration budget is spent on the repositories owning the strongest issues
    :returns: nothing
    """
    weak = _issue("weak/project", 1, title="Update the changelog", updated_at="2024-01-01T00:00:00Z")
    strong = _issue("strong/project", 2, title="Fix postgresql retry handling", labels=["help wanted"])
    assert hydration_targets([weak, strong], PROFILE, limit=1, now=NOW) == ["strong/project"]


def test_discovery_can_promote_a_repository_the_user_has_never_seen() -> None:
    """
    an unknown repository with a strong issue match outranks a known but irrelevant one
    :returns: nothing
    """
    known = _repository("saved/gallery", description="illustration gallery", language="CSS", topics=["design"])
    unknown = _repository("unknown/service", description="backend service", language="Python", topics=["backend"])
    issues = [
        _issue("saved/gallery", 1, title="Refresh the illustration palette"),
        _issue("unknown/service", 2, title="Add postgresql retry handling coverage"),
    ]
    client = FakeDiscoveryClient([issues], repositories={"saved/gallery": known, "unknown/service": unknown})
    recommendations, _ = generate_contribution_recommendations(client, PROFILE, [known], [], now=NOW)
    assert [item.issue.repository for item in recommendations] == ["unknown/service", "saved/gallery"]
    assert source_labels([known], []).get("unknown/service") is None


def test_saved_status_does_not_by_itself_outrank_relevance() -> None:
    """
    saving a repository adds no ranking credit; identical issues score identically
    :returns: nothing
    """
    saved = _repository("saved/service")
    unknown = _repository("unknown/service")
    issues = [_issue("saved/service", 1), _issue("unknown/service", 1)]
    client = FakeDiscoveryClient([issues], repositories={"saved/service": saved, "unknown/service": unknown})
    recommendations, _ = generate_contribution_recommendations(client, PROFILE, [saved], [], now=NOW)
    assert {item.score for item in recommendations} == {recommendations[0].score}
    assert [item.issue.repository for item in recommendations] == ["saved/service", "unknown/service"]


def test_discovery_drops_pull_requests_closed_issues_and_unknown_repositories() -> None:
    """
    normalization re-applies the client boundary guards for every sourced candidate
    :returns: nothing
    """
    repository = _repository("owner/repository")
    candidates = [
        _issue("owner/repository", 1),
        _issue("owner/repository", 2, is_pull_request=True),
        _issue("owner/repository", 3, state="closed"),
        _issue("owner/repository", 0),
        _issue("nowhere/unknown", 4),
    ]
    kept = normalize_candidates(candidates, {"owner/repository": repository})
    assert [issue.number for issue in kept] == [1]


def test_normalization_drops_archived_repositories_and_collapses_duplicates() -> None:
    """
    duplicate issues across searches collapse and archived repositories never rank
    :returns: nothing
    """
    active = _repository("owner/active")
    archived = _repository("owner/archived", archived=True)
    repositories = {"owner/active": active, "owner/archived": archived}
    candidates = [
        _issue("owner/active", 1),
        _issue("Owner/Active", 1, title="duplicate from a second search"),
        _issue("owner/archived", 2),
    ]
    kept = normalize_candidates(candidates, repositories)
    assert [(issue.repository, issue.number) for issue in kept] == [("owner/active", 1)]


def test_discovery_excludes_owned_and_rejected_repositories_before_hydration() -> None:
    """
    the hydration budget is never spent on repositories the run would drop anyway
    :returns: nothing
    """
    issues = [
        _issue("example/mine", 1),
        _issue("portfolio/project", 2),
        _issue("never/again", 3),
        _issue("good/repository", 4),
    ]
    client = FakeDiscoveryClient([issues])
    imported = ImportedProfile("portfolio", repositories=[ImportedRepository("project")])
    recommendations, _ = generate_contribution_recommendations(
        client, PROFILE, [], [], "example", {"never/again": "blocked"}, 10, imported, now=NOW
    )
    assert client.lookups == ["good/repository"]
    assert [item.issue.repository for item in recommendations] == ["good/repository"]


def test_discovery_is_deterministic_across_result_order() -> None:
    """
    the same candidates produce the same ordering regardless of how GitHub returned them
    :returns: nothing
    """
    issues = [
        _issue("owner/one", 3, title="Improve postgresql retry handling"),
        _issue("owner/two", 1, labels=["help wanted"]),
        _issue("owner/one", 1),
    ]
    first, _ = generate_contribution_recommendations(FakeDiscoveryClient([issues]), PROFILE, [], [], now=NOW)
    second, _ = generate_contribution_recommendations(
        FakeDiscoveryClient([list(reversed(issues))]), PROFILE, [], [], now=NOW
    )
    assert [(item.issue.repository, item.issue.number) for item in first] == [
        (item.issue.repository, item.issue.number) for item in second
    ]
    assert (first[0].issue.repository, first[0].issue.number) == ("owner/one", 3)


def test_discovery_explanations_stay_evidence_backed() -> None:
    """
    a newly discovered repository explains itself with signals that actually scored
    :returns: nothing
    """
    client = FakeDiscoveryClient([[_issue("unknown/service", 1, title="Add postgresql retry handling")]])
    recommendations, _ = generate_contribution_recommendations(client, PROFILE, [], [], now=NOW)
    reasons = recommendations[0].reasons
    assert any("Issue mentions retry, postgresql" in reason for reason in reasons)
    assert any(reason.startswith("unknown/service:") for reason in reasons)
    # newness is presentation metadata, never scoring evidence
    assert not any("new" in reason.lower() for reason in reasons)


def test_hydration_skips_a_missing_repository_but_stops_on_a_rate_limit() -> None:
    """
    one deleted repository costs one candidate; a rate limit stops the remaining budget
    :returns: nothing
    """
    issues = [_issue("gone/project", 1), _issue("owner/repository", 2)]
    skipping = FakeDiscoveryClient([issues], lookup_errors={"gone/project": GitHubError("Not Found", 404)})
    repositories, warning = hydrate_repositories(skipping, issues, PROFILE, now=NOW)
    assert warning is None
    assert set(repositories) == {"owner/repository"}

    limited = FakeDiscoveryClient(
        [issues], lookup_errors={"gone/project": GitHubError("GitHub API rate limit exceeded", 403)}
    )
    repositories, warning = hydrate_repositories(limited, issues, PROFILE, now=NOW)
    assert repositories == {}
    assert warning is not None and "rate limit" in warning
    assert limited.lookups == ["gone/project"]


def test_unsupported_scope_is_rejected() -> None:
    """
    an unknown scope fails loudly instead of silently falling back to a default
    :returns: nothing
    """
    with pytest.raises(ValueError, match="Unsupported contribution scope"):
        generate_contribution_recommendations(FakeDiscoveryClient(), PROFILE, [], [], scope="everything", now=NOW)


def test_search_request_budget_covers_every_scope() -> None:
    """
    every supported scope declares an explicit upper bound on Search API requests
    :returns: nothing
    """
    assert set(SEARCH_REQUEST_BUDGET) == set(CONTRIBUTION_SCOPES)
    assert SEARCH_REQUEST_BUDGET[SCOPE_SAVED_STARRED] == 2
    assert all(budget > 0 for budget in SEARCH_REQUEST_BUDGET.values())
    repositories = [_repository(f"owner/repository-{index}") for index in range(MAX_SOURCE_REPOSITORIES)]
    assert len(build_issue_queries(repositories)) == SEARCH_REQUEST_BUDGET[SCOPE_SAVED_STARRED]


def test_discovery_terms_drop_signals_the_language_qualifier_already_asserts() -> None:
    """
    a term slot is never spent restating a language the query qualifier already carries
    :returns: nothing
    """
    profile = PreferenceProfile(
        languages={"Python": 1.0},
        topics={"python": 1.0, "automation": 0.9, "backend": 0.8},
        keywords={"retry": 0.7},
    )
    terms = discovery_terms(profile, 3, ["Python"])
    assert "python" not in terms
    assert terms == ["automation", "backend", "retry"]
    assert all('"python"' not in query for query in build_discovery_queries(profile))
