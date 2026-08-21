from repo_radar.models import ImportedProfile, ImportedRepository, Repository, SeedPreferences
from repo_radar.profile import build_profile, extract_keywords


def test_build_profile_normalizes_signals() -> None:
    """
    profile signals normalize against the most frequent value
    :returns: nothing
    """
    repositories = [
        Repository("a/one", "Python automation tool", "Python", ["automation"], 10),
        Repository("b/two", "Python developer utility", "Python", ["developer-tools"], 100),
        Repository("c/three", None, None, [], 1),
    ]
    profile = build_profile(repositories)
    assert profile.languages == {"Python": 1.0}
    assert profile.topics["automation"] == 1.0
    assert profile.median_stars == 10
    assert "python" in profile.keywords


def test_empty_profile_and_missing_description() -> None:
    """
    empty inputs and missing descriptions produce empty signals
    :returns: nothing
    """
    assert build_profile([]).languages == {}
    assert extract_keywords(None) == []


def test_seed_preferences_build_profile_without_stars() -> None:
    """
    manual preferences produce profile signals without starred repositories
    :returns: nothing
    """
    seeds = SeedPreferences(["Python", "TypeScript"], ["developer-tools"], ["cli"])
    profile = build_profile([], seeds)
    assert profile.languages == {"Python": 1.0, "TypeScript": 1.0}
    assert profile.topics == {"developer-tools": 1.0}
    assert profile.keywords == {"cli": 1.0}
    assert profile.median_stars == 0


def test_seed_preferences_merge_with_starred_signals() -> None:
    """
    manual and inferred preferences contribute to one normalized profile
    :returns: nothing
    """
    repositories = [
        Repository("one/repo", language="Python", topics=["automation"]),
        Repository("two/repo", language="Python", topics=["backend"]),
    ]
    seeds = SeedPreferences(["TypeScript"], ["automation"], ["api"])
    profile = build_profile(repositories, seeds)
    assert profile.languages == {"Python": 1.0, "TypeScript": 0.3}
    assert profile.topics["automation"] == 1.0
    assert profile.topics["backend"] == 0.625
    assert profile.keywords["api"] == 1.0


def test_imported_profile_weights_pinned_above_other_repositories() -> None:
    """
    pinned owned repositories contribute more than normal owned repositories
    :returns: nothing
    """
    imported = ImportedProfile(
        "owner",
        repositories=[
            ImportedRepository("pinned", "api tool", language="Python", pinned=True),
            ImportedRepository("other", "web tool", language="JavaScript"),
            ImportedRepository("archived", language="Rust", archived=True),
            ImportedRepository("fork", language="Go", is_fork=True),
        ],
    )
    profile = build_profile([], imported_profile=imported)
    assert profile.languages == {"Python": 1.0, "JavaScript": 0.437}
    assert "Rust" not in profile.languages
    assert "Go" not in profile.languages


def test_imported_profile_combines_with_stars_and_seeds() -> None:
    """
    imported starred and manual signals share one normalized profile
    :returns: nothing
    """
    starred = [Repository("starred/repo", language="Python", topics=["automation"])]
    seeds = SeedPreferences(["TypeScript"], ["backend"], ["cli"])
    imported = ImportedProfile(
        "owner",
        repositories=[ImportedRepository("owned", "api service", language="Go", pinned=True)],
    )
    profile = build_profile(starred, seeds, imported)
    assert profile.languages == {"Python": 1.0, "Go": 0.8, "TypeScript": 0.6}
    assert profile.topics["automation"] == 1.0
    assert profile.topics["backend"] == 0.6
    assert profile.keywords["api"] > profile.keywords["cli"]
