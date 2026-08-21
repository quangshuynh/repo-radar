from repo_radar.cli import run_profile, run_recommend
from repo_radar.models import Repository
from repo_radar.storage import Storage


class EmptyRecommendationClient:
    """authenticated client used for an empty recommendation profile"""

    def get_authenticated_user(self) -> str:
        """
        return the mocked authenticated login
        :returns: authenticated login
        """
        return "empty-user"

    def search_repositories(self, query: str, limit: int) -> list[Repository]:
        """
        return no search results
        :param query: generated repository query
        :param limit: requested result limit
        :returns: empty repository list
        """
        return []


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


def test_recommend_succeeds_with_no_starred_repositories(tmp_path, monkeypatch, capsys) -> None:
    """
    an empty starred cache produces an empty recommendation result
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    monkeypatch.setattr("repo_radar.cli.GitHubClient", EmptyRecommendationClient)

    assert run_recommend(storage, 10) == 0
    assert "No eligible recommendations found" in capsys.readouterr().out
