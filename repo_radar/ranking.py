"""transparent relevance ranking with novelty"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .models import PreferenceProfile, Recommendation, Repository
from .profile import extract_keywords


def _parse_date(value: str | None) -> datetime | None:
    """
    parse a GitHub timestamp
    :param value: ISO formatted timestamp
    :returns: parsed UTC datetime or none
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _activity_score(repository: Repository, now: datetime) -> float:
    """
    score recent repository activity with gradual decay
    :param repository: candidate repository
    :param now: reference time
    :returns: activity score from zero to one
    """
    pushed = _parse_date(repository.pushed_at or repository.updated_at)
    if not pushed:
        return 0.2
    days = max(0, (now - pushed).days)
    return max(0.0, 1.0 - days / 730)


def _similarity(left: Repository, right: Repository) -> float:
    """
    estimate similarity between two candidates
    :param left: first candidate
    :param right: second candidate
    :returns: similarity from zero to one
    """
    left_topics = set(left.topics)
    right_topics = set(right.topics)
    topic_union = left_topics | right_topics
    topic_score = len(left_topics & right_topics) / len(topic_union) if topic_union else 0.0
    language_score = 1.0 if left.language and left.language == right.language else 0.0
    return 0.7 * topic_score + 0.3 * language_score


def score_repository(
    repository: Repository, profile: PreferenceProfile, now: datetime | None = None
) -> tuple[float, str]:
    """
    calculate a candidate relevance score and explanation
    :param repository: candidate repository
    :param profile: user preference profile
    :param now: optional reference time for deterministic scoring
    :returns: raw score and explanation
    """
    topic_matches = [
        (topic, profile.topics.get(topic.lower(), 0.0)) for topic in repository.topics
    ]
    topic_score = sum(score for _, score in topic_matches[:4]) / 4
    language_score = profile.languages.get(repository.language or "", 0.0)
    keyword_matches = [
        (word, profile.keywords.get(word, 0.0))
        for word in set(extract_keywords(repository.description))
    ]
    keyword_score = sum(score for _, score in keyword_matches[:5]) / 5
    quality_score = min(1.0, math.log10(repository.stars + repository.forks * 2 + 1) / 4)
    activity_score = _activity_score(repository, now or datetime.now(timezone.utc))
    raw = (
        0.38 * topic_score
        + 0.25 * language_score
        + 0.17 * keyword_score
        + 0.1 * activity_score
        + 0.1 * quality_score
    )
    reasons: list[str] = []
    if language_score:
        reasons.append(repository.language or "")
    reasons.extend(
        topic
        for topic, score in sorted(topic_matches, key=lambda item: item[1], reverse=True)
        if score > 0
    )
    reasons.extend(
        word
        for word, score in sorted(keyword_matches, key=lambda item: item[1], reverse=True)
        if score > 0
    )
    reasons = list(dict.fromkeys(reasons))[:3]
    explanation = (
        "strong match for " + ", ".join(reasons)
        if reasons
        else "active repository with useful quality signals"
    )
    return min(1.0, raw), explanation


def rank_candidates(
    repositories: list[Repository], profile: PreferenceProfile, limit: int = 10, now: datetime | None = None
) -> list[Recommendation]:
    """
    rank candidates while penalizing near duplicate selections
    :param repositories: eligible candidate repositories
    :param profile: user preference profile
    :param limit: maximum recommendations
    :param now: optional reference time for deterministic scoring
    :returns: ranked recommendations
    """
    scored = [(repository, *score_repository(repository, profile, now)) for repository in repositories]
    selected: list[Recommendation] = []
    remaining = list(scored)
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda item: item[1]
            - 0.2
            * max(
                (_similarity(item[0], chosen.repository) for chosen in selected),
                default=0.0,
            ),
        )
        novelty_penalty = 0.2 * max((_similarity(best[0], chosen.repository) for chosen in selected), default=0.0)
        selected.append(Recommendation(best[0], max(0.0, best[1] - novelty_penalty), best[2]))
        remaining.remove(best)
    return selected
