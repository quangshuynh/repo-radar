from repo_radar.models import Repository
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
