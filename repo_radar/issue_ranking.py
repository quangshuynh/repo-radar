"""transparent contribution opportunity ranking

Issue ranking is deliberately a separate scoring layer from repository ranking. It reuses the
repository score as one input signal but never modifies the established repository weights,
because the two questions differ: repository ranking answers "would this project interest
you", issue ranking answers "is this specific open issue worth your time to investigate".
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import Issue, IssueRecommendation, PreferenceProfile, Repository
from .profile import extract_keywords
from .ranking import _parse_date, _strongest_matches, score_repository

# Top level weights. Personalization (repository relevance plus issue relevance) carries 0.65,
# so contribution friendliness cannot lift an unrelated issue above a strongly relevant one.
# The weights are chosen against reachable ranges, not nominal size: a beginner label alone is
# worth 0.5 friendliness credit and therefore 0.075 of score, while a title matching two strong
# profile terms is worth roughly 0.6 issue relevance and therefore 0.115. Friendliness stays a
# tie-breaker between comparably relevant issues instead of a shortcut past relevance.
REPOSITORY_RELEVANCE_WEIGHT = 0.30
ISSUE_RELEVANCE_WEIGHT = 0.35
FRIENDLINESS_WEIGHT = 0.15
FRESHNESS_WEIGHT = 0.10
READINESS_WEIGHT = 0.10

# Issue relevance sub-weights. The title is the most reliable statement of what an issue is
# about; labels are curated but coarse; the body is noisy and frequently templated.
TITLE_RELEVANCE_WEIGHT = 0.55
LABEL_RELEVANCE_WEIGHT = 0.25
BODY_RELEVANCE_WEIGHT = 0.20

# Evidence bounds follow the repository ranker's convention of averaging the strongest matches
# over a fixed limit, so more matching evidence always scores at least as high. The limits are
# smaller than the repository ranker's because an issue title carries only a handful of content
# words; requiring four strong matches would leave the term flat near zero for real issues.
TITLE_MATCH_LIMIT = 3
LABEL_MATCH_LIMIT = 2
BODY_MATCH_LIMIT = 3
# bounding the body keeps long contribution templates and stack traces from swamping the
# handful of tokens that actually describe the work
BODY_CHARACTER_LIMIT = 600

# An issue untouched for six months is treated as fully stale. Repository activity decays
# over 730 days because a stable library is not a dead one, but an open issue with no
# movement for half a year is materially less likely to be actionable.
FRESHNESS_HORIZON_DAYS = 180

FRIENDLY_BEGINNER_CREDIT = 0.5
FRIENDLY_HELP_WANTED_CREDIT = 0.35
UNASSIGNED_CREDIT = 0.3
DESCRIBED_CREDIT = 0.2
USEFUL_DESCRIPTION_CHARACTERS = 120

READINESS_BASE = 0.5
CAUTION_LABEL_PENALTY = 0.3
REPRODUCTION_EVIDENCE_BONUS = 0.2
CODE_REFERENCE_BONUS = 0.15
FOCUSED_DISCUSSION_BONUS = 0.15
CONTESTED_DISCUSSION_PENALTY = 0.15
FOCUSED_COMMENT_LIMIT = 5
CONTESTED_COMMENT_LIMIT = 25
FOCUSED_SCOPE_THRESHOLD = 0.7

# Small, transparent label vocabularies rather than an exhaustive catalogue of every label
# GitHub projects invent. Labels are normalized before matching, so "Good First Issue",
# "good-first-issue", and "good_first_issue" all reach the same entry.
BEGINNER_LABELS = frozenset(
    {"good first issue", "good first bug", "beginner", "beginner friendly", "first timers only", "starter", "easy"}
)
HELP_WANTED_LABELS = frozenset({"help wanted", "contributions welcome", "up for grabs", "hacktoberfest"})
CAUTION_LABELS = frozenset(
    {
        "blocked",
        "on hold",
        "needs design",
        "needs discussion",
        "discussion",
        "rfc",
        "epic",
        "tracking",
        "question",
        "stale",
        "wontfix",
    }
)

REPRODUCTION_MARKERS = ("to reproduce", "steps to reproduce", "expected behavior", "expected behaviour", "traceback")
CODE_REFERENCE_PATTERN = re.compile(r"```|\b[\w./-]+\.(py|js|ts|tsx|go|rs|rb|java|c|cc|cpp|h|md|toml|yml|yaml|json)\b")


def normalize_label(value: str) -> str:
    """
    normalize a GitHub label name for signal matching
    :param value: raw label name
    :returns: lowercase label with separators collapsed to single spaces
    """
    return " ".join(re.split(r"[\s\-_/:]+", value.strip().lower())).strip()


def _preference_strength(term: str, profile: PreferenceProfile) -> float:
    """
    look up how strongly one term matches the preference profile
    :param term: lowercase candidate term
    :param profile: user preference profile
    :returns: strongest normalized preference weight for the term
    """
    return max(profile.topics.get(term, 0.0), profile.keywords.get(term, 0.0))


def _term_score(terms: list[str], profile: PreferenceProfile, limit: int) -> tuple[float, list[str]]:
    """
    score bounded term evidence against the preference profile
    :param terms: candidate terms in any order
    :param profile: user preference profile
    :param limit: maximum retained matches
    :returns: mean strength of the strongest matches and the matched terms
    """
    unique = dict.fromkeys(terms)
    matches = _strongest_matches(((term, _preference_strength(term, profile)) for term in unique), limit)
    return sum(score for _, score in matches) / limit, [term for term, score in matches if score > 0]


def issue_relevance(issue: Issue, profile: PreferenceProfile) -> tuple[float, list[str]]:
    """
    score how well an issue itself matches the preference profile
    :param issue: candidate issue
    :param profile: user preference profile
    :returns: relevance from zero to one and the matched terms
    """
    title_score, title_terms = _term_score(extract_keywords(issue.title), profile, TITLE_MATCH_LIMIT)
    label_score, label_terms = _term_score(
        [normalize_label(label) for label in issue.labels], profile, LABEL_MATCH_LIMIT
    )
    body_score, body_terms = _term_score(
        extract_keywords((issue.body or "")[:BODY_CHARACTER_LIMIT]), profile, BODY_MATCH_LIMIT
    )
    score = (
        TITLE_RELEVANCE_WEIGHT * title_score + LABEL_RELEVANCE_WEIGHT * label_score + BODY_RELEVANCE_WEIGHT * body_score
    )
    return score, list(dict.fromkeys([*title_terms, *label_terms, *body_terms]))


def _matching_labels(issue: Issue, vocabulary: frozenset[str]) -> list[str]:
    """
    collect the issue labels belonging to one signal vocabulary
    :param issue: candidate issue
    :param vocabulary: normalized label vocabulary
    :returns: matching normalized labels sorted deterministically
    """
    return sorted({normalize_label(label) for label in issue.labels} & vocabulary)


def contribution_friendliness(issue: Issue) -> tuple[float, list[str]]:
    """
    score explicit signals that an issue invites outside contribution
    :param issue: candidate issue
    :returns: friendliness from zero to one and the evidence behind it
    """
    credit = 0.0
    evidence: list[str] = []
    beginner = _matching_labels(issue, BEGINNER_LABELS)
    help_wanted = _matching_labels(issue, HELP_WANTED_LABELS)
    if beginner:
        credit += FRIENDLY_BEGINNER_CREDIT
        evidence.extend(f"Label: {label}" for label in beginner)
    if help_wanted:
        credit += FRIENDLY_HELP_WANTED_CREDIT
        evidence.extend(f"Label: {label}" for label in help_wanted)
    if issue.assignee_count == 0:
        credit += UNASSIGNED_CREDIT
        evidence.append("No assignee")
    if len(issue.body or "") >= USEFUL_DESCRIPTION_CHARACTERS:
        credit += DESCRIBED_CREDIT
        evidence.append("Issue includes a written description")
    return min(1.0, credit), evidence


def freshness(issue: Issue, now: datetime) -> tuple[float, list[str]]:
    """
    score how recently an issue was updated
    :param issue: candidate issue
    :param now: reference time
    :returns: freshness from zero to one and the evidence behind it
    """
    updated = _parse_date(issue.updated_at or issue.created_at)
    if not updated:
        return 0.0, []
    days = max(0, (now - updated).days)
    if days >= FRESHNESS_HORIZON_DAYS:
        return 0.0, [f"No update in over {FRESHNESS_HORIZON_DAYS} days"]
    label = "Updated today" if days == 0 else f"Updated {days} day{'s' if days > 1 else ''} ago"
    return 1.0 - days / FRESHNESS_HORIZON_DAYS, [label]


def scope_readiness(issue: Issue) -> tuple[float, list[str]]:
    """
    score explicit signals about how well defined the work appears

    This measures stated readiness, not engineering difficulty. Issue metadata cannot support
    a difficulty or time estimate, so none is produced.
    :param issue: candidate issue
    :returns: readiness from zero to one and the evidence behind it
    """
    readiness = READINESS_BASE
    evidence: list[str] = []
    caution = _matching_labels(issue, CAUTION_LABELS)
    if caution:
        readiness -= CAUTION_LABEL_PENALTY
        evidence.append(f"Caution label: {', '.join(caution)}")
    body = (issue.body or "").lower()
    if any(marker in body for marker in REPRODUCTION_MARKERS):
        readiness += REPRODUCTION_EVIDENCE_BONUS
        evidence.append("Reproduction or expected behavior described")
    if CODE_REFERENCE_PATTERN.search(issue.body or ""):
        readiness += CODE_REFERENCE_BONUS
        evidence.append("References code or a specific file")
    if issue.comments <= FOCUSED_COMMENT_LIMIT:
        readiness += FOCUSED_DISCUSSION_BONUS
        evidence.append(f"{issue.comments} comments so far")
    elif issue.comments >= CONTESTED_COMMENT_LIMIT:
        readiness -= CONTESTED_DISCUSSION_PENALTY
        evidence.append(f"Long discussion with {issue.comments} comments")
    return min(1.0, max(0.0, readiness)), evidence


def _scope_signal(readiness: float, issue: Issue) -> str:
    """
    describe the readiness score in cautious product language
    :param readiness: readiness score
    :param issue: candidate issue
    :returns: short scope signal label
    """
    if _matching_labels(issue, CAUTION_LABELS):
        return "Needs discussion"
    return "Focused" if readiness >= FOCUSED_SCOPE_THRESHOLD else "Unclear"


def score_issue(
    issue: Issue, repository: Repository, profile: PreferenceProfile, now: datetime | None = None
) -> IssueRecommendation:
    """
    score one contribution opportunity and record the evidence behind it
    :param issue: candidate issue
    :param repository: repository owning the issue
    :param profile: user preference profile
    :param now: optional reference time for deterministic scoring
    :returns: scored contribution recommendation
    """
    reference = now or datetime.now(timezone.utc)
    repository_score, repository_explanation = score_repository(repository, profile, reference)
    relevance, relevant_terms = issue_relevance(issue, profile)
    friendliness, friendliness_evidence = contribution_friendliness(issue)
    fresh, freshness_evidence = freshness(issue, reference)
    readiness, readiness_evidence = scope_readiness(issue)
    score = (
        REPOSITORY_RELEVANCE_WEIGHT * repository_score
        + ISSUE_RELEVANCE_WEIGHT * relevance
        + FRIENDLINESS_WEIGHT * friendliness
        + FRESHNESS_WEIGHT * fresh
        + READINESS_WEIGHT * readiness
    )
    reasons: list[str] = []
    if repository_score > 0:
        reasons.append(f"{repository.full_name}: {repository_explanation}")
    if relevant_terms:
        reasons.append(f"Issue mentions {', '.join(relevant_terms)}")
    reasons.extend(friendliness_evidence)
    reasons.extend(freshness_evidence)
    if issue.assignee_count:
        reasons.append(f"Assigned to {issue.assignee_count}, so it may already be in progress")
    return IssueRecommendation(
        issue=issue,
        repository=repository,
        score=min(1.0, score),
        reasons=reasons,
        scope_signal=_scope_signal(readiness, issue),
        scope_evidence=readiness_evidence,
    )


def rank_issues(
    issues: list[Issue],
    repositories: dict[str, Repository],
    profile: PreferenceProfile,
    limit: int = 10,
    per_repository_limit: int = 3,
    now: datetime | None = None,
) -> list[IssueRecommendation]:
    """
    rank contribution opportunities and keep one repository from filling every slot
    :param issues: candidate issues
    :param repositories: source repositories keyed by lowercase full name
    :param profile: user preference profile
    :param limit: maximum recommendations
    :param per_repository_limit: maximum recommendations from any one repository
    :param now: optional reference time for deterministic scoring
    :returns: ranked contribution recommendations
    """
    scored = [
        score_issue(issue, repositories[issue.repository.lower()], profile, now)
        for issue in issues
        if issue.repository.lower() in repositories
    ]
    # ordering is fully deterministic: score first, then repository name, then issue number
    scored.sort(key=lambda item: (-item.score, item.issue.repository.lower(), item.issue.number))
    # the per repository cap is a selection rule applied after scoring, so a recommendation
    # score always reports raw relevance rather than a diversity adjusted value
    selected: list[IssueRecommendation] = []
    counts: dict[str, int] = {}
    for recommendation in scored:
        key = recommendation.issue.repository.lower()
        if counts.get(key, 0) >= per_repository_limit:
            continue
        counts[key] = counts.get(key, 0) + 1
        selected.append(recommendation)
        if len(selected) >= limit:
            break
    return selected
