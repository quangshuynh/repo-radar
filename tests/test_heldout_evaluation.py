import json
from datetime import datetime, timezone

import pytest

from repo_radar.heldout_evaluation import (
    BASELINE_PATH,
    LANGUAGE_MODES,
    RANKING_WINDOW,
    SNAPSHOT_PATH,
    HeldOutEvaluationError,
    HeldOutSnapshot,
    Trial,
    apply_language_mode,
    build_trial_candidates,
    build_trial_profile,
    build_trials,
    hit_rate_at_k,
    load_snapshot,
    main,
    mean_reciprocal_rank,
    partition_stars,
    rank_distribution,
    recall_at_k,
    run_held_out_evaluation,
    score_components,
    star_exclusion_reason,
    summarize_ranks,
)
from repo_radar.models import ImportedProfile, ImportedRepository, PreferenceProfile, Repository

SNAPSHOT_DATE = "2026-08-25"


def _repository(
    full_name: str,
    language: str | None = "Python",
    topics: list[str] | None = None,
    stars: int = 100,
    description: str | None = "a useful repository for testing ranking",
    **overrides: object,
) -> Repository:
    """
    build a repository with sensible held-out evaluation defaults
    :param full_name: repository identity
    :param language: primary language
    :param topics: repository topics
    :param stars: star count
    :param description: repository description
    :param overrides: additional repository fields
    :returns: repository instance
    """
    fields: dict[str, object] = {
        "description": description,
        "language": language,
        "topics": list(topics if topics is not None else ["cli", "automation"]),
        "stars": stars,
        "forks": stars // 10,
        "owner": full_name.split("/")[0],
        "url": f"https://github.com/{full_name}",
        "pushed_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }
    fields.update(overrides)
    return Repository(full_name=full_name, **fields)


def _snapshot(
    stars: list[Repository] | None = None,
    candidates: list[Repository] | None = None,
    owned_profile: ImportedProfile | None = None,
) -> HeldOutSnapshot:
    """
    build a small in-memory snapshot for metric and leakage tests
    :param stars: starred repositories
    :param candidates: distractor repositories
    :param owned_profile: optional owned repository profile
    :returns: held-out snapshot
    """
    stars = stars or [_repository(f"owner{index}/star{index}") for index in range(4)]
    candidates = candidates or [_repository(f"distractor{index}/repo{index}") for index in range(6)]
    return HeldOutSnapshot(
        snapshot_date=SNAPSHOT_DATE,
        owner="profile-user",
        stars=stars,
        candidates=candidates,
        owned_profile=owned_profile,
    )


# ---------------------------------------------------------------------------
# eligibility
# ---------------------------------------------------------------------------


def test_eligible_star_reports_no_exclusion_reason() -> None:
    """
    a public star with metadata the ranker consumes is usable as a held-out positive
    :returns: nothing
    """
    assert star_exclusion_reason(_repository("owner/repo"), "profile-user") is None


@pytest.mark.parametrize(
    ("repository", "fragment"),
    [
        (_repository("owner/repo", private=True), "private"),
        (_repository("owner/repo", archived=True), "archived"),
        (_repository("profile-user/repo"), "owned by the profile user"),
        (_repository("owner/repo", language=None, topics=[], description=None), "no language, topics"),
        (_repository("owner/repo", pushed_at=None, updated_at=None), "no activity timestamp"),
    ],
)
def test_ineligible_stars_report_an_explicit_reason(repository: Repository, fragment: str) -> None:
    """
    every excluded star carries a reason rather than disappearing silently
    :param repository: candidate star
    :param fragment: expected reason fragment
    :returns: nothing
    """
    reason = star_exclusion_reason(repository, "profile-user")
    assert reason is not None
    assert fragment in reason


def test_partition_stars_separates_eligible_from_excluded() -> None:
    """
    partitioning reports both sides of the eligibility decision
    :returns: nothing
    """
    snapshot = _snapshot(stars=[_repository("owner/good"), _repository("owner/stale", archived=True)])
    eligible, excluded = partition_stars(snapshot)
    assert [repository.full_name for repository in eligible] == ["owner/good"]
    assert excluded == [{"repository": "owner/stale", "reason": excluded[0]["reason"]}]
    assert "archived" in excluded[0]["reason"]


# ---------------------------------------------------------------------------
# deterministic trial splitting
# ---------------------------------------------------------------------------


def test_trials_enumerate_every_split_when_the_star_set_is_small() -> None:
    """
    a small eligible set uses every distinct split rather than a lucky sample
    :returns: nothing
    """
    stars = [_repository(f"owner{index}/repo{index}") for index in range(4)]
    trials, strategy = build_trials(stars, stars, holdout_size=2, max_trials=50)
    assert strategy == "exhaustive"
    assert len(trials) == 6
    assert len({tuple(sorted(trial.holdout_names())) for trial in trials}) == 6


def test_trials_are_deterministic_across_repeated_calls() -> None:
    """
    repeated trial construction produces identical splits in identical order
    :returns: nothing
    """
    stars = [_repository(f"owner{index}/repo{index}") for index in range(8)]
    first, _ = build_trials(stars, stars, holdout_size=2, max_trials=5, seed=7)
    second, _ = build_trials(stars, stars, holdout_size=2, max_trials=5, seed=7)
    assert [sorted(trial.holdout_names()) for trial in first] == [sorted(trial.holdout_names()) for trial in second]


def test_sampled_trials_respect_the_trial_ceiling() -> None:
    """
    a large eligible set falls back to a bounded seeded sample
    :returns: nothing
    """
    stars = [_repository(f"owner{index}/repo{index}") for index in range(12)]
    trials, strategy = build_trials(stars, stars, holdout_size=2, max_trials=5, seed=7)
    assert strategy == "sampled"
    assert len(trials) == 5


def test_training_and_holdout_partition_the_star_set() -> None:
    """
    every trial hides exactly the holdout and trains on everything else
    :returns: nothing
    """
    stars = [_repository(f"owner{index}/repo{index}") for index in range(5)]
    trials, _ = build_trials(stars, stars, holdout_size=2, max_trials=50)
    for trial in trials:
        training = {repository.full_name.lower() for repository in trial.training}
        assert len(trial.holdout) == 2
        assert len(training) == 3
        assert training.isdisjoint(trial.holdout_names())


def test_a_holdout_larger_than_the_star_set_fails_loudly() -> None:
    """
    an impossible split raises instead of silently producing an empty profile
    :returns: nothing
    """
    stars = [_repository("owner/only")]
    with pytest.raises(HeldOutEvaluationError, match="cannot support a holdout"):
        build_trials(stars, stars, holdout_size=2)


# ---------------------------------------------------------------------------
# leakage prevention
# ---------------------------------------------------------------------------


def test_held_out_repositories_never_reach_profile_construction() -> None:
    """
    a held-out repository contributes no language, topic, or keyword signal
    :returns: nothing
    """
    hidden = _repository("owner/hidden", language="Rust", topics=["quantum"], description="quantum lattice solver")
    stars = [hidden, _repository("owner/kept", language="Python", topics=["cli"], description="command line helper")]
    snapshot = _snapshot(stars=stars)
    trial = Trial(index=0, holdout=[hidden], training=[stars[1]])
    profile = build_trial_profile(snapshot, trial)
    assert "Rust" not in profile.languages
    assert "quantum" not in profile.topics
    assert "lattice" not in profile.keywords


def test_owned_repositories_cannot_reintroduce_a_held_out_identity() -> None:
    """
    a held-out repository is stripped from the owned profile source as well
    :returns: nothing
    """
    hidden = _repository("profile-user/hidden", language="Rust", topics=["quantum"])
    owned = ImportedProfile(
        username="profile-user",
        repositories=[
            ImportedRepository(name="hidden", language="Rust", topics=["quantum"], description="quantum solver"),
            ImportedRepository(name="visible", language="Go", topics=["infra"], description="infra helper"),
        ],
    )
    snapshot = _snapshot(stars=[hidden], owned_profile=owned)
    trial = Trial(index=0, holdout=[hidden], training=[_repository("owner/kept")])
    profile = build_trial_profile(snapshot, trial)
    assert "Rust" not in profile.languages
    assert "quantum" not in profile.topics
    assert "Go" in profile.languages


def test_leakage_through_the_training_set_is_rejected() -> None:
    """
    a malformed trial that trains on its own holdout fails loudly
    :returns: nothing
    """
    leaked = _repository("owner/leaked")
    snapshot = _snapshot(stars=[leaked])
    trial = Trial(index=0, holdout=[leaked], training=[leaked])
    with pytest.raises(HeldOutEvaluationError, match="leaked into profile construction"):
        build_trial_profile(snapshot, trial)


# ---------------------------------------------------------------------------
# candidate pool
# ---------------------------------------------------------------------------


def test_candidate_pool_mixes_holdout_positives_with_real_distractors() -> None:
    """
    the pool contains the held-out positives plus the frozen distractors
    :returns: nothing
    """
    hidden = _repository("owner/hidden")
    stars = [hidden, _repository("owner/kept")]
    snapshot = _snapshot(stars=stars)
    trial = Trial(index=0, holdout=[hidden], training=[stars[1]])
    names = {repository.full_name.lower() for repository in build_trial_candidates(snapshot, trial)}
    assert "owner/hidden" in names
    assert len(names) == len(snapshot.candidates) + 1


def test_training_stars_are_excluded_from_the_candidate_pool() -> None:
    """
    a star the profile has already seen is not offered back as a candidate
    :returns: nothing
    """
    hidden = _repository("owner/hidden")
    seen = _repository("owner/seen")
    snapshot = _snapshot(stars=[hidden, seen], candidates=[seen, _repository("distractor/other")])
    trial = Trial(index=0, holdout=[hidden], training=[seen])
    names = {repository.full_name.lower() for repository in build_trial_candidates(snapshot, trial)}
    assert "owner/seen" not in names
    assert "owner/hidden" in names


def test_a_holdout_positive_lost_to_production_filtering_fails_loudly() -> None:
    """
    a positive the production filter would drop is an error, not a silent zero
    :returns: nothing
    """
    hidden = _repository("owner/hidden", archived=True)
    snapshot = _snapshot(stars=[hidden])
    trial = Trial(index=0, holdout=[hidden], training=[_repository("owner/kept")])
    with pytest.raises(HeldOutEvaluationError, match="lost held-out positives"):
        build_trial_candidates(snapshot, trial)


def test_candidates_are_not_duplicated_when_a_positive_is_also_a_distractor() -> None:
    """
    a held-out star already present in the frozen pool appears exactly once
    :returns: nothing
    """
    hidden = _repository("owner/hidden")
    snapshot = _snapshot(stars=[hidden], candidates=[hidden, _repository("distractor/other")])
    trial = Trial(index=0, holdout=[hidden], training=[_repository("owner/kept")])
    candidates = build_trial_candidates(snapshot, trial)
    names = [repository.full_name.lower() for repository in candidates]
    assert names.count("owner/hidden") == 1


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def test_hit_rate_counts_trials_with_at_least_one_recovered_positive() -> None:
    """
    hit rate is per trial, not per positive
    :returns: nothing
    """
    trials = [[3, 40], [60, 70], [1, 2]]
    assert hit_rate_at_k(trials, 5) == pytest.approx(2 / 3)
    assert hit_rate_at_k(trials, 100) == pytest.approx(1.0)
    assert hit_rate_at_k([], 10) == 0.0


def test_hit_rate_treats_unranked_positives_as_misses() -> None:
    """
    a positive outside the ranking window never counts as a hit
    :returns: nothing
    """
    assert hit_rate_at_k([[None, None]], 20) == 0.0
    assert hit_rate_at_k([[None, 4]], 20) == 1.0


def test_recall_counts_every_positive_not_just_the_first() -> None:
    """
    recall is measured across all held-out positives
    :returns: nothing
    """
    trials = [[3, 40], [8, 9]]
    assert recall_at_k(trials, 10) == pytest.approx(3 / 4)
    assert recall_at_k(trials, 5) == pytest.approx(1 / 4)
    assert recall_at_k([], 10) == 0.0


def test_mean_reciprocal_rank_uses_the_first_recovered_positive() -> None:
    """
    MRR averages one reciprocal rank per trial
    :returns: nothing
    """
    assert mean_reciprocal_rank([[2, 4]]) == pytest.approx(0.5)
    assert mean_reciprocal_rank([[2], [4]]) == pytest.approx((0.5 + 0.25) / 2)


def test_mean_reciprocal_rank_is_zero_when_nothing_is_recovered() -> None:
    """
    a trial that recovers nothing contributes zero rather than raising
    :returns: nothing
    """
    assert mean_reciprocal_rank([[None, None]]) == 0.0
    assert mean_reciprocal_rank([]) == 0.0


def test_rank_distribution_reports_median_and_keeps_misses_visible() -> None:
    """
    unranked positives are excluded from rank statistics and counted separately
    :returns: nothing
    """
    distribution = rank_distribution([[1, 3], [5, None]])
    assert distribution["median_rank"] == pytest.approx(3.0)
    assert distribution["ranked_positives"] == 3
    assert distribution["unranked_positives"] == 1
    assert distribution["best_rank"] == 1
    assert distribution["worst_rank"] == 5


def test_rank_distribution_is_defined_when_nothing_is_recovered() -> None:
    """
    a completely failed run reports none rather than crashing on an empty median
    :returns: nothing
    """
    distribution = rank_distribution([[None], [None]])
    assert distribution["median_rank"] is None
    assert distribution["mean_rank"] is None
    assert distribution["ranked_positives"] == 0
    assert distribution["unranked_positives"] == 2


def test_summarize_ranks_produces_every_documented_metric() -> None:
    """
    the metric set covers the cutoffs, MRR, and the rank distribution
    :returns: nothing
    """
    metrics = summarize_ranks([[2, 12], [30, None]])
    for key in ("hit_rate_at_5", "hit_rate_at_10", "hit_rate_at_20", "recall_at_10", "mrr", "median_rank"):
        assert key in metrics
    assert metrics["hit_rate_at_5"] == pytest.approx(0.5)
    assert metrics["recall_at_20"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# baselines and ablations
# ---------------------------------------------------------------------------


def test_popularity_baseline_orders_by_popularity_alone() -> None:
    """
    the popularity baseline ignores the profile entirely
    :returns: nothing
    """
    from repo_radar.heldout_evaluation import RANKERS

    candidates = [
        _repository("owner/small", stars=1),
        _repository("owner/huge", stars=90000),
        _repository("owner/medium", stars=500),
    ]
    profile = PreferenceProfile(languages={"Python": 1.0})
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    ordering = RANKERS["popularity"](candidates, profile, now, 1, RANKING_WINDOW)
    assert [repository.full_name for repository in ordering] == ["owner/huge", "owner/medium", "owner/small"]


def test_random_baseline_is_deterministic_for_a_given_seed() -> None:
    """
    the sanity floor is reproducible across runs
    :returns: nothing
    """
    from repo_radar.heldout_evaluation import RANKERS

    candidates = [_repository(f"owner/repo{index}") for index in range(10)]
    profile = PreferenceProfile()
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    first = RANKERS["random"](candidates, profile, now, 5, RANKING_WINDOW)
    second = RANKERS["random"](candidates, profile, now, 5, RANKING_WINDOW)
    assert [repository.full_name for repository in first] == [repository.full_name for repository in second]


def test_language_ablations_do_not_mutate_the_production_profile() -> None:
    """
    an evaluation-only reweighting returns a new profile and leaves the original intact
    :returns: nothing
    """
    profile = PreferenceProfile(languages={"Python": 1.0, "Go": 0.25}, topics={"cli": 1.0}, keywords={"fast": 1.0})
    original = dict(profile.languages)
    uniform = apply_language_mode(profile, "uniform")
    compressed = apply_language_mode(profile, "compressed")
    assert profile.languages == original
    assert uniform.languages == {"Python": 1.0, "Go": 1.0}
    assert compressed.languages == {"Python": 1.0, "Go": 0.5}
    assert uniform.topics == profile.topics
    uniform.languages["Go"] = 0.0
    assert profile.languages == original


def test_current_language_mode_reproduces_production_weights() -> None:
    """
    the control arm applies no transformation at all
    :returns: nothing
    """
    profile = PreferenceProfile(languages={"Python": 1.0, "Go": 0.25})
    assert apply_language_mode(profile, "current").languages == profile.languages


def test_an_unknown_language_mode_fails_loudly() -> None:
    """
    a typo in an ablation name raises rather than silently falling back
    :returns: nothing
    """
    with pytest.raises(HeldOutEvaluationError, match="unknown language mode"):
        apply_language_mode(PreferenceProfile(), "does-not-exist")


# ---------------------------------------------------------------------------
# score decomposition
# ---------------------------------------------------------------------------


def test_score_components_sum_to_the_production_score() -> None:
    """
    the diagnostic decomposition reads production scoring rather than reimplementing it
    :returns: nothing
    """
    from repo_radar.ranking import score_repository

    profile = PreferenceProfile(
        languages={"Python": 1.0, "Go": 0.4},
        topics={"cli": 1.0, "automation": 0.3},
        keywords={"useful": 0.6, "testing": 0.2},
    )
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    for repository in (
        _repository("owner/plain"),
        _repository("owner/unmatched", language="Rust", topics=["quantum"], description=None),
        _repository("owner/popular", stars=90000),
    ):
        components = score_components(repository, profile, now)
        expected, _ = score_repository(repository, profile, now)
        assert sum(components.values()) == pytest.approx(expected, abs=1e-6)


def test_score_components_cover_every_weighted_term() -> None:
    """
    the decomposition reports all five scoring terms
    :returns: nothing
    """
    components = score_components(
        _repository("owner/repo"), PreferenceProfile(), datetime(2026, 8, 25, tzinfo=timezone.utc)
    )
    assert set(components) == {"topic", "language", "keyword", "activity", "quality"}


# ---------------------------------------------------------------------------
# snapshot loading and privacy
# ---------------------------------------------------------------------------


def test_a_snapshot_containing_a_private_repository_is_rejected(tmp_path) -> None:
    """
    private identities must never survive into a committed evaluation snapshot
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "snapshot_date": SNAPSHOT_DATE,
                "owner": "profile-user",
                "stars": [_repository("owner/secret", private=True).to_dict()],
                "candidates": [_repository("distractor/repo").to_dict()],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(HeldOutEvaluationError, match="private repositories"):
        load_snapshot(path)


def test_a_missing_snapshot_explains_how_to_regenerate_it(tmp_path) -> None:
    """
    a missing snapshot points at the generation command instead of a bare traceback
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    with pytest.raises(HeldOutEvaluationError, match="heldout_snapshot"):
        load_snapshot(tmp_path / "absent.json")


def test_the_checked_in_snapshot_parses_and_is_public() -> None:
    """
    the committed snapshot loads and contains no private repository identities
    :returns: nothing
    """
    snapshot = load_snapshot(SNAPSHOT_PATH)
    assert snapshot.stars
    assert snapshot.candidates
    assert not any(repository.private for repository in [*snapshot.stars, *snapshot.candidates])


# ---------------------------------------------------------------------------
# end to end determinism
# ---------------------------------------------------------------------------


def test_repeated_evaluation_produces_identical_results() -> None:
    """
    the experiment is deterministic across runs within a process
    :returns: nothing
    """
    first = run_held_out_evaluation(max_trials=3, window=25)
    second = run_held_out_evaluation(max_trials=3, window=25)
    assert first == second


def test_evaluation_recovers_held_out_stars_and_reports_coverage() -> None:
    """
    a real snapshot run reports coverage, trial configuration, baselines, and ablations
    :returns: nothing
    """
    report = run_held_out_evaluation(max_trials=3, window=25)
    assert report["coverage"]["eligible_stars"] > 0
    assert report["trial_configuration"]["holdout_per_trial"] == 2
    assert set(report["language_ablations"]) == set(LANGUAGE_MODES)
    assert "popularity" in report["baselines"]
    assert report["production"]["unranked_positives"] >= 0


def test_the_checked_in_baseline_matches_the_current_trial_configuration() -> None:
    """
    the recorded baseline documents the configuration the code actually runs
    :returns: nothing
    """
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    configuration = baseline["trial_configuration"]
    assert configuration["ranking_window"] == RANKING_WINDOW
    assert configuration["selection"] == "exhaustive"
    assert baseline["production"]["unranked_positives"] == 0


def test_the_command_line_runs_and_emits_json(capsys) -> None:
    """
    the held-out CLI supports human readable and machine readable output
    :param capsys: pytest capture fixture
    :returns: nothing
    """
    assert main(["--max-trials", "2", "--window", "20"]) == 0
    assert "Held-out star evaluation" in capsys.readouterr().out
    assert main(["--max-trials", "2", "--window", "20", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trial_configuration"]["trials"] == 2


def test_the_command_line_reports_a_missing_snapshot(tmp_path, capsys) -> None:
    """
    a missing snapshot exits non zero with a readable message
    :param tmp_path: pytest temporary directory
    :param capsys: pytest capture fixture
    :returns: nothing
    """
    assert main(["--snapshot", str(tmp_path / "absent.json")]) == 1
    assert "held-out evaluation failed" in capsys.readouterr().err
