import json

from repo_radar.heldout_snapshot import build_snapshot, main
from repo_radar.models import ImportedProfile, ImportedRepository, Repository
from repo_radar.storage import Storage


class FakeClient:
    """stand in for the authenticated GitHub client during snapshot generation"""

    def __init__(self, starred: list[Repository], discovered: list[Repository], owner: str = "profile-user") -> None:
        """
        record the responses the fake client should return
        :param starred: repositories the user has starred
        :param discovered: repositories returned by every search
        :param owner: authenticated login
        :returns: nothing
        """
        self.starred = starred
        self.discovered = discovered
        self.owner = owner
        self.queries: list[str] = []

    def get_authenticated_user(self) -> str:
        """
        return the authenticated login
        :returns: GitHub login
        """
        return self.owner

    def get_starred_repositories(self) -> list[Repository]:
        """
        return the recorded starred repositories
        :returns: starred repositories
        """
        return list(self.starred)

    def search_repositories(self, query: str, limit: int = 30) -> list[Repository]:
        """
        return the recorded discovery results for any query
        :param query: search query
        :param limit: unused result limit
        :returns: discovered repositories
        """
        self.queries.append(query)
        return list(self.discovered)


def _repository(full_name: str, private: bool = False, **overrides: object) -> Repository:
    """
    build a repository for snapshot generation tests
    :param full_name: repository identity
    :param private: whether the repository is private
    :param overrides: additional repository fields
    :returns: repository instance
    """
    owner = full_name.split("/")[0]
    defaults: dict[str, object] = {
        "description": "a repository used in snapshot tests",
        "language": "Python",
        "topics": ["cli"],
        "stars": 50,
        "owner": owner,
        "pushed_at": "2026-08-01T00:00:00Z",
        "private": private,
    }
    defaults.update(overrides)
    return Repository(full_name=full_name, **defaults)


def test_private_starred_repositories_are_excluded_with_a_reason(tmp_path) -> None:
    """
    a private star never reaches the committed snapshot and is reported as excluded
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    client = FakeClient(
        starred=[_repository("owner/public"), _repository("secret/internal", private=True)],
        discovered=[_repository("distractor/repo")],
    )
    snapshot = build_snapshot(client, Storage(tmp_path), "2026-08-25")
    names = [entry["full_name"] for entry in snapshot["stars"]]
    assert names == ["owner/public"]
    assert snapshot["coverage"]["private_stars_excluded"] == 1
    assert snapshot["coverage"]["excluded_stars"][0]["repository"] == "secret/internal"


def test_private_candidates_are_excluded_from_the_pool(tmp_path) -> None:
    """
    a private search result never reaches the committed snapshot
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    client = FakeClient(
        starred=[_repository("owner/public")],
        discovered=[_repository("distractor/repo"), _repository("secret/candidate", private=True)],
    )
    snapshot = build_snapshot(client, Storage(tmp_path), "2026-08-25")
    assert [entry["full_name"] for entry in snapshot["candidates"]] == ["distractor/repo"]
    assert snapshot["coverage"]["private_candidates_excluded"] == 1


def test_the_profile_users_own_repositories_are_not_used_as_distractors(tmp_path) -> None:
    """
    candidates production filtering would drop are not frozen into the pool
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    storage = Storage(tmp_path)
    storage.save_imported_profile(
        ImportedProfile(username="profile-user", repositories=[ImportedRepository(name="mine", language="Python")])
    )
    client = FakeClient(
        starred=[_repository("owner/public")],
        discovered=[_repository("profile-user/mine"), _repository("distractor/repo")],
    )
    snapshot = build_snapshot(client, Storage(tmp_path), "2026-08-25")
    assert [entry["full_name"] for entry in snapshot["candidates"]] == ["distractor/repo"]


def test_the_snapshot_is_ordered_and_reproducible(tmp_path) -> None:
    """
    repeated generation from identical inputs produces an identical snapshot
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    client = FakeClient(
        starred=[_repository("zeta/last"), _repository("alpha/first")],
        discovered=[_repository("zulu/repo"), _repository("bravo/repo")],
    )
    first = build_snapshot(client, Storage(tmp_path), "2026-08-25")
    second = build_snapshot(client, Storage(tmp_path), "2026-08-25")
    assert first == second
    assert [entry["full_name"] for entry in first["stars"]] == ["alpha/first", "zeta/last"]
    assert [entry["full_name"] for entry in first["candidates"]] == ["bravo/repo", "zulu/repo"]


def test_the_snapshot_records_the_production_search_queries(tmp_path) -> None:
    """
    the frozen pool documents the production searches that produced it
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    client = FakeClient(starred=[_repository("owner/public")], discovered=[_repository("distractor/repo")])
    snapshot = build_snapshot(client, Storage(tmp_path), "2026-08-25")
    assert snapshot["search_queries"]
    assert all(isinstance(query, str) for query in snapshot["search_queries"])
    assert snapshot["search_queries"] == client.queries[: len(snapshot["search_queries"])]


def test_snapshot_generation_reports_a_missing_token(tmp_path, monkeypatch, capsys) -> None:
    """
    generation without credentials exits non zero with a readable message
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest capture fixture
    :returns: nothing
    """
    monkeypatch.setattr("repo_radar.github_client.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    output = tmp_path / "snapshot.json"
    assert main(["--data-dir", str(tmp_path), "--output", str(output)]) == 1
    assert "snapshot generation failed" in capsys.readouterr().err
    assert not output.exists()


def test_the_snapshot_file_is_written_as_sorted_json(tmp_path, monkeypatch) -> None:
    """
    the generated file is stable JSON so snapshot diffs stay reviewable
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    client = FakeClient(starred=[_repository("owner/public")], discovered=[_repository("distractor/repo")])
    monkeypatch.setattr("repo_radar.heldout_snapshot.GitHubClient", lambda *args, **kwargs: client)
    output = tmp_path / "nested" / "snapshot.json"
    assert main(["--data-dir", str(tmp_path), "--output", str(output), "--snapshot-date", "2026-08-25"]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["snapshot_date"] == "2026-08-25"
    assert list(payload) == sorted(payload)
