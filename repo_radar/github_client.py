"""minimal authenticated GitHub REST API client"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from dotenv import load_dotenv

from .models import Issue, Repository

# The GitHub Search API is limited to 30 authenticated requests per minute, far below the
# 5000 per hour core limit, so issue search must stay grouped and single page.
ISSUE_SEARCH_RESULT_LIMIT = 100


class GitHubError(RuntimeError):
    """user facing GitHub API failure"""


class GitHubClient:
    """perform authenticated requests against the GitHub REST API"""

    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com") -> None:
        """
        initialize the GitHub client
        :param token: GitHub personal access token
        :param base_url: GitHub API base URL
        :returns: nothing
        """
        load_dotenv(override=False)
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.base_url = base_url.rstrip("/")
        if not self.token:
            raise GitHubError("GITHUB_TOKEN is required. Set it in your environment before running this command.")

    def _request(
        self,
        path: str,
        parameters: dict[str, str | int] | None = None,
        method: str = "GET",
    ) -> tuple[Any, dict[str, str]]:
        """
        issue one authenticated API request
        :param path: API path
        :param parameters: query string parameters
        :param method: HTTP request method
        :returns: decoded response and headers
        """
        query = urllib.parse.urlencode(parameters or {})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "repo-radar/0.1",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if method == "PUT":
            headers["Content-Length"] = "0"
        request = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = getattr(response, "status", 200)
                if not 200 <= status < 300:
                    raise GitHubError(f"GitHub API request failed with status {status}")
                body = response.read()
                if not body:
                    data = None
                else:
                    try:
                        data = json.loads(body)
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise GitHubError("GitHub API returned an invalid JSON response") from error
                return data, dict(response.headers.items())
        except urllib.error.HTTPError as error:
            remaining = error.headers.get("X-RateLimit-Remaining")
            reset = error.headers.get("X-RateLimit-Reset")
            detail = error.read().decode("utf-8", errors="replace")
            if error.code in (403, 429) and remaining == "0":
                raise GitHubError(f"GitHub API rate limit exceeded. Reset timestamp: {reset or 'unknown'}") from error
            if error.code == 403 and method == "PUT" and path.startswith("/user/starred/"):
                raise GitHubError(
                    "GitHub denied the star request. Update the fine-grained token to allow Starring write "
                    "and Metadata read, then restart Repo Radar"
                ) from error
            raise GitHubError(f"GitHub API request failed with status {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GitHubError(f"Could not connect to GitHub: {error}") from error

    def _paginate(self, path: str, parameters: dict[str, str | int] | None = None) -> list[Any]:
        """
        collect all pages from a list endpoint
        :param path: API path
        :param parameters: query string parameters
        :returns: combined API items
        """
        items: list[Any] = []
        page = 1
        while True:
            page_parameters = dict(parameters or {})
            page_parameters.update({"per_page": 100, "page": page})
            data, _ = self._request(path, page_parameters)
            if not isinstance(data, list):
                raise GitHubError(f"Unexpected response from GitHub endpoint {path}")
            items.extend(data)
            if len(data) < 100:
                return items
            page += 1

    def get_authenticated_user(self) -> str:
        """
        fetch the authenticated user login
        :returns: GitHub login
        """
        data, _ = self._request("/user")
        login = data.get("login") if isinstance(data, dict) else None
        if not login:
            raise GitHubError("GitHub did not return an authenticated user login")
        return str(login)

    def get_starred_repositories(self) -> list[Repository]:
        """
        fetch every repository starred by the authenticated user
        :returns: normalized starred repositories
        """
        items = self._paginate("/user/starred")
        if any(not isinstance(item, dict) for item in items):
            raise GitHubError("GitHub returned invalid starred repository data")
        return [Repository.from_github(item) for item in items]

    def search_repositories(self, query: str, limit: int = 30) -> list[Repository]:
        """
        search GitHub repositories
        :param query: GitHub repository search query
        :param limit: maximum candidates to return
        :returns: matching repositories
        """
        data, _ = self._request("/search/repositories", {"q": query, "sort": "updated", "per_page": min(limit, 100)})
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise GitHubError("Unexpected response from GitHub repository search")
        return [Repository.from_github(item) for item in data["items"][:limit]]

    def search_issues(self, query: str, limit: int = 50) -> list[Issue]:
        """
        search GitHub issues and keep only usable open issues
        :param query: GitHub issue search query
        :param limit: maximum candidates to return
        :returns: normalized open issues
        """
        bounded = max(1, min(limit, ISSUE_SEARCH_RESULT_LIMIT))
        # advanced_search selects GitHub's current issue search syntax, which is what the
        # grouped `(repo:a/b OR repo:c/d)` scope built by the contribution pipeline requires
        data, _ = self._request(
            "/search/issues",
            {"q": query, "sort": "updated", "order": "desc", "per_page": bounded, "advanced_search": "true"},
        )
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise GitHubError("Unexpected response from GitHub issue search")
        items = data["items"][:bounded]
        if any(not isinstance(item, dict) for item in items):
            raise GitHubError("GitHub returned invalid issue search data")
        # a single unusable row must not discard an otherwise good batch, so individual
        # pull requests, closed issues, and identity-less rows are dropped rather than raised
        issues = [Issue.from_github(item) for item in items]
        return [
            issue for issue in issues if issue.is_identifiable() and not issue.is_pull_request and issue.state == "open"
        ]

    def star_repository(self, repository: str) -> None:
        """
        star one repository for the authenticated user
        :param repository: repository in owner and name form
        :returns: nothing
        """
        parts = repository.strip().split("/")
        if len(parts) != 2 or not all(parts):
            raise GitHubError("Repository must use the owner/name format")
        owner, name = (urllib.parse.quote(part, safe="") for part in parts)
        self._request(f"/user/starred/{owner}/{name}", method="PUT")
