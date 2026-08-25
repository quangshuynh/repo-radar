"""transparent relevance ranking with novelty"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime, timezone

from .models import PreferenceProfile, Recommendation, Repository
from .profile import extract_keywords

TOPIC_MATCH_LIMIT = 4
KEYWORD_MATCH_LIMIT = 5
NOVELTY_WEIGHT = 0.2

# candidate_similarity is 0.7 * topic jaccard + 0.3 * shared language, so reaching this
# threshold requires the same language plus a topic jaccard of at least six sevenths. For
# repositories carrying the usual three to five topics the only reachable similarity at or
# above it is 1.0: an identical topic set in the same language, which leaves the ranker no
# metadata to tell the two candidates apart. Sharing four of five topics scores 0.86 and
# deliberately stays below the threshold, so a current tool and its stale predecessor keep
# the soft penalty. Two repositories in different languages cannot exceed 0.7 and are never
# treated as duplicates.
DUPLICATE_SIMILARITY_THRESHOLD = 0.9

# At maximal similarity a duplicate retains none of its raw relevance, because raw scores are
# capped at 1.0. That guarantees an effective duplicate sorts behind every candidate holding a
# positive adjusted score instead of merely losing a fifth of its own.
DUPLICATE_NOVELTY_WEIGHT = 1.0


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


def _strongest_matches(matches: Iterable[tuple[str, float]], limit: int) -> list[tuple[str, float]]:
    """
    order scored preference evidence deterministically before bounding it
    :param matches: scored preference evidence
    :param limit: maximum retained matches
    :returns: strongest matches ordered by score then name
    """
    return sorted(matches, key=lambda item: (-item[1], item[0]))[:limit]


def candidate_similarity(left: Repository, right: Repository) -> float:
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


def _redundancy_penalty(similarity: float) -> float:
    """
    convert a similarity into a penalty, suppressing effective duplicates far more strongly
    :param similarity: similarity against the most similar selected recommendation
    :returns: penalty to subtract from the raw score
    """
    weight = DUPLICATE_NOVELTY_WEIGHT if similarity >= DUPLICATE_SIMILARITY_THRESHOLD else NOVELTY_WEIGHT
    return weight * similarity


def _novelty_penalty(repository: Repository, selected: list[Recommendation]) -> float:
    """
    calculate the redundancy penalty against already selected recommendations
    :param repository: candidate repository
    :param selected: recommendations already chosen
    :returns: novelty penalty to subtract from the raw score
    """
    similarities = (candidate_similarity(repository, chosen.repository) for chosen in selected)
    return _redundancy_penalty(max(similarities, default=0.0))


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
    topics = dict.fromkeys(topic.lower() for topic in repository.topics)
    topic_matches = _strongest_matches(((topic, profile.topics.get(topic, 0.0)) for topic in topics), TOPIC_MATCH_LIMIT)
    topic_score = sum(score for _, score in topic_matches) / TOPIC_MATCH_LIMIT
    language_score = profile.languages.get(repository.language or "", 0.0)
    keyword_matches = _strongest_matches(
        ((word, profile.keywords.get(word, 0.0)) for word in set(extract_keywords(repository.description))),
        KEYWORD_MATCH_LIMIT,
    )
    keyword_score = sum(score for _, score in keyword_matches) / KEYWORD_MATCH_LIMIT
    quality_score = min(1.0, math.log10(repository.stars + repository.forks * 2 + 1) / 4)
    activity_score = _activity_score(repository, now or datetime.now(timezone.utc))
    raw = 0.38 * topic_score + 0.25 * language_score + 0.17 * keyword_score + 0.1 * activity_score + 0.1 * quality_score
    reasons: list[str] = []
    if language_score:
        reasons.append(repository.language or "")
    reasons.extend(topic for topic, score in topic_matches if score > 0)
    reasons.extend(word for word, score in keyword_matches if score > 0)
    reasons = list(dict.fromkeys(reasons))[:3]
    explanation = (
        "strong match for " + ", ".join(reasons) if reasons else "active repository with useful quality signals"
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
        best = min(
            remaining,
            key=lambda item: (-(item[1] - _novelty_penalty(item[0], selected)), item[0].full_name),
        )
        novelty_penalty = _novelty_penalty(best[0], selected)
        selected.append(Recommendation(best[0], max(0.0, best[1] - novelty_penalty), best[2]))
        remaining.remove(best)
    return selected
