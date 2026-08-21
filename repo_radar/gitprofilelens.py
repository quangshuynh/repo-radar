"""GitProfileLens Markdown fetching parsing and import orchestration"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .models import ImportedProfile, ImportedRepository
from .storage import Storage

SOURCE_URL = "https://gitprofilelens.vercel.app/"


class GitProfileLensError(RuntimeError):
    """safe GitProfileLens import failure"""


def _optional_value(value: str) -> str | None:
    """
    normalize an optional report value
    :param value: raw Markdown field value
    :returns: normalized value or none
    """
    cleaned = value.strip()
    if cleaned.lower() in {"", "none", "never", "not specified", "unavailable", "no description"}:
        return None
    return cleaned


def _boolean_value(value: str) -> bool:
    """
    parse a report boolean value
    :param value: raw Markdown field value
    :returns: parsed boolean
    """
    return value.strip().lower() in {"yes", "true", "1"}


def _integer_value(value: str) -> int:
    """
    parse a report integer with a safe default
    :param value: raw Markdown field value
    :returns: parsed nonnegative integer
    """
    match = re.search(r"\d+", value.replace(",", ""))
    return int(match.group()) if match else 0


def _topics_value(value: str) -> list[str]:
    """
    parse comma separated report topics
    :param value: raw Markdown field value
    :returns: normalized topics
    """
    if value.strip().lower() in {"", "none", "not specified"}:
        return []
    return [topic.strip().lower() for topic in value.split(",") if topic.strip()]


def _repository_from_fields(username: str, fields: dict[str, str], pinned_names: set[str]) -> ImportedRepository:
    """
    normalize labeled Markdown fields into an imported repository
    :param username: report username
    :param fields: repository fields keyed by normalized label
    :param pinned_names: repository names listed as pinned
    :returns: imported repository
    """
    name = _optional_value(fields.get("name", ""))
    if not name:
        raise GitProfileLensError("GitProfileLens report contains a repository without a name")
    pinned_value = fields.get("pinned on profile", "")
    return ImportedRepository(
        name=name,
        description=_optional_value(fields.get("desc", fields.get("description", ""))),
        url=_optional_value(fields.get("url", "")) or f"https://github.com/{username}/{name}",
        pinned=_boolean_value(pinned_value) or name.lower() in pinned_names,
        created_at=_optional_value(fields.get("created", fields.get("created date", ""))),
        updated_at=_optional_value(fields.get("last updated", fields.get("updated date", ""))),
        pushed_at=_optional_value(fields.get("last pushed", fields.get("pushed date", ""))),
        language=_optional_value(fields.get("primary language", fields.get("language", ""))),
        topics=_topics_value(fields.get("topics", "")),
        stars=_integer_value(fields.get("stars", "0")),
        forks=_integer_value(fields.get("forks", "0")),
        archived=_boolean_value(fields.get("archived", "")),
        is_fork=_boolean_value(fields.get("forked repository", fields.get("fork", ""))),
    )


def parse_markdown_report(markdown: str) -> ImportedProfile:
    """
    parse a labeled GitProfileLens Markdown report
    :param markdown: raw GitProfileLens Markdown report
    :returns: structured imported profile
    """
    content = markdown.strip()
    if not content:
        raise GitProfileLensError("GitProfileLens returned an empty report")
    if re.search(r"<!doctype\s+html|<html[\s>]", content, re.IGNORECASE):
        raise GitProfileLensError("GitProfileLens returned HTML instead of a Markdown report")
    username_match = re.search(r"^\s*username\s*:\s*(.+?)\s*$", content, re.IGNORECASE | re.MULTILINE)
    if not username_match:
        raise GitProfileLensError("GitProfileLens report is missing the username field")
    username = username_match.group(1).strip().lstrip("@")
    count_match = re.search(
        r"^\s*public repositories(?: in report)?\s*:\s*(.+?)\s*$",
        content,
        re.IGNORECASE | re.MULTILINE,
    )
    pinned_section = re.search(
        r"^#\s+pinned repositories\s*:\s*$([\s\S]*?)(?=^#\s+repositories\s*:|\Z)",
        content,
        re.IGNORECASE | re.MULTILINE,
    )
    pinned_names = (
        {
            match.group(1).strip().lower()
            for match in re.finditer(r"^\s*-\s+(.+?)\s*$", pinned_section.group(1), re.MULTILINE)
        }
        if pinned_section
        else set()
    )
    repository_blocks = re.split(r"^\s*###\s+repo\s+\d+\s*:\s*$", content, flags=re.IGNORECASE | re.MULTILINE)[1:]
    repositories: list[ImportedRepository] = []
    for block in repository_blocks:
        fields = {
            match.group(1).strip().lower(): match.group(2).strip()
            for match in re.finditer(r"^\s*-\s*([^:]+)\s*:\s*(.*?)\s*$", block, re.MULTILINE)
        }
        repositories.append(_repository_from_fields(username, fields, pinned_names))
    reported_count = _integer_value(count_match.group(1) if count_match else str(len(repositories)))
    if reported_count != len(repositories):
        raise GitProfileLensError("GitProfileLens report repository count does not match parsed repositories")
    return ImportedProfile(
        username=username,
        public_repository_count=reported_count,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source_url=f"{SOURCE_URL}?{urllib.parse.urlencode({'user': username, 'view': 'markdown'})}",
        repositories=repositories,
    )


def fetch_markdown_report(username: str, timeout: int = 30) -> str:
    """
    fetch a GitProfileLens Markdown report
    :param username: GitHub username to import
    :param timeout: network timeout in seconds
    :returns: Markdown report text
    """
    query = urllib.parse.urlencode({"user": username, "view": "markdown"})
    request = urllib.request.Request(
        f"{SOURCE_URL}?{query}",
        headers={"Accept": "text/markdown", "User-Agent": "repo-radar/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            try:
                body = response.read().decode("utf-8")
            except UnicodeDecodeError as error:
                raise GitProfileLensError("GitProfileLens returned unreadable report data") from error
    except urllib.error.HTTPError as error:
        raise GitProfileLensError(f"GitProfileLens request failed with status {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GitProfileLensError("GitProfileLens is unavailable") from error
    if "text/html" in content_type.lower():
        raise GitProfileLensError("GitProfileLens returned HTML instead of a Markdown report")
    return body


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
    report = fetch_markdown_report(cleaned_username)
    profile = parse_markdown_report(report)
    if profile.username.lower() != cleaned_username.lower():
        raise GitProfileLensError("GitProfileLens report username does not match the requested user")
    storage.save_imported_profile(profile)
    return profile
