"""offline recommendation quality evaluation against a frozen corpus"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from .discovery import filter_candidates
from .models import PreferenceProfile, Recommendation, Repository, SeedPreferences
from .profile import build_profile
from .ranking import candidate_similarity, rank_candidates

EVALUATION_DIRECTORY = Path(__file__).resolve().parent.parent / "evaluation"
CORPUS_PATH = EVALUATION_DIRECTORY / "corpus.json"
SCENARIOS_PATH = EVALUATION_DIRECTORY / "scenarios.json"
BASELINE_PATH = EVALUATION_DIRECTORY / "baseline.json"

TOP_K = 10
RELEVANT_THRESHOLD = 2
STRONGLY_RELEVANT_THRESHOLD = 3
EVALUATION_OWNER = "evaluation-profile-owner"

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


class EvaluationError(RuntimeError):
    """raised when evaluation fixtures are missing or inconsistent"""


@dataclass(slots=True)
class Corpus:
    """frozen repository snapshot used for offline evaluation"""

    snapshot_date: str
    repositories: list[Repository] = field(default_factory=list)

    def by_name(self) -> dict[str, Repository]:
        """
        index the corpus by case insensitive full name
        :returns: repositories keyed by lowercase full name
        """
        return {repository.full_name.lower(): repository for repository in self.repositories}

    def evaluated_at(self) -> datetime:
        """
        derive the frozen reference time used for activity scoring
        :returns: snapshot date as a UTC datetime
        """
        return datetime.fromisoformat(f"{self.snapshot_date}T00:00:00+00:00")


@dataclass(slots=True)
class Scenario:
    """a labelled evaluation scenario built from real preference abstractions"""

    identifier: str
    name: str
    description: str
    starred: list[str] = field(default_factory=list)
    seed_preferences: SeedPreferences = field(default_factory=SeedPreferences)
    labels: dict[str, int] = field(default_factory=dict)

    def label_for(self, repository: Repository) -> int:
        """
        look up the graded relevance label for a candidate
        :param repository: candidate repository
        :returns: graded relevance from zero to three
        """
        return self.labels[repository.full_name.lower()]


def discounted_cumulative_gain(labels: list[int]) -> float:
    """
    calculate discounted cumulative gain for graded relevance labels in rank order
    :param labels: graded relevance labels ordered by rank
    :returns: discounted cumulative gain
    """
    return sum((2**label - 1) / math.log2(position + 2) for position, label in enumerate(labels))


def ndcg_at_k(ranked_labels: list[int], all_labels: list[int], k: int = TOP_K) -> float:
    """
    calculate normalized discounted cumulative gain against the best possible ranking
    :param ranked_labels: graded labels for the produced ranking
    :param all_labels: graded labels for every eligible candidate
    :param k: rank cutoff
    :returns: NDCG from zero to one
    """
    ideal = discounted_cumulative_gain(sorted(all_labels, reverse=True)[:k])
    if ideal == 0.0:
        return 0.0
    return discounted_cumulative_gain(ranked_labels[:k]) / ideal


def precision_at_k(ranked_labels: list[int], k: int = TOP_K, threshold: int = RELEVANT_THRESHOLD) -> float:
    """
    calculate the share of returned results that meet the relevance threshold
    :param ranked_labels: graded labels for the produced ranking
    :param k: rank cutoff
    :param threshold: minimum label treated as relevant
    :returns: precision from zero to one
    """
    window = ranked_labels[:k]
    if not window:
        return 0.0
    return sum(1 for label in window if label >= threshold) / len(window)


def recall_at_k(
    ranked_labels: list[int], all_labels: list[int], k: int = TOP_K, threshold: int = RELEVANT_THRESHOLD
) -> float:
    """
    calculate the share of all relevant candidates that were returned
    :param ranked_labels: graded labels for the produced ranking
    :param all_labels: graded labels for every eligible candidate
    :param k: rank cutoff
    :param threshold: minimum label treated as relevant
    :returns: recall from zero to one
    """
    total_relevant = sum(1 for label in all_labels if label >= threshold)
    if total_relevant == 0:
        return 0.0
    return sum(1 for label in ranked_labels[:k] if label >= threshold) / total_relevant


def reciprocal_rank(ranked_labels: list[int], threshold: int = STRONGLY_RELEVANT_THRESHOLD) -> float:
    """
    calculate the reciprocal rank of the first result meeting the threshold
    :param ranked_labels: graded labels for the produced ranking
    :param threshold: minimum label treated as a hit
    :returns: reciprocal rank from zero to one
    """
    for position, label in enumerate(ranked_labels):
        if label >= threshold:
            return 1.0 / (position + 1)
    return 0.0


def mean_pairwise_similarity(repositories: list[Repository]) -> float:
    """
    measure redundancy using the same similarity concept as the novelty penalty
    :param repositories: repositories to compare
    :returns: mean pairwise similarity from zero to one
    """
    pairs = list(combinations(repositories, 2))
    if not pairs:
        return 0.0
    return sum(candidate_similarity(left, right) for left, right in pairs) / len(pairs)


def maximum_pairwise_similarity(repositories: list[Repository]) -> float:
    """
    measure the most redundant pair among a result set
    :param repositories: repositories to compare
    :returns: maximum pairwise similarity from zero to one
    """
    pairs = list(combinations(repositories, 2))
    if not pairs:
        return 0.0
    return max(candidate_similarity(left, right) for left, right in pairs)


def _repository_from_snapshot(value: dict[str, Any]) -> Repository:
    """
    build a repository from a corpus snapshot, ignoring documentation only fields
    :param value: corpus snapshot entry
    :returns: repository instance
    """
    data = {key: item for key, item in value.items() if key in _REPOSITORY_FIELDS}
    if "full_name" not in data:
        raise EvaluationError("corpus entry is missing full_name")
    return Repository(**data)


def load_corpus(path: Path = CORPUS_PATH) -> Corpus:
    """
    load the frozen evaluation corpus
    :param path: corpus file location
    :returns: parsed corpus
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"could not read corpus at {path}: {error}") from error
    snapshot_date = payload.get("snapshot_date")
    if not snapshot_date:
        raise EvaluationError("corpus is missing snapshot_date")
    repositories = [_repository_from_snapshot(entry) for entry in payload.get("repositories", [])]
    if not repositories:
        raise EvaluationError("corpus contains no repositories")
    names = [repository.full_name.lower() for repository in repositories]
    if len(set(names)) != len(names):
        raise EvaluationError("corpus contains duplicate repository names")
    return Corpus(snapshot_date=str(snapshot_date), repositories=repositories)


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[Scenario]:
    """
    load labelled evaluation scenarios
    :param path: scenarios file location
    :returns: parsed scenarios
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"could not read scenarios at {path}: {error}") from error
    scenarios: list[Scenario] = []
    for entry in payload.get("scenarios", []):
        profile_sources = entry.get("profile", {})
        seeds = profile_sources.get("seed_preferences", {})
        labels = {name.lower(): int(label) for name, label in entry.get("labels", {}).items()}
        for label in labels.values():
            if label not in {0, 1, 2, 3}:
                raise EvaluationError(f"scenario {entry.get('id')} uses a label outside the zero to three scale")
        scenarios.append(
            Scenario(
                identifier=str(entry["id"]),
                name=str(entry.get("name", entry["id"])),
                description=str(entry.get("description", "")),
                starred=[str(name) for name in profile_sources.get("starred", [])],
                seed_preferences=SeedPreferences(
                    languages=[str(value) for value in seeds.get("languages", [])],
                    topics=[str(value) for value in seeds.get("topics", [])],
                    keywords=[str(value) for value in seeds.get("keywords", [])],
                ),
                labels=labels,
            )
        )
    if not scenarios:
        raise EvaluationError("no scenarios are defined")
    return scenarios


def build_scenario_profile(scenario: Scenario, corpus: Corpus) -> PreferenceProfile:
    """
    build a preference profile using the production profile abstraction
    :param scenario: evaluation scenario
    :param corpus: frozen repository corpus
    :returns: preference profile for the scenario
    """
    index = corpus.by_name()
    starred: list[Repository] = []
    for name in scenario.starred:
        repository = index.get(name.lower())
        if repository is None:
            raise EvaluationError(f"scenario {scenario.identifier} references unknown repository {name}")
        starred.append(repository)
    return build_profile(starred, scenario.seed_preferences)


def scenario_candidates(scenario: Scenario, corpus: Corpus) -> list[Repository]:
    """
    apply production candidate filtering to the corpus for a scenario
    :param scenario: evaluation scenario
    :param corpus: frozen repository corpus
    :returns: eligible candidate repositories
    """
    starred_names = {name for name in scenario.starred}
    candidates = filter_candidates(corpus.repositories, starred_names, EVALUATION_OWNER, {})
    missing = [repository.full_name for repository in candidates if repository.full_name.lower() not in scenario.labels]
    if missing:
        raise EvaluationError(f"scenario {scenario.identifier} is missing relevance labels for {', '.join(missing)}")
    return candidates


def popularity_diagnostics(
    candidates: list[Repository], ranked: list[Recommendation], scenario: Scenario
) -> dict[str, Any]:
    """
    inspect whether popularity is overwhelming personalized relevance
    :param candidates: eligible candidate repositories
    :param ranked: produced recommendations
    :param scenario: evaluation scenario providing relevance labels
    :returns: popularity diagnostic values
    """
    candidate_median = float(statistics.median([repository.stars for repository in candidates]))
    top_repositories = [item.repository for item in ranked]
    top_median = float(statistics.median([repository.stars for repository in top_repositories])) if ranked else 0.0
    relevant_low_star = [
        repository.full_name
        for repository in top_repositories
        if scenario.label_for(repository) >= RELEVANT_THRESHOLD and repository.stars <= candidate_median
    ]
    irrelevant_high_star = [
        repository.full_name
        for repository in top_repositories
        if scenario.label_for(repository) < RELEVANT_THRESHOLD and repository.stars > candidate_median
    ]
    return {
        "candidate_median_stars": candidate_median,
        "top_k_median_stars": top_median,
        "top_k_median_star_ratio": round(top_median / candidate_median, 4) if candidate_median else 0.0,
        "relevant_low_star_in_top_k": relevant_low_star,
        "irrelevant_high_star_in_top_k": irrelevant_high_star,
    }


def evaluate_scenario(scenario: Scenario, corpus: Corpus, k: int = TOP_K) -> dict[str, Any]:
    """
    run the production ranking pipeline for one scenario and measure the result
    :param scenario: evaluation scenario
    :param corpus: frozen repository corpus
    :param k: rank cutoff
    :returns: metrics, diagnostics, and the produced ranking
    """
    profile = build_scenario_profile(scenario, corpus)
    candidates = scenario_candidates(scenario, corpus)
    ranked = rank_candidates(candidates, profile, k, corpus.evaluated_at())
    ranked_labels = [scenario.label_for(item.repository) for item in ranked]
    all_labels = [scenario.label_for(repository) for repository in candidates]
    top_repositories = [item.repository for item in ranked]
    return {
        "scenario": scenario.identifier,
        "name": scenario.name,
        "description": scenario.description,
        "candidate_count": len(candidates),
        "relevant_candidate_count": sum(1 for label in all_labels if label >= RELEVANT_THRESHOLD),
        "metrics": {
            "ndcg_at_10": round(ndcg_at_k(ranked_labels, all_labels, k), 4),
            "precision_at_10": round(precision_at_k(ranked_labels, k), 4),
            "recall_at_10": round(recall_at_k(ranked_labels, all_labels, k), 4),
            "mrr": round(reciprocal_rank(ranked_labels), 4),
        },
        "diagnostics": {
            "mean_pairwise_similarity_top_k": round(mean_pairwise_similarity(top_repositories), 4),
            "maximum_pairwise_similarity_top_k": round(maximum_pairwise_similarity(top_repositories), 4),
            "popularity": popularity_diagnostics(candidates, ranked, scenario),
        },
        "ranking": [
            {
                "rank": position + 1,
                "repository": item.repository.full_name,
                "score": round(item.score, 6),
                "label": scenario.label_for(item.repository),
                "stars": item.repository.stars,
                "explanation": item.explanation,
            }
            for position, item in enumerate(ranked)
        ],
    }


def run_evaluation(
    corpus_path: Path = CORPUS_PATH, scenarios_path: Path = SCENARIOS_PATH, k: int = TOP_K
) -> dict[str, Any]:
    """
    evaluate every scenario against the frozen corpus
    :param corpus_path: corpus file location
    :param scenarios_path: scenarios file location
    :param k: rank cutoff
    :returns: complete evaluation report
    """
    corpus = load_corpus(corpus_path)
    scenarios = load_scenarios(scenarios_path)
    return {
        "corpus_snapshot_date": corpus.snapshot_date,
        "corpus_size": len(corpus.repositories),
        "top_k": k,
        "relevant_threshold": RELEVANT_THRESHOLD,
        "strongly_relevant_threshold": STRONGLY_RELEVANT_THRESHOLD,
        "scenarios": [evaluate_scenario(scenario, corpus, k) for scenario in scenarios],
    }


def format_report(report: dict[str, Any]) -> str:
    """
    render a human readable evaluation report
    :param report: evaluation report
    :returns: formatted report text
    """
    lines = [
        "Repo Radar recommendation evaluation",
        f"Corpus snapshot: {report['corpus_snapshot_date']}",
        f"Corpus size: {report['corpus_size']} repositories",
        f"Relevant threshold: label >= {report['relevant_threshold']}",
        f"Strongly relevant threshold (MRR): label >= {report['strongly_relevant_threshold']}",
    ]
    for result in report["scenarios"]:
        metrics = result["metrics"]
        diagnostics = result["diagnostics"]
        popularity = diagnostics["popularity"]
        lines.extend(
            [
                "",
                f"Scenario: {result['name']} ({result['scenario']})",
                f"  {result['description']}",
                f"  Candidates: {result['candidate_count']} ({result['relevant_candidate_count']} labelled relevant)",
                f"  NDCG@10:      {metrics['ndcg_at_10']:.4f}",
                f"  Precision@10: {metrics['precision_at_10']:.4f}",
                f"  Recall@10:    {metrics['recall_at_10']:.4f}",
                f"  MRR:          {metrics['mrr']:.4f}",
                f"  Mean pairwise similarity in top 10: {diagnostics['mean_pairwise_similarity_top_k']:.4f}",
                f"  Max pairwise similarity in top 10:  {diagnostics['maximum_pairwise_similarity_top_k']:.4f}",
                f"  Median stars, candidates: {popularity['candidate_median_stars']:.1f}",
                f"  Median stars, top 10:     {popularity['top_k_median_stars']:.1f} "
                f"(ratio {popularity['top_k_median_star_ratio']:.2f})",
                f"  Relevant low star results in top 10:   {len(popularity['relevant_low_star_in_top_k'])}",
                f"  Irrelevant high star results in top 10: {len(popularity['irrelevant_high_star_in_top_k'])}",
                "  Ranking:",
            ]
        )
        lines.extend(
            f"    {entry['rank']:>2}. [{entry['label']}] {entry['repository']} "
            f"(score {entry['score']:.4f}, {entry['stars']} stars)"
            for entry in result["ranking"]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """
    run the evaluation from the command line
    :param argv: optional argument list
    :returns: process exit code
    """
    parser = argparse.ArgumentParser(
        prog="python -m repo_radar.evaluation",
        description="Offline recommendation quality evaluation against a frozen corpus",
    )
    parser.add_argument("--json", action="store_true", help="emit the machine readable report")
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH, help="corpus file location")
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS_PATH, help="scenarios file location")
    parser.add_argument("--write-baseline", action="store_true", help="overwrite the checked in baseline report")
    arguments = parser.parse_args(argv)
    try:
        report = run_evaluation(arguments.corpus, arguments.scenarios)
    except EvaluationError as error:
        print(f"evaluation failed: {error}", file=sys.stderr)
        return 1
    if arguments.write_baseline:
        BASELINE_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"baseline written to {BASELINE_PATH}")
        return 0
    print(json.dumps(report, indent=2) if arguments.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
