import copy
import json

import pytest

from repo_radar.contribution_evaluation import (
    ACTIONABLE_THRESHOLD,
    COMPARED_METRICS,
    FIXTURES_PATH,
    JUDGMENTS_PATH,
    ContributionEvaluationError,
    compare_reports,
    format_labeling_sheet,
    issue_identifier,
    load_fixture,
    load_judgments,
    main,
    run_contribution_evaluation,
    scope_metrics,
    unique_repository_count,
)

# Two repositories so the diversity diagnostic has something to report, and four issues whose
# relevance ordering is obvious by inspection: the profile is backend Python plus postgresql.
FIXTURE = {
    "snapshot_date": "2026-01-01",
    "owner": "example",
    "profile": {
        "languages": {"Python": 1.0},
        "topics": {"backend": 1.0},
        "keywords": {"postgresql": 1.0, "retry": 0.9},
        "median_stars": 100.0,
    },
    "scopes": {
        "discover": {
            "queries": ['is:issue is:open archived:false language:"Python" ("backend")'],
            "issue_ids": ["acme/service#1", "acme/service#2", "studio/gallery#3", "studio/gallery#4"],
        }
    },
    "repositories": [
        {
            "full_name": "acme/service",
            "owner": "acme",
            "description": "backend service",
            "language": "Python",
            "topics": ["backend"],
            "stars": 900,
            "pushed_at": "2025-12-01T00:00:00Z",
        },
        {
            "full_name": "studio/gallery",
            "owner": "studio",
            "description": "illustration gallery",
            "language": "CSS",
            "topics": ["design"],
            "stars": 900,
            "pushed_at": "2025-12-01T00:00:00Z",
        },
    ],
    "issues": [
        {
            "repository": "acme/service",
            "number": 1,
            "title": "Add postgresql retry handling coverage",
            "url": "https://github.com/acme/service/issues/1",
            "updated_at": "2025-12-28T00:00:00Z",
        },
        {
            "repository": "acme/service",
            "number": 2,
            "title": "Update the changelog",
            "url": "https://github.com/acme/service/issues/2",
            "updated_at": "2025-12-28T00:00:00Z",
        },
        {
            "repository": "studio/gallery",
            "number": 3,
            "title": "Refresh the illustration palette",
            "url": "https://github.com/studio/gallery/issues/3",
            "updated_at": "2025-12-28T00:00:00Z",
        },
        {
            "repository": "studio/gallery",
            "number": 4,
            "title": "Rename a colour swatch",
            "url": "https://github.com/studio/gallery/issues/4",
            "updated_at": "2025-12-28T00:00:00Z",
        },
    ],
}

JUDGMENTS = {
    "scale": {"0": "would skip", "3": "strong contribution candidate"},
    "actionable_threshold": 2,
    "judgments": {
        "acme/service#1": 3,
        "acme/service#2": 1,
        "studio/gallery#3": 0,
        "studio/gallery#4": 0,
    },
}


def _write(tmp_path, fixture=None, judgments=None):
    """
    write a fixture and judgments pair to a temporary directory
    :param tmp_path: pytest temporary directory
    :param fixture: optional fixture payload override
    :param judgments: optional judgments payload override
    :returns: fixture and judgments paths
    """
    fixtures_path = tmp_path / "fixtures.json"
    judgments_path = tmp_path / "judgments.json"
    fixtures_path.write_text(json.dumps(fixture or FIXTURE), encoding="utf-8")
    judgments_path.write_text(json.dumps(judgments or JUDGMENTS), encoding="utf-8")
    return fixtures_path, judgments_path


def _unjudged(judgments=None):
    """
    build a judgments payload with every label removed
    :param judgments: optional judgments payload to blank
    :returns: fully unjudged judgments payload
    """
    payload = copy.deepcopy(judgments or JUDGMENTS)
    payload["judgments"] = dict.fromkeys(payload["judgments"])
    return payload


# ---------------------------------------------------------------------------
# judgment parsing
# ---------------------------------------------------------------------------


def test_graded_judgments_parse_with_explicit_unjudged_entries(tmp_path) -> None:
    """
    a null judgment stays null instead of collapsing into an implicit zero
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    payload = copy.deepcopy(JUDGMENTS)
    payload["judgments"]["ACME/Service#2"] = None
    _, judgments_path = _write(tmp_path, judgments=payload)
    parsed = load_judgments(judgments_path)
    assert parsed["acme/service#1"] == 3
    assert parsed["acme/service#2"] is None
    assert 0 not in {parsed["acme/service#2"]}


def test_judgments_outside_the_scale_fail_loudly(tmp_path) -> None:
    """
    an out of range or non integer label is an error rather than a silent clamp
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    payload = copy.deepcopy(JUDGMENTS)
    payload["judgments"]["acme/service#1"] = 7
    _, judgments_path = _write(tmp_path, judgments=payload)
    with pytest.raises(ContributionEvaluationError, match="zero to three"):
        load_judgments(judgments_path)

    payload["judgments"]["acme/service#1"] = "strong"
    judgments_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ContributionEvaluationError, match="not an integer"):
        load_judgments(judgments_path)


def test_fixture_referencing_an_unknown_issue_fails_loudly(tmp_path) -> None:
    """
    a scope may not point at a candidate the fixture does not carry
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    payload = copy.deepcopy(FIXTURE)
    payload["scopes"]["discover"]["issue_ids"].append("ghost/repo#9")
    fixtures_path, _ = _write(tmp_path, fixture=payload)
    with pytest.raises(ContributionEvaluationError, match="unknown issues"):
        load_fixture(fixtures_path)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def test_metric_calculations_match_hand_computed_values() -> None:
    """
    NDCG, precision, MRR, and diversity agree with values computed by hand
    :returns: nothing
    """
    entries = [
        {"repository": "acme/service"},
        {"repository": "acme/service"},
        {"repository": "studio/gallery"},
        {"repository": "acme/service"},
        {"repository": "other/tool"},
    ]
    metrics = scope_metrics([3, 0, 2, 0, 1], [3, 2, 1, 0, 0], entries)
    # DCG@5 = 7/1 + 3/2 + 1/log2(6) = 8.886853; IDCG@5 = 7/1 + 3/log2(3) + 1/2 = 9.392789
    assert metrics["ndcg_at_5"] == 0.9461
    assert metrics["ndcg_at_10"] == 0.9461
    # two of the five results reach the actionable threshold of 2
    assert metrics["precision_at_5"] == 0.4
    # the first actionable result is at rank one
    assert metrics["mrr"] == 1.0
    assert metrics["unique_repositories_at_5"] == 3
    assert set(metrics) == COMPARED_METRICS


def test_reciprocal_rank_uses_the_actionable_threshold() -> None:
    """
    MRR measures the first result the user would actually investigate
    :returns: nothing
    """
    metrics = scope_metrics([0, 1, 3, 2], [3, 2, 1, 0], [{"repository": "a/b"}] * 4)
    assert ACTIONABLE_THRESHOLD == 2
    assert metrics["mrr"] == 0.3333
    assert metrics["ndcg_at_5"] == 0.5774
    assert metrics["precision_at_5"] == 0.5
    assert metrics["unique_repositories_at_5"] == 1


def test_repository_diversity_counts_distinct_repositories_case_insensitively() -> None:
    """
    the diversity diagnostic reports repositories, not results
    :returns: nothing
    """
    entries = [{"repository": "Acme/Service"}, {"repository": "acme/service"}, {"repository": "other/tool"}]
    assert unique_repository_count(entries, 5) == 2
    assert unique_repository_count(entries, 1) == 1
    assert unique_repository_count([], 5) == 0


# ---------------------------------------------------------------------------
# evaluation behavior
# ---------------------------------------------------------------------------


def test_judged_corpus_produces_metrics_and_a_ranking(tmp_path) -> None:
    """
    a fully judged corpus reports metrics alongside the behavioral snapshot
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path)
    report = run_contribution_evaluation(fixtures_path, judgments_path)
    scope = report["scopes"][0]
    assert report["judgments"]["complete"] is True
    assert report["judgments"]["distribution"] == {"0": 2, "1": 1, "2": 0, "3": 1}
    assert scope["candidate_count"] == 4
    assert scope["repository_count"] == 2
    assert scope["actionable_candidate_count"] == 1
    assert scope["ranking"][0]["issue"] == "acme/service#1"
    assert scope["ranking"][0]["judgment"] == 3
    assert scope["metrics"]["mrr"] == 1.0
    assert report["summary"]["mean_ndcg_at_5"] == scope["metrics"]["ndcg_at_5"]


def test_evaluation_is_deterministic(tmp_path) -> None:
    """
    the same frozen inputs always produce the same report
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path)
    first = run_contribution_evaluation(fixtures_path, judgments_path)
    second = run_contribution_evaluation(fixtures_path, judgments_path)
    assert first == second


def test_evaluation_does_not_mutate_the_frozen_fixture(tmp_path) -> None:
    """
    evaluating a corpus never rewrites the fixture or the judgments
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path)
    before = (fixtures_path.read_bytes(), judgments_path.read_bytes())
    run_contribution_evaluation(fixtures_path, judgments_path)
    assert (fixtures_path.read_bytes(), judgments_path.read_bytes()) == before


def test_unjudged_candidates_suppress_every_quality_metric(tmp_path) -> None:
    """
    an unlabelled corpus reports behavior and explicitly no metrics
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path, judgments=_unjudged())
    report = run_contribution_evaluation(fixtures_path, judgments_path)
    scope = report["scopes"][0]
    assert report["judgments"]["complete"] is False
    assert report["judgments"]["judged"] == 0
    assert sorted(report["judgments"]["unjudged"]) == sorted(FIXTURE["scopes"]["discover"]["issue_ids"])
    assert scope["metrics"] is None
    assert scope["actionable_candidate_count"] is None
    assert report["summary"] is None
    # the ranking itself is still recorded, because it needs no labels to be true
    assert [entry["issue"] for entry in scope["ranking"]]


def test_a_partially_judged_corpus_still_refuses_metrics(tmp_path) -> None:
    """
    a missing label fails the scope loudly rather than counting as irrelevant
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    payload = copy.deepcopy(JUDGMENTS)
    payload["judgments"]["studio/gallery#4"] = None
    fixtures_path, judgments_path = _write(tmp_path, judgments=payload)
    report = run_contribution_evaluation(fixtures_path, judgments_path)
    assert report["judgments"]["judged"] == 3
    assert report["judgments"]["complete"] is False
    assert report["scopes"][0]["metrics"] is None
    assert report["scopes"][0]["unjudged_candidates"] == ["studio/gallery#4"]


def test_baseline_is_refused_without_genuine_judgments(tmp_path, capsys) -> None:
    """
    a quality baseline cannot be frozen from an unjudged corpus
    :param tmp_path: pytest temporary directory
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path, judgments=_unjudged())
    baseline_path = tmp_path / "baseline.json"
    exit_code = main(
        [
            "--fixtures",
            str(fixtures_path),
            "--judgments",
            str(judgments_path),
            "--baseline",
            str(baseline_path),
            "--write-baseline",
        ]
    )
    assert exit_code == 1
    assert "refusing to freeze a quality baseline" in capsys.readouterr().err
    assert not baseline_path.exists()


def test_baseline_is_written_once_the_corpus_is_judged(tmp_path) -> None:
    """
    a judged corpus freezes a baseline carrying metrics and the ranking
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    exit_code = main(
        [
            "--fixtures",
            str(fixtures_path),
            "--judgments",
            str(judgments_path),
            "--baseline",
            str(baseline_path),
            "--write-baseline",
        ]
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert set(baseline["scopes"][0]["metrics"]) == COMPARED_METRICS
    assert baseline["scopes"][0]["ranking"]


def test_labeling_sheet_lists_unjudged_candidates_without_showing_the_ranking(tmp_path) -> None:
    """
    the labeling sheet presents candidates neutrally so labels are not anchored to the ranker
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path, judgments=_unjudged())
    report = run_contribution_evaluation(fixtures_path, judgments_path)
    sheet = format_labeling_sheet(report, load_fixture(fixtures_path))
    identifiers = report["judgments"]["unjudged"]
    positions = [sheet.index(f'"{identifier}"') for identifier in identifiers]
    # candidates appear in identifier order, and the sheet carries no rank or score at all
    assert identifiers == sorted(identifiers)
    assert positions == sorted(positions)
    assert "score" not in sheet
    assert "rank" not in sheet.lower()
    assert "would skip" in sheet
    assert "hindsight" in sheet


# ---------------------------------------------------------------------------
# baseline comparison
# ---------------------------------------------------------------------------


def test_baseline_comparison_reports_no_change_for_identical_behavior(tmp_path) -> None:
    """
    an unchanged ranker compares clean against its own baseline
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path)
    report = run_contribution_evaluation(fixtures_path, judgments_path)
    comparison = compare_reports(copy.deepcopy(report), report)
    assert comparison["identical"] is True
    assert comparison["metric_changes"] == []
    assert comparison["ranking_changes"] == []


def test_baseline_comparison_detects_a_reordering_that_moves_no_metric(tmp_path) -> None:
    """
    the comparison is not vacuous: a shuffled ranking is reported even when metrics match

    This is the specific mistake the repository baseline already made once, where the stored
    values being compared could not express the behavior that changed.
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path)
    baseline = run_contribution_evaluation(fixtures_path, judgments_path)
    report = copy.deepcopy(baseline)
    ranking = report["scopes"][0]["ranking"]
    ranking[0], ranking[1] = ranking[1], ranking[0]
    ranking[0]["rank"], ranking[1]["rank"] = 1, 2
    comparison = compare_reports(baseline, report)
    assert comparison["metric_changes"] == []
    assert comparison["identical"] is False
    assert {change["issue"] for change in comparison["ranking_changes"]} == {
        "acme/service#1",
        "acme/service#2",
    }


def test_baseline_comparison_detects_a_metric_change(tmp_path) -> None:
    """
    a moved metric is reported with both the frozen and the current value
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path)
    baseline = run_contribution_evaluation(fixtures_path, judgments_path)
    report = copy.deepcopy(baseline)
    report["scopes"][0]["metrics"]["ndcg_at_5"] = 0.1234
    comparison = compare_reports(baseline, report)
    assert comparison["identical"] is False
    assert comparison["metric_changes"] == [
        {
            "scope": "discover",
            "metric": "ndcg_at_5",
            "baseline": baseline["scopes"][0]["metrics"]["ndcg_at_5"],
            "current": 0.1234,
        }
    ]


def test_baseline_comparison_covers_every_reported_metric(tmp_path) -> None:
    """
    adding a metric without teaching the comparison about it fails here

    A comparison that silently ignores a new metric is how a check becomes vacuous.
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    fixtures_path, judgments_path = _write(tmp_path)
    baseline = run_contribution_evaluation(fixtures_path, judgments_path)
    assert set(baseline["scopes"][0]["metrics"]) == COMPARED_METRICS
    for name in COMPARED_METRICS:
        report = copy.deepcopy(baseline)
        report["scopes"][0]["metrics"][name] = "changed"
        comparison = compare_reports(baseline, report)
        assert [change["metric"] for change in comparison["metric_changes"]] == [name]


# ---------------------------------------------------------------------------
# the checked in corpus
# ---------------------------------------------------------------------------


def test_evaluation_performs_no_github_requests(monkeypatch) -> None:
    """
    the offline evaluation reads frozen files and never opens a connection
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """

    def forbidden(request, timeout=None):
        """
        reject any outgoing request
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: no response
        """
        raise AssertionError("Contribution evaluation must not contact GitHub")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    report = run_contribution_evaluation()
    assert report["scopes"]


def test_checked_in_corpus_is_real_public_and_replayable() -> None:
    """
    the frozen corpus carries both scopes, real issues, and no private repositories
    :returns: nothing
    """
    fixture = load_fixture(FIXTURES_PATH)
    judgments = load_judgments(JUDGMENTS_PATH)
    assert {scope.scope for scope in fixture.scopes} == {"discover", "saved_starred"}
    assert fixture.issues and fixture.repositories
    assert all(not repository.private for repository in fixture.repositories.values())
    assert all(scope.queries for scope in fixture.scopes)
    # every candidate carries a judgment slot, even if it is still explicitly unjudged
    assert set(judgments) == set(fixture.issues)
    for scope in fixture.scopes:
        for identifier in scope.issue_ids:
            issue = fixture.issues[identifier]
            assert issue_identifier(issue.repository, issue.number) == identifier
            assert not issue.is_pull_request
            assert issue.state == "open"
            assert issue.repository.lower() in fixture.repositories


def test_discovery_scope_contains_repositories_outside_saved_and_starred() -> None:
    """
    the frozen corpus proves discovery reached repositories local evidence never named
    :returns: nothing
    """
    fixture = load_fixture(FIXTURES_PATH)
    scopes = {scope.scope: scope for scope in fixture.scopes}
    discovered = {fixture.issues[identifier].repository.lower() for identifier in scopes["discover"].issue_ids}
    followed = {fixture.issues[identifier].repository.lower() for identifier in scopes["saved_starred"].issue_ids}
    assert discovered - followed
