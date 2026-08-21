import io
import json
import urllib.error
from email.message import Message
from urllib.parse import parse_qs, urlparse

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
        self.status = 200

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

    def fake_urlopen(request, timeout):
        """
        return the mocked GitHub response
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: fake API response
        """
        return FakeResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    repositories = GitHubClient("test-token").get_starred_repositories()
    assert repositories[0].full_name == "a/b"


def test_starred_request_uses_endpoint_authentication_and_pagination(monkeypatch) -> None:
    """
    starred requests use the required endpoint headers and all response pages
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    requests = []
    first_page = [
        {
            "full_name": f"owner/repository-{index}",
            "owner": {"login": "owner"},
            "topics": ["python"],
            "stargazers_count": index,
        }
        for index in range(100)
    ]
    second_page = [
        {
            "full_name": "owner/repository-100",
            "owner": {"login": "owner"},
            "topics": [],
            "stargazers_count": 100,
        }
    ]

    def fake_urlopen(request, timeout):
        """
        return a response selected by the requested page
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: fake paginated API response
        """
        requests.append(request)
        page = parse_qs(urlparse(request.full_url).query)["page"][0]
        return FakeResponse(first_page if page == "1" else second_page)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    repositories = GitHubClient("test-token").get_starred_repositories()

    assert len(repositories) == 101
    assert urlparse(requests[0].full_url).path == "/user/starred"
    assert parse_qs(urlparse(requests[0].full_url).query) == {"per_page": ["100"], "page": ["1"]}
    assert requests[0].get_header("Authorization") == "Bearer test-token"
    assert requests[0].get_header("Accept") == "application/vnd.github+json"
    assert requests[0].get_header("X-github-api-version") == "2026-03-10"
    assert repositories[-1].full_name == "owner/repository-100"
    assert repositories[-1].stars == 100


def test_star_permission_failure_has_actionable_message(monkeypatch) -> None:
    """
    star permission failures explain the required token permissions
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """

    def fake_urlopen(request, timeout):
        """
        raise a mocked GitHub permission failure
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: no response
        """
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"denied"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        GitHubClient("test-token").star_repository("owner/repository")
    except RuntimeError as error:
        assert "Starring write" in str(error)
        assert "Metadata read" in str(error)
    else:
        raise AssertionError("Expected a GitHub permission error")


def test_star_repository_uses_authenticated_put_request(monkeypatch) -> None:
    """
    starring uses the authenticated GitHub user starred endpoint
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    requests = []

    def fake_urlopen(request, timeout):
        """
        capture the outgoing star request
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: fake empty API response
        """
        requests.append(request)
        return FakeResponse(None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    GitHubClient("test-token").star_repository("owner/repository")
    assert requests[0].method == "PUT"
    assert urlparse(requests[0].full_url).path == "/user/starred/owner/repository"
    assert requests[0].get_header("Authorization") == "Bearer test-token"
    assert requests[0].get_header("Content-length") == "0"
    assert requests[0].get_header("X-github-api-version") == "2026-03-10"
