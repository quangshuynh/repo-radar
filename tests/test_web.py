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


def test_local_feedback_never_stars_without_star_action(tmp_path, monkeypatch) -> None:
    """
    ordinary feedback actions never invoke the GitHub star mutation
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))

    class ForbiddenGitHubClient:
        """GitHub client that fails if constructed by a local feedback action"""

        def __init__(self) -> None:
            """
            reject unexpected GitHub client construction
            :returns: nothing
            """
            raise AssertionError("Local feedback must not construct a GitHub client")

    monkeypatch.setattr("repo_radar.web.GitHubClient", ForbiddenGitHubClient)
    response = TestClient(app).post(
        "/api/feedback",
        json={"repository": "owner/tool", "classification": "interested"},
    )
    assert response.status_code == 200
    assert Storage(tmp_path).load_interested_repositories() == [Repository("owner/tool", owner="owner")]


def test_empty_bulk_star_action_does_not_contact_github(tmp_path, monkeypatch) -> None:
    """
    an empty confirmed batch performs no GitHub mutation
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))

    class ForbiddenGitHubClient:
        """GitHub client that fails if constructed for an empty batch"""

        def __init__(self) -> None:
            """
            reject unexpected GitHub client construction
            :returns: nothing
            """
            raise AssertionError("An empty batch must not construct a GitHub client")

    monkeypatch.setattr("repo_radar.web.GitHubClient", ForbiddenGitHubClient)
    response = TestClient(app).post("/api/interested/star-all")
    assert response.json() == {"starred_count": 0}


def test_web_sync_removes_externally_starred_saved_repository(tmp_path, monkeypatch) -> None:
    """
    web synchronization reconciles saved repositories starred outside the app
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    storage = Storage(tmp_path)
    storage.save_interested_repositories([Repository("owner/tool"), Repository("owner/keep")])
    storage.save_feedback({"owner/tool": "interested", "owner/keep": "interested"})

    class FakeGitHubClient:
        """mock GitHub client for web synchronization"""

        def get_authenticated_user(self) -> str:
            """
            return the mocked authenticated user
            :returns: authenticated user login
            """
            return "example"

        def get_starred_repositories(self) -> list[Repository]:
            """
            return a repository starred outside Repo Radar
            :returns: synchronized starred repositories
            """
            return [Repository("owner/tool")]

    monkeypatch.setattr("repo_radar.web.GitHubClient", FakeGitHubClient)
    response = TestClient(app).post("/api/sync")
    assert response.json()["reconciled_count"] == 1
    assert storage.load_interested_repositories() == [Repository("owner/keep")]
    assert storage.load_feedback() == {"owner/keep": "interested", "owner/tool": "starred"}


def test_recommendation_search_honors_an_explicit_non_python_language(tmp_path, monkeypatch) -> None:
    """
    a JavaScript search returns JavaScript repositories despite an all Python profile
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    pool = [
        Repository("py/runner", "python task runner", "Python", ["cli"], 900, owner="py"),
        Repository("js/jest", "javascript testing framework", "JavaScript", ["testing"], 800, owner="js"),
        Repository("decoy/js-helpers", "javascript helpers in python", "Python", [], 950, owner="decoy"),
    ]
    Storage(tmp_path).save_repositories([Repository("py/runner", language="Python", topics=["cli"], stars=900)])
    queries: list[str] = []

    class FakeGitHubClient:
        """mock GitHub client honoring the language qualifier"""

        def get_authenticated_user(self) -> str:
            """
            return the mocked authenticated user
            :returns: authenticated user login
            """
            return "me"

        def search_repositories(self, query: str, limit: int) -> list[Repository]:
            """
            return pool repositories matching the requested language qualifier
            :param query: generated search query
            :param limit: requested result limit
            :returns: fake search results
            """
            queries.append(query)
            requested = next(
                (part.split(":", maxsplit=1)[1].strip('"').casefold() for part in query.split() if "language:" in part),
                "",
            )
            return [item for item in pool if not requested or (item.language or "").casefold() == requested]

    monkeypatch.setattr("repo_radar.web.GitHubClient", FakeGitHubClient)
    client = TestClient(app)
    javascript = client.get("/api/recommendations", params={"search": "javascript testing"}).json()
    default = client.get("/api/recommendations", params={"search": "task runner"}).json()
    names = [item["full_name"] for item in javascript["recommendations"]]
    assert names == ["js/jest"]
    assert {item["language"] for item in javascript["recommendations"]} == {"JavaScript"}
    assert any("language:JavaScript" in query for query in queries)
    assert {item["language"] for item in default["recommendations"]} == {"Python"}


def test_recommendation_search_defaults_to_python_for_every_language_free_query(tmp_path, monkeypatch) -> None:
    """
    an omitted, empty, or purely topical search constrains candidates to Python
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    pool = [
        Repository("py/runner", "python task runner", "Python", ["cli"], 900, owner="py"),
        Repository("rs/ripgrep", "rust cli search", "Rust", ["cli"], 800, owner="rs"),
        Repository("js/jest", "javascript testing framework", "JavaScript", ["testing"], 700, owner="js"),
    ]
    # a Rust dominated profile must not be able to select the language
    Storage(tmp_path).save_repositories([Repository("rs/ripgrep", language="Rust", topics=["cli"], stars=800)])
    queries: list[str] = []

    class FakeGitHubClient:
        """mock GitHub client honoring the language qualifier"""

        def get_authenticated_user(self) -> str:
            """
            return the mocked authenticated user
            :returns: authenticated user login
            """
            return "me"

        def search_repositories(self, query: str, limit: int) -> list[Repository]:
            """
            return pool repositories matching the requested language qualifier
            :param query: generated search query
            :param limit: requested result limit
            :returns: fake search results
            """
            queries.append(query)
            requested = next(
                (part.split(":", maxsplit=1)[1].strip('"').casefold() for part in query.split() if "language:" in part),
                "",
            )
            return [item for item in pool if not requested or (item.language or "").casefold() == requested]

    monkeypatch.setattr("repo_radar.web.GitHubClient", FakeGitHubClient)
    client = TestClient(app)
    for parameters in ({}, {"search": ""}, {"search": "   "}, {"search": "task runner"}):
        results = client.get("/api/recommendations", params=parameters).json()["recommendations"]
        assert [item["full_name"] for item in results] == ["py/runner"], parameters
    assert queries
    assert all("language:Python" in query for query in queries)


def test_starred_saved_influence_and_feedback_history_apis(tmp_path, monkeypatch) -> None:
    """
    library and feedback APIs expose local state and support undo
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    storage = Storage(tmp_path)
    storage.save_repositories([Repository("starred/tool", language="Python")])
    storage.save_interested_repositories(
        [
            Repository("saved/small", language="Go"),
            Repository("saved/strong", "Rust terminal automation", "Rust", ["terminal", "cli"]),
        ]
    )
    storage.save_feedback({"blocked/tool": "blocked", "dismissed/tool": "not interested"})
    client = TestClient(app)
    starred = client.get("/api/starred").json()["repositories"]
    interested = client.get("/api/interested").json()["repositories"]
    feedback = client.get("/api/feedback").json()["records"]
    removed = client.delete("/api/feedback/blocked/tool")
    assert starred[0]["full_name"] == "starred/tool"
    assert interested[0]["full_name"] == "saved/strong"
    assert interested[0]["preference_weight"] == 0.7
    assert interested[0]["signal_count"] > interested[1]["signal_count"]
    assert feedback == [
        {"repository": "blocked/tool", "classification": "blocked"},
        {"repository": "dismissed/tool", "classification": "not interested"},
    ]
    assert removed.json() == {"repository": "blocked/tool", "classification": "blocked", "removed": True}
    assert storage.load_feedback() == {"dismissed/tool": "not interested"}
