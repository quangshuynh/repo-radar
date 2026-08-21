"""transparent preference profile calculation"""

from __future__ import annotations

import re
import statistics
from collections import Counter

from .models import ImportedProfile, PreferenceProfile, Repository, SeedPreferences

STARRED_REPOSITORY_WEIGHT = 1.0
PINNED_REPOSITORY_WEIGHT = 0.8
OWNED_REPOSITORY_WEIGHT = 0.35
SEED_SIGNAL_WEIGHT = 0.6

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "your",
    "using",
    "tool",
}


def _normalize(counter: Counter[str]) -> dict[str, float]:
    """
    normalize counts against the strongest signal
    :param counter: counted preference signals
    :returns: normalized scores sorted by strength
    """
    if not counter:
        return {}
    maximum = max(counter.values())
    return {key: round(value / maximum, 3) for key, value in counter.most_common()}


def extract_keywords(description: str | None) -> list[str]:
    """
    extract useful lowercase keywords from a description
    :param description: repository description
    :returns: description keywords
    """
    words = re.findall(r"[a-z][a-z0-9+#.-]{2,}", (description or "").lower())
    return [word.strip(".-") for word in words if word not in STOP_WORDS]


def build_profile(
    repositories: list[Repository],
    seed_preferences: SeedPreferences | None = None,
    imported_profile: ImportedProfile | None = None,
) -> PreferenceProfile:
    """
    build a normalized profile from starred repositories and manual preferences
    :param repositories: repositories used as positive preferences
    :param seed_preferences: optional manually entered interests
    :param imported_profile: optional GitProfileLens public repository profile
    :returns: calculated preference profile
    """
    languages: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    keywords: Counter[str] = Counter()
    for repository in repositories:
        if repository.language:
            languages[repository.language] += STARRED_REPOSITORY_WEIGHT
        topics.update({topic.lower(): STARRED_REPOSITORY_WEIGHT for topic in repository.topics})
        keywords.update({word: STARRED_REPOSITORY_WEIGHT for word in set(extract_keywords(repository.description))})
    for repository in imported_profile.repositories if imported_profile else []:
        if repository.archived or repository.is_fork:
            continue
        weight = PINNED_REPOSITORY_WEIGHT if repository.pinned else OWNED_REPOSITORY_WEIGHT
        if repository.language:
            languages[repository.language] += weight
        topics.update({topic.lower(): weight for topic in repository.topics})
        keywords.update({word: weight for word in set(extract_keywords(repository.description))})
    seeds = seed_preferences or SeedPreferences()
    languages.update({language: SEED_SIGNAL_WEIGHT for language in seeds.languages})
    topics.update({topic.lower(): SEED_SIGNAL_WEIGHT for topic in seeds.topics})
    keywords.update({keyword.lower(): SEED_SIGNAL_WEIGHT for keyword in seeds.keywords})
    stars = [repository.stars for repository in repositories]
    return PreferenceProfile(
        languages=_normalize(languages),
        topics=_normalize(topics),
        keywords=_normalize(keywords),
        median_stars=float(statistics.median(stars)) if stars else 0.0,
    )
