import io
import socket
import urllib.error

import pytest

from repo_radar.gitprofilelens import (
    GitProfileLensError,
    fetch_markdown_report,
    import_profile,
    parse_markdown_report,
)
from repo_radar.models import ImportedProfile, ImportedRepository
from repo_radar.storage import Storage

REPORT = """username: example
public repositories in report: 2

# pinned repositories:

- pinned-tool

# repositories:

### repo 2:

- topics: automation, cli
- name: pinned-tool
- unknown future field: ignored
- primary language: Python
- desc: A useful automation CLI
- url: https://github.com/example/pinned-tool
- pinned on profile: Yes
- archived: No
- forked repository: No
- stars: 12
- forks: 2
- last pushed: Jan 2, 2026

### repo 1:

- name: old-fork
- desc: No description
- primary language: Not specified
- topics: None
- pinned on profile: No
- archived: Yes
- forked repository: Yes
"""


class FakeResponse:
    """context managed text response"""

    def __init__(self, body: str, content_type: str = "text/markdown") -> None:
        """
        initialize a mocked HTTP response
        :param body: response body
        :param content_type: response content type
        :returns: nothing
        """
        self.body = io.BytesIO(body.encode())
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

    def read(self) -> bytes:
        """
        return the response body
        :returns: response bytes
        """
        return self.body.read()


def test_parse_valid_report_with_flexible_fields() -> None:
    """
    labeled fields parse regardless of order and unknown fields
    :returns: nothing
    """
    profile = parse_markdown_report(REPORT)
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


@pytest.mark.parametrize("report", ["", "# repositories:\n", "<html>not markdown</html>"])
def test_parse_rejects_empty_malformed_and_html_reports(report: str) -> None:
    """
    unusable reports fail explicitly
    :param report: invalid report content
    :returns: nothing
    """
    with pytest.raises(GitProfileLensError):
        parse_markdown_report(report)


def test_fetch_handles_html_network_http_and_timeout(monkeypatch) -> None:
    """
    fetch failures and unexpected HTML remain explicit
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
        return FakeResponse("<html></html>", "text/html")

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
            fetch_markdown_report("example")


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

    def fail_fetch(username: str, timeout: int = 30) -> str:
        """
        raise a mocked network failure
        :param username: requested username
        :param timeout: request timeout
        :returns: no report
        """
        raise GitProfileLensError("offline")

    monkeypatch.setattr("repo_radar.gitprofilelens.fetch_markdown_report", fail_fetch)
    with pytest.raises(GitProfileLensError):
        import_profile("example", storage)
    assert storage.load_imported_profile() == previous
