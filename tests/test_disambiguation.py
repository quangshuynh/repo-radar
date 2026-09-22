from datetime import datetime, timezone

from repo_radar.disambiguation import (
    CONSOLE_HOMEBREW,
    MACOS_PACKAGE_MANAGER,
    preference_applies,
    profile_senses,
    resolve_sense,
    sense_context,
)
from repo_radar.issue_ranking import rank_issues, score_issue
from repo_radar.models import Issue, PreferenceProfile, Repository
from repo_radar.ranking import score_repository

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PUSHED = "2025-12-01T00:00:00Z"
FRESH = "2025-12-28T00:00:00Z"

# the profile reported in the false positive: console homebrew signals plus the ambiguous term
SWITCH_PROFILE = PreferenceProfile(
    languages={"C++": 1.0},
    topics={"nintendo-switch": 1.0, "homebrew": 1.0},
    keywords={"homebrew": 1.0, "switch": 0.8},
)

# the mirror image: a user who genuinely follows the macOS package manager
MACOS_PROFILE = PreferenceProfile(
    languages={"Ruby": 1.0},
    topics={"homebrew": 1.0, "macos": 1.0, "package-manager": 0.9},
    keywords={"homebrew": 1.0, "formula": 0.8},
)

# nothing in this profile resolves "homebrew" either way
GENERIC_HOMEBREW_PROFILE = PreferenceProfile(
    languages={"C++": 1.0},
    topics={"homebrew": 1.0},
    keywords={"homebrew": 1.0},
)

BACKEND_PROFILE = PreferenceProfile(
    languages={"Python": 1.0},
    topics={"backend": 1.0, "api": 0.9},
    keywords={"postgresql": 1.0, "testing": 0.9},
)


def _console_repository() -> Repository:
    """
    build a Nintendo Switch homebrew repository
    :returns: console homebrew candidate
    """
    return Repository(
        "dev/save-editor",
        "Nintendo Switch homebrew save editor",
        "C++",
        ["nintendo-switch", "homebrew", "libnx"],
        900,
        pushed_at=PUSHED,
    )


def _tap_repository(full_name: str = "dev/tooling") -> Repository:
    """
    build a macOS package manager repository carrying the same ambiguous term
    :param full_name: repository full name
    :returns: macOS Homebrew candidate
    """
    return Repository(
        full_name,
        "Homebrew tap with formulae for macOS package management",
        "C++",
        ["homebrew", "macos", "tap"],
        900,
        pushed_at=PUSHED,
    )


def _tap_repository_without_the_term(full_name: str = "dev/tooling") -> Repository:
    """
    build the same macOS repository with every mention of the ambiguous term removed

    Scoring this alongside `_tap_repository` measures exactly what the ambiguous term
    contributed, without freezing any float internal.
    :param full_name: repository full name
    :returns: macOS candidate carrying no ambiguous term
    """
    return Repository(
        full_name,
        "Tap with formulae for macOS package management",
        "C++",
        ["macos", "tap"],
        900,
        pushed_at=PUSHED,
    )


def _issue(repository: str, number: int, title: str, **overrides) -> Issue:
    """
    build an issue candidate with useful defaults
    :param repository: owning repository full name
    :param number: issue number
    :param title: issue title
    :param overrides: issue field overrides
    :returns: issue candidate
    """
    fields = {
        "repository": repository,
        "number": number,
        "title": title,
        "url": f"https://github.com/{repository}/issues/{number}",
        "body": None,
        "labels": [],
        "assignee_count": 0,
        "comments": 2,
        "updated_at": FRESH,
    }
    fields.update(overrides)
    return Issue(**fields)


def test_sense_context_reads_two_word_markers_from_free_text() -> None:
    """
    an ordered phrase contributes its adjacent word pairs so hyphenated markers are reachable
    :returns: nothing
    """
    context = sense_context(["Nintendo-Switch"], [["nintendo", "switch", "homebrew"]])
    assert "nintendo-switch" in context
    assert "switch-homebrew" in context
    # a standalone term list is not a phrase, so no pair is invented across unrelated topics
    assert "nintendo-switch" not in sense_context(["nintendo", "switch"])


def test_sense_resolution_requires_decisive_evidence() -> None:
    """
    a sense is assigned only when one side is corroborated by strictly more markers
    :returns: nothing
    """
    assert resolve_sense("homebrew", sense_context(["nintendo-switch", "libnx"])) == CONSOLE_HOMEBREW
    assert resolve_sense("brew", sense_context(["macos", "tap"])) == MACOS_PACKAGE_MANAGER
    # nothing to read it against, and equally corroborated evidence, both stay undetermined
    assert resolve_sense("homebrew", sense_context(["cli"])) is None
    assert resolve_sense("homebrew", sense_context(["nintendo-switch", "macos"])) is None
    # unambiguous terms are never sense resolved at all
    assert resolve_sense("postgresql", sense_context(["macos"])) is None


def test_unambiguous_profiles_resolve_no_senses() -> None:
    """
    a profile without ambiguous terms is unaffected, so every preference keeps applying
    :returns: nothing
    """
    assert profile_senses(BACKEND_PROFILE) == {}
    assert preference_applies("postgresql", profile_senses(BACKEND_PROFILE), sense_context(["macos", "tap"]))


def test_switch_profile_keeps_console_homebrew_relevance() -> None:
    """
    a console homebrew candidate still earns the profile's homebrew weight
    :returns: nothing
    """
    score, explanation = score_repository(_console_repository(), SWITCH_PROFILE, NOW)
    assert "homebrew" in explanation
    assert score > score_repository(_tap_repository(), SWITCH_PROFILE, NOW)[0]


def test_switch_profile_withholds_homebrew_credit_from_a_macos_tap_repository() -> None:
    """
    the ambiguous term contributes nothing to a candidate that clearly means the other sense
    :returns: nothing
    """
    score, explanation = score_repository(_tap_repository(), SWITCH_PROFILE, NOW)
    without_term, _ = score_repository(_tap_repository_without_the_term(), SWITCH_PROFILE, NOW)
    assert score == without_term
    assert "homebrew" not in explanation


def test_macos_profile_keeps_its_homebrew_relevance() -> None:
    """
    the same macOS candidate stays relevant for a user whose profile means that sense
    :returns: nothing
    """
    score, explanation = score_repository(_tap_repository(), MACOS_PROFILE, NOW)
    without_term, _ = score_repository(_tap_repository_without_the_term(), MACOS_PROFILE, NOW)
    assert "homebrew" in explanation
    assert score > without_term


def test_ambiguous_homebrew_evidence_keeps_its_credit() -> None:
    """
    undetermined evidence on either side scores exactly as it did before disambiguation
    :returns: nothing
    """
    # the profile says only "homebrew", so nothing justifies reading the candidate against it
    assert "homebrew" in score_repository(_tap_repository(), GENERIC_HOMEBREW_PROFILE, NOW)[1]
    # the candidate corroborates both senses equally, so the console profile still credits it
    both = Repository(
        "dev/toolchain",
        "Homebrew formula installing the devkitPro toolchain",
        "C++",
        ["homebrew", "macos", "nintendo-switch"],
        900,
        pushed_at=PUSHED,
    )
    assert "homebrew" in score_repository(both, SWITCH_PROFILE, NOW)[1]


def test_switch_profile_withholds_homebrew_credit_from_a_tap_issue() -> None:
    """
    an issue about brew taps earns no homebrew relevance for a console homebrew profile
    :returns: nothing
    """
    repository = _tap_repository_without_the_term("dev/cli")
    issue = _issue("dev/cli", 1, "Add a Homebrew tap and formula for the CLI")
    neutral = _issue("dev/cli", 1, "Add a tap and formula for the CLI")
    scored = score_issue(issue, repository, SWITCH_PROFILE, NOW)
    assert scored.score == score_issue(neutral, repository, SWITCH_PROFILE, NOW).score
    assert "homebrew" not in " ".join(scored.reasons).lower()


def test_macos_profile_keeps_homebrew_relevance_on_a_tap_issue() -> None:
    """
    the same issue keeps its homebrew relevance and evidence for a macOS oriented profile
    :returns: nothing
    """
    repository = _tap_repository_without_the_term("dev/cli")
    issue = _issue("dev/cli", 1, "Add a Homebrew tap and formula for the CLI")
    neutral = _issue("dev/cli", 1, "Add a tap and formula for the CLI")
    scored = score_issue(issue, repository, MACOS_PROFILE, NOW)
    assert scored.score > score_issue(neutral, repository, MACOS_PROFILE, NOW).score
    assert "homebrew" in " ".join(scored.reasons).lower()


def test_console_homebrew_issue_outranks_a_brew_tap_issue() -> None:
    """
    for a console homebrew profile the Switch issue ranks above the package manager issue
    :returns: nothing
    """
    console = _console_repository()
    tap = _tap_repository_without_the_term("dev/cli")
    repositories = {console.full_name.lower(): console, tap.full_name.lower(): tap}
    switch_issue = _issue(console.full_name, 1, "Homebrew launcher crashes when loading a save")
    # the package manager issue is deliberately the friendlier one
    tap_issue = _issue(tap.full_name, 2, "Add a Homebrew tap and formula", labels=["good first issue"])
    ranked = rank_issues([tap_issue, switch_issue], repositories, SWITCH_PROFILE, now=NOW)
    assert [item.issue.number for item in ranked] == [1, 2]


def test_unrelated_recommendations_are_unchanged_by_disambiguation() -> None:
    """
    candidates and profiles with no ambiguous term keep their existing ordering and evidence
    :returns: nothing
    """
    relevant = Repository("acme/service", "backend api service", "Python", ["backend", "api"], 900, pushed_at=PUSHED)
    unrelated = Repository("studio/gallery", "illustration gallery", "CSS", ["design"], 900, pushed_at=PUSHED)
    repositories = {relevant.full_name.lower(): relevant, unrelated.full_name.lower(): unrelated}
    strong = _issue(relevant.full_name, 1, "Flaky PostgreSQL testing fixture")
    weak = _issue(unrelated.full_name, 2, "Refresh the illustration palette", labels=["good first issue"])
    ranked = rank_issues([weak, strong], repositories, BACKEND_PROFILE, now=NOW)
    assert [item.issue.number for item in ranked] == [1, 2]
    assert score_repository(relevant, BACKEND_PROFILE, NOW)[0] > score_repository(unrelated, BACKEND_PROFILE, NOW)[0]
