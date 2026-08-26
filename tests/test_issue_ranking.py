from datetime import datetime, timezone

from repo_radar.issue_ranking import (
    normalize_label,
    rank_issues,
    score_issue,
)
from repo_radar.models import Issue, PreferenceProfile, Repository

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
FRESH = "2025-12-28T00:00:00Z"

BACKEND_PROFILE = PreferenceProfile(
    languages={"Python": 1.0},
    topics={"backend": 1.0, "api": 0.9},
    keywords={"postgresql": 1.0, "testing": 0.9, "retry": 0.8, "integration": 0.7},
)


def _backend_repository(full_name: str = "acme/service") -> Repository:
    """
    build a repository that matches the shared backend profile strongly
    :param full_name: repository full name
    :returns: relevant source repository
    """
    return Repository(
        full_name, "backend api service", "Python", ["backend", "api"], 900, pushed_at="2025-12-01T00:00:00Z"
    )


def _unrelated_repository(full_name: str = "studio/gallery") -> Repository:
    """
    build a repository that does not match the shared backend profile
    :param full_name: repository full name
    :returns: irrelevant source repository
    """
    return Repository(full_name, "illustration gallery", "CSS", ["design"], 900, pushed_at="2025-12-01T00:00:00Z")


def _issue(number: int, title: str, **overrides) -> Issue:
    """
    build an issue candidate with useful defaults
    :param number: issue number
    :param title: issue title
    :param overrides: issue field overrides
    :returns: issue candidate
    """
    fields = {
        "repository": "acme/service",
        "number": number,
        "title": title,
        "url": f"https://github.com/acme/service/issues/{number}",
        "body": None,
        "labels": [],
        "assignee_count": 0,
        "comments": 2,
        "updated_at": FRESH,
    }
    fields.update(overrides)
    return Issue(**fields)


def test_label_normalization_collapses_common_separators() -> None:
    """
    equivalent label spellings normalize to one signal name
    :returns: nothing
    """
    spellings = ["Good First Issue", "good-first-issue", "good_first_issue", " good  first issue "]
    assert {normalize_label(value) for value in spellings} == {"good first issue"}


def test_profile_relevant_issue_outranks_a_beginner_labeled_unrelated_issue() -> None:
    """
    a beginner label alone must not lift an unrelated issue above a strongly relevant one
    :returns: nothing
    """
    repository = _backend_repository()
    relevant = _issue(1, "Add PostgreSQL integration coverage for retry handling")
    unrelated = _issue(2, "Refresh the marketing illustration palette", labels=["good first issue"])
    ranked = rank_issues([unrelated, relevant], {"acme/service": repository}, BACKEND_PROFILE, now=NOW)
    assert [item.issue.number for item in ranked] == [1, 2]
    assert ranked[0].score > ranked[1].score


def test_repository_relevance_separates_identical_issues() -> None:
    """
    the same issue text ranks higher inside a more relevant repository
    :returns: nothing
    """
    repositories = {
        "acme/service": _backend_repository(),
        "studio/gallery": _unrelated_repository(),
    }
    relevant = _issue(1, "Improve retry handling")
    irrelevant = _issue(1, "Improve retry handling", repository="studio/gallery")
    ranked = rank_issues([irrelevant, relevant], repositories, BACKEND_PROFILE, now=NOW)
    assert [item.issue.repository for item in ranked] == ["acme/service", "studio/gallery"]


def test_contribution_friendliness_separates_comparably_relevant_issues() -> None:
    """
    friendliness signals decide between issues with equal relevance
    :returns: nothing
    """
    repository = _backend_repository()
    plain = _issue(1, "Improve retry handling", assignee_count=2)
    friendly = _issue(2, "Improve retry handling", labels=["help wanted"])
    ranked = rank_issues([plain, friendly], {"acme/service": repository}, BACKEND_PROFILE, now=NOW)
    assert [item.issue.number for item in ranked] == [2, 1]


def test_stale_and_blocked_signals_modestly_lower_rank() -> None:
    """
    staleness and a caution label reduce a relevant issue without discarding it
    :returns: nothing
    """
    repository = _backend_repository()
    healthy = _issue(1, "Add PostgreSQL retry handling coverage")
    troubled = _issue(
        2,
        "Add PostgreSQL retry handling coverage",
        labels=["blocked"],
        updated_at="2024-01-01T00:00:00Z",
    )
    strong = score_issue(healthy, repository, BACKEND_PROFILE, NOW)
    weak = score_issue(troubled, repository, BACKEND_PROFILE, NOW)
    assert weak.score < strong.score
    assert strong.score - weak.score < 0.2
    assert weak.scope_signal == "Needs discussion"
    assert weak.score > 0


def test_ranking_is_deterministic_across_input_order_and_ties() -> None:
    """
    equal scores break on repository name then issue number regardless of input order
    :returns: nothing
    """
    repositories = {"alpha/tool": _backend_repository("alpha/tool"), "beta/tool": _backend_repository("beta/tool")}
    candidates = [
        _issue(9, "Improve retry handling", repository="beta/tool"),
        _issue(4, "Improve retry handling", repository="alpha/tool"),
        _issue(2, "Improve retry handling", repository="beta/tool"),
    ]
    forward = rank_issues(candidates, repositories, BACKEND_PROFILE, now=NOW)
    backward = rank_issues(list(reversed(candidates)), repositories, BACKEND_PROFILE, now=NOW)
    identity = [(item.issue.repository, item.issue.number) for item in forward]
    assert identity == [(item.issue.repository, item.issue.number) for item in backward]
    assert identity == [("alpha/tool", 4), ("beta/tool", 2), ("beta/tool", 9)]


def test_one_repository_cannot_fill_every_result_slot() -> None:
    """
    the per repository cap leaves room for a second repository
    :returns: nothing
    """
    repositories = {"acme/service": _backend_repository(), "beta/tool": _backend_repository("beta/tool")}
    crowded = [_issue(number, "Improve retry handling for postgresql") for number in range(1, 6)]
    other = _issue(1, "Improve retry handling", repository="beta/tool")
    ranked = rank_issues([*crowded, other], repositories, BACKEND_PROFILE, limit=4, per_repository_limit=3, now=NOW)
    assert [item.issue.repository for item in ranked].count("acme/service") == 3
    assert "beta/tool" in [item.issue.repository for item in ranked]


def test_scores_are_not_adjusted_by_the_per_repository_cap() -> None:
    """
    a recommendation score always reports raw relevance rather than a diversity adjustment
    :returns: nothing
    """
    repository = _backend_repository()
    candidates = [_issue(number, "Improve retry handling") for number in range(1, 4)]
    capped = rank_issues(candidates, {"acme/service": repository}, BACKEND_PROFILE, per_repository_limit=1, now=NOW)
    assert capped[0].score == score_issue(candidates[0], repository, BACKEND_PROFILE, NOW).score


def test_explanations_only_claim_evidence_that_scored() -> None:
    """
    reasons never mention preference matches, labels, or assignment that did not contribute
    :returns: nothing
    """
    repository = _backend_repository()
    assigned = _issue(1, "Refresh the illustration palette", assignee_count=1)
    bare = score_issue(assigned, repository, BACKEND_PROFILE, NOW)
    assert not any(reason.startswith("Issue mentions") for reason in bare.reasons)
    assert not any(reason.startswith("Label:") for reason in bare.reasons)
    assert "No assignee" not in bare.reasons
    assert any("may already be in progress" in reason for reason in bare.reasons)


def test_explanations_report_matched_terms_labels_and_freshness() -> None:
    """
    contributing evidence appears in the explanation in readable form
    :returns: nothing
    """
    repository = _backend_repository()
    issue = _issue(
        1,
        "Add PostgreSQL integration coverage",
        labels=["Good First Issue"],
        body="Steps to reproduce: run the suite against tests/test_retry.py and compare expected behavior.",
    )
    recommendation = score_issue(issue, repository, BACKEND_PROFILE, NOW)
    assert "acme/service: strong match for Python, backend, api" in recommendation.reasons
    assert "Issue mentions postgresql, integration" in recommendation.reasons
    assert "Label: good first issue" in recommendation.reasons
    assert "No assignee" in recommendation.reasons
    assert "Updated 4 days ago" in recommendation.reasons


def test_scope_signal_reports_evidence_without_difficulty_claims() -> None:
    """
    the scope signal stays descriptive and is backed by the evidence that produced it
    :returns: nothing
    """
    repository = _backend_repository()
    focused = score_issue(
        _issue(
            1,
            "Fix retry handling",
            body="Steps to reproduce: call the client twice. See src/retry.py for expected behavior.",
        ),
        repository,
        BACKEND_PROFILE,
        NOW,
    )
    assert focused.scope_signal == "Focused"
    assert "Reproduction or expected behavior described" in focused.scope_evidence
    assert "References code or a specific file" in focused.scope_evidence
    banned = ("easy", "hour", "perfect for you", "guaranteed")
    text = " ".join([focused.scope_signal, *focused.scope_evidence, *focused.reasons]).lower()
    assert not any(word in text for word in banned)


def test_long_discussion_lowers_readiness() -> None:
    """
    a heavily debated issue reads as less ready than a quiet one
    :returns: nothing
    """
    repository = _backend_repository()
    quiet = score_issue(_issue(1, "Fix retry handling", comments=1), repository, BACKEND_PROFILE, NOW)
    contested = score_issue(_issue(2, "Fix retry handling", comments=40), repository, BACKEND_PROFILE, NOW)
    assert contested.score < quiet.score
    assert any("40 comments" in evidence for evidence in contested.scope_evidence)


def test_issues_without_a_known_source_repository_are_dropped() -> None:
    """
    ranking never invents repository metadata for an unexpected search result
    :returns: nothing
    """
    ranked = rank_issues([_issue(1, "Improve retry handling")], {}, BACKEND_PROFILE, now=NOW)
    assert ranked == []
