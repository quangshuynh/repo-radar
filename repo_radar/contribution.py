"""bounded contribution opportunity discovery

Repositories come from local evidence that the user already cares about (saved repositories
first, then the synchronized star cache), so a contribution run costs no repository search
requests at all. Only the grouped issue searches reach GitHub.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .github_client import GitHubClient, GitHubError
from .issue_ranking import rank_issues
from .models import ImportedProfile, Issue, IssueRecommendation, PreferenceProfile, Repository
from .ranking import score_repository

# Bounds for one contribution run. Ten repositories batched five at a time is two Search API
# requests, well inside the 30 requests per minute authenticated search limit, and keeps the
# query under GitHub's 256 character limit and its cap on boolean operators per query.
MAX_SOURCE_REPOSITORIES = 10
REPOSITORY_BATCH_SIZE = 5
ISSUES_PER_QUERY = 60
MAX_ISSUE_CANDIDATES = 120
PER_REPOSITORY_LIMIT = 3

# repositories the user actively rejected stay out of contribution discovery; interested and
# starred classifications are the evidence this feature is built on and are never excluded
EXCLUDED_CLASSIFICATIONS = frozenset({"not interested", "blocked"})


def select_source_repositories(
    saved: list[Repository],
    starred: list[Repository],
    profile: PreferenceProfile,
    owner: str = "",
    feedback: dict[str, str] | None = None,
    excluded_owners: set[str] | None = None,
    limit: int = MAX_SOURCE_REPOSITORIES,
    now: datetime | None = None,
) -> list[Repository]:
    """
    choose the bounded set of relevant repositories to search for issues
    :param saved: repositories the user explicitly saved
    :param starred: repositories in the synchronized star cache
    :param profile: user preference profile
    :param owner: authenticated GitHub login
    :param feedback: prior repository classifications
    :param excluded_owners: additional repository owners to exclude
    :param limit: maximum source repositories
    :param now: optional reference time for deterministic scoring
    :returns: source repositories ordered by explicit interest then relevance
    """
    rejected = {name.lower() for name, value in (feedback or {}).items() if value in EXCLUDED_CLASSIFICATIONS}
    owners = {value.lower() for value in {owner, *(excluded_owners or set())} if value}
    ranked: dict[str, tuple[int, float, str, Repository]] = {}
    for priority, group in enumerate((saved, starred)):
        for repository in group:
            key = repository.full_name.lower()
            if key in ranked or key in rejected or repository.archived or repository.owner.lower() in owners:
                continue
            if "/" not in repository.full_name:
                continue
            relevance, _ = score_repository(repository, profile, now or datetime.now(timezone.utc))
            ranked[key] = (priority, -relevance, key, repository)
    ordered = sorted(ranked.values(), key=lambda item: item[:3])
    return [repository for *_, repository in ordered][:limit]


def build_issue_queries(repositories: list[Repository], batch_size: int = REPOSITORY_BATCH_SIZE) -> list[str]:
    """
    build grouped open issue searches for the selected repositories
    :param repositories: bounded source repositories
    :param batch_size: repositories searched by one query
    :returns: grouped GitHub issue search queries
    """
    queries: list[str] = []
    for start in range(0, len(repositories), max(1, batch_size)):
        batch = repositories[start : start + max(1, batch_size)]
        scope = " OR ".join(f"repo:{repository.full_name}" for repository in batch)
        queries.append(f"is:issue is:open archived:false ({scope})")
    return queries


def collect_issue_candidates(
    client: GitHubClient,
    repositories: list[Repository],
    per_query: int = ISSUES_PER_QUERY,
    max_candidates: int = MAX_ISSUE_CANDIDATES,
) -> tuple[list[Issue], str | None]:
    """
    run the grouped issue searches and combine their bounded results
    :param client: authenticated GitHub client
    :param repositories: bounded source repositories
    :param per_query: result limit for each search
    :param max_candidates: maximum combined issue candidates
    :returns: deduplicated issue candidates and an optional degradation warning
    """
    unique: dict[tuple[str, int], Issue] = {}
    for query in build_issue_queries(repositories):
        try:
            results = client.search_issues(query, per_query)
        except GitHubError as error:
            # issue search failures are usually rate limits, which the next query would hit
            # too, so stop early and report whatever was already collected
            return list(unique.values())[:max_candidates], str(error)
        for issue in results:
            unique.setdefault((issue.repository.lower(), issue.number), issue)
        if len(unique) >= max_candidates:
            break
    return list(unique.values())[:max_candidates], None


def generate_contribution_recommendations(
    client: GitHubClient,
    profile: PreferenceProfile,
    saved: list[Repository],
    starred: list[Repository],
    owner: str = "",
    feedback: dict[str, str] | None = None,
    limit: int = 10,
    imported_profile: ImportedProfile | None = None,
    unassigned_only: bool = False,
    now: datetime | None = None,
) -> tuple[list[IssueRecommendation], str | None]:
    """
    generate ranked contribution recommendations through the shared pipeline
    :param client: authenticated GitHub client
    :param profile: current preference profile
    :param saved: repositories the user explicitly saved
    :param starred: repositories in the synchronized star cache
    :param owner: authenticated GitHub login
    :param feedback: prior repository classifications
    :param limit: maximum recommendations
    :param imported_profile: optional owned repository profile to exclude
    :param unassigned_only: whether to drop issues that already have an assignee
    :param now: optional reference time for deterministic scoring
    :returns: ranked contribution recommendations and an optional degradation warning
    """
    excluded_owners = {imported_profile.username} if imported_profile else set()
    sources = select_source_repositories(saved, starred, profile, owner, feedback, excluded_owners, now=now)
    if not sources:
        return [], None
    issues, warning = collect_issue_candidates(client, sources)
    if unassigned_only:
        issues = [issue for issue in issues if issue.assignee_count == 0]
    repositories = {repository.full_name.lower(): repository for repository in sources}
    ranked = rank_issues(issues, repositories, profile, max(1, limit), PER_REPOSITORY_LIMIT, now)
    return ranked, warning


def source_labels(saved: list[Repository], starred: list[Repository]) -> dict[str, str]:
    """
    record which local evidence made each repository a contribution source
    :param saved: repositories the user explicitly saved
    :param starred: repositories in the synchronized star cache
    :returns: mapping from lowercase full name to source label
    """
    labels = {repository.full_name.lower(): "starred" for repository in starred}
    labels.update({repository.full_name.lower(): "saved" for repository in saved})
    return labels
