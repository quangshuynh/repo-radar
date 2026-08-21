from fastapi.testclient import TestClient

from repo_radar.gitprofilelens import GitProfileLensError
from repo_radar.models import ImportedProfile, ImportedRepository, SeedPreferences
from repo_radar.storage import Storage
from repo_radar.web import app


def test_profile_and_empty_recommendation_apis(tmp_path, monkeypatch) -> None:
    """
    web profile and recommendation APIs support an empty local state
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    profile = client.get("/api/profile")
    recommendations = client.get("/api/recommendations")
    assert profile.status_code == 200
    assert profile.json()["starred_count"] == 0
    assert recommendations.status_code == 200
    assert recommendations.json()["recommendations"] == []


def test_preference_feedback_and_import_apis(tmp_path, monkeypatch) -> None:
    """
    web mutations use existing local persistence formats
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))

    def fake_import(username: str, storage: Storage) -> ImportedProfile:
        """
        save and return a mocked GitProfileLens profile
        :param username: requested username
        :param storage: local storage manager
        :returns: mocked imported profile
        """
        profile = ImportedProfile(
            username,
            1,
            repositories=[ImportedRepository("tool", pinned=True, language="Python", topics=["cli"])],
        )
        storage.save_imported_profile(profile)
        return profile

    monkeypatch.setattr("repo_radar.web.import_profile", fake_import)
    client = TestClient(app)
    preferences = client.post(
        "/api/preferences",
        json={"languages": ["Python"], "topics": ["Automation"], "keywords": ["CLI"]},
    )
    feedback = client.post("/api/feedback", json={"repository": "owner/repo", "classification": "blocked"})
    imported = client.post("/api/import-profile", json={"username": "example"})
    assert preferences.status_code == 200
    assert feedback.status_code == 200
    assert imported.json() == {
        "username": "example",
        "repository_count": 1,
        "pinned_count": 1,
        "language_count": 1,
        "topic_count": 1,
    }
    assert Storage(tmp_path).load_seed_preferences() == SeedPreferences(["Python"], ["automation"], ["cli"])


def test_import_failure_preserves_data_and_redacts_token(tmp_path, monkeypatch) -> None:
    """
    web import errors preserve data and never return configured credentials
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_TOKEN", "secret-test-token")
    previous = ImportedProfile("example", repositories=[ImportedRepository("existing")])
    Storage(tmp_path).save_imported_profile(previous)

    def fail_import(username: str, storage: Storage) -> ImportedProfile:
        """
        raise a safe mocked import error containing a credential
        :param username: requested username
        :param storage: local storage manager
        :returns: no imported profile
        """
        raise GitProfileLensError("failure secret-test-token")

    monkeypatch.setattr("repo_radar.web.import_profile", fail_import)
    response = TestClient(app).post("/api/import-profile", json={"username": "example"})
    assert response.status_code == 502
    assert "secret-test-token" not in response.text
    assert Storage(tmp_path).load_imported_profile() == previous
