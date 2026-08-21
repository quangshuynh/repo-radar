import io
import json
import socket
import urllib.error

import pytest

from repo_radar.gitprofilelens import (
    GitProfileLensError,
    fetch_json_report,
    import_profile,
    parse_json_report,
)
from repo_radar.models import ImportedProfile, ImportedRepository
from repo_radar.storage import Storage

REPORT = {
    "username": "example",
    "public_repositories": 2,
    "pinned_repositories": ["pinned-tool"],
    "unknown_future_field": "ignored",
    "repositories": [
        {
            "topics": ["automation", "cli"],
            "name": "pinned-tool",
            "unknown_future_field": "ignored",
            "primary_language": "Python",
            "description": "A useful automation CLI",
            "url": "https://github.com/example/pinned-tool",
            "pinned": True,
            "archived": False,
            "forked": False,
            "stars": 12,
            "forks": 2,
            "pushed_at": "2026-01-02T00:00:00Z",
        },
        {
            "name": "old-fork",
            "description": None,
            "primary_language": None,
            "topics": [],
            "pinned": False,
            "archived": True,
            "forked": True,
        },
    ],
}


class FakeResponse:
    """context managed JSON response"""

    def __init__(self, body, content_type: str = "application/json") -> None:
        """
        initialize a mocked HTTP response
        :param body: JSON serializable response body
        :param content_type: response content type
        :returns: nothing
        """
        self.body = io.BytesIO(json.dumps(body).encode())
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        """
        enter the response context
        :returns: mocked response
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
        return the response body
        :param size: maximum bytes to read
        :returns: response bytes
        """
        return self.body.read(size)


def test_parse_valid_json_report_with_flexible_fields() -> None:
    """
    JSON fields parse with missing optional and unknown fields
    :returns: nothing
    """
    profile = parse_json_report(REPORT)
    pinned, inactive = profile.repositories
    assert profile.username == "example"
    assert profile.public_repository_count == 2
    assert pinned.pinned is True
    assert pinned.language == "Python"
    assert pinned.topics == ["automation", "cli"]
    assert pinned.description == "A useful automation CLI"
    assert pinned.stars == 12
    assert inactive.language is None
    assert inactive.description is None
    assert inactive.topics == []
    assert inactive.archived is True
    assert inactive.is_fork is True


@pytest.mark.parametrize(
    "report",
    [None, [], {}, {"username": "example"}, {"username": "example", "repositories": [None]}],
)
def test_parse_rejects_malformed_json_reports(report) -> None:
    """
    malformed JSON report structures fail explicitly
    :param report: invalid decoded report
    :returns: nothing
    """
    with pytest.raises(GitProfileLensError):
        parse_json_report(report)


def test_fetch_uses_json_endpoint_and_headers(monkeypatch) -> None:
    """
    fetching uses the deployed report endpoint and JSON accept header
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    requests = []

    def fake_urlopen(request, timeout):
        """
        capture the outgoing request and return a JSON report
        :param request: outgoing request
        :param timeout: request timeout
        :returns: mocked JSON response
        """
        requests.append(request)
        return FakeResponse(REPORT)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert fetch_json_report("example") == REPORT
    assert requests[0].full_url == "https://gitprofilelens.vercel.app/api/report?user=example"
    assert requests[0].get_header("Accept") == "application/json"


def test_fetch_handles_non_json_network_http_and_timeout(monkeypatch) -> None:
    """
    fetch failures and unexpected content remain explicit
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """

    def return_html(request, timeout):
        """
        return an unexpected HTML response
        :param request: outgoing request
        :param timeout: request timeout
        :returns: HTML response
        """
        return FakeResponse({}, "text/html")

    def raise_network_error(request, timeout):
        """
        raise a mocked network failure
        :param request: outgoing request
        :param timeout: request timeout
        :returns: no response
        """
        raise urllib.error.URLError("offline")

    def raise_http_error(request, timeout):
        """
        raise a mocked HTTP failure
        :param request: outgoing request
        :param timeout: request timeout
        :returns: no response
        """
        raise urllib.error.HTTPError(request.full_url, 503, "failure", {}, None)

    def raise_timeout(request, timeout):
        """
        raise a mocked timeout
        :param request: outgoing request
        :param timeout: request timeout
        :returns: no response
        """
        raise socket.timeout()

    failures = [return_html, raise_network_error, raise_http_error, raise_timeout]
    for failure in failures:
        monkeypatch.setattr("urllib.request.urlopen", failure)
        with pytest.raises(GitProfileLensError):
            fetch_json_report("example")


def test_failed_import_preserves_previous_profile(tmp_path, monkeypatch) -> None:
    """
    a failed refresh does not replace the last valid imported profile
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    previous = ImportedProfile("example", 1, repositories=[ImportedRepository("existing")])
    storage.save_imported_profile(previous)

    def fail_fetch(username: str, timeout: int = 30):
        """
        raise a mocked network failure
        :param username: requested username
        :param timeout: request timeout
        :returns: no report
        """
        raise GitProfileLensError("offline")

    monkeypatch.setattr("repo_radar.gitprofilelens.fetch_json_report", fail_fetch)
    with pytest.raises(GitProfileLensError):
        import_profile("example", storage)
    assert storage.load_imported_profile() == previous
