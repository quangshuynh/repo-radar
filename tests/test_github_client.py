import io
import json
from email.message import Message

from repo_radar.github_client import GitHubClient


class FakeResponse:
    """context managed JSON API response"""

    def __init__(self, payload) -> None:
        """
        initialize a fake response
        :param payload: JSON serializable response payload
        :returns: nothing
        """
        self.stream = io.BytesIO(json.dumps(payload).encode())
        self.headers = Message()

    def __enter__(self):
        """
        enter the response context
        :returns: fake response
        """
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        """
        exit the response context
        :param exception_type: active exception type
        :param exception: active exception
        :param traceback: active traceback
        :returns: nothing
        """
        return None

    def read(self, size: int = -1) -> bytes:
        """
        return serialized response content
        :param size: maximum bytes to read
        :returns: JSON response bytes
        """
        return self.stream.read(size)


def test_starred_api_interaction_is_mocked(monkeypatch) -> None:
    """
    starred repositories are normalized without a network request
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    payload = [{"full_name": "a/b", "owner": {"login": "a"}, "topics": [], "stargazers_count": 2}]
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse(payload))
    repositories = GitHubClient("test-token").get_starred_repositories()
    assert repositories[0].full_name == "a/b"
