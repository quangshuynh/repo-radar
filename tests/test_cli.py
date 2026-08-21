from repo_radar.cli import run_import_profile, run_init, run_profile, run_recommend
from repo_radar.models import ImportedProfile, ImportedRepository, Repository, SeedPreferences
from repo_radar.storage import Storage


class ColdStartRecommendationClient:
    """GitHub client returning cold-start discovery candidates"""

    def __init__(self) -> None:
        """
        initialize search query tracking
        :returns: nothing
        """
        self.queries: list[str] = []

    def get_authenticated_user(self) -> str:
        """
        return the mocked authenticated login
        :returns: authenticated login
        """
        return "empty-user"

    def search_repositories(self, query: str, limit: int) -> list[Repository]:
        """
        return candidates for each generated seed query
        :param query: generated repository query
        :param limit: requested result limit
        :returns: mocked repository candidates
        """
        self.queries.append(query)
        return [
            Repository(
                "useful/tool",
                "automation cli",
                "Python",
                ["automation"],
                50,
                owner="useful",
                url="https://github.com/useful/tool",
            ),
            Repository("empty-user/owned", language="Python", owner="empty-user"),
            Repository("old/archived", language="Python", archived=True, owner="old"),
            Repository("blocked/repo", language="Python", owner="blocked"),
        ]


def test_profile_succeeds_with_no_starred_repositories(tmp_path, capsys) -> None:
    """
    an empty starred cache produces a readable empty profile
    :param tmp_path: pytest temporary directory
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)

    assert run_profile(storage) == 0
    assert storage.load_profile() is not None
    assert "no signals yet" in capsys.readouterr().out


def test_recommend_succeeds_without_preference_signals(tmp_path, capsys) -> None:
    """
    empty stars and seeds produce a helpful recommendation message
    :param tmp_path: pytest temporary directory
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    assert run_recommend(storage, 10) == 0
    assert "No preference signals are available yet" in capsys.readouterr().out


def test_init_replaces_and_persists_seed_preferences(tmp_path) -> None:
    """
    repeated setup replaces existing locally persisted seed preferences
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    storage = Storage(tmp_path)
    storage.save_seed_preferences(SeedPreferences(["Go"], ["systems"], ["networking"]))
    answers = iter(["Python, TypeScript, python", "Automation, backend", "CLI, APIs"])

    def fake_input(prompt: str) -> str:
        """
        return the next setup response
        :param prompt: interactive input prompt
        :returns: next mocked answer
        """
        return next(answers)

    assert run_init(storage, fake_input) == 0
    assert storage.load_seed_preferences() == SeedPreferences(
        ["Python", "TypeScript"], ["automation", "backend"], ["cli", "apis"]
    )


def test_recommend_uses_only_manual_preferences_and_filters_candidates(tmp_path, monkeypatch, capsys) -> None:
    """
    seed-only discovery uses existing ranking and filtering behavior
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    storage.save_seed_preferences(SeedPreferences(["Python"], ["automation"], ["cli"]))
    storage.save_feedback({"blocked/repo": "blocked"})
    client = ColdStartRecommendationClient()

    def make_client() -> ColdStartRecommendationClient:
        """
        return the shared mocked GitHub client
        :returns: mocked GitHub client
        """
        return client

    monkeypatch.setattr("repo_radar.cli.GitHubClient", make_client)
    assert run_recommend(storage, 10) == 0
    output = capsys.readouterr().out
    assert client.queries
    assert "useful/tool" in output
    assert "empty-user/owned" not in output
    assert "old/archived" not in output
    assert "blocked/repo" not in output


def test_cli_import_profile_uses_shared_importer(tmp_path, monkeypatch, capsys) -> None:
    """
    the CLI imports and summarizes a GitProfileLens profile
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)

    def fake_import(username: str, target_storage: Storage) -> ImportedProfile:
        """
        save and return a mocked imported profile
        :param username: requested username
        :param target_storage: local storage manager
        :returns: mocked imported profile
        """
        profile = ImportedProfile(
            username,
            2,
            repositories=[ImportedRepository("one", pinned=True), ImportedRepository("two")],
        )
        target_storage.save_imported_profile(profile)
        return profile

    monkeypatch.setattr("repo_radar.cli.import_profile", fake_import)
    assert run_import_profile(storage, "example") == 0
    assert storage.load_imported_profile() is not None
    assert "Imported 2 public repositories" in capsys.readouterr().out
