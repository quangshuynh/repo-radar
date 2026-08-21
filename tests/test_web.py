from fastapi.testclient import TestClient

from repo_radar.gitprofilelens import GitProfileLensError
from repo_radar.models import ImportedProfile, ImportedRepository, Repository, SeedPreferences
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


def test_interested_repository_is_saved_and_updates_profile(tmp_path, monkeypatch) -> None:
    """
    interested feedback persists metadata and contributes profile signals
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        json={
            "repository": "owner/tool",
            "classification": "interested",
            "description": "Rust terminal helper",
            "language": "Rust",
            "topics": ["terminal"],
            "stars": 12,
            "url": "https://github.com/owner/tool",
        },
    )
    interested = client.get("/api/interested")
    profile = client.get("/api/profile")
    assert response.status_code == 200
    assert interested.json()["repositories"][0]["full_name"] == "owner/tool"
    assert profile.json()["interested_count"] == 1
    assert profile.json()["languages"] == {"Rust": 1.0}


def test_star_repository_calls_github_and_updates_local_cache(tmp_path, monkeypatch) -> None:
    """
    web star action calls GitHub then updates local starred state
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    starred = []

    class FakeGitHubClient:
        """mock GitHub client for web starring"""

        def star_repository(self, repository: str) -> None:
            """
            record one requested GitHub star
            :param repository: repository full name
            :returns: nothing
            """
            starred.append(repository)

    monkeypatch.setattr("repo_radar.web.GitHubClient", FakeGitHubClient)
    response = TestClient(app).post(
        "/api/star",
        json={"repository": "owner/tool", "language": "Python", "topics": ["cli"], "stars": 9},
    )
    storage = Storage(tmp_path)
    assert response.json() == {"repository": "owner/tool", "starred": True}
    assert starred == ["owner/tool"]
    assert storage.load_repositories() == [
        Repository("owner/tool", language="Python", topics=["cli"], stars=9, owner="owner")
    ]
    assert storage.load_feedback()["owner/tool"] == "starred"


def test_saved_repository_removal_endpoints_clear_feedback(tmp_path, monkeypatch) -> None:
    """
    single and bulk removal clear saved repositories and interested feedback
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    storage = Storage(tmp_path)
    repositories = [Repository("one/tool"), Repository("two/tool")]
    storage.save_interested_repositories(repositories)
    storage.save_feedback({"one/tool": "interested", "two/tool": "interested", "keep/rejected": "blocked"})
    client = TestClient(app)
    removed = client.delete("/api/interested/one/tool")
    cleared = client.delete("/api/interested")
    assert removed.json() == {"repository": "one/tool", "removed": True}
    assert cleared.json() == {"removed_count": 1}
    assert storage.load_interested_repositories() == []
    assert storage.load_feedback() == {"keep/rejected": "blocked"}


def test_star_all_saved_repositories_updates_each_confirmed_star(tmp_path, monkeypatch) -> None:
    """
    bulk starring sends every saved repository to GitHub and clears the saved list
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    storage = Storage(tmp_path)
    storage.save_interested_repositories([Repository("one/tool"), Repository("two/tool")])
    storage.save_feedback({"one/tool": "interested", "two/tool": "interested"})
    starred = []

    class FakeGitHubClient:
        """mock GitHub client for bulk starring"""

        def star_repository(self, repository: str) -> None:
            """
            record one requested GitHub star
            :param repository: repository full name
            :returns: nothing
            """
            starred.append(repository)

    monkeypatch.setattr("repo_radar.web.GitHubClient", FakeGitHubClient)
    response = TestClient(app).post("/api/interested/star-all")
    assert response.json() == {"starred_count": 2}
    assert starred == ["one/tool", "two/tool"]
    assert storage.load_interested_repositories() == []
    assert {repository.full_name for repository in storage.load_repositories()} == {"one/tool", "two/tool"}
