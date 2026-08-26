"""capture a frozen snapshot of real contribution candidates for offline evaluation

Run once, evaluate offline forever. This is the only part of the contribution evaluation
that contacts GitHub; `repo_radar.contribution_evaluation` reads the resulting files and
nothing else.

Both scopes are captured through the production sourcing path, so the corpus represents the
product as shipped: repositories the user already saved or starred, *and* repositories the
default discovery workflow found on its own.

Only public repositories are written. Existing human judgments are preserved across a
refresh; new candidates are added explicitly unjudged.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contribution import (
    SCOPE_DISCOVER,
    SCOPE_SAVED_STARRED,
    build_discovery_queries,
    build_issue_queries,
    collect_discovery_candidates,
    collect_issue_candidates,
    exclude_issues,
    hydrate_repositories,
    normalize_candidates,
    select_source_repositories,
)
from .contribution_evaluation import (
    ACTIONABLE_THRESHOLD,
    FIXTURES_PATH,
    JUDGMENT_SCALE,
    JUDGMENTS_PATH,
    issue_identifier,
)
from .github_client import GitHubClient, GitHubError
from .models import Issue, PreferenceProfile, Repository
from .profile import build_profile
from .storage import Storage

# Issue bodies are truncated before freezing. Nothing in issue ranking reads past a few
# hundred characters -- relevance stops at BODY_CHARACTER_LIMIT, friendliness only asks
# whether the body reaches USEFUL_DESCRIPTION_CHARACTERS, and readiness scans for markers
# that appear in an issue template's opening section. This keeps a fixture of real
# user-written text to a reviewable size; the fidelity cost is recorded in the README.
FROZEN_BODY_CHARACTERS = 2000

_ISSUE_FIELDS = (
    "repository",
    "number",
    "title",
    "url",
    "labels",
    "assignee_count",
    "comments",
    "created_at",
    "updated_at",
    "state",
    "is_pull_request",
)


def _frozen_issue(issue: Issue) -> dict[str, Any]:
    """
    serialize one issue with only the fields ranking consumes
    :param issue: captured issue
    :returns: fixture issue entry
    """
    data = {field: getattr(issue, field) for field in _ISSUE_FIELDS}
    body = issue.body or ""
    data["body"] = body[:FROZEN_BODY_CHARACTERS] if body else None
    return data


def _public_repositories(repositories: dict[str, Repository]) -> dict[str, Repository]:
    """
    drop private repositories before anything is written to disk
    :param repositories: hydrated repositories by lowercase full name
    :returns: public repositories only
    """
    return {name: repository for name, repository in repositories.items() if not repository.private}


def capture_scope(
    client: GitHubClient,
    profile: PreferenceProfile,
    storage: Storage,
    scope: str,
    owner: str,
    now: datetime,
) -> tuple[list[str], list[Issue], dict[str, Repository]]:
    """
    collect one scope's real candidates through the production sourcing path
    :param client: authenticated GitHub client
    :param profile: preference profile built from public evidence
    :param storage: local storage manager
    :param scope: contribution scope to capture
    :param owner: authenticated GitHub login
    :param now: reference time for deterministic selection
    :returns: the queries issued, the normalized candidates, and their repositories
    """
    saved = storage.load_interested_repositories()
    starred = storage.load_repositories()
    imported = storage.load_imported_profile()
    feedback = storage.load_feedback()
    excluded_owners = {imported.username} if imported else set()
    if scope == SCOPE_SAVED_STARRED:
        sources = select_source_repositories(saved, starred, profile, owner, feedback, excluded_owners, now=now)
        queries = build_issue_queries(sources)
        issues, warning = collect_issue_candidates(client, sources)
        repositories = {repository.full_name.lower(): repository for repository in sources}
    else:
        queries = build_discovery_queries(profile)
        issues, warning = collect_discovery_candidates(client, profile)
        issues = exclude_issues(issues, owner, feedback, excluded_owners)
        repositories, warning = hydrate_repositories(client, issues, profile, now=now)
    if warning:
        raise GitHubError(f"contribution capture for scope {scope} degraded: {warning}")
    repositories = _public_repositories(repositories)
    issues = exclude_issues(issues, owner, feedback, excluded_owners)
    return queries, normalize_candidates(issues, repositories), repositories


def build_fixture(client: GitHubClient, storage: Storage, snapshot_date: str | None = None) -> dict[str, Any]:
    """
    capture both contribution scopes and assemble the frozen fixture
    :param client: authenticated GitHub client
    :param storage: local storage manager
    :param snapshot_date: optional explicit snapshot date
    :returns: fixture payload ready to serialize
    """
    owner = client.get_authenticated_user()
    starred = [repository for repository in storage.load_repositories() if not repository.private]
    saved = [repository for repository in storage.load_interested_repositories() if not repository.private]
    imported = storage.load_imported_profile()
    profile = build_profile(starred, storage.load_seed_preferences(), imported, saved)
    now = datetime.now(timezone.utc)
    issues: dict[str, Issue] = {}
    repositories: dict[str, Repository] = {}
    scopes: dict[str, Any] = {}
    for scope in (SCOPE_DISCOVER, SCOPE_SAVED_STARRED):
        queries, candidates, scope_repositories = capture_scope(client, profile, storage, scope, owner, now)
        repositories.update(scope_repositories)
        identifiers: list[str] = []
        for issue in candidates:
            identifier = issue_identifier(issue.repository, issue.number)
            issues.setdefault(identifier, issue)
            identifiers.append(identifier)
        scopes[scope] = {"queries": queries, "issue_ids": sorted(set(identifiers))}
    return {
        "snapshot_date": snapshot_date or now.date().isoformat(),
        "generated_by": "python -m repo_radar.contribution_snapshot",
        "owner": owner,
        "frozen_body_characters": FROZEN_BODY_CHARACTERS,
        "profile": profile.to_dict(),
        "scopes": scopes,
        "repositories": [
            repositories[name].to_dict() for name in sorted(repositories) if not repositories[name].private
        ],
        "issues": [_frozen_issue(issues[identifier]) for identifier in sorted(issues)],
    }


def merge_judgments(fixture: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    """
    build the judgments file, preserving every judgment the user already recorded

    A refresh must never silently discard human labels, and it must never invent one for a
    newly captured issue. Candidates that disappeared from the fixture are dropped; new
    candidates arrive explicitly `null`.
    :param fixture: freshly captured fixture payload
    :param existing: previously recorded judgments payload
    :returns: judgments payload ready to serialize
    """
    previous = (existing or {}).get("judgments", {})
    recorded = {str(key).lower(): value for key, value in previous.items()}
    identifiers = [issue_identifier(str(entry["repository"]), int(entry["number"])) for entry in fixture["issues"]]
    return {
        "scale": {str(grade): meaning for grade, meaning in sorted(JUDGMENT_SCALE.items())},
        "actionable_threshold": ACTIONABLE_THRESHOLD,
        "instructions": (
            "Would you actually open this issue and investigate it as a possible contribution, "
            "judged only on what Repo Radar could show you at recommendation time? Do not label "
            "on hindsight such as whether the fix turned out to be easy or whether a pull request "
            "was merged. Replace null with 0, 1, 2, or 3."
        ),
        "judged_by": (existing or {}).get("judged_by"),
        "judged_at": (existing or {}).get("judged_at"),
        "judgments": {identifier: recorded.get(identifier) for identifier in sorted(identifiers)},
    }


def main(argv: list[str] | None = None) -> int:
    """
    capture the contribution evaluation fixture from the command line
    :param argv: optional argument list
    :returns: process exit code
    """
    parser = argparse.ArgumentParser(
        prog="python -m repo_radar.contribution_snapshot",
        description="Fetch real contribution candidates once and freeze them for offline evaluation",
    )
    parser.add_argument("--data-dir", default="data", help="local private data directory")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_PATH, help="fixture file location")
    parser.add_argument("--judgments", type=Path, default=JUDGMENTS_PATH, help="judgments file location")
    parser.add_argument("--snapshot-date", help="override the recorded snapshot date")
    arguments = parser.parse_args(argv)
    try:
        fixture = build_fixture(GitHubClient(), Storage(arguments.data_dir), arguments.snapshot_date)
    except (GitHubError, RuntimeError, ValueError) as error:
        print(f"contribution snapshot failed: {error}", file=sys.stderr)
        return 1
    existing = None
    if arguments.judgments.exists():
        existing = json.loads(arguments.judgments.read_text(encoding="utf-8"))
    judgments = merge_judgments(fixture, existing)
    arguments.fixtures.parent.mkdir(parents=True, exist_ok=True)
    arguments.fixtures.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    arguments.judgments.write_text(json.dumps(judgments, indent=2) + "\n", encoding="utf-8")
    unjudged = sum(1 for value in judgments["judgments"].values() if value is None)
    print(f"contribution fixture written to {arguments.fixtures}")
    print(f"  snapshot date: {fixture['snapshot_date']}")
    print(f"  issues:        {len(fixture['issues'])}")
    print(f"  repositories:  {len(fixture['repositories'])}")
    for scope, entry in sorted(fixture["scopes"].items()):
        print(f"  {scope}: {len(entry['issue_ids'])} candidates from {len(entry['queries'])} searches")
    print(f"judgments written to {arguments.judgments} ({unjudged} unjudged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
