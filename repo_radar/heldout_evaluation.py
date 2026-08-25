"""held-out star evaluation: a behavioral proxy over real repository metadata

This is not ground truth. A GitHub star may mean sustained interest, a bookmark, a
dependency, a favour, or a moment of curiosity, and an unstarred repository is not
evidence of disinterest. What this measures is narrower and still useful: given the
preference evidence that remains after some real stars are hidden, does the production
ranker put those hidden repositories near the top of a realistic candidate pool?

Kept deliberately separate from the synthetic graded evaluation in `evaluation.py`,
which answers a different question with hand-authored labels.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from .discovery import filter_candidates
from .models import ImportedProfile, PreferenceProfile, Repository
from .profile import build_profile, extract_keywords
from .ranking import (
    DUPLICATE_SIMILARITY_THRESHOLD,
    KEYWORD_MATCH_LIMIT,
    TOPIC_MATCH_LIMIT,
    _activity_score,
    _strongest_matches,
    candidate_similarity,
    rank_candidates,
)

HELDOUT_DIRECTORY = Path(__file__).resolve().parent.parent / "evaluation" / "heldout"
SNAPSHOT_PATH = HELDOUT_DIRECTORY / "snapshot.json"
BASELINE_PATH = HELDOUT_DIRECTORY / "baseline.json"

HOLDOUT_SIZE = 2
MAX_TRIALS = 50
TRIAL_SEED = 20260825
CUTOFFS = (5, 10, 20)
STRONG_TOPIC_THRESHOLD = 0.5

# Production ranking is greedy: each selection rescores every remaining candidate against
# everything already chosen, so producing a full ordering of a 200 candidate pool costs
# roughly cubic time and dominates the runtime of the whole experiment. Every ranker is
# therefore truncated to the same window, and a held-out repository that falls outside it
# is reported as unranked rather than assigned an invented rank. The window is deliberately
# far deeper than the ten results the product surfaces, so it bounds cost without bounding
# the result: if `unranked_positives` is ever non-zero the window is too shallow and the
# metrics below it must not be read as if it were.
RANKING_WINDOW = 100

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
    "private",
}


class HeldOutEvaluationError(RuntimeError):
    """raised when the held-out snapshot is missing, unusable, or leaks held-out identities"""


@dataclass(slots=True)
class HeldOutSnapshot:
    """frozen real repository metadata captured once for repeated offline evaluation"""

    snapshot_date: str
    owner: str
    stars: list[Repository] = field(default_factory=list)
    candidates: list[Repository] = field(default_factory=list)
    owned_profile: ImportedProfile | None = None
    search_queries: list[str] = field(default_factory=list)

    def evaluated_at(self) -> datetime:
        """
        derive the frozen reference time used for activity scoring
        :returns: snapshot date as a UTC datetime
        """
        return datetime.fromisoformat(f"{self.snapshot_date}T00:00:00+00:00")

    def owned_names(self) -> set[str]:
        """
        collect the full names of repositories owned by the snapshot user
        :returns: lowercase owned repository full names
        """
        if not self.owned_profile:
            return set()
        return {
            f"{self.owned_profile.username}/{repository.name}".lower() for repository in self.owned_profile.repositories
        }


@dataclass(slots=True)
class Trial:
    """one deterministic train and holdout split of the eligible stars"""

    index: int
    holdout: list[Repository] = field(default_factory=list)
    training: list[Repository] = field(default_factory=list)

    def holdout_names(self) -> set[str]:
        """
        collect the lowercase identities hidden from profile construction
        :returns: held-out repository full names
        """
        return {repository.full_name.lower() for repository in self.holdout}


# ---------------------------------------------------------------------------
# eligibility
# ---------------------------------------------------------------------------


def star_exclusion_reason(repository: Repository, owner: str) -> str | None:
    """
    decide whether a stored star can serve as a held-out positive
    :param repository: stored starred repository
    :param owner: authenticated snapshot user
    :returns: exclusion reason or none when the star is eligible
    """
    if repository.private:
        return "private repository"
    if not repository.full_name or "/" not in repository.full_name:
        return "missing repository identity"
    if repository.archived:
        return "archived; production filtering would never return it"
    if repository.owner.lower() == owner.lower():
        return "owned by the profile user; production filtering excludes own repositories"
    if not (repository.language or repository.topics or repository.description):
        return "no language, topics, or description for the ranker to match"
    if not (repository.pushed_at or repository.updated_at):
        return "no activity timestamp"
    return None


def partition_stars(snapshot: HeldOutSnapshot) -> tuple[list[Repository], list[dict[str, str]]]:
    """
    split stored stars into held-out candidates and reported exclusions
    :param snapshot: frozen held-out snapshot
    :returns: eligible stars sorted by identity and exclusion records
    """
    eligible: list[Repository] = []
    excluded: list[dict[str, str]] = []
    for repository in sorted(snapshot.stars, key=lambda item: item.full_name.lower()):
        reason = star_exclusion_reason(repository, snapshot.owner)
        if reason:
            excluded.append({"repository": repository.full_name, "reason": reason})
        else:
            eligible.append(repository)
    return eligible, excluded


# ---------------------------------------------------------------------------
# deterministic trials
# ---------------------------------------------------------------------------


def build_trials(
    eligible: list[Repository],
    stars: list[Repository],
    holdout_size: int = HOLDOUT_SIZE,
    max_trials: int = MAX_TRIALS,
    seed: int = TRIAL_SEED,
) -> tuple[list[Trial], str]:
    """
    enumerate deterministic train and holdout splits over the eligible stars

    Every distinct split is used when the eligible set is small enough to enumerate them
    all, which removes the "lucky split" question entirely. Larger sets fall back to a
    seeded sample drawn from the same deterministic enumeration order.

    :param eligible: stars that may be held out, sorted by identity
    :param stars: every stored star, used as training evidence when not held out
    :param holdout_size: number of stars hidden per trial
    :param max_trials: upper bound on the number of trials
    :param seed: fixed seed used only when the enumeration must be subsampled
    :returns: trials and the selection strategy used
    """
    if holdout_size < 1:
        raise HeldOutEvaluationError("holdout size must be at least one repository")
    if len(eligible) <= holdout_size:
        raise HeldOutEvaluationError(
            f"{len(eligible)} eligible stars cannot support a holdout of {holdout_size} "
            "with training evidence left over"
        )
    splits = [tuple(split) for split in combinations(eligible, holdout_size)]
    strategy = "exhaustive"
    if len(splits) > max_trials:
        strategy = "sampled"
        splits = sorted(
            random.Random(seed).sample(splits, max_trials),
            key=lambda split: tuple(repository.full_name.lower() for repository in split),
        )
    trials: list[Trial] = []
    for index, split in enumerate(splits):
        hidden = {repository.full_name.lower() for repository in split}
        training = [repository for repository in stars if repository.full_name.lower() not in hidden]
        trials.append(Trial(index=index, holdout=list(split), training=training))
    return trials, strategy


# ---------------------------------------------------------------------------
# profile construction and leakage control
# ---------------------------------------------------------------------------

LanguageTransform = Callable[[dict[str, float]], dict[str, float]]


def _current_languages(languages: dict[str, float]) -> dict[str, float]:
    """
    keep the production relative language normalization untouched
    :param languages: normalized production language weights
    :returns: an independent copy of the same weights
    """
    return dict(languages)


def _uniform_languages(languages: dict[str, float]) -> dict[str, float]:
    """
    treat every language present in the training profile as equally preferred
    :param languages: normalized production language weights
    :returns: uniform weights over the same languages
    """
    return {language: 1.0 for language in languages}


def _compressed_languages(languages: dict[str, float]) -> dict[str, float]:
    """
    compress the gap between the dominant language and the rest without erasing it
    :param languages: normalized production language weights
    :returns: square root compressed weights over the same languages
    """
    return {language: round(math.sqrt(weight), 3) for language, weight in languages.items()}


LANGUAGE_MODES: dict[str, LanguageTransform] = {
    "current": _current_languages,
    "uniform": _uniform_languages,
    "compressed": _compressed_languages,
}


def apply_language_mode(profile: PreferenceProfile, mode: str) -> PreferenceProfile:
    """
    produce an evaluation-only variant of a profile with reweighted languages

    Returns a new profile. The production profile is never mutated, so an ablation cannot
    contaminate the run it is being compared against.

    :param profile: profile produced by the production build_profile
    :param mode: language weighting mode name
    :returns: a new profile carrying the reweighted languages
    """
    transform = LANGUAGE_MODES.get(mode)
    if transform is None:
        raise HeldOutEvaluationError(f"unknown language mode {mode}")
    return PreferenceProfile(
        languages=transform(profile.languages),
        topics=dict(profile.topics),
        keywords=dict(profile.keywords),
        median_stars=profile.median_stars,
    )


def _assert_no_leakage(sources: Iterable[tuple[str, str]], holdout_names: set[str]) -> None:
    """
    fail loudly when a held-out identity reaches profile construction through any source
    :param sources: preference source name and repository identity pairs
    :param holdout_names: lowercase held-out repository identities
    :returns: nothing
    """
    for source, name in sources:
        if name.lower() in holdout_names:
            raise HeldOutEvaluationError(f"held-out repository {name} leaked into profile construction via {source}")


def build_trial_profile(snapshot: HeldOutSnapshot, trial: Trial, mode: str = "current") -> PreferenceProfile:
    """
    build a preference profile from the training evidence using the production path

    Allowed sources are the non-held-out stars and the user's own public repositories.
    Saved/interested repositories and manual seeds are excluded because the snapshot does
    not carry them; that is recorded as a limitation rather than silently substituted.

    :param snapshot: frozen held-out snapshot
    :param trial: train and holdout split
    :param mode: language weighting mode applied after production construction
    :returns: preference profile that has never seen the held-out repositories
    """
    holdout_names = trial.holdout_names()
    owned = snapshot.owned_profile
    if owned is not None:
        retained = [
            repository
            for repository in owned.repositories
            if f"{owned.username}/{repository.name}".lower() not in holdout_names
        ]
        owned = ImportedProfile(
            username=owned.username,
            public_repository_count=owned.public_repository_count,
            fetched_at=owned.fetched_at,
            source_url=owned.source_url,
            repositories=retained,
        )
    sources = [("starred", repository.full_name) for repository in trial.training]
    if owned is not None:
        sources.extend(("owned", f"{owned.username}/{repository.name}") for repository in owned.repositories)
    _assert_no_leakage(sources, holdout_names)
    return apply_language_mode(build_profile(trial.training, None, owned, None), mode)


# ---------------------------------------------------------------------------
# candidate pool
# ---------------------------------------------------------------------------


def build_trial_candidates(snapshot: HeldOutSnapshot, trial: Trial) -> list[Repository]:
    """
    assemble a realistic candidate pool of real repositories for one trial

    The held-out positives are injected because the frozen distractor pool was captured
    from searches that reflect the whole star history; without injection recovery would be
    impossible by construction. Everything then passes through the production filter, so
    the experiment never scores repositories the application would have dropped.

    :param snapshot: frozen held-out snapshot
    :param trial: train and holdout split
    :returns: eligible candidate repositories including the held-out positives
    """
    holdout_names = trial.holdout_names()
    pool = [repository for repository in snapshot.candidates if repository.full_name.lower() not in holdout_names]
    pool.extend(trial.holdout)
    # only the training stars are "already seen"; the held-out ones must stay eligible
    excluded = {repository.full_name for repository in trial.training} | snapshot.owned_names()
    candidates = filter_candidates(pool, excluded, snapshot.owner, {})
    survivors = {repository.full_name.lower() for repository in candidates}
    missing = sorted(name for name in holdout_names if name not in survivors)
    if missing:
        raise HeldOutEvaluationError(
            f"trial {trial.index} lost held-out positives to production filtering: {', '.join(missing)}"
        )
    return candidates


# ---------------------------------------------------------------------------
# evaluation-only rankers
# ---------------------------------------------------------------------------

Ranker = Callable[[list[Repository], PreferenceProfile, datetime, int, int], list[Repository]]


def _production_ranker(
    candidates: list[Repository], profile: PreferenceProfile, now: datetime, seed: int, window: int
) -> list[Repository]:
    """
    order candidates with the production ranking pipeline
    :param candidates: eligible candidates
    :param profile: training preference profile
    :param now: frozen reference time
    :param seed: unused determinism seed
    :param window: maximum ranked positions
    :returns: production ordering truncated to the window
    """
    return [item.repository for item in rank_candidates(candidates, profile, min(window, len(candidates)), now)]


def _popularity_ranker(
    candidates: list[Repository], profile: PreferenceProfile, now: datetime, seed: int, window: int
) -> list[Repository]:
    """
    order candidates by popularity alone, ignoring the profile entirely
    :param candidates: eligible candidates
    :param profile: unused preference profile
    :param now: unused reference time
    :param seed: unused determinism seed
    :param window: maximum ranked positions
    :returns: candidates ordered by stars and forks
    """
    return sorted(candidates, key=lambda item: (-(item.stars + 2 * item.forks), item.full_name))[:window]


def _activity_ranker(
    candidates: list[Repository], profile: PreferenceProfile, now: datetime, seed: int, window: int
) -> list[Repository]:
    """
    order candidates by recency of activity alone, ignoring the profile entirely
    :param candidates: eligible candidates
    :param profile: unused preference profile
    :param now: unused reference time
    :param seed: unused determinism seed
    :param window: maximum ranked positions
    :returns: candidates ordered by most recent push
    """
    by_name = sorted(candidates, key=lambda item: item.full_name)
    return sorted(by_name, key=lambda item: item.pushed_at or item.updated_at or "", reverse=True)[:window]


def _random_ranker(
    candidates: list[Repository], profile: PreferenceProfile, now: datetime, seed: int, window: int
) -> list[Repository]:
    """
    shuffle candidates deterministically as a sanity floor
    :param candidates: eligible candidates
    :param profile: unused preference profile
    :param now: unused reference time
    :param seed: per trial determinism seed
    :param window: maximum ranked positions
    :returns: deterministically shuffled candidates
    """
    order = sorted(candidates, key=lambda item: item.full_name)
    random.Random(seed).shuffle(order)
    return order[:window]


RANKERS: dict[str, Ranker] = {
    "production": _production_ranker,
    "popularity": _popularity_ranker,
    "activity": _activity_ranker,
    "random": _random_ranker,
}


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def hit_rate_at_k(trial_ranks: Sequence[list[int | None]], k: int) -> float:
    """
    calculate the share of trials recovering at least one held-out positive by rank k
    :param trial_ranks: held-out ranks per trial, none when unranked
    :param k: rank cutoff
    :returns: hit rate from zero to one
    """
    if not trial_ranks:
        return 0.0
    hits = sum(1 for ranks in trial_ranks if any(rank is not None and rank <= k for rank in ranks))
    return hits / len(trial_ranks)


def recall_at_k(trial_ranks: Sequence[list[int | None]], k: int) -> float:
    """
    calculate the share of all held-out positives recovered by rank k
    :param trial_ranks: held-out ranks per trial, none when unranked
    :param k: rank cutoff
    :returns: recall from zero to one
    """
    positives = [rank for ranks in trial_ranks for rank in ranks]
    if not positives:
        return 0.0
    return sum(1 for rank in positives if rank is not None and rank <= k) / len(positives)


def mean_reciprocal_rank(trial_ranks: Sequence[list[int | None]]) -> float:
    """
    average the reciprocal rank of the first recovered held-out positive per trial
    :param trial_ranks: held-out ranks per trial, none when unranked
    :returns: mean reciprocal rank from zero to one
    """
    if not trial_ranks:
        return 0.0
    total = 0.0
    for ranks in trial_ranks:
        recovered = [rank for rank in ranks if rank is not None]
        if recovered:
            total += 1.0 / min(recovered)
    return total / len(trial_ranks)


def rank_distribution(trial_ranks: Sequence[list[int | None]]) -> dict[str, Any]:
    """
    summarize where held-out positives land, keeping unranked positives visible
    :param trial_ranks: held-out ranks per trial, none when unranked
    :returns: median, mean, percentile, and unranked counts
    """
    positives = [rank for ranks in trial_ranks for rank in ranks]
    recovered = sorted(rank for rank in positives if rank is not None)
    unranked = len(positives) - len(recovered)
    if not recovered:
        return {
            "median_rank": None,
            "mean_rank": None,
            "p25_rank": None,
            "p75_rank": None,
            "best_rank": None,
            "worst_rank": None,
            "ranked_positives": 0,
            "unranked_positives": unranked,
        }
    return {
        "median_rank": float(statistics.median(recovered)),
        "mean_rank": round(statistics.fmean(recovered), 4),
        "p25_rank": float(recovered[max(0, math.ceil(0.25 * len(recovered)) - 1)]),
        "p75_rank": float(recovered[max(0, math.ceil(0.75 * len(recovered)) - 1)]),
        "best_rank": recovered[0],
        "worst_rank": recovered[-1],
        "ranked_positives": len(recovered),
        "unranked_positives": unranked,
    }


def summarize_ranks(trial_ranks: Sequence[list[int | None]]) -> dict[str, Any]:
    """
    build the full held-out metric set for one ranking configuration
    :param trial_ranks: held-out ranks per trial, none when unranked
    :returns: hit rates, recalls, MRR, and the rank distribution
    """
    metrics: dict[str, Any] = {}
    for cutoff in CUTOFFS:
        metrics[f"hit_rate_at_{cutoff}"] = round(hit_rate_at_k(trial_ranks, cutoff), 4)
    for cutoff in CUTOFFS:
        metrics[f"recall_at_{cutoff}"] = round(recall_at_k(trial_ranks, cutoff), 4)
    metrics["mrr"] = round(mean_reciprocal_rank(trial_ranks), 4)
    metrics.update(rank_distribution(trial_ranks))
    return metrics


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------


SCORE_WEIGHTS = {"topic": 0.38, "language": 0.25, "keyword": 0.17, "activity": 0.10, "quality": 0.10}


def score_components(repository: Repository, profile: PreferenceProfile, now: datetime) -> dict[str, float]:
    """
    decompose a production raw score into its five weighted contributions

    This reads production scoring rather than reimplementing it: the same match limits,
    the same deterministic evidence ordering, the same activity decay. It exists because a
    held-out rank on its own cannot say *which* term decided it, and the interesting
    question is usually whether the terms carrying the most nominal weight are actually
    doing any discriminating. A test asserts the parts sum to the production score.

    :param repository: candidate repository
    :param profile: preference profile
    :param now: frozen reference time
    :returns: weighted contribution of each scoring term
    """
    topics = dict.fromkeys(topic.lower() for topic in repository.topics)
    topic_matches = _strongest_matches(((topic, profile.topics.get(topic, 0.0)) for topic in topics), TOPIC_MATCH_LIMIT)
    keyword_matches = _strongest_matches(
        ((word, profile.keywords.get(word, 0.0)) for word in set(extract_keywords(repository.description))),
        KEYWORD_MATCH_LIMIT,
    )
    raw = {
        "topic": sum(score for _, score in topic_matches) / TOPIC_MATCH_LIMIT,
        "language": profile.languages.get(repository.language or "", 0.0),
        "keyword": sum(score for _, score in keyword_matches) / KEYWORD_MATCH_LIMIT,
        "activity": _activity_score(repository, now),
        "quality": min(1.0, math.log10(repository.stars + repository.forks * 2 + 1) / 4),
    }
    return {term: round(SCORE_WEIGHTS[term] * value, 6) for term, value in raw.items()}


def _term_spreads(diagnostics: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """
    measure how much each scoring term actually separates the held-out positives

    A term with a large nominal weight but a narrow spread across real candidates is not
    discriminating; whichever term has the widest spread is the one deciding the ranking.

    The interquartile spread is reported alongside the full range because they answer
    different questions. A term whose full range is wide but whose interquartile spread is
    near zero is not grading anything — it is acting as a presence flag, separating the
    handful of candidates missing that metadata entirely from everyone else, while telling
    the ranker nothing about the candidates that do have it.

    :param diagnostics: per positive diagnostics carrying score components
    :returns: nominal weight, observed range, and observed spread per term
    """
    spreads: dict[str, dict[str, float]] = {}
    for term, weight in SCORE_WEIGHTS.items():
        values = sorted(item["score_components"][term] for item in diagnostics)
        if not values:
            continue
        low = values[max(0, math.ceil(0.25 * len(values)) - 1)]
        high = values[max(0, math.ceil(0.75 * len(values)) - 1)]
        spreads[term] = {
            "nominal_weight": weight,
            "observed_min": round(values[0], 6),
            "observed_max": round(values[-1], 6),
            "observed_spread": round(values[-1] - values[0], 6),
            "observed_p25": round(low, 6),
            "observed_p75": round(high, 6),
            "interquartile_spread": round(high - low, 6),
        }
    return spreads


def _positive_diagnostics(
    repository: Repository,
    rank: int | None,
    ordering: list[Repository],
    profile: PreferenceProfile,
    training: list[Repository],
    now: datetime,
) -> dict[str, Any]:
    """
    explain one held-out outcome using profile shape and neighbourhood similarity
    :param repository: held-out repository
    :param rank: produced rank or none
    :param ordering: full produced ordering
    :param profile: training preference profile
    :param training: training repositories
    :param now: frozen reference time
    :returns: diagnostic values for one held-out positive
    """
    dominant = next(iter(profile.languages), None)
    topics = {topic.lower() for topic in repository.topics}
    matched_topics = sorted(topic for topic in topics if profile.topics.get(topic, 0.0) > 0)
    keywords = set(extract_keywords(repository.description))
    higher = ordering[: (rank - 1)] if rank else ordering
    similarity_above = max((candidate_similarity(repository, other) for other in higher), default=0.0)
    nearest_training = max((candidate_similarity(repository, other) for other in training), default=0.0)
    return {
        "repository": repository.full_name,
        "rank": rank,
        "language": repository.language,
        "language_is_dominant_training_language": bool(dominant and repository.language == dominant),
        "training_language_weight": profile.languages.get(repository.language or "", 0.0),
        "matched_training_topics": matched_topics,
        "matched_training_keywords": sorted(word for word in keywords if profile.keywords.get(word, 0.0) > 0),
        "stars": repository.stars,
        "pushed_at": repository.pushed_at,
        "score_components": score_components(repository, profile, now),
        "nearest_training_similarity": round(nearest_training, 4),
        "max_similarity_to_higher_ranked": round(similarity_above, 4),
        "redundancy_suppressed": similarity_above >= DUPLICATE_SIMILARITY_THRESHOLD,
    }


def _profile_diagnostics(profile: PreferenceProfile) -> dict[str, Any]:
    """
    describe the breadth of a training profile
    :param profile: training preference profile
    :returns: language, topic, and concentration diagnostics
    """
    weights = list(profile.topics.values())
    total = sum(weights)
    entropy = 0.0
    if total > 0:
        shares = [weight / total for weight in weights if weight > 0]
        entropy = -sum(share * math.log2(share) for share in shares)
    return {
        "active_languages": len(profile.languages),
        "dominant_language": next(iter(profile.languages), None),
        "topic_count": len(profile.topics),
        "strong_topic_count": sum(1 for weight in profile.topics.values() if weight >= STRONG_TOPIC_THRESHOLD),
        "topic_entropy_bits": round(entropy, 4),
    }


def _segment(diagnostics: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """
    summarize held-out ranks for a subset of positives
    :param diagnostics: per positive diagnostics
    :param predicate: subset selector
    :returns: count and rank summary for the subset
    """
    selected = [item for item in diagnostics if predicate(item)]
    ranks: list[list[int | None]] = [[item["rank"]] for item in selected]
    recovered = sorted(item["rank"] for item in selected if item["rank"] is not None)
    return {
        "positives": len(selected),
        "recall_at_10": round(recall_at_k(ranks, 10), 4) if selected else None,
        "median_rank": float(statistics.median(recovered)) if recovered else None,
    }


# ---------------------------------------------------------------------------
# snapshot loading
# ---------------------------------------------------------------------------


def _repository_from_snapshot(value: dict[str, Any]) -> Repository:
    """
    build a repository from a snapshot entry, ignoring documentation only fields
    :param value: snapshot entry
    :returns: repository instance
    """
    data = {key: item for key, item in value.items() if key in _REPOSITORY_FIELDS}
    if not data.get("full_name"):
        raise HeldOutEvaluationError("snapshot entry is missing full_name")
    return Repository(**data)


def load_snapshot(path: Path = SNAPSHOT_PATH) -> HeldOutSnapshot:
    """
    load the frozen real metadata snapshot
    :param path: snapshot file location
    :returns: parsed snapshot
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HeldOutEvaluationError(
            f"could not read the held-out snapshot at {path}: {error}. "
            "Regenerate it with `python -m repo_radar.heldout_snapshot`."
        ) from error
    snapshot_date = payload.get("snapshot_date")
    if not snapshot_date:
        raise HeldOutEvaluationError("snapshot is missing snapshot_date")
    owner = payload.get("owner")
    if not owner:
        raise HeldOutEvaluationError("snapshot is missing owner")
    stars = [_repository_from_snapshot(entry) for entry in payload.get("stars", [])]
    candidates = [_repository_from_snapshot(entry) for entry in payload.get("candidates", [])]
    if not stars:
        raise HeldOutEvaluationError("snapshot contains no starred repositories")
    if not candidates:
        raise HeldOutEvaluationError("snapshot contains no candidate repositories")
    private = [repository.full_name for repository in [*stars, *candidates] if repository.private]
    if private:
        raise HeldOutEvaluationError(f"snapshot contains private repositories: {', '.join(sorted(private))}")
    owned = payload.get("owned_profile")
    return HeldOutSnapshot(
        snapshot_date=str(snapshot_date),
        owner=str(owner),
        stars=stars,
        candidates=candidates,
        owned_profile=ImportedProfile.from_dict(owned) if owned else None,
        search_queries=[str(query) for query in payload.get("search_queries", [])],
    )


# ---------------------------------------------------------------------------
# experiment
# ---------------------------------------------------------------------------


def _run_configuration(
    snapshot: HeldOutSnapshot,
    trials: list[Trial],
    ranker_name: str,
    mode: str,
    window: int = RANKING_WINDOW,
    seed: int = TRIAL_SEED,
    collect_details: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    evaluate one ranker and language mode across every trial
    :param snapshot: frozen held-out snapshot
    :param trials: deterministic train and holdout splits
    :param ranker_name: ranking strategy name
    :param mode: language weighting mode
    :param window: maximum ranked positions per trial
    :param seed: deterministic seed offset for the random baseline
    :param collect_details: whether to gather per trial diagnostics
    :returns: metrics, per trial records, and per positive diagnostics
    """
    ranker = RANKERS[ranker_name]
    now = snapshot.evaluated_at()
    trial_ranks: list[list[int | None]] = []
    trial_records: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for trial in trials:
        profile = build_trial_profile(snapshot, trial, mode)
        candidates = build_trial_candidates(snapshot, trial)
        ordering = ranker(candidates, profile, now, seed + trial.index, window)
        positions = {repository.full_name.lower(): index + 1 for index, repository in enumerate(ordering)}
        ranks = [positions.get(repository.full_name.lower()) for repository in trial.holdout]
        trial_ranks.append(ranks)
        if not collect_details:
            continue
        trial_records.append(
            {
                "trial": trial.index,
                "held_out": [repository.full_name for repository in trial.holdout],
                "ranks": ranks,
                "candidate_count": len(candidates),
                "training_star_count": len(trial.training),
                "profile": _profile_diagnostics(profile),
                "top_10": [repository.full_name for repository in ordering[:10]],
            }
        )
        diagnostics.extend(
            _positive_diagnostics(repository, rank, ordering, profile, trial.training, now)
            for repository, rank in zip(trial.holdout, ranks, strict=True)
        )
    return summarize_ranks(trial_ranks), trial_records, diagnostics


def run_held_out_evaluation(
    snapshot_path: Path = SNAPSHOT_PATH,
    holdout_size: int = HOLDOUT_SIZE,
    max_trials: int = MAX_TRIALS,
    seed: int = TRIAL_SEED,
    window: int = RANKING_WINDOW,
) -> dict[str, Any]:
    """
    run the complete held-out star experiment
    :param snapshot_path: snapshot file location
    :param holdout_size: number of stars hidden per trial
    :param max_trials: upper bound on the number of trials
    :param seed: deterministic trial seed
    :param window: maximum ranked positions per trial
    :returns: complete held-out report
    """
    snapshot = load_snapshot(snapshot_path)
    eligible, excluded = partition_stars(snapshot)
    trials, strategy = build_trials(eligible, snapshot.stars, holdout_size, max_trials, seed)
    production, trial_records, diagnostics = _run_configuration(
        snapshot, trials, "production", "current", window, seed, True
    )
    baselines = {
        name: _run_configuration(snapshot, trials, name, "current", window, seed)[0]
        for name in ("popularity", "activity", "random")
    }
    ablations = {
        mode: _run_configuration(snapshot, trials, "production", mode, window, seed)[0] for mode in LANGUAGE_MODES
    }
    candidate_counts = [record["candidate_count"] for record in trial_records]
    suppressed = [item for item in diagnostics if item["redundancy_suppressed"]]
    median_stars = statistics.median([item["stars"] for item in diagnostics] or [0])
    return {
        "snapshot_date": snapshot.snapshot_date,
        "snapshot_owner": snapshot.owner,
        "coverage": {
            "total_stored_stars": len(snapshot.stars),
            "eligible_stars": len(eligible),
            "excluded_stars": len(excluded),
            "exclusions": excluded,
            "eligible_star_names": [repository.full_name for repository in eligible],
        },
        "trial_configuration": {
            "trials": len(trials),
            "selection": strategy,
            "holdout_per_trial": holdout_size,
            "training_stars_per_trial": len(snapshot.stars) - holdout_size,
            "seed": seed,
            "max_trials": max_trials,
            "ranking_window": window,
            "unranked_treatment": "counted as a miss for hit rate and recall; excluded from rank statistics",
            "candidate_pool_size": len(snapshot.candidates),
            "candidates_per_trial_min": min(candidate_counts) if candidate_counts else 0,
            "candidates_per_trial_max": max(candidate_counts) if candidate_counts else 0,
            "positive_ratio_per_trial": round(holdout_size / statistics.fmean(candidate_counts), 6)
            if candidate_counts
            else 0.0,
            "profile_sources": ["starred (training only)", "owned public repositories"],
            "search_queries": snapshot.search_queries,
        },
        "production": production,
        "baselines": baselines,
        "language_ablations": ablations,
        "segments": {
            "dominant_training_language": _segment(
                diagnostics, lambda item: item["language_is_dominant_training_language"]
            ),
            "secondary_or_unseen_language": _segment(
                diagnostics, lambda item: not item["language_is_dominant_training_language"]
            ),
            "topics_seen_in_training": _segment(diagnostics, lambda item: bool(item["matched_training_topics"])),
            "no_topic_overlap": _segment(diagnostics, lambda item: not item["matched_training_topics"]),
            "above_median_stars": _segment(diagnostics, lambda item: item["stars"] > median_stars),
            "at_or_below_median_stars": _segment(diagnostics, lambda item: item["stars"] <= median_stars),
        },
        "score_term_spreads": _term_spreads(diagnostics),
        "redundancy": {
            "positives_behind_a_near_duplicate": len(suppressed),
            "examples": [
                {
                    "repository": item["repository"],
                    "rank": item["rank"],
                    "max_similarity_to_higher_ranked": item["max_similarity_to_higher_ranked"],
                }
                for item in suppressed[:5]
            ],
        },
        "trials": trial_records,
        "held_out_diagnostics": diagnostics,
    }


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _format_metrics(title: str, metrics: dict[str, Any], indent: str = "  ") -> list[str]:
    """
    render one metric block
    :param title: block title
    :param metrics: metric values
    :param indent: leading indentation
    :returns: formatted lines
    """
    median = metrics["median_rank"]
    return [
        title,
        f"{indent}HitRate@5:         {metrics['hit_rate_at_5']:.4f}",
        f"{indent}HitRate@10:        {metrics['hit_rate_at_10']:.4f}",
        f"{indent}HitRate@20:        {metrics['hit_rate_at_20']:.4f}",
        f"{indent}Recall@5:          {metrics['recall_at_5']:.4f}",
        f"{indent}Recall@10:         {metrics['recall_at_10']:.4f}",
        f"{indent}Recall@20:         {metrics['recall_at_20']:.4f}",
        f"{indent}MRR:               {metrics['mrr']:.4f}",
        f"{indent}Median rank:       {median if median is None else f'{median:.1f}'}",
        f"{indent}Mean rank:         {metrics['mean_rank']}",
        f"{indent}Rank p25 / p75:    {metrics['p25_rank']} / {metrics['p75_rank']}",
        f"{indent}Best / worst rank: {metrics['best_rank']} / {metrics['worst_rank']}",
        f"{indent}Unranked positives: {metrics['unranked_positives']}",
    ]


def format_report(report: dict[str, Any]) -> str:
    """
    render a human readable held-out report
    :param report: held-out evaluation report
    :returns: formatted report text
    """
    coverage = report["coverage"]
    configuration = report["trial_configuration"]
    lines = [
        "Held-out star evaluation (behavioral proxy, not ground truth)",
        f"Snapshot:            {report['snapshot_date']} ({report['snapshot_owner']})",
        f"Stored stars:        {coverage['total_stored_stars']}",
        f"Eligible stars:      {coverage['eligible_stars']}",
        f"Excluded stars:      {coverage['excluded_stars']}",
    ]
    lines.extend(f"  - {entry['repository']}: {entry['reason']}" for entry in coverage["exclusions"])
    lines.extend(
        [
            f"Trials:              {configuration['trials']} "
            f"({configuration['selection']}, seed {configuration['seed']})",
            f"Held-out/trial:      {configuration['holdout_per_trial']}",
            f"Training stars:      {configuration['training_stars_per_trial']}",
            f"Candidates/trial:    {configuration['candidates_per_trial_min']}"
            f"-{configuration['candidates_per_trial_max']} "
            f"(positive ratio {configuration['positive_ratio_per_trial']:.4f})",
            f"Ranking window:      top {configuration['ranking_window']} ({configuration['unranked_treatment']})",
            "",
        ]
    )
    lines.extend(_format_metrics("Production ranking", report["production"]))
    for name, metrics in report["baselines"].items():
        lines.append("")
        lines.extend(_format_metrics(f"{name.capitalize()} baseline (evaluation only)", metrics))
    lines.extend(["", "Language ablations (evaluation only, production profile unchanged)"])
    for mode, metrics in report["language_ablations"].items():
        median = metrics["median_rank"]
        lines.append(
            f"  {mode:<12} HitRate@5 {metrics['hit_rate_at_5']:.4f}  HitRate@10 {metrics['hit_rate_at_10']:.4f}  "
            f"Recall@10 {metrics['recall_at_10']:.4f}  MRR {metrics['mrr']:.4f}  "
            f"median rank {median if median is None else f'{median:.1f}'}"
        )
    lines.extend(["", "Segments (production ranking)"])
    for name, segment in report["segments"].items():
        median = segment["median_rank"]
        lines.append(
            f"  {name:<32} positives {segment['positives']:>3}  "
            f"Recall@10 {segment['recall_at_10'] if segment['recall_at_10'] is not None else 'n/a'}  "
            f"median rank {median if median is None else f'{median:.1f}'}"
        )
    lines.extend(
        [
            "",
            "Score term discrimination across held-out positives",
            "  (a large nominal weight with a narrow IQR spread is not grading anything;",
            "   wide range plus narrow IQR means the term is acting as a presence flag)",
            f"  {'term':<10}{'weight':>8}{'min':>10}{'max':>10}{'range':>10}{'IQR':>10}",
        ]
    )
    lines.extend(
        f"  {term:<10}{spread['nominal_weight']:>8.2f}{spread['observed_min']:>10.4f}"
        f"{spread['observed_max']:>10.4f}{spread['observed_spread']:>10.4f}"
        f"{spread['interquartile_spread']:>10.4f}"
        for term, spread in report["score_term_spreads"].items()
    )
    redundancy = report["redundancy"]
    lines.extend(
        [
            "",
            "Redundancy diagnostic",
            f"  Held-out positives ranked behind a near duplicate: {redundancy['positives_behind_a_near_duplicate']}",
        ]
    )
    lines.extend(
        f"    {entry['repository']} at rank {entry['rank']} (similarity {entry['max_similarity_to_higher_ranked']:.2f})"
        for entry in redundancy["examples"]
    )
    lines.extend(["", "Per-trial held-out ranks"])
    for record in report["trials"]:
        outcomes = ", ".join(
            f"{name} -> {rank if rank is not None else 'unranked'}"
            for name, rank in zip(record["held_out"], record["ranks"], strict=True)
        )
        lines.append(f"  {record['trial']:>3}. {outcomes}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """
    run the held-out evaluation from the command line
    :param argv: optional argument list
    :returns: process exit code
    """
    parser = argparse.ArgumentParser(
        prog="python -m repo_radar.heldout_evaluation",
        description="Held-out star evaluation against a frozen snapshot of real repository metadata",
    )
    parser.add_argument("--json", action="store_true", help="emit the machine readable report")
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PATH, help="snapshot file location")
    parser.add_argument("--holdout-size", type=int, default=HOLDOUT_SIZE, help="stars hidden per trial")
    parser.add_argument("--max-trials", type=int, default=MAX_TRIALS, help="upper bound on trial count")
    parser.add_argument("--seed", type=int, default=TRIAL_SEED, help="deterministic trial seed")
    parser.add_argument("--window", type=int, default=RANKING_WINDOW, help="maximum ranked positions per trial")
    parser.add_argument("--write-baseline", action="store_true", help="overwrite the checked in held-out baseline")
    arguments = parser.parse_args(argv)
    try:
        report = run_held_out_evaluation(
            arguments.snapshot, arguments.holdout_size, arguments.max_trials, arguments.seed, arguments.window
        )
    except HeldOutEvaluationError as error:
        print(f"held-out evaluation failed: {error}", file=sys.stderr)
        return 1
    if arguments.write_baseline:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"held-out baseline written to {BASELINE_PATH}")
        return 0
    print(json.dumps(report, indent=2) if arguments.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
