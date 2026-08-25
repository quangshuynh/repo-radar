import json

import pytest

from repo_radar.evaluation import (
    BASELINE_PATH,
    RELEVANT_THRESHOLD,
    TOP_K,
    Corpus,
    EvaluationError,
    discounted_cumulative_gain,
    format_report,
    load_corpus,
    load_scenarios,
    main,
    maximum_pairwise_similarity,
    mean_pairwise_similarity,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    run_evaluation,
    scenario_candidates,
)
from repo_radar.models import Repository


def test_discounted_cumulative_gain_uses_graded_gains() -> None:
    """
    discounted cumulative gain applies exponential gains and logarithmic discounts
    :returns: nothing
    """
    assert discounted_cumulative_gain([]) == 0.0
    assert discounted_cumulative_gain([3]) == pytest.approx(7.0)
    assert discounted_cumulative_gain([3, 1]) == pytest.approx(7.0 + 1.0 / 1.5849625007211562)


def test_ndcg_is_one_for_the_ideal_ranking() -> None:
    """
    a perfectly ordered result set scores one and a reversed one scores less
    :returns: nothing
    """
    all_labels = [3, 2, 1, 0]
    assert ndcg_at_k([3, 2, 1, 0], all_labels, 4) == pytest.approx(1.0)
    assert ndcg_at_k([0, 1, 2, 3], all_labels, 4) < 1.0


def test_ndcg_is_zero_when_no_candidate_is_relevant() -> None:
    """
    an all zero label set produces a defined zero score rather than a division error
    :returns: nothing
    """
    assert ndcg_at_k([0, 0], [0, 0, 0], 2) == 0.0


def test_ndcg_rewards_ranking_strong_results_earlier() -> None:
    """
    moving a strongly relevant result earlier must not lower NDCG
    :returns: nothing
    """
    all_labels = [3, 2, 2, 0, 0]
    early = ndcg_at_k([3, 2, 0, 0, 2], all_labels, 5)
    late = ndcg_at_k([0, 2, 0, 2, 3], all_labels, 5)
    assert early > late


def test_precision_at_k_counts_only_the_returned_window() -> None:
    """
    precision measures the share of returned results meeting the threshold
    :returns: nothing
    """
    assert precision_at_k([3, 2, 1, 0], 4) == pytest.approx(0.5)
    assert precision_at_k([3, 2, 1, 0], 2) == pytest.approx(1.0)
    assert precision_at_k([], 10) == 0.0


def test_recall_at_k_measures_coverage_of_all_relevant_candidates() -> None:
    """
    recall compares retrieved relevant results against every relevant candidate
    :returns: nothing
    """
    all_labels = [3, 3, 2, 1, 0]
    assert recall_at_k([3, 3], all_labels, 2) == pytest.approx(2 / 3)
    assert recall_at_k([3, 3, 2], all_labels, 3) == pytest.approx(1.0)
    assert recall_at_k([1, 0], [0, 0, 1], 2) == 0.0


def test_reciprocal_rank_finds_the_first_strongly_relevant_result() -> None:
    """
    the reciprocal rank reflects the position of the first result meeting the threshold
    :returns: nothing
    """
    assert reciprocal_rank([3, 0, 0]) == pytest.approx(1.0)
    assert reciprocal_rank([0, 0, 3]) == pytest.approx(1 / 3)
    assert reciprocal_rank([2, 2, 2]) == 0.0
    assert reciprocal_rank([2, 2, 2], threshold=RELEVANT_THRESHOLD) == pytest.approx(1.0)


def test_pairwise_similarity_diagnostics_bound_correctly() -> None:
    """
    redundancy diagnostics are zero for a single result and one for identical results
    :returns: nothing
    """
    first = Repository("a/one", language="Python", topics=["cli", "automation"])
    identical = Repository("b/two", language="Python", topics=["cli", "automation"])
    unrelated = Repository("c/three", language="Go", topics=["gamedev"])
    assert mean_pairwise_similarity([first]) == 0.0
    assert maximum_pairwise_similarity([]) == 0.0
    assert mean_pairwise_similarity([first, identical]) == pytest.approx(1.0)
    assert mean_pairwise_similarity([first, unrelated]) == 0.0
    assert maximum_pairwise_similarity([first, identical, unrelated]) == pytest.approx(1.0)


def test_corpus_fixture_parses_and_is_frozen() -> None:
    """
    the checked in corpus parses into repositories and carries a snapshot date
    :returns: nothing
    """
    corpus = load_corpus()
    assert len(corpus.repositories) >= 40
    assert corpus.snapshot_date
    assert corpus.evaluated_at().isoformat().startswith(corpus.snapshot_date)
    assert all(repository.full_name and repository.owner for repository in corpus.repositories)


def test_scenario_fixtures_parse_and_label_every_candidate() -> None:
    """
    each scenario provides graded labels for every candidate that survives filtering
    :returns: nothing
    """
    corpus = load_corpus()
    scenarios = load_scenarios()
    assert len(scenarios) >= 3
    for scenario in scenarios:
        candidates = scenario_candidates(scenario, corpus)
        assert candidates
        assert all(scenario.label_for(repository) in {0, 1, 2, 3} for repository in candidates)
        assert not any(repository.full_name in scenario.starred for repository in candidates)


def test_scenario_candidates_reject_missing_labels() -> None:
    """
    an unlabelled candidate is reported rather than silently scored as irrelevant
    :returns: nothing
    """
    corpus = load_corpus()
    scenario = load_scenarios()[0]
    scenario.labels.pop(next(iter(scenario.labels)))
    with pytest.raises(EvaluationError, match="missing relevance labels"):
        scenario_candidates(scenario, corpus)


def test_corpus_loader_rejects_a_missing_snapshot_date(tmp_path) -> None:
    """
    a corpus without a snapshot date cannot be used for frozen evaluation
    :param tmp_path: pytest temporary directory fixture
    :returns: nothing
    """
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps({"repositories": []}), encoding="utf-8")
    with pytest.raises(EvaluationError, match="snapshot_date"):
        load_corpus(path)


def test_evaluation_runs_and_produces_valid_metrics() -> None:
    """
    every scenario produces bounded metrics and a full length ranking
    :returns: nothing
    """
    report = run_evaluation()
    assert report["corpus_size"] >= 40
    assert report["top_k"] == TOP_K
    for result in report["scenarios"]:
        metrics = result["metrics"]
        assert len(result["ranking"]) == TOP_K
        assert len({entry["repository"] for entry in result["ranking"]}) == TOP_K
        assert [entry["rank"] for entry in result["ranking"]] == list(range(1, TOP_K + 1))
        for value in metrics.values():
            assert 0.0 <= value <= 1.0
        popularity = result["diagnostics"]["popularity"]
        assert popularity["candidate_median_stars"] > 0
        assert 0.0 <= result["diagnostics"]["mean_pairwise_similarity_top_k"] <= 1.0


def test_evaluation_is_deterministic_across_runs() -> None:
    """
    repeated evaluation of the frozen corpus produces byte identical reports
    :returns: nothing
    """
    assert json.dumps(run_evaluation(), sort_keys=True) == json.dumps(run_evaluation(), sort_keys=True)


def test_ranking_scores_decrease_monotonically() -> None:
    """
    the novelty adjusted selection order never reports an increasing score
    :returns: nothing
    """
    for result in run_evaluation()["scenarios"]:
        scores = [entry["score"] for entry in result["ranking"]]
        assert scores == sorted(scores, reverse=True)


def test_baseline_matches_the_current_fixture_shape() -> None:
    """
    the checked in baseline parses and describes the same corpus and scenarios
    :returns: nothing
    """
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    report = run_evaluation()
    assert baseline["corpus_snapshot_date"] == report["corpus_snapshot_date"]
    assert baseline["corpus_size"] == report["corpus_size"]
    assert [item["scenario"] for item in baseline["scenarios"]] == [item["scenario"] for item in report["scenarios"]]
    for item in baseline["scenarios"]:
        assert set(item["metrics"]) == {"ndcg_at_10", "precision_at_10", "recall_at_10", "mrr"}


def test_report_formatting_mentions_every_required_field() -> None:
    """
    the human readable report identifies the corpus, scenarios, and each metric
    :returns: nothing
    """
    text = format_report(run_evaluation())
    for fragment in ("Corpus snapshot", "Corpus size", "NDCG@10", "Precision@10", "Recall@10", "MRR", "Median stars"):
        assert fragment in text
    for scenario in load_scenarios():
        assert scenario.identifier in text


def test_command_line_entry_point_supports_text_and_json(capsys) -> None:
    """
    the documented command runs in both human readable and machine readable modes
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    assert main([]) == 0
    assert "NDCG@10" in capsys.readouterr().out
    assert main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["corpus_size"] >= 40


def test_command_line_entry_point_reports_missing_fixtures(capsys, tmp_path) -> None:
    """
    a missing corpus produces a clear failure instead of a traceback
    :param capsys: pytest output capture fixture
    :param tmp_path: pytest temporary directory fixture
    :returns: nothing
    """
    assert main(["--corpus", str(tmp_path / "absent.json")]) == 1
    assert "evaluation failed" in capsys.readouterr().err


def test_empty_corpus_object_has_no_index() -> None:
    """
    an empty corpus indexes to an empty mapping
    :returns: nothing
    """
    assert Corpus(snapshot_date="2026-01-01").by_name() == {}
