"""targeted candidate discovery and filtering"""

from __future__ import annotations

from .github_client import GitHubClient
from .models import ImportedProfile, PreferenceProfile, Recommendation, Repository, SearchQuery
from .ranking import rank_candidates


def _language_qualifier(language: str) -> str:
    """
    build a GitHub search qualifier restricting results to one top language
    :param language: GitHub language name
    :returns: search qualifier quoting names GitHub cannot read bare
    """
    return f"language:{language}" if language.isalnum() else f'language:"{language}"'


def _requested_language_queries(search: SearchQuery, topics: list[str], limit: int) -> list[str]:
    """
    build GitHub searches constrained to the language the user asked for
    :param search: parsed search intent
    :param topics: strongest profile topics
    :param limit: maximum number of generated searches
    :returns: targeted search queries
    """
    qualifier = _language_qualifier(search.language)
    terms = search.text()
    queries = [f"{terms} {qualifier} archived:false"] if terms else []
    # the unfiltered language search backfills the pool so a narrow topical phrase can
    # never be the only reason a requested language returns nothing
    queries.append(f"{qualifier} stars:10..50000 archived:false")
    queries.extend(f"{qualifier} topic:{topic} archived:false" for topic in topics)
    return list(dict.fromkeys(queries))[:limit]


def build_search_queries(profile: PreferenceProfile, search: SearchQuery | None = None, limit: int = 8) -> list[str]:
    """
    build several focused GitHub searches from profile signals
    :param profile: user preference profile
    :param search: optional parsed search intent constraining the primary language
    :param limit: maximum number of generated searches
    :returns: targeted search queries
    """
    topics = list(profile.topics)[:4]
    if search is not None:
        return _requested_language_queries(search, topics, limit)
    languages = list(profile.languages)[:3]
    queries = [f"{_language_qualifier(language)} stars:10..50000 archived:false" for language in languages]
    queries.extend(f"topic:{topic} stars:5..50000 archived:false" for topic in topics)
    if languages and topics:
        queries.insert(0, f"{_language_qualifier(languages[0])} topic:{topics[0]} archived:false")
    return list(dict.fromkeys(queries))[:limit]


def deduplicate_candidates(repositories: list[Repository]) -> list[Repository]:
    """
    deduplicate candidates by case insensitive full name
    :param repositories: candidate repositories
    :returns: unique candidates preserving order
    """
    unique: dict[str, Repository] = {}
    for repository in repositories:
        unique.setdefault(repository.full_name.lower(), repository)
    return list(unique.values())


def filter_candidates(
    repositories: list[Repository],
    starred_names: set[str],
    owner: str,
    feedback: dict[str, str],
    excluded_owners: set[str] | None = None,
) -> list[Repository]:
    """
    remove ineligible and previously handled candidates
    :param repositories: discovered repositories
    :param starred_names: full names already starred by the user
    :param owner: authenticated GitHub login
    :param feedback: prior repository classifications
    :param excluded_owners: additional repository owners to exclude
    :returns: eligible candidate repositories
    """
    excluded_feedback = {
        name.lower()
        for name, value in feedback.items()
        if value in {"interested", "not interested", "blocked", "starred"}
    }
    starred = {name.lower() for name in starred_names}
    owners = {owner.lower(), *(value.lower() for value in excluded_owners or set())}
    return [
        repository
        for repository in deduplicate_candidates(repositories)
        if repository.full_name.lower() not in starred
        and repository.full_name.lower() not in excluded_feedback
        and repository.owner.lower() not in owners
        and not repository.archived
    ]


def filter_by_primary_language(repositories: list[Repository], language: str) -> list[Repository]:
    """
    keep only repositories whose top GitHub language is the requested language
    :param repositories: candidate repositories
    :param language: requested primary language
    :returns: repositories reporting the requested language as their top language
    """
    requested = language.casefold()
    return [repository for repository in repositories if (repository.language or "").casefold() == requested]


def discover_candidates(
    client: GitHubClient,
    profile: PreferenceProfile,
    per_query: int = 30,
    search: SearchQuery | None = None,
) -> list[Repository]:
    """
    execute focused searches and combine their results
    :param client: authenticated GitHub client
    :param profile: preference profile used for query generation
    :param per_query: result limit for each search
    :param search: optional parsed search intent constraining the primary language
    :returns: deduplicated discovered repositories
    """
    candidates: list[Repository] = []
    for query in build_search_queries(profile, search):
        candidates.extend(client.search_repositories(query, per_query))
    return deduplicate_candidates(candidates)


def generate_recommendations(
    client: GitHubClient,
    profile: PreferenceProfile,
    starred: list[Repository],
    owner: str,
    feedback: dict[str, str],
    limit: int = 10,
    imported_profile: ImportedProfile | None = None,
    search: SearchQuery | None = None,
) -> list[Recommendation]:
    """
    generate recommendations through the shared discovery and ranking pipeline
    :param client: authenticated GitHub client
    :param profile: current preference profile
    :param starred: cached starred repositories
    :param owner: authenticated GitHub login
    :param feedback: prior repository classifications
    :param limit: maximum recommendations
    :param imported_profile: optional owned repository profile to exclude
    :param search: optional parsed search intent constraining the primary language
    :returns: ranked eligible recommendations
    """
    discovered = discover_candidates(client, profile, search=search)
    excluded_names = {item.full_name for item in starred}
    excluded_owners: set[str] = set()
    if imported_profile:
        excluded_names.update(
            f"{imported_profile.username}/{repository.name}" for repository in imported_profile.repositories
        )
        excluded_owners.add(imported_profile.username)
    candidates = filter_candidates(discovered, excluded_names, owner, feedback, excluded_owners)
    if search is not None:
        candidates = filter_by_primary_language(candidates, search.language)
    return rank_candidates(candidates, profile, max(1, limit))
