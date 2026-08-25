"""capture a frozen snapshot of real repository metadata for held-out star evaluation

Run once, evaluate offline forever. This is the only part of the held-out experiment that
contacts GitHub; `repo_radar.heldout_evaluation` reads the resulting file and nothing else.

Only public repositories are written. A star on a private repository is filtered out with a
reported reason rather than silently anonymized, because the snapshot is meant to be
committed to a public repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .discovery import build_search_queries, discover_candidates
from .github_client import GitHubClient, GitHubError
from .heldout_evaluation import SNAPSHOT_PATH, star_exclusion_reason
from .models import ImportedProfile, Repository
from .profile import build_profile
from .storage import Storage


def _public_only(repositories: list[Repository]) -> tuple[list[Repository], list[dict[str, str]]]:
    """
    drop private repositories before anything is written to disk
    :param repositories: repositories fetched from GitHub
    :returns: public repositories and records of what was dropped
    """
    public = [repository for repository in repositories if not repository.private]
    dropped = [
        {"repository": repository.full_name, "reason": "private repository"}
        for repository in repositories
        if repository.private
    ]
    return public, dropped


def _sorted_repositories(repositories: list[Repository]) -> list[dict[str, Any]]:
    """
    serialize repositories in a stable order so the snapshot diffs cleanly
    :param repositories: repositories to serialize
    :returns: repository dictionaries sorted by identity
    """
    return [repository.to_dict() for repository in sorted(repositories, key=lambda item: item.full_name.lower())]


def build_snapshot(client: GitHubClient, storage: Storage, snapshot_date: str | None = None) -> dict[str, Any]:
    """
    fetch real starred and candidate metadata and assemble the frozen snapshot

    Candidate discovery runs through the production `discover_candidates` path using a
    profile built from the full star history, so the distractors are the kind of
    repositories the application would genuinely surface rather than random noise.

    :param client: authenticated GitHub client
    :param storage: local storage manager supplying the imported public profile
    :param snapshot_date: optional explicit snapshot date
    :returns: snapshot payload ready to serialize
    """
    owner = client.get_authenticated_user()
    starred, dropped = _public_only(client.get_starred_repositories())
    imported = storage.load_imported_profile()
    profile = build_profile(starred, None, imported, None)
    candidates, candidate_dropped = _public_only(discover_candidates(client, profile))
    owned_names = (
        {f"{imported.username}/{repository.name}".lower() for repository in imported.repositories}
        if imported
        else set()
    )
    candidates = [
        repository
        for repository in candidates
        if repository.owner.lower() != owner.lower() and repository.full_name.lower() not in owned_names
    ]
    eligibility = [
        {"repository": repository.full_name, "reason": reason}
        for repository in sorted(starred, key=lambda item: item.full_name.lower())
        if (reason := star_exclusion_reason(repository, owner)) is not None
    ]
    return {
        "snapshot_date": snapshot_date or datetime.now(timezone.utc).date().isoformat(),
        "generated_by": "python -m repo_radar.heldout_snapshot",
        "owner": owner,
        "search_queries": build_search_queries(profile),
        "coverage": {
            "total_stored_stars": len(starred) + len(dropped),
            "public_stars": len(starred),
            "private_stars_excluded": len(dropped),
            "eligible_stars": len(starred) - len(eligibility),
            "excluded_stars": [*dropped, *eligibility],
            "candidate_pool_size": len(candidates),
            "private_candidates_excluded": len(candidate_dropped),
        },
        "owned_profile": _public_owned_profile(imported),
        "stars": _sorted_repositories(starred),
        "candidates": _sorted_repositories(candidates),
    }


def _public_owned_profile(imported: ImportedProfile | None) -> dict[str, Any] | None:
    """
    serialize the imported public repository profile used as a preference source
    :param imported: optional GitProfileLens profile
    :returns: serialized profile or none
    """
    if not imported:
        return None
    payload = imported.to_dict()
    payload["repositories"] = sorted(payload["repositories"], key=lambda item: str(item["name"]).lower())
    return payload


def main(argv: list[str] | None = None) -> int:
    """
    generate the held-out evaluation snapshot from the command line
    :param argv: optional argument list
    :returns: process exit code
    """
    parser = argparse.ArgumentParser(
        prog="python -m repo_radar.heldout_snapshot",
        description="Fetch real repository metadata once and freeze it for offline held-out evaluation",
    )
    parser.add_argument("--data-dir", default="data", help="local private data directory")
    parser.add_argument("--output", type=Path, default=SNAPSHOT_PATH, help="snapshot file location")
    parser.add_argument("--snapshot-date", help="override the recorded snapshot date")
    arguments = parser.parse_args(argv)
    try:
        snapshot = build_snapshot(GitHubClient(), Storage(arguments.data_dir), arguments.snapshot_date)
    except (GitHubError, RuntimeError) as error:
        print(f"snapshot generation failed: {error}", file=sys.stderr)
        return 1
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    coverage = snapshot["coverage"]
    print(f"snapshot written to {arguments.output}")
    print(f"  snapshot date:   {snapshot['snapshot_date']}")
    print(f"  public stars:    {coverage['public_stars']} ({coverage['eligible_stars']} eligible)")
    print(f"  candidate pool:  {coverage['candidate_pool_size']}")
    for entry in coverage["excluded_stars"]:
        print(f"  excluded {entry['repository']}: {entry['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
