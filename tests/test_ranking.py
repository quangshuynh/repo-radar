from datetime import datetime, timezone

import pytest

from repo_radar.models import PreferenceProfile, Repository
from repo_radar.ranking import (
    DUPLICATE_NOVELTY_WEIGHT,
    DUPLICATE_SIMILARITY_THRESHOLD,
    NOVELTY_WEIGHT,
    _redundancy_penalty,
    candidate_similarity,
    rank_candidates,
    score_repository,
)

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


def test_topic_order_does_not_change_score() -> None:
    """
    reordering repository topics must not change the raw score
    :returns: nothing
    """
    profile = PreferenceProfile({}, {"alpha": 1.0, "beta": 0.8, "gamma": 0.6, "delta": 0.4, "epsilon": 0.2}, {})
    topics = ["alpha", "beta", "gamma", "delta", "epsilon"]
    forward, forward_explanation = score_repository(Repository("a/b", topics=list(topics)), profile, NOW)
    reversed_score, reversed_explanation = score_repository(
        Repository("a/b", topics=list(reversed(topics))), profile, NOW
    )
    assert forward == reversed_score
    assert forward_explanation == reversed_explanation


def test_strongest_topics_survive_the_match_bound() -> None:
    """
    the bounded topic set keeps the strongest preference evidence rather than the first declared topics
    :returns: nothing
    """
    profile = PreferenceProfile({}, {"weak": 0.1, "strong": 1.0}, {})
    filler = ["noise-one", "noise-two", "noise-three"]
    buried = Repository("a/b", topics=[*filler, "weak", "strong"])
    exposed = Repository("a/b", topics=["strong", "weak", *filler])
    assert score_repository(buried, profile, NOW)[0] == score_repository(exposed, profile, NOW)[0]


def test_duplicate_topic_casing_is_equivalent_evidence() -> None:
    """
    a topic repeated in a different case must not count twice
    :returns: nothing
    """
    profile = PreferenceProfile({}, {"automation": 1.0}, {})
    single, _ = score_repository(Repository("a/b", topics=["automation"]), profile, NOW)
    duplicated, _ = score_repository(Repository("a/b", topics=["automation", "Automation"]), profile, NOW)
    assert single == duplicated


def test_keyword_evidence_is_deterministic_for_equivalent_descriptions() -> None:
    """
    equivalent description keyword evidence must score identically regardless of word order
    :returns: nothing
    """
    profile = PreferenceProfile(
        {}, {}, {"alpha": 1.0, "beta": 0.8, "gamma": 0.6, "delta": 0.4, "epsilon": 0.2, "zeta": 0.1}
    )
    forward, _ = score_repository(Repository("a/b", "alpha beta gamma delta epsilon zeta"), profile, NOW)
    backward, _ = score_repository(Repository("a/b", "zeta epsilon delta gamma beta alpha"), profile, NOW)
    assert forward == backward


def test_candidate_input_order_does_not_change_ranking() -> None:
    """
    shuffling distinctly scored candidates must not change the ranked order
    :returns: nothing
    """
    profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0, "cli": 0.5}, {"workflow": 1.0})
    candidates = [
        Repository("a/one", "workflow runner", "Python", ["automation", "cli"], 400, pushed_at="2025-12-01T00:00:00Z"),
        Repository("b/two", "unrelated game", "C++", ["gamedev"], 90000, pushed_at="2025-12-01T00:00:00Z"),
        Repository("c/three", "workflow notes", "Rust", ["cli"], 1200, pushed_at="2025-06-01T00:00:00Z"),
        Repository("d/four", "recipe website", "Ruby", ["recipes"], 40, pushed_at="2024-01-01T00:00:00Z"),
    ]
    forward = [item.repository.full_name for item in rank_candidates(candidates, profile, now=NOW)]
    backward = [item.repository.full_name for item in rank_candidates(list(reversed(candidates)), profile, now=NOW)]
    assert forward == backward


def test_stronger_preference_evidence_does_not_reduce_relevance() -> None:
    """
    adding a matching topic to the profile must not lower a candidate raw score
    :returns: nothing
    """
    repository = Repository("a/b", "workflow runner", "Python", ["automation", "cli"], 400)
    weak_profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0}, {})
    strong_profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0, "cli": 0.9}, {})
    assert score_repository(repository, strong_profile, NOW)[0] >= score_repository(repository, weak_profile, NOW)[0]


def test_popularity_alone_does_not_overcome_a_large_relevance_gap() -> None:
    """
    an enormous irrelevant repository must not outrank a small strongly relevant one
    :returns: nothing
    """
    profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0, "cli": 1.0}, {"workflow": 1.0})
    relevant = Repository(
        "small/relevant", "workflow automation", "Python", ["automation", "cli"], 25, pushed_at="2025-12-01T00:00:00Z"
    )
    popular = Repository(
        "huge/unrelated", "unrelated game engine", "C++", ["gamedev"], 400000, 90000, pushed_at="2025-12-01T00:00:00Z"
    )
    ranked = rank_candidates([popular, relevant], profile, now=NOW)
    assert ranked[0].repository.full_name == "small/relevant"


def test_activity_alone_does_not_overcome_a_large_relevance_gap() -> None:
    """
    a freshly pushed irrelevant repository must not outrank a stale strongly relevant one
    :returns: nothing
    """
    profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0, "cli": 1.0}, {"workflow": 1.0})
    relevant = Repository(
        "stale/relevant", "workflow automation", "Python", ["automation", "cli"], 500, pushed_at="2025-02-01T00:00:00Z"
    )
    fresh = Repository(
        "fresh/unrelated", "unrelated recipes", "Ruby", ["recipes"], 500, pushed_at="2025-12-31T00:00:00Z"
    )
    ranked = rank_candidates([fresh, relevant], profile, now=NOW)
    assert ranked[0].repository.full_name == "stale/relevant"


def test_novelty_does_not_change_the_first_selection() -> None:
    """
    the novelty adjustment must never displace the highest raw scoring candidate
    :returns: nothing
    """
    profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0, "cli": 0.7}, {"workflow": 0.5})
    candidates = [
        Repository(
            "a/one", "workflow automation", "Python", ["automation", "cli"], 900, pushed_at="2025-11-01T00:00:00Z"
        ),
        Repository(
            "b/two", "workflow automation clone", "Python", ["automation", "cli"], 880, pushed_at="2025-11-01T00:00:00Z"
        ),
        Repository("c/three", "unrelated feeds", "Ruby", ["rss"], 300, pushed_at="2025-11-01T00:00:00Z"),
    ]
    best_raw = max(candidates, key=lambda repository: score_repository(repository, profile, NOW)[0])
    ranked = rank_candidates(candidates, profile, now=NOW)
    assert ranked[0].repository.full_name == best_raw.full_name


def test_novelty_promotes_a_less_redundant_second_recommendation() -> None:
    """
    a slightly weaker but less redundant candidate can be promoted ahead of a near duplicate
    :returns: nothing
    """
    profile = PreferenceProfile({"Python": 1.0}, {"automation": 1.0, "cli": 1.0, "devops": 1.0}, {"workflow": 1.0})
    leader = Repository(
        "a/leader", "workflow automation", "Python", ["automation", "cli"], 900, pushed_at="2025-11-01T00:00:00Z"
    )
    duplicate = Repository(
        "b/duplicate", "workflow automation", "Python", ["automation", "cli"], 880, pushed_at="2025-11-01T00:00:00Z"
    )
    different = Repository(
        "c/different", "workflow automation", "Python", ["devops"], 850, pushed_at="2025-11-01T00:00:00Z"
    )
    raw_duplicate, _ = score_repository(duplicate, profile, NOW)
    raw_different, _ = score_repository(different, profile, NOW)
    assert raw_duplicate > raw_different
    assert candidate_similarity(leader, duplicate) > candidate_similarity(leader, different)
    ranked = rank_candidates([leader, duplicate, different], profile, now=NOW)
    assert ranked[0].repository.full_name == "a/leader"
    assert ranked[1].repository.full_name == "c/different"


def _duplicate_pair_profile() -> PreferenceProfile:
    """
    build a profile that scores the shared duplicate metadata strongly
    :returns: preference profile for duplicate suppression tests
    """
    return PreferenceProfile(
        {"Python": 1.0},
        {"automation": 1.0, "cli": 1.0, "task-runner": 1.0, "devops": 0.9},
        {"workflow": 1.0, "runner": 1.0},
    )


def test_effective_duplicate_loses_its_prominent_slot() -> None:
    """
    a maximally similar duplicate is demoted behind weaker but distinct candidates
    :returns: nothing
    """
    profile = _duplicate_pair_profile()
    topics = ["automation", "cli", "task-runner"]
    leader = Repository("a/leader", "workflow runner", "Python", topics, 900, pushed_at="2025-11-01T00:00:00Z")
    duplicate = Repository("b/duplicate", "workflow runner", "Python", topics, 880, pushed_at="2025-11-01T00:00:00Z")
    weaker = Repository("c/weaker", "unrelated feeds", "Ruby", ["rss"], 40, pushed_at="2024-06-01T00:00:00Z")
    assert candidate_similarity(leader, duplicate) == pytest.approx(1.0)
    raw_duplicate, _ = score_repository(duplicate, profile, NOW)
    raw_weaker, _ = score_repository(weaker, profile, NOW)
    assert raw_duplicate > raw_weaker
    ranked = rank_candidates([leader, duplicate, weaker], profile, now=NOW)
    assert [item.repository.full_name for item in ranked] == ["a/leader", "c/weaker", "b/duplicate"]


def test_moderate_overlap_is_not_treated_as_a_duplicate() -> None:
    """
    sharing four of five topics stays below the duplicate threshold and keeps the soft penalty
    :returns: nothing
    """
    profile = _duplicate_pair_profile()
    leader = Repository(
        "a/leader",
        "workflow runner",
        "Python",
        ["automation", "cli", "task-runner", "devops"],
        900,
        pushed_at="2025-11-01T00:00:00Z",
    )
    overlapping = Repository(
        "b/overlapping",
        "workflow runner",
        "Python",
        ["automation", "cli", "task-runner", "devops", "packaging"],
        880,
        pushed_at="2025-11-01T00:00:00Z",
    )
    weaker = Repository("c/weaker", "unrelated feeds", "Ruby", ["rss"], 40, pushed_at="2024-06-01T00:00:00Z")
    similarity = candidate_similarity(leader, overlapping)
    assert 0.8 < similarity < DUPLICATE_SIMILARITY_THRESHOLD
    ranked = rank_candidates([leader, overlapping, weaker], profile, now=NOW)
    assert [item.repository.full_name for item in ranked] == ["a/leader", "b/overlapping", "c/weaker"]


def test_identical_topics_in_another_language_are_not_duplicates() -> None:
    """
    the same idea implemented in a different language stays eligible for a prominent slot
    :returns: nothing
    """
    profile = _duplicate_pair_profile()
    topics = ["automation", "cli", "task-runner"]
    leader = Repository("a/leader", "workflow runner", "Python", topics, 900, pushed_at="2025-11-01T00:00:00Z")
    ported = Repository("b/ported", "workflow runner", "Rust", topics, 880, pushed_at="2025-11-01T00:00:00Z")
    weaker = Repository("c/weaker", "unrelated feeds", "Ruby", ["rss"], 40, pushed_at="2024-06-01T00:00:00Z")
    assert candidate_similarity(leader, ported) == pytest.approx(0.7)
    ranked = rank_candidates([leader, ported, weaker], profile, now=NOW)
    assert [item.repository.full_name for item in ranked] == ["a/leader", "b/ported", "c/weaker"]


def test_duplicate_suppression_does_not_change_the_first_selection() -> None:
    """
    the strongest raw candidate is still selected first when the pool is full of duplicates
    :returns: nothing
    """
    profile = _duplicate_pair_profile()
    topics = ["automation", "cli", "task-runner"]
    candidates = [
        Repository("b/second", "workflow runner", "Python", topics, 880, pushed_at="2025-11-01T00:00:00Z"),
        Repository("a/leader", "workflow runner", "Python", topics, 900, pushed_at="2025-11-01T00:00:00Z"),
        Repository("c/third", "workflow runner", "Python", topics, 870, pushed_at="2025-11-01T00:00:00Z"),
    ]
    best_raw = max(candidates, key=lambda repository: score_repository(repository, profile, NOW)[0])
    ranked = rank_candidates(candidates, profile, now=NOW)
    assert ranked[0].repository.full_name == best_raw.full_name
    assert len(ranked) == len(candidates)


def test_duplicate_suppression_is_order_independent() -> None:
    """
    duplicate demotion produces the same ranking regardless of candidate input order
    :returns: nothing
    """
    profile = _duplicate_pair_profile()
    topics = ["automation", "cli", "task-runner"]
    candidates = [
        Repository("a/leader", "workflow runner", "Python", topics, 900, pushed_at="2025-11-01T00:00:00Z"),
        Repository("b/duplicate", "workflow runner", "Python", topics, 880, pushed_at="2025-11-01T00:00:00Z"),
        Repository("c/distinct", "cluster operations", "Go", ["devops"], 700, pushed_at="2025-11-01T00:00:00Z"),
        Repository("d/weaker", "unrelated feeds", "Ruby", ["rss"], 40, pushed_at="2024-06-01T00:00:00Z"),
    ]
    forward = [item.repository.full_name for item in rank_candidates(candidates, profile, now=NOW)]
    backward = [item.repository.full_name for item in rank_candidates(list(reversed(candidates)), profile, now=NOW)]
    assert forward == backward
    assert forward.index("b/duplicate") > forward.index("c/distinct")


def test_redundancy_penalty_is_monotonic_across_the_duplicate_threshold() -> None:
    """
    the penalty never decreases as similarity rises, including at the duplicate cliff
    :returns: nothing
    """
    steps = [index / 100 for index in range(101)]
    penalties = [_redundancy_penalty(similarity) for similarity in steps]
    assert penalties == sorted(penalties)
    assert _redundancy_penalty(0.0) == 0.0
    assert _redundancy_penalty(0.5) == pytest.approx(NOVELTY_WEIGHT * 0.5)
    assert _redundancy_penalty(1.0) == pytest.approx(DUPLICATE_NOVELTY_WEIGHT)
