"""bounded contribution opportunity discovery

Two candidate sources, one normalization step, one ranker:

    scope=discover                         scope=saved_starred
    profile derived issue searches         grouped repo: issue searches
    + bounded repository hydration         + local repository metadata
                    \\                     /
                     exclude_issues()  ->  normalize_candidates()
                                 |
                            rank_issues()

The scopes differ only in *where candidates come from*. They converge on the same
normalized issue candidates and the same `issue_ranking.rank_issues` implementation, so
there is exactly one place that decides what a good contribution opportunity is.

`ContributionFilters` narrows what either scope retrieves. Filters are label qualifiers on
the queries this module builds and a guard in `normalize_candidates`; they never reach
`rank_issues`, so a filtered run reports the same score for an issue that an unfiltered run
would.

Every GitHub bound for both scopes lives in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .github_client import GitHubClient, GitHubError
from .issue_ranking import issue_priority, normalize_label, rank_issues
from .models import ImportedProfile, Issue, IssueRecommendation, PreferenceProfile, Repository
from .ranking import score_repository

SCOPE_DISCOVER = "discover"
SCOPE_SAVED_STARRED = "saved_starred"
CONTRIBUTION_SCOPES = (SCOPE_DISCOVER, SCOPE_SAVED_STARRED)
# discovery is the default because the product question is "what are the best contribution
# opportunities for me", which cannot be answered from repositories the user already knows
DEFAULT_SCOPE = SCOPE_DISCOVER

# Bounds for one saved/starred run. Ten repositories batched five at a time is two Search API
# requests, well inside the 30 requests per minute authenticated search limit, and keeps the
# query under GitHub's 256 character limit and its cap on boolean operators per query.
MAX_SOURCE_REPOSITORIES = 10
REPOSITORY_BATCH_SIZE = 5
ISSUES_PER_QUERY = 60
MAX_ISSUE_CANDIDATES = 120
PER_REPOSITORY_LIMIT = 3

# Bounds for one discovery run. Two languages times two search strategies is the worst case.
# Repository hydration is core API traffic (5000 per hour), not Search API traffic, but it is
# still per repository so it is capped well below the candidate count: twelve repositories is
# enough to fill ten result slots under a per repository cap of three, and no more.
MAX_DISCOVERY_QUERIES = 4
DISCOVERY_LANGUAGES = 2
DISCOVERY_TERMS_PER_QUERY = 4
MAX_REPOSITORY_HYDRATIONS = 12
QUERY_CHARACTER_LIMIT = 256

# worst case Search API requests per run, per scope; the /user request the surfaces make is
# core API traffic and is not counted here
SEARCH_REQUEST_BUDGET = {
    SCOPE_DISCOVER: MAX_DISCOVERY_QUERIES,
    SCOPE_SAVED_STARRED: -(-MAX_SOURCE_REPOSITORIES // REPOSITORY_BATCH_SIZE),
}

# repositories the user actively rejected stay out of contribution discovery; interested and
# starred classifications are evidence this feature is built on and are never excluded
EXCLUDED_CLASSIFICATIONS = frozenset({"not interested", "blocked"})

ISSUE_QUERY_BASE = "is:issue is:open archived:false"

# The issue categories a user may filter on. Kept to a small closed vocabulary of labels
# GitHub itself seeds repositories with, so a selection means the same thing across projects.
# Declaration order is the canonical order: it is what makes a selection's query text
# independent of the order the user happened to click or type the categories in.
ISSUE_CATEGORIES = ("bug", "documentation", "enhancement", "accessibility")

# Labels a project uses to explicitly invite outside contribution. Separate from the
# categories above because "what kind of work is this" and "does this project want help with
# it" are independent questions, and a user may ask either, both, or neither.
CONTRIBUTION_INVITATION_LABELS = ("good first issue", "help wanted", "contributions welcome", "up for grabs")

# GitHub issue search has no topic qualifier, so profile topics and keywords are used as
# free text. Terms are sanitized to characters GitHub search treats as ordinary word
# characters, which also makes it impossible for a stored preference to break out of the
# quoted term and change the query's structure.
_TERM_PATTERN = re.compile(r"[^a-z0-9+#._-]+")
_LANGUAGE_PATTERN = re.compile(r"[^A-Za-z0-9+#. -]+")
MINIMUM_TERM_LENGTH = 3

# a repository specific failure costs one candidate; these failures would repeat on every
# following request, so hydration stops instead of burning the rest of the budget
FATAL_HYDRATION_STATUSES = frozenset({401, 403, 429})


def _label_qualifier(labels: tuple[str, ...]) -> str:
    """
    express one label group as a single GitHub qualifier

    Comma separated values inside one `label:` qualifier are an OR, and separate `label:`
    qualifiers are ANDed. That is what lets a category group and the invitation group be
    combined as `(bug OR docs) AND (good first issue OR help wanted OR ...)` in one request
    instead of one request per label.
    :param labels: label names belonging to one group
    :returns: GitHub label qualifier or an empty string when the group is empty
    """
    if not labels:
        return ""
    quoted = ",".join('"' + label + '"' for label in labels)
    return f"label:{quoted}"


CONTRIBUTION_LABEL_QUALIFIER = _label_qualifier(CONTRIBUTION_INVITATION_LABELS)


@dataclass(frozen=True)
class ContributionFilters:
    """the user's explicit issue filters, normalized once and applied everywhere

    Categories and contributor friendliness stay separate fields rather than one label list,
    because they are separate questions with different query semantics: categories are an OR
    group the user chose, invitation labels are a fixed OR group, and selecting both means the
    two groups are ANDed.

    Construct through `create`, which normalizes and validates; the constructor is left
    permissive so an already-normalized value can be rebuilt cheaply.
    """

    categories: tuple[str, ...] = ()
    contributor_friendly: bool = False

    @classmethod
    def create(
        cls,
        categories: list[str] | tuple[str, ...] | None = None,
        contributor_friendly: bool = False,
    ) -> ContributionFilters:
        """
        normalize and validate user selected filters
        :param categories: selected issue categories in any order, possibly repeated
        :param contributor_friendly: whether to require a contribution invitation label
        :returns: normalized filters
        """
        selected = {value.strip().lower() for value in categories or () if value.strip()}
        unsupported = sorted(selected - set(ISSUE_CATEGORIES))
        if unsupported:
            raise ValueError(
                f"Unsupported issue category {', '.join(unsupported)}. Expected one of {', '.join(ISSUE_CATEGORIES)}"
            )
        # canonical order, not selection order, so the same set always builds the same query
        ordered = tuple(name for name in ISSUE_CATEGORIES if name in selected)
        return cls(categories=ordered, contributor_friendly=bool(contributor_friendly))

    @property
    def category_qualifier(self) -> str:
        """
        express the selected categories as one OR group
        :returns: GitHub label qualifier or an empty string when no category is selected
        """
        return _label_qualifier(self.categories)

    @property
    def qualifiers(self) -> tuple[str, ...]:
        """
        express every selected filter as ANDed GitHub qualifiers
        :returns: label qualifiers, one per selected group
        """
        groups = (self.category_qualifier, CONTRIBUTION_LABEL_QUALIFIER if self.contributor_friendly else "")
        return tuple(group for group in groups if group)

    def matches(self, issue: Issue) -> bool:
        """
        re-apply the selected filters to one retrieved candidate

        GitHub already applied these qualifiers, so this normally changes nothing. It exists
        because normalization is the one place every candidate passes through regardless of
        which query sourced it, and because a filter the user explicitly asked for should not
        depend on a remote service having honored it. This is retrieval, not ranking: no score
        is consulted or adjusted here.
        :param issue: retrieved issue candidate
        :returns: whether the issue satisfies every selected filter group
        """
        labels = {normalize_label(label) for label in issue.labels}
        if self.categories and not labels.intersection(self.categories):
            return False
        if self.contributor_friendly and not labels.intersection(CONTRIBUTION_INVITATION_LABELS):
            return False
        return True


NO_FILTERS = ContributionFilters()


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


def build_issue_queries(
    repositories: list[Repository],
    filters: ContributionFilters = NO_FILTERS,
    batch_size: int = REPOSITORY_BATCH_SIZE,
) -> list[str]:
    """
    build grouped open issue searches for the selected repositories

    Selected filters are prepended as label qualifiers rather than expanded into extra
    searches, so the Search API cost of a run is `ceil(len(repositories) / batch_size)`
    whether zero or every filter is selected. Label qualifiers consume the same 256 character
    query budget the repository group does, so a batch closes early when the next repository
    would overflow the limit and the remainder moves to the following batch. Filtering
    therefore narrows how many repositories one request can cover, and the repositories that
    do not fit are the weakest ones rather than an arbitrary subset; it never buys another
    request.
    :param repositories: bounded source repositories
    :param filters: user selected issue filters
    :param batch_size: repositories searched by one query
    :returns: grouped GitHub issue search queries
    """
    size = max(1, batch_size)
    prefix = " ".join((ISSUE_QUERY_BASE, *filters.qualifiers))
    # the request count follows from the repository count alone, so no filter can raise it
    budget = -(-len(repositories) // size)
    queries: list[str] = []
    batch: list[str] = []
    for repository in repositories:
        if len(queries) >= budget:
            break
        scope = f"repo:{repository.full_name}"
        candidate = [*batch, scope]
        if len(candidate) > size or len(f"{prefix} ({' OR '.join(candidate)})") > QUERY_CHARACTER_LIMIT:
            if batch:
                queries.append(f"{prefix} ({' OR '.join(batch)})")
            batch = []
            candidate = [scope]
            # one repository whose name alone overflows the limit is unsearchable, not fatal
            if len(f"{prefix} ({scope})") > QUERY_CHARACTER_LIMIT:
                continue
        batch = candidate
    if batch and len(queries) < budget:
        queries.append(f"{prefix} ({' OR '.join(batch)})")
    return queries


def _search_term(value: str) -> str:
    """
    reduce one profile signal to a safe GitHub free text term
    :param value: topic or description keyword
    :returns: sanitized term or an empty string when unusable
    """
    cleaned = _TERM_PATTERN.sub("", value.strip().lower())
    return cleaned if len(cleaned) >= MINIMUM_TERM_LENGTH else ""


def _language_qualifier(language: str) -> str:
    """
    build the GitHub language qualifier for one profile language
    :param language: profile language name
    :returns: quoted language qualifier or an empty string
    """
    cleaned = _LANGUAGE_PATTERN.sub("", language).strip()
    return f'language:"{cleaned}"' if cleaned else ""


def _strongest(values: dict[str, float], limit: int) -> list[str]:
    """
    order profile signals deterministically before bounding them
    :param values: normalized profile signal weights
    :param limit: maximum retained signals
    :returns: strongest signals ordered by weight then name
    """
    ordered = sorted((item for item in values.items() if item[1] > 0), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ordered][:limit]


def discovery_terms(
    profile: PreferenceProfile,
    limit: int = DISCOVERY_TERMS_PER_QUERY,
    languages: list[str] | None = None,
) -> list[str]:
    """
    derive the bounded free text terms used to search for relevant issues

    Topics come first because they are curated project vocabulary; description keywords fill
    the remaining slots because they are noisier but often carry the specific subject matter.

    Terms that merely restate a language already carried by the `language:` qualifier are
    dropped. A profile built from Python repositories inevitably ranks the topic `python`
    near the top, and spending a term slot asserting something the qualifier already asserts
    narrows nothing while displacing a term that would.
    :param profile: user preference profile
    :param limit: maximum retained terms
    :param languages: languages already expressed as query qualifiers
    :returns: sanitized deduplicated search terms strongest first
    """
    redundant = {term for term in (_search_term(name) for name in languages or []) if term}
    # request extra candidates so dropping redundant ones still fills the term budget
    candidates = [*_strongest(profile.topics, limit * 2), *_strongest(profile.keywords, limit * 2)]
    terms = [term for term in (_search_term(value) for value in candidates) if term and term not in redundant]
    return list(dict.fromkeys(terms))[:limit]


def _bounded_query(parts: list[str], terms: list[str]) -> str:
    """
    assemble a term query, dropping the weakest terms until it fits GitHub's query limit
    :param parts: leading query qualifiers
    :param terms: search terms strongest first
    :returns: query within the character limit or an empty string
    """
    for count in range(len(terms), 0, -1):
        grouped = " OR ".join(f'"{term}"' for term in terms[:count])
        query = " ".join([part for part in parts if part] + [f"({grouped})"])
        if len(query) <= QUERY_CHARACTER_LIMIT:
            return query
    return ""


def build_discovery_queries(
    profile: PreferenceProfile,
    max_queries: int = MAX_DISCOVERY_QUERIES,
    filters: ContributionFilters = NO_FILTERS,
) -> list[str]:
    """
    derive bounded GitHub-wide contribution searches from the preference profile

    Two deliberately different strategies run per language. The relevance strategy carries no
    invitation qualifier, so a highly relevant unassigned bug with no beginner label is
    reachable; the invitation strategy carries no profile terms, so a repository the user has
    never encountered can still enter the pool on an explicit call for contributors. Relevance
    queries are emitted first, so a reduced budget keeps the strategy that is not restricted
    to beginner labels.

    Selected filters narrow both strategies rather than replacing either. Selected categories
    are ANDed onto every query as one OR group. `contributor_friendly` additionally puts the
    invitation qualifier on the relevance strategy, because leaving one query unrestricted
    would quietly return exactly the issues the user asked to exclude; the two strategies stay
    distinct because one still carries profile terms and the other still does not.

    Generation is a pure function of the profile and the filters: signals are sorted by weight
    then name and categories are held in canonical order, so the same inputs always yield the
    same queries in the same order.
    :param profile: user preference profile
    :param max_queries: maximum searches to issue
    :param filters: user selected issue filters
    :returns: bounded deterministic issue search queries
    """
    languages = _strongest(profile.languages, DISCOVERY_LANGUAGES)
    terms = discovery_terms(profile, DISCOVERY_TERMS_PER_QUERY, languages)
    if not languages and not terms:
        return []
    scopes = [qualifier for qualifier in (_language_qualifier(name) for name in languages) if qualifier] or [""]
    relevance = (
        [_bounded_query([ISSUE_QUERY_BASE, scope, *filters.qualifiers], terms) for scope in scopes] if terms else []
    )
    invitation = [
        " ".join(
            part for part in (ISSUE_QUERY_BASE, scope, filters.category_qualifier, CONTRIBUTION_LABEL_QUALIFIER) if part
        )
        for scope in scopes
    ]
    queries = [query for query in [*relevance, *invitation] if query and len(query) <= QUERY_CHARACTER_LIMIT]
    return list(dict.fromkeys(queries))[: max(0, max_queries)]


def _run_issue_searches(
    client: GitHubClient,
    queries: list[str],
    per_query: int,
    max_candidates: int,
) -> tuple[list[Issue], str | None]:
    """
    run bounded issue searches and combine their deduplicated results
    :param client: authenticated GitHub client
    :param queries: bounded issue search queries
    :param per_query: result limit for each search
    :param max_candidates: maximum combined issue candidates
    :returns: deduplicated issue candidates and an optional degradation warning
    """
    unique: dict[tuple[str, int], Issue] = {}
    for query in queries:
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


def collect_issue_candidates(
    client: GitHubClient,
    repositories: list[Repository],
    per_query: int = ISSUES_PER_QUERY,
    max_candidates: int = MAX_ISSUE_CANDIDATES,
    filters: ContributionFilters = NO_FILTERS,
) -> tuple[list[Issue], str | None]:
    """
    run the grouped issue searches and combine their bounded results
    :param client: authenticated GitHub client
    :param repositories: bounded source repositories
    :param per_query: result limit for each search
    :param max_candidates: maximum combined issue candidates
    :param filters: user selected issue filters
    :returns: deduplicated issue candidates and an optional degradation warning
    """
    return _run_issue_searches(client, build_issue_queries(repositories, filters), per_query, max_candidates)


def collect_discovery_candidates(
    client: GitHubClient,
    profile: PreferenceProfile,
    per_query: int = ISSUES_PER_QUERY,
    max_candidates: int = MAX_ISSUE_CANDIDATES,
    max_queries: int = MAX_DISCOVERY_QUERIES,
    filters: ContributionFilters = NO_FILTERS,
) -> tuple[list[Issue], str | None]:
    """
    run the profile derived GitHub-wide searches and combine their bounded results
    :param client: authenticated GitHub client
    :param profile: user preference profile
    :param per_query: result limit for each search
    :param max_candidates: maximum combined issue candidates
    :param max_queries: maximum searches to issue
    :param filters: user selected issue filters
    :returns: deduplicated issue candidates and an optional degradation warning
    """
    queries = build_discovery_queries(profile, max_queries, filters)
    return _run_issue_searches(client, queries, per_query, max_candidates)


def exclude_issues(
    issues: list[Issue],
    owner: str = "",
    feedback: dict[str, str] | None = None,
    excluded_owners: set[str] | None = None,
) -> list[Issue]:
    """
    drop issues belonging to repositories the user owns or actively rejected

    Saved and starred repositories are deliberately *not* excluded. Discovery is a superset
    of the narrower scope, and an open issue in a repository the user already starred is a
    perfectly good contribution opportunity.
    :param issues: raw issue candidates
    :param owner: authenticated GitHub login
    :param feedback: prior repository classifications
    :param excluded_owners: additional repository owners to exclude
    :returns: issues from eligible repositories
    """
    rejected = {name.lower() for name, value in (feedback or {}).items() if value in EXCLUDED_CLASSIFICATIONS}
    owners = {value.lower() for value in {owner, *(excluded_owners or set())} if value}
    return [
        issue
        for issue in issues
        if issue.repository.lower() not in rejected and issue.repository.partition("/")[0].lower() not in owners
    ]


def hydration_targets(
    issues: list[Issue],
    profile: PreferenceProfile,
    limit: int = MAX_REPOSITORY_HYDRATIONS,
    now: datetime | None = None,
) -> list[str]:
    """
    choose which repositories are worth one metadata request each

    Issues are ordered by `issue_priority`, which is the production issue score without the
    repository term, then their repositories are taken in first appearance order. That spends
    the bounded hydration budget on the repositories owning the strongest issue candidates
    rather than on whichever repository GitHub happened to return first.
    :param issues: eligible issue candidates
    :param profile: user preference profile
    :param limit: maximum repositories to hydrate
    :param now: optional reference time for deterministic scoring
    :returns: repository full names strongest first
    """
    reference = now or datetime.now(timezone.utc)
    ordered = sorted(
        issues,
        key=lambda issue: (-issue_priority(issue, profile, reference), issue.repository.lower(), issue.number),
    )
    names: dict[str, str] = {}
    for issue in ordered:
        if "/" in issue.repository:
            names.setdefault(issue.repository.lower(), issue.repository)
    return list(names.values())[: max(0, limit)]


def hydrate_repositories(
    client: GitHubClient,
    issues: list[Issue],
    profile: PreferenceProfile,
    limit: int = MAX_REPOSITORY_HYDRATIONS,
    now: datetime | None = None,
) -> tuple[dict[str, Repository], str | None]:
    """
    fetch bounded repository metadata for the strongest issue candidates

    GitHub's issue search returns `repository_url` but no repository language, topics,
    description, popularity, or activity, all of which `score_repository` needs. Rather than
    dropping the repository relevance term for discovered issues or fanning out one request
    per candidate, exactly `limit` core API reads are spent on the most promising
    repositories. Issues whose repository was not hydrated are dropped by normalization; a
    repository specific failure costs one candidate and is not reported as a run warning,
    matching the existing convention that one unusable row cannot discard a batch.
    :param client: authenticated GitHub client
    :param issues: eligible issue candidates
    :param profile: user preference profile
    :param limit: maximum repositories to hydrate
    :param now: optional reference time for deterministic scoring
    :returns: repositories keyed by lowercase full name and an optional degradation warning
    """
    repositories: dict[str, Repository] = {}
    for name in hydration_targets(issues, profile, limit, now):
        try:
            repository = client.get_repository(name)
        except GitHubError as error:
            if error.status is None or error.status in FATAL_HYDRATION_STATUSES:
                return repositories, str(error)
            continue
        if repository.full_name:
            repositories[repository.full_name.lower()] = repository
    return repositories, None


def normalize_candidates(
    issues: list[Issue],
    repositories: dict[str, Repository],
    unassigned_only: bool = False,
    filters: ContributionFilters = NO_FILTERS,
) -> list[Issue]:
    """
    apply the candidate rules both scopes converge on before ranking

    The pull request and open state guards repeat the client boundary check on purpose: this
    is the one place every candidate passes through regardless of how it was sourced. The
    selected filters are re-applied here for the same reason; they are explicit user requests,
    not the hidden beginner-label filtering the project rejects, and nothing about them
    reaches ranking.
    :param issues: eligible issue candidates
    :param repositories: repository metadata keyed by lowercase full name
    :param unassigned_only: whether to drop issues that already have an assignee
    :param filters: user selected issue filters
    :returns: deduplicated rankable issue candidates
    """
    unique: dict[tuple[str, int], Issue] = {}
    for issue in issues:
        if issue.is_pull_request or issue.state != "open" or not issue.is_identifiable():
            continue
        repository = repositories.get(issue.repository.lower())
        if repository is None or repository.archived:
            continue
        if unassigned_only and issue.assignee_count:
            continue
        if not filters.matches(issue):
            continue
        unique.setdefault((issue.repository.lower(), issue.number), issue)
    return list(unique.values())


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
    scope: str = DEFAULT_SCOPE,
    filters: ContributionFilters | None = None,
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
    :param scope: candidate sourcing scope, discover or saved_starred
    :param filters: user selected issue filters applied during candidate retrieval
    :param now: optional reference time for deterministic scoring
    :returns: ranked contribution recommendations and an optional degradation warning
    """
    if scope not in CONTRIBUTION_SCOPES:
        raise ValueError(f"Unsupported contribution scope {scope}. Expected one of {', '.join(CONTRIBUTION_SCOPES)}")
    reference = now or datetime.now(timezone.utc)
    selected = filters or NO_FILTERS
    excluded_owners = {imported_profile.username} if imported_profile else set()
    if scope == SCOPE_SAVED_STARRED:
        sources = select_source_repositories(saved, starred, profile, owner, feedback, excluded_owners, now=reference)
        if not sources:
            return [], None
        issues, warning = collect_issue_candidates(client, sources, filters=selected)
        repositories = {repository.full_name.lower(): repository for repository in sources}
        issues = exclude_issues(issues, owner, feedback, excluded_owners)
    else:
        issues, warning = collect_discovery_candidates(client, profile, filters=selected)
        issues = exclude_issues(issues, owner, feedback, excluded_owners)
        repositories, hydration_warning = hydrate_repositories(client, issues, profile, now=reference)
        warning = warning or hydration_warning
    candidates = normalize_candidates(issues, repositories, unassigned_only, selected)
    ranked = rank_issues(candidates, repositories, profile, max(1, limit), PER_REPOSITORY_LIMIT, reference)
    return ranked, warning


def source_labels(saved: list[Repository], starred: list[Repository]) -> dict[str, str]:
    """
    record which local evidence made each repository a contribution source

    A repository absent from this mapping is one the user has never saved or starred. Callers
    report that as `new`; it is presentation metadata and never contributes to a score.
    :param saved: repositories the user explicitly saved
    :param starred: repositories in the synchronized star cache
    :returns: mapping from lowercase full name to source label
    """
    labels = {repository.full_name.lower(): "starred" for repository in starred}
    labels.update({repository.full_name.lower(): "saved" for repository in saved})
    return labels
