import io
import json
import urllib.error
from email.message import Message
from urllib.parse import parse_qs, urlparse

from repo_radar.github_client import GitHubClient, GitHubError


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


def _issue_search_client(monkeypatch, payload, requests=None):
    """
    build a GitHub client whose issue search returns a prepared payload
    :param monkeypatch: pytest monkeypatch fixture
    :param payload: mocked issue search response payload
    :param requests: optional list collecting outgoing requests
    :returns: GitHub client using the mocked transport
    """

    def fake_urlopen(request, timeout):
        """
        return the mocked issue search response
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: fake API response
        """
        if requests is not None:
            requests.append(request)
        return FakeResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return GitHubClient("test-token")


def _issue_item(**overrides):
    """
    build a GitHub issue search item with useful defaults
    :param overrides: issue field overrides
    :returns: mocked GitHub issue search item
    """
    item = {
        "repository_url": "https://api.github.com/repos/owner/repository",
        "number": 12,
        "title": "Fix the retry handler",
        "html_url": "https://github.com/owner/repository/issues/12",
        "body": "It fails after three attempts.",
        "labels": [{"name": "Good First Issue"}, {"name": "good-first-issue"}, {"name": "bug"}],
        "assignees": [],
        "comments": 3,
        "created_at": "2025-11-01T00:00:00Z",
        "updated_at": "2025-12-20T00:00:00Z",
        "state": "open",
    }
    item.update(overrides)
    return item


def test_issue_search_normalizes_a_valid_result(monkeypatch) -> None:
    """
    a complete issue search item becomes a normalized issue
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    requests = []
    client = _issue_search_client(monkeypatch, {"items": [_issue_item()]}, requests)
    issues = client.search_issues("is:issue is:open (repo:owner/repository)", 40)
    query = parse_qs(urlparse(requests[0].full_url).query)

    assert urlparse(requests[0].full_url).path == "/search/issues"
    assert query["q"] == ["is:issue is:open (repo:owner/repository)"]
    assert query["advanced_search"] == ["true"]
    assert query["per_page"] == ["40"]
    assert len(issues) == 1
    assert issues[0].repository == "owner/repository"
    assert issues[0].number == 12
    assert issues[0].labels == ["good first issue", "good-first-issue", "bug"]
    assert issues[0].assignee_count == 0
    assert issues[0].comments == 3
    assert not issues[0].is_pull_request


def test_issue_search_excludes_pull_requests_and_closed_issues(monkeypatch) -> None:
    """
    pull requests and closed issues never reach the contribution pipeline
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    payload = {
        "items": [
            _issue_item(number=1, pull_request={"url": "https://api.github.com/repos/owner/repository/pulls/1"}),
            _issue_item(number=2, state="closed"),
            _issue_item(number=3),
        ]
    }
    issues = _issue_search_client(monkeypatch, payload).search_issues("query")
    assert [issue.number for issue in issues] == [3]


def test_issue_search_drops_results_without_a_usable_identity(monkeypatch) -> None:
    """
    partial rows without a repository, number, or title are dropped instead of guessed
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    payload = {
        "items": [
            _issue_item(number=1, repository_url=None, html_url=""),
            _issue_item(number=0),
            _issue_item(number=3, title="   "),
            _issue_item(number=4),
        ]
    }
    issues = _issue_search_client(monkeypatch, payload).search_issues("query")
    assert [issue.number for issue in issues] == [4]


def test_issue_search_tolerates_missing_optional_fields(monkeypatch) -> None:
    """
    a minimal issue payload normalizes without raising
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    minimal = {
        "html_url": "https://github.com/owner/repository/issues/7",
        "number": 7,
        "title": "Document the retry policy",
    }
    issues = _issue_search_client(monkeypatch, {"items": [minimal]}).search_issues("query")
    assert issues[0].repository == "owner/repository"
    assert issues[0].labels == []
    assert issues[0].body is None
    assert issues[0].comments == 0
    assert issues[0].state == "open"
    assert issues[0].updated_at is None


def test_issue_search_normalizes_labels_and_assignees(monkeypatch) -> None:
    """
    string labels, blank labels, and repeated assignees normalize predictably
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    item = _issue_item(
        labels=["Help Wanted", {"name": ""}, {"name": "help wanted"}, None],
        assignees=[{"login": "maintainer"}, {"login": "maintainer"}, {}],
        assignee={"login": "reviewer"},
    )
    issues = _issue_search_client(monkeypatch, {"items": [item]}).search_issues("query")
    assert issues[0].labels == ["help wanted"]
    assert issues[0].assignee_count == 2


def test_malformed_issue_search_response_fails_safely(monkeypatch) -> None:
    """
    an unexpected issue search shape raises instead of returning partial nonsense
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    client = _issue_search_client(monkeypatch, {"items": "not-a-list"})
    try:
        client.search_issues("query")
    except GitHubError as error:
        assert "Unexpected response from GitHub issue search" in str(error)
    else:
        raise AssertionError("Expected malformed GitHub issue data to fail")

    invalid = _issue_search_client(monkeypatch, {"items": ["not-an-issue"]})
    try:
        invalid.search_issues("query")
    except GitHubError as error:
        assert "invalid issue search data" in str(error)
    else:
        raise AssertionError("Expected invalid GitHub issue items to fail")


def test_malformed_starred_response_fails_safely(monkeypatch) -> None:
    """
    malformed starred items raise an explicit error without any mutation request
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    requests = []

    def fake_urlopen(request, timeout):
        """
        return malformed starred repository data
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: fake malformed API response
        """
        requests.append(request)
        return FakeResponse(["not-a-repository"])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        GitHubClient("test-token").get_starred_repositories()
    except GitHubError as error:
        assert "invalid starred repository data" in str(error)
    else:
        raise AssertionError("Expected malformed GitHub data to fail")
    assert [request.method for request in requests] == ["GET"]


def test_repository_lookup_normalizes_metadata_from_the_core_api(monkeypatch) -> None:
    """
    hydration reads the core repository endpoint and returns ranking ready metadata
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    requests = []
    payload = {
        "full_name": "owner/repository",
        "owner": {"login": "owner"},
        "description": "backend service",
        "language": "Python",
        "topics": ["backend", "api"],
        "stargazers_count": 900,
        "forks_count": 40,
        "pushed_at": "2026-01-01T00:00:00Z",
    }

    def fake_urlopen(request, timeout):
        """
        return the mocked repository response
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: fake API response
        """
        requests.append(request)
        return FakeResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    repository = GitHubClient("test-token").get_repository("owner/repository")
    assert urlparse(requests[0].full_url).path == "/repos/owner/repository"
    assert requests[0].method == "GET"
    assert repository.full_name == "owner/repository"
    assert repository.language == "Python"
    assert repository.topics == ["backend", "api"]
    assert repository.stars == 900


def test_repository_lookup_rejects_a_malformed_name_without_a_request(monkeypatch) -> None:
    """
    an unusable repository name fails before any GitHub traffic
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    requests = []

    def fake_urlopen(request, timeout):
        """
        record an unexpected request
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: fake API response
        """
        requests.append(request)
        return FakeResponse({})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        GitHubClient("test-token").get_repository("not-a-full-name")
    except GitHubError as error:
        assert "owner/name" in str(error)
    else:
        raise AssertionError("Expected a malformed repository name to fail")
    assert requests == []


def test_repository_lookup_failures_carry_the_http_status(monkeypatch) -> None:
    """
    hydration can tell a missing repository from a rate limit it must stop on
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """

    def fake_urlopen(request, timeout):
        """
        raise a mocked missing repository failure
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: no response
        """
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", Message(), io.BytesIO(b"missing"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        GitHubClient("test-token").get_repository("gone/project")
    except GitHubError as error:
        assert error.status == 404
    else:
        raise AssertionError("Expected a missing repository to fail")


def test_malformed_repository_response_fails_safely(monkeypatch) -> None:
    """
    an identity-less repository response raises rather than hydrating an empty repository
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """

    def fake_urlopen(request, timeout):
        """
        return an unusable repository payload
        :param request: outgoing URL request
        :param timeout: outgoing request timeout
        :returns: fake API response
        """
        return FakeResponse({"owner": {"login": "owner"}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        GitHubClient("test-token").get_repository("owner/repository")
    except GitHubError as error:
        assert "Unexpected response from GitHub repository lookup" in str(error)
    else:
        raise AssertionError("Expected a malformed repository response to fail")
