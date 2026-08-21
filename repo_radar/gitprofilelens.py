"""GitProfileLens JSON fetching parsing and import orchestration"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .models import ImportedProfile, ImportedRepository
from .storage import Storage

SOURCE_URL = "https://gitprofilelens.vercel.app/api/report"


class GitProfileLensError(RuntimeError):
    """safe GitProfileLens import failure"""


def _optional_string(value: Any) -> str | None:
    """
    normalize an optional JSON string
    :param value: raw report value
    :returns: normalized string or none
    """
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _integer_value(value: Any) -> int:
    """
    normalize a nonnegative JSON integer
    :param value: raw report value
    :returns: normalized integer
    """
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _topic_values(value: Any) -> list[str]:
    """
    normalize JSON repository topics
    :param value: raw topics value
    :returns: normalized topic strings
    """
    if not isinstance(value, list):
        return []
    return [str(topic).strip().lower() for topic in value if str(topic).strip()]


def _parse_repository(username: str, value: dict[str, Any], pinned_names: set[str]) -> ImportedRepository:
    """
    normalize one GitProfileLens JSON repository
    :param username: report username
    :param value: repository JSON object
    :param pinned_names: repository names listed as pinned
    :returns: imported repository
    """
    name = _optional_string(value.get("name"))
    if not name:
        raise GitProfileLensError("GitProfileLens report contains a repository without a name")
    return ImportedRepository(
        name=name,
        description=_optional_string(value.get("description")),
        url=_optional_string(value.get("url")) or f"https://github.com/{username}/{name}",
        pinned=bool(value.get("pinned")) or name.lower() in pinned_names,
        created_at=_optional_string(value.get("created_at")),
        updated_at=_optional_string(value.get("updated_at")),
        pushed_at=_optional_string(value.get("pushed_at")),
        language=_optional_string(value.get("primary_language")),
        topics=_topic_values(value.get("topics")),
        stars=_integer_value(value.get("stars")),
        forks=_integer_value(value.get("forks")),
        archived=bool(value.get("archived")),
        is_fork=bool(value.get("forked")),
    )


def parse_json_report(value: Any) -> ImportedProfile:
    """
    parse a structured GitProfileLens JSON report
    :param value: decoded JSON report
    :returns: structured imported profile
    """
    if not isinstance(value, dict):
        raise GitProfileLensError("GitProfileLens returned a malformed JSON report")
    username = _optional_string(value.get("username"))
    repositories_value = value.get("repositories")
    if not username:
        raise GitProfileLensError("GitProfileLens report is missing the username field")
    if not isinstance(repositories_value, list):
        raise GitProfileLensError("GitProfileLens report is missing the repositories list")
    if any(not isinstance(repository, dict) for repository in repositories_value):
        raise GitProfileLensError("GitProfileLens report contains malformed repository data")
    pinned_value = value.get("pinned_repositories", [])
    pinned_names = (
        {str(name).strip().lower() for name in pinned_value if str(name).strip()}
        if isinstance(pinned_value, list)
        else set()
    )
    repositories = [_parse_repository(username, repository, pinned_names) for repository in repositories_value]
    reported_count = _integer_value(value.get("public_repositories"))
    if reported_count != len(repositories):
        raise GitProfileLensError("GitProfileLens report repository count does not match parsed repositories")
    query = urllib.parse.urlencode({"user": username})
    return ImportedProfile(
        username=username,
        public_repository_count=reported_count,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source_url=f"{SOURCE_URL}?{query}",
        repositories=repositories,
    )


def fetch_json_report(username: str, timeout: int = 30) -> Any:
    """
    fetch a GitProfileLens JSON report
    :param username: GitHub username to import
    :param timeout: network timeout in seconds
    :returns: decoded JSON report
    """
    query = urllib.parse.urlencode({"user": username})
    request = urllib.request.Request(
        f"{SOURCE_URL}?{query}",
        headers={"Accept": "application/json", "User-Agent": "repo-radar/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "application/json" not in content_type.lower():
                raise GitProfileLensError("GitProfileLens returned a non-JSON response")
            try:
                return json.load(response)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise GitProfileLensError("GitProfileLens returned invalid JSON") from error
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise GitProfileLensError("GitProfileLens could not find that GitHub user") from error
        if error.code in {403, 429}:
            raise GitProfileLensError("GitProfileLens or GitHub rate limit was reached") from error
        raise GitProfileLensError(f"GitProfileLens request failed with status {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GitProfileLensError("GitProfileLens is unavailable") from error


def import_profile(username: str, storage: Storage) -> ImportedProfile:
    """
    fetch parse and persist one GitProfileLens profile
    :param username: GitHub username to import
    :param storage: local storage manager
    :returns: saved imported profile
    """
    cleaned_username = username.strip().lstrip("@")
    if not cleaned_username:
        raise GitProfileLensError("A GitHub username is required")
    profile = parse_json_report(fetch_json_report(cleaned_username))
    if profile.username.lower() != cleaned_username.lower():
        raise GitProfileLensError("GitProfileLens report username does not match the requested user")
    storage.save_imported_profile(profile)
    return profile
