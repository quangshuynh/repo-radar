from repo_radar.cli import run_sync
from repo_radar.models import Repository
from repo_radar.storage import Storage


class EmptyStarredClient:
    """authenticated client returning an unexpected empty starred list"""

    def get_authenticated_user(self) -> str:
        """
        return the mocked authenticated login
        :returns: authenticated login
        """
        return "expected-user"

    def get_starred_repositories(self) -> list[Repository]:
        """
        return an empty starred repository response
        :returns: empty repository list
        """
        return []


class SuccessfulStarredClient:
    """authenticated client returning one starred repository"""

    def get_authenticated_user(self) -> str:
        """
        return the mocked authenticated login
        :returns: authenticated login
        """
        return "expected-user"

    def get_starred_repositories(self) -> list[Repository]:
        """
        return a successful starred repository response
        :returns: one repository
        """
        return [Repository("owner/new", owner="owner")]


def test_sync_persists_valid_empty_starred_response(tmp_path, monkeypatch, capsys) -> None:
    """
    an account with no stars produces a valid empty cache
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    storage.save_repositories([Repository("owner/existing", owner="owner")])
    monkeypatch.setattr("repo_radar.cli.GitHubClient", EmptyStarredClient)

    assert run_sync(storage) == 0
    assert storage.load_repositories() == []
    assert "Cached 0 starred repositories for expected-user" in capsys.readouterr().out


def test_sync_persists_successful_starred_response(tmp_path, monkeypatch, capsys) -> None:
    """
    a successful response replaces the cache with converted repositories
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    monkeypatch.setattr("repo_radar.cli.GitHubClient", SuccessfulStarredClient)

    assert run_sync(storage) == 0
    assert [repository.full_name for repository in storage.load_repositories()] == ["owner/new"]
    assert "Cached 1 starred repositories for expected-user" in capsys.readouterr().out
