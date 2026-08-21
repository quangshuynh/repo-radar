"""transparent preference profile calculation"""

from __future__ import annotations

import re
import statistics
from collections import Counter

from .models import PreferenceProfile, Repository, SeedPreferences

SEED_SIGNAL_WEIGHT = 2.0

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "that", "the", "this", "to", "with", "your", "using", "tool",
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
    repositories: list[Repository], seed_preferences: SeedPreferences | None = None
) -> PreferenceProfile:
    """
    build a normalized profile from starred repositories and manual preferences
    :param repositories: repositories used as positive preferences
    :param seed_preferences: optional manually entered interests
    :returns: calculated preference profile
    """
    languages = Counter(repository.language for repository in repositories if repository.language)
    topics = Counter(topic.lower() for repository in repositories for topic in repository.topics)
    keywords = Counter(word for repository in repositories for word in set(extract_keywords(repository.description)))
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
