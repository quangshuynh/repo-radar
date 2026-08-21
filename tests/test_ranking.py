from datetime import datetime, timezone

from repo_radar.models import PreferenceProfile, Repository
from repo_radar.ranking import rank_candidates, score_repository

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_relevant_small_repository_beats_irrelevant_popular_one() -> None:
    """
    relevance outweighs raw popularity
    :returns: nothing
    """
    profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0}, {"workflow": 1.0})
    relevant = Repository(
        "small/relevant",
        "workflow helper",
        "Python",
        ["automation"],
        30,
        pushed_at="2025-12-01T00:00:00Z",
    )
    popular = Repository("huge/unrelated", "unrelated game", "C++", ["game"], 500000, pushed_at="2025-12-01T00:00:00Z")
    ranked = rank_candidates([popular, relevant], profile, now=NOW)
    assert ranked[0].repository == relevant
    assert "Python" in ranked[0].explanation


def test_missing_metadata_can_be_scored() -> None:
    """
    candidates with missing optional metadata remain rankable
    :returns: nothing
    """
    score, explanation = score_repository(Repository("x/y"), PreferenceProfile(), NOW)
    assert score >= 0
    assert explanation


def test_novelty_penalizes_repeated_candidates() -> None:
    """
    a near duplicate selected later receives a novelty penalty
    :returns: nothing
    """
    profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0}, {})
    first = Repository("a/one", language="Python", topics=["automation"], stars=100)
    second = Repository("b/two", language="Python", topics=["automation"], stars=99)
    ranked = rank_candidates([first, second], profile, now=NOW)
    raw_second, _ = score_repository(ranked[1].repository, profile, NOW)
    assert ranked[1].score < raw_second
