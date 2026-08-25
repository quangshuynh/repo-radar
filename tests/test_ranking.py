from datetime import datetime, timezone

from repo_radar.models import PreferenceProfile, Repository
from repo_radar.ranking import candidate_similarity, rank_candidates, score_repository

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
