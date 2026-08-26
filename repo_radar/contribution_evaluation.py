"""offline contribution ranking evaluation against a frozen real-issue corpus

This answers one question and refuses to answer any other:

    Does Repo Radar rank contribution opportunities the user would actually
    investigate near the top of the list?

The corpus is real GitHub issues, captured once by `repo_radar.contribution_snapshot` and
frozen. This module reads those files and nothing else — no network, no `data/`, no live
metadata. Ranking runs through the production `normalize_candidates` and `rank_issues`, so
there is no evaluation-only recommender to drift away from the product.

**Quality metrics require human judgments.** Judgments are not derived from Repo Radar's own
score, and an unjudged corpus produces a behavioral snapshot with no metrics rather than a
flattering number. See `evaluation/contributions/README.md`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .contribution import CONTRIBUTION_SCOPES, PER_REPOSITORY_LIMIT, normalize_candidates
from .evaluation import ndcg_at_k, precision_at_k, reciprocal_rank
from .issue_ranking import rank_issues
from .models import Issue, PreferenceProfile, Repository

CONTRIBUTION_DIRECTORY = Path(__file__).resolve().parent.parent / "evaluation" / "contributions"
FIXTURES_PATH = CONTRIBUTION_DIRECTORY / "fixtures.json"
JUDGMENTS_PATH = CONTRIBUTION_DIRECTORY / "judgments.json"
BASELINE_PATH = CONTRIBUTION_DIRECTORY / "baseline.json"

# NDCG@5 is primary: a contribution session is a short list, and the question is whether the
# first handful are worth opening. NDCG@10 reports the broader ordering.
PRIMARY_K = 5
SECONDARY_K = 10
RESULT_LIMIT = SECONDARY_K

# "Actionable" means the user would open the issue and look, which is what a recommendation
# can honestly be judged on. It is deliberately not "the user contributed a fix"; that is a
# task outcome, not a recommendation outcome.
ACTIONABLE_THRESHOLD = 2

JUDGMENT_SCALE = {
    0: "would skip",
    1: "maybe inspect",
    2: "likely inspect",
    3: "strong contribution candidate",
}

# Every metric a baseline comparison must cover. A test asserts that the metrics a scope
# actually reports are exactly this set, so adding a metric without teaching the comparison
# about it fails the suite instead of silently producing a comparison that cannot see it.
COMPARED_METRICS = frozenset(
    {
        "ndcg_at_5",
        "ndcg_at_10",
        "precision_at_5",
        "mrr",
        "unique_repositories_at_5",
        "unique_repositories_at_10",
    }
)

_REPOSITORY_FIELDS = {
    "full_name",
    "description",
    "language",
    "topics",
    "stars",
    "forks",
    "archived",
    "is_fork",
    "created_at",
    "updated_at",
    "pushed_at",
    "owner",
    "url",
}

_ISSUE_FIELDS = {
    "repository",
    "number",
    "title",
    "url",
    "body",
    "labels",
    "assignee_count",
    "comments",
    "created_at",
    "updated_at",
    "state",
    "is_pull_request",
}


class ContributionEvaluationError(RuntimeError):
    """raised when the contribution fixture or judgments are missing or inconsistent"""


def issue_identifier(repository: str, number: int) -> str:
    """
    build the stable identity used to join issues, judgments, and baselines
    :param repository: repository full name
    :param number: issue number
    :returns: canonical lowercase issue identifier
    """
    return f"{repository.lower()}#{number}"


@dataclass(slots=True)
class ScopeFixture:
    """the frozen candidate set captured for one contribution scope"""

    scope: str
    queries: list[str] = field(default_factory=list)
    issue_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContributionFixture:
    """frozen real issues, their repositories, and the profile that ranked them"""

    snapshot_date: str
    owner: str
    profile: PreferenceProfile
    repositories: dict[str, Repository] = field(default_factory=dict)
    issues: dict[str, Issue] = field(default_factory=dict)
    scopes: list[ScopeFixture] = field(default_factory=list)

    def evaluated_at(self) -> datetime:
        """
        derive the frozen reference time used for freshness and activity scoring
        :returns: snapshot date as a UTC datetime
        """
        return datetime.fromisoformat(f"{self.snapshot_date}T00:00:00+00:00")


def _repository_from_snapshot(value: dict[str, Any]) -> Repository:
    """
    build a repository from a fixture entry, ignoring documentation only fields
    :param value: fixture repository entry
    :returns: repository instance
    """
    data = {key: item for key, item in value.items() if key in _REPOSITORY_FIELDS}
    if not data.get("full_name"):
        raise ContributionEvaluationError("fixture repository entry is missing full_name")
    return Repository(**data)


def _issue_from_snapshot(value: dict[str, Any]) -> Issue:
    """
    build an issue from a fixture entry, ignoring documentation only fields
    :param value: fixture issue entry
    :returns: issue instance
    """
    data = {key: item for key, item in value.items() if key in _ISSUE_FIELDS}
    if not data.get("repository") or not data.get("number"):
        raise ContributionEvaluationError("fixture issue entry is missing a repository or number")
    return Issue(**data)


def _signals(value: dict[str, Any], name: str) -> dict[str, float]:
    """
    read one normalized profile signal group from a fixture
    :param value: fixture profile payload
    :param name: signal group name
    :returns: normalized signal weights
    """
    return {str(key): float(weight) for key, weight in value.get(name, {}).items()}


def _profile_from_snapshot(value: dict[str, Any]) -> PreferenceProfile:
    """
    rebuild the frozen preference profile that ranked the corpus
    :param value: fixture profile payload
    :returns: preference profile
    """
    return PreferenceProfile(
        languages=_signals(value, "languages"),
        topics=_signals(value, "topics"),
        keywords=_signals(value, "keywords"),
        median_stars=float(value.get("median_stars", 0.0)),
    )


def load_fixture(path: Path = FIXTURES_PATH) -> ContributionFixture:
    """
    load the frozen contribution corpus
    :param path: fixture file location
    :returns: parsed fixture
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContributionEvaluationError(
            f"could not read the contribution fixture at {path}: {error}. "
            "Capture it once with `python -m repo_radar.contribution_snapshot`."
        ) from error
    snapshot_date = payload.get("snapshot_date")
    if not snapshot_date:
        raise ContributionEvaluationError("fixture is missing snapshot_date")
    repositories = {}
    for entry in payload.get("repositories", []):
        repository = _repository_from_snapshot(entry)
        repositories[repository.full_name.lower()] = repository
    issues = {}
    for entry in payload.get("issues", []):
        issue = _issue_from_snapshot(entry)
        issues[issue_identifier(issue.repository, issue.number)] = issue
    if not issues:
        raise ContributionEvaluationError("fixture contains no issues")
    scopes: list[ScopeFixture] = []
    for name in CONTRIBUTION_SCOPES:
        entry = payload.get("scopes", {}).get(name)
        if entry is None:
            continue
        identifiers = [str(value).lower() for value in entry.get("issue_ids", [])]
        unknown = sorted(set(identifiers) - set(issues))
        if unknown:
            raise ContributionEvaluationError(f"scope {name} references unknown issues: {', '.join(unknown)}")
        scopes.append(
            ScopeFixture(scope=name, queries=[str(query) for query in entry.get("queries", [])], issue_ids=identifiers)
        )
    if not scopes:
        raise ContributionEvaluationError("fixture defines no evaluation scopes")
    return ContributionFixture(
        snapshot_date=str(snapshot_date),
        owner=str(payload.get("owner", "")),
        profile=_profile_from_snapshot(payload.get("profile", {})),
        repositories=repositories,
        issues=issues,
        scopes=scopes,
    )


def load_judgments(path: Path = JUDGMENTS_PATH) -> dict[str, int | None]:
    """
    load graded human relevance judgments

    A `null` entry is an explicitly unjudged issue, which is not the same thing as a zero.
    Treating an unlabelled issue as irrelevant would silently reward a ranker for burying
    everything the user never got around to reading.
    :param path: judgments file location
    :returns: judgment by issue identifier, none where unjudged
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContributionEvaluationError(f"could not read contribution judgments at {path}: {error}") from error
    judgments: dict[str, int | None] = {}
    for identifier, value in payload.get("judgments", {}).items():
        if value is None:
            judgments[str(identifier).lower()] = None
            continue
        try:
            graded = int(value)
        except (TypeError, ValueError) as error:
            raise ContributionEvaluationError(f"judgment for {identifier} is not an integer or null") from error
        if graded not in JUDGMENT_SCALE:
            raise ContributionEvaluationError(f"judgment {graded} for {identifier} is outside the zero to three scale")
        judgments[str(identifier).lower()] = graded
    return judgments


def scope_candidates(fixture: ContributionFixture, scope: ScopeFixture) -> list[Issue]:
    """
    apply the production normalization step to one scope's frozen candidates
    :param fixture: frozen contribution corpus
    :param scope: scope candidate set
    :returns: rankable issue candidates
    """
    issues = [fixture.issues[identifier] for identifier in scope.issue_ids]
    return normalize_candidates(issues, fixture.repositories)


def unique_repository_count(entries: list[dict[str, Any]], k: int) -> int:
    """
    count how many distinct repositories appear in the top results
    :param entries: ranking entries in rank order
    :param k: rank cutoff
    :returns: number of distinct repositories
    """
    return len({str(entry["repository"]).lower() for entry in entries[:k]})


def scope_metrics(ranked_labels: list[int], all_labels: list[int], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    calculate the graded ranking metrics for one scope
    :param ranked_labels: judgments for the produced ranking in rank order
    :param all_labels: judgments for every eligible candidate in the scope
    :param entries: ranking entries in rank order
    :returns: metric values
    """
    return {
        "ndcg_at_5": round(ndcg_at_k(ranked_labels, all_labels, PRIMARY_K), 4),
        "ndcg_at_10": round(ndcg_at_k(ranked_labels, all_labels, SECONDARY_K), 4),
        "precision_at_5": round(precision_at_k(ranked_labels, PRIMARY_K, ACTIONABLE_THRESHOLD), 4),
        "mrr": round(reciprocal_rank(ranked_labels, ACTIONABLE_THRESHOLD), 4),
        "unique_repositories_at_5": unique_repository_count(entries, PRIMARY_K),
        "unique_repositories_at_10": unique_repository_count(entries, SECONDARY_K),
    }


def evaluate_scope(
    fixture: ContributionFixture,
    scope: ScopeFixture,
    judgments: dict[str, int | None],
    limit: int = RESULT_LIMIT,
) -> dict[str, Any]:
    """
    rank one scope's frozen candidates and record the behavior plus any valid metrics
    :param fixture: frozen contribution corpus
    :param scope: scope candidate set
    :param judgments: graded human judgments by issue identifier
    :param limit: maximum ranked results
    :returns: scope result carrying the ranking, coverage, and metrics when judged
    """
    candidates = scope_candidates(fixture, scope)
    ranked = rank_issues(
        candidates,
        fixture.repositories,
        fixture.profile,
        limit,
        PER_REPOSITORY_LIMIT,
        fixture.evaluated_at(),
    )
    entries: list[dict[str, Any]] = []
    for position, recommendation in enumerate(ranked, start=1):
        issue = recommendation.issue
        identifier = issue_identifier(issue.repository, issue.number)
        entries.append(
            {
                "rank": position,
                "issue": identifier,
                "repository": issue.repository,
                "number": issue.number,
                "title": issue.title,
                "score": round(recommendation.score, 6),
                "judgment": judgments.get(identifier),
                "labels": issue.labels,
                "assignee_count": issue.assignee_count,
                "comments": issue.comments,
                "updated_at": issue.updated_at,
                "scope_signal": recommendation.scope_signal,
                "reasons": recommendation.reasons,
            }
        )
    candidate_ids = [issue_identifier(issue.repository, issue.number) for issue in candidates]
    unjudged = sorted(identifier for identifier in candidate_ids if judgments.get(identifier) is None)
    all_labels = [judgments[identifier] for identifier in candidate_ids if judgments.get(identifier) is not None]
    ranked_labels = [entry["judgment"] for entry in entries if entry["judgment"] is not None]
    complete = not unjudged
    return {
        "scope": scope.scope,
        "queries": scope.queries,
        "candidate_count": len(candidates),
        "repository_count": len({issue.repository.lower() for issue in candidates}),
        "judged_candidate_count": len(all_labels),
        "unjudged_candidates": unjudged,
        "actionable_candidate_count": sum(1 for label in all_labels if label >= ACTIONABLE_THRESHOLD)
        if complete
        else None,
        "metrics": scope_metrics(ranked_labels, all_labels, entries) if complete else None,
        "ranking": entries,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    average each metric across the evaluated scopes

    This is a mean over evaluation queries, the standard way to report a ranking metric over
    more than one query. It is deliberately not a combined quality score: the individual
    metrics are never folded into each other.
    :param results: per scope results
    :returns: mean of each metric, or none when any scope lacks metrics
    """
    if not results or any(result["metrics"] is None for result in results):
        return None
    return {
        f"mean_{name}": round(statistics.fmean([result["metrics"][name] for result in results]), 4)
        for name in sorted(COMPARED_METRICS)
    }


def run_contribution_evaluation(
    fixtures_path: Path = FIXTURES_PATH,
    judgments_path: Path = JUDGMENTS_PATH,
    limit: int = RESULT_LIMIT,
) -> dict[str, Any]:
    """
    evaluate every frozen scope offline against the current issue ranking
    :param fixtures_path: fixture file location
    :param judgments_path: judgments file location
    :param limit: maximum ranked results per scope
    :returns: complete contribution evaluation report
    """
    fixture = load_fixture(fixtures_path)
    judgments = load_judgments(judgments_path)
    results = [evaluate_scope(fixture, scope, judgments, limit) for scope in fixture.scopes]
    judged = {identifier: value for identifier, value in judgments.items() if value is not None}
    unjudged = sorted(identifier for identifier in fixture.issues if judgments.get(identifier) is None)
    complete = not any(result["unjudged_candidates"] for result in results)
    return {
        "snapshot_date": fixture.snapshot_date,
        "owner": fixture.owner,
        "primary_k": PRIMARY_K,
        "secondary_k": SECONDARY_K,
        "actionable_threshold": ACTIONABLE_THRESHOLD,
        "fixture": {
            "issue_count": len(fixture.issues),
            "repository_count": len(fixture.repositories),
            "scopes": [scope.scope for scope in fixture.scopes],
        },
        "judgments": {
            "total_issues": len(fixture.issues),
            "judged": len(judged),
            "unjudged": unjudged,
            "complete": complete,
            "distribution": {
                str(grade): sum(1 for value in judged.values() if value == grade) for grade in sorted(JUDGMENT_SCALE)
            },
        },
        "scopes": results,
        "summary": summarize(results),
    }


# ---------------------------------------------------------------------------
# baseline comparison
# ---------------------------------------------------------------------------


def compare_reports(baseline: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """
    diff a new contribution evaluation against a frozen baseline

    The project has already been burned once by a baseline comparison that read the wrong
    stored values and could not fail. This comparison covers both halves that can move: every
    metric in `COMPARED_METRICS`, and the produced ordering itself, so a change that shuffles
    results without moving a single metric is still reported.
    :param baseline: frozen baseline report
    :param report: freshly produced report
    :returns: metric differences, ranking differences, and whether anything moved
    """
    baseline_scopes = {result["scope"]: result for result in baseline.get("scopes", [])}
    report_scopes = {result["scope"]: result for result in report.get("scopes", [])}
    metric_changes: list[dict[str, Any]] = []
    ranking_changes: list[dict[str, Any]] = []
    for scope in sorted(set(baseline_scopes) | set(report_scopes)):
        before = baseline_scopes.get(scope)
        after = report_scopes.get(scope)
        if before is None or after is None:
            ranking_changes.append({"scope": scope, "change": "scope added" if before is None else "scope removed"})
            continue
        before_metrics = before.get("metrics") or {}
        after_metrics = after.get("metrics") or {}
        for name in sorted(set(before_metrics) | set(after_metrics)):
            if before_metrics.get(name) != after_metrics.get(name):
                metric_changes.append(
                    {
                        "scope": scope,
                        "metric": name,
                        "baseline": before_metrics.get(name),
                        "current": after_metrics.get(name),
                    }
                )
        before_order = [entry["issue"] for entry in before.get("ranking", [])]
        after_order = [entry["issue"] for entry in after.get("ranking", [])]
        if before_order != after_order:
            before_ranks = {entry["issue"]: entry["rank"] for entry in before.get("ranking", [])}
            after_ranks = {entry["issue"]: entry["rank"] for entry in after.get("ranking", [])}
            ranking_changes.extend(
                {
                    "scope": scope,
                    "issue": identifier,
                    "baseline_rank": before_ranks.get(identifier),
                    "current_rank": after_ranks.get(identifier),
                }
                for identifier in sorted(set(before_ranks) | set(after_ranks))
                if before_ranks.get(identifier) != after_ranks.get(identifier)
            )
    return {
        "metric_changes": metric_changes,
        "ranking_changes": ranking_changes,
        "identical": not metric_changes and not ranking_changes,
    }


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def format_labeling_sheet(report: dict[str, Any], fixture: ContributionFixture) -> str:
    """
    render the unjudged candidates in a practical order for manual labeling

    Candidates are listed by repository and issue number, **not** in Repo Radar's ranked
    order. Presenting the ranking would anchor the labels to the thing being measured.
    :param report: contribution evaluation report
    :param fixture: frozen contribution corpus
    :returns: labeling sheet text
    """
    unjudged = report["judgments"]["unjudged"]
    lines = [
        "Repo Radar contribution labeling sheet",
        "",
        "Answer one question per issue: would you actually open this and investigate it as a",
        "possible contribution, judged only on what is shown here?",
        "",
        *(f"  {grade} = {meaning}" for grade, meaning in sorted(JUDGMENT_SCALE.items())),
        "",
        "Judge the recommendation, not the eventual outcome. Do not label with hindsight:",
        "whether the fix turned out to be easy, whether a pull request was merged, how long it",
        "took, or anything else you could only know after solving it.",
        "",
        f"Record each answer in evaluation/contributions/judgments.json ({len(unjudged)} unjudged).",
        "",
    ]
    for identifier in unjudged:
        issue = fixture.issues[identifier]
        repository = fixture.repositories.get(issue.repository.lower())
        assignment = f"assigned to {issue.assignee_count}" if issue.assignee_count else "unassigned"
        lines.extend(
            [
                f'"{identifier}": ,',
                f"    {issue.title}",
                f"    {repository.language if repository else 'unknown language'} | {assignment} | "
                f"{issue.comments} comments | updated {issue.updated_at or 'unknown'}",
                f"    labels: {', '.join(issue.labels) if issue.labels else 'none'}",
                f"    {issue.url}",
                "",
            ]
        )
    return "\n".join(lines)


def format_report(report: dict[str, Any]) -> str:
    """
    render a human readable contribution evaluation report
    :param report: contribution evaluation report
    :returns: formatted report text
    """
    judgments = report["judgments"]
    lines = [
        "Repo Radar contribution ranking evaluation",
        f"Fixture snapshot:     {report['snapshot_date']}",
        f"Issues / repositories: {report['fixture']['issue_count']} / {report['fixture']['repository_count']}",
        f"Judged:               {judgments['judged']} of {judgments['total_issues']}",
        f"Actionable threshold: judgment >= {report['actionable_threshold']}",
    ]
    if not judgments["complete"]:
        lines.extend(
            [
                "",
                "NO QUALITY BASELINE: the corpus is not fully judged, so no ranking metric is",
                f"reported. {len(judgments['unjudged'])} issues are still unlabelled. Run this command with",
                "--labeling-sheet, then fill evaluation/contributions/judgments.json.",
                "The rankings below are a behavioral snapshot only.",
            ]
        )
    for result in report["scopes"]:
        lines.extend(
            [
                "",
                f"Scope: {result['scope']}",
                f"  Candidates: {result['candidate_count']} across {result['repository_count']} repositories",
            ]
        )
        metrics = result["metrics"]
        if metrics is None:
            lines.append(f"  Metrics: unavailable ({len(result['unjudged_candidates'])} unjudged candidates)")
        else:
            lines.extend(
                [
                    f"  Actionable candidates: {result['actionable_candidate_count']}",
                    f"  NDCG@5:       {metrics['ndcg_at_5']:.4f}",
                    f"  NDCG@10:      {metrics['ndcg_at_10']:.4f}",
                    f"  Precision@5:  {metrics['precision_at_5']:.4f}",
                    f"  MRR:          {metrics['mrr']:.4f}",
                    f"  Unique repositories in top 5 / 10: "
                    f"{metrics['unique_repositories_at_5']} / {metrics['unique_repositories_at_10']}",
                ]
            )
        lines.append("  Ranking:")
        lines.extend(
            f"    {entry['rank']:>2}. [{entry['judgment'] if entry['judgment'] is not None else '?'}] "
            f"{entry['issue']} (score {entry['score']:.4f}) {entry['title'][:60]}"
            for entry in result["ranking"]
        )
    summary = report["summary"]
    if summary:
        lines.extend(["", "Mean across scopes"])
        lines.extend(f"  {name:<28}{value:.4f}" for name, value in sorted(summary.items()))
    return "\n".join(lines)


def format_comparison(comparison: dict[str, Any]) -> str:
    """
    render a baseline comparison
    :param comparison: baseline comparison result
    :returns: formatted comparison text
    """
    if comparison["identical"]:
        return "Contribution ranking is unchanged against the frozen baseline."
    metric_lines = [
        f"  {change['scope']} {change['metric']}: {change['baseline']} -> {change['current']}"
        for change in comparison["metric_changes"]
    ]
    ranking_lines = [
        f"  {change.get('scope')} {change.get('issue', change.get('change'))}: "
        f"{change.get('baseline_rank')} -> {change.get('current_rank')}"
        for change in comparison["ranking_changes"]
    ]
    return "\n".join(
        [
            "Contribution ranking differs from the frozen baseline.",
            "",
            "Metric changes:",
            *(metric_lines or ["  none"]),
            "",
            "Ranking changes:",
            *(ranking_lines or ["  none"]),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """
    run the contribution evaluation from the command line
    :param argv: optional argument list
    :returns: process exit code
    """
    parser = argparse.ArgumentParser(
        prog="python -m repo_radar.contribution_evaluation",
        description="Offline contribution ranking evaluation against a frozen real-issue corpus",
    )
    parser.add_argument("--json", action="store_true", help="emit the machine readable report")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_PATH, help="fixture file location")
    parser.add_argument("--judgments", type=Path, default=JUDGMENTS_PATH, help="judgments file location")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH, help="baseline file location")
    parser.add_argument("--labeling-sheet", action="store_true", help="print the unjudged candidates for labeling")
    parser.add_argument("--compare", action="store_true", help="diff the current behavior against the baseline")
    parser.add_argument("--write-baseline", action="store_true", help="overwrite the checked in baseline report")
    arguments = parser.parse_args(argv)
    try:
        report = run_contribution_evaluation(arguments.fixtures, arguments.judgments)
        if arguments.labeling_sheet:
            print(format_labeling_sheet(report, load_fixture(arguments.fixtures)))
            return 0
        if arguments.compare:
            baseline = json.loads(arguments.baseline.read_text(encoding="utf-8"))
            comparison = compare_reports(baseline, report)
            print(json.dumps(comparison, indent=2) if arguments.json else format_comparison(comparison))
            return 0 if comparison["identical"] else 1
        if arguments.write_baseline:
            if not report["judgments"]["complete"]:
                raise ContributionEvaluationError(
                    f"refusing to freeze a quality baseline: {len(report['judgments']['unjudged'])} issues "
                    "are unjudged. Label them first with --labeling-sheet."
                )
            arguments.baseline.parent.mkdir(parents=True, exist_ok=True)
            arguments.baseline.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"contribution baseline written to {arguments.baseline}")
            return 0
    except (ContributionEvaluationError, OSError, json.JSONDecodeError) as error:
        print(f"contribution evaluation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2) if arguments.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
