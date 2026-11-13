"""deterministic local Repo Radar server for browser tests and README screenshots

The real FastAPI application runs on a free localhost port against a temporary data
directory. Only the GitHub boundary is replaced: `FixtureGitHub` answers searches from the
frozen public snapshots under `evaluation/`, so discovery and contribution ranking run through
the production pipeline without network access, a token, or the personal `data/` directory.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

from repo_radar import web
from repo_radar.contribution_evaluation import load_fixture
from repo_radar.feedback import record_feedback
from repo_radar.github_client import GitHubError
from repo_radar.heldout_evaluation import load_snapshot
from repo_radar.models import ImportedProfile, ImportedRepository, Issue, Repository, SeedPreferences
from repo_radar.storage import Storage

DEMO_LOGIN = "radar-demo"

# repositories from the frozen snapshots that the demo profile has already acted on
DEMO_STARRED = (
    "apache/airflow",
    "Kozea/Radicale",
    "PrefectHQ/prefect",
    "getsentry/sentry",
    "galaxyproject/galaxy",
    "cloudposse/atmos",
    "DataBiosphere/toil",
    "LukasNiessen/ArchUnitTS",
)
DEMO_SAVED = ("vadimdemedes/ink", "Skyvern-AI/skyvern", "josh-project/josh")
DEMO_BLOCKED = ("brookfacehe2421/flashloan-scalper-bot",)
DEMO_DISMISSED = ("proxifly/free-proxy-list", "howie6879/weekly")

_LANGUAGE_QUALIFIER = re.compile(r'language:(?:"([^"]+)"|(\S+))')
_REPOSITORY_QUALIFIER = re.compile(r"repo:([\w.-]+/[\w.-]+)")
_LABEL_QUALIFIER = re.compile(r"label:((?:\"[^\"]+\"|[^\s\"(),]+)(?:,(?:\"[^\"]+\"|[^\s\"(),]+))*)")


class FixtureGitHub:
    """mutable fake GitHub state shared by every client the application creates"""

    def __init__(self) -> None:
        """
        load the frozen public repositories and issues
        :returns: nothing
        """
        snapshot = load_snapshot()
        fixture = load_fixture()
        self.repositories: dict[str, Repository] = {}
        for repository in [*snapshot.candidates, *fixture.repositories.values()]:
            self.repositories.setdefault(repository.full_name.lower(), repository)
        self.issues: list[Issue] = list(fixture.issues.values())
        self.reset()

    def reset(self) -> None:
        """
        forget mutations and injected failures between tests
        :returns: nothing
        """
        self.starred: list[str] = list(DEMO_STARRED)
        self.star_requests: list[str] = []
        self.failures: dict[str, str] = {}
        self.delays: dict[str, float] = {}
        self.search_queries: list[str] = []

    def repository(self, full_name: str) -> Repository:
        """
        return one frozen repository
        :param full_name: repository full name
        :returns: repository metadata
        """
        try:
            return self.repositories[full_name.lower()]
        except KeyError as error:
            raise GitHubError(f"GitHub repository {full_name} was not found", status=404) from error

    def _enter(self, operation: str) -> None:
        """
        apply the configured latency and failure for one operation
        :param operation: fake GitHub operation name
        :returns: nothing
        """
        if self.delays.get(operation):
            time.sleep(self.delays[operation])
        if operation in self.failures:
            raise GitHubError(self.failures[operation], status=502)

    def client_class(self) -> type:
        """
        build a `GitHubClient` stand-in bound to this state
        :returns: client class accepting the production constructor arguments
        """
        state = self

        class FixtureClient:
            """GitHub client answering from frozen fixtures"""

            def __init__(self, token: str | None = None, base_url: str = "") -> None:
                """
                accept the production constructor arguments
                :param token: ignored token
                :param base_url: ignored API base URL
                :returns: nothing
                """
                state._enter("client")

            def get_authenticated_user(self) -> str:
                """
                return the demo login
                :returns: authenticated login
                """
                state._enter("user")
                return DEMO_LOGIN

            def get_starred_repositories(self) -> list[Repository]:
                """
                return the repositories starred in the fake GitHub account
                :returns: starred repositories
                """
                state._enter("starred")
                return [state.repository(name) for name in state.starred]

            def get_repository(self, full_name: str) -> Repository:
                """
                return one frozen repository
                :param full_name: repository full name
                :returns: repository metadata
                """
                state._enter("repository")
                return state.repository(full_name)

            def search_repositories(self, query: str, limit: int = 30) -> list[Repository]:
                """
                answer a repository search from the frozen candidate pool
                :param query: generated GitHub search query
                :param limit: maximum results
                :returns: repositories matching the language qualifier, most starred first
                """
                state._enter("search_repositories")
                state.search_queries.append(query)
                match = _LANGUAGE_QUALIFIER.search(query)
                language = (match.group(1) or match.group(2)).casefold() if match else ""
                topic = next((part[6:] for part in query.split() if part.startswith("topic:")), "")
                pool = [
                    repository
                    for repository in state.repositories.values()
                    if (not language or (repository.language or "").casefold() == language)
                    and (not topic or topic in repository.topics)
                ]
                pool.sort(key=lambda repository: (-repository.stars, repository.full_name.lower()))
                return pool[:limit]

            def search_issues(self, query: str, limit: int = 50) -> list[Issue]:
                """
                answer an issue search from the frozen issue corpus
                :param query: generated GitHub issue search query
                :param limit: maximum results
                :returns: issues honoring repository and label qualifiers
                """
                state._enter("search_issues")
                state.search_queries.append(query)
                scopes = {name.lower() for name in _REPOSITORY_QUALIFIER.findall(query)}
                groups = [
                    {value.strip('"').lower() for value in group.split(",")}
                    for group in _LABEL_QUALIFIER.findall(query)
                ]
                issues = [
                    issue
                    for issue in state.issues
                    if (not scopes or issue.repository.lower() in scopes)
                    and all(group & set(issue.labels) for group in groups)
                ]
                return issues[:limit]

            def star_repository(self, repository: str) -> None:
                """
                star one repository in the fake GitHub account
                :param repository: repository full name
                :returns: nothing
                """
                state._enter("star")
                state.star_requests.append(repository)
                if repository.lower() not in {name.lower() for name in state.starred}:
                    state.starred.append(repository)

        return FixtureClient


def seed_demo_data(data_dir: Path, github: FixtureGitHub) -> Storage:
    """
    write a realistic local state built only from frozen public fixture repositories
    :param data_dir: empty data directory
    :param github: fixture state supplying repository metadata
    :returns: storage for the seeded directory
    """
    storage = Storage(data_dir)
    storage.save_seed_preferences(
        SeedPreferences(
            languages=["Python", "TypeScript"],
            topics=["developer-tools", "automation", "cli", "testing", "workflow"],
            keywords=["terminal", "browser", "api"],
        )
    )
    storage.save_repositories([github.repository(name) for name in DEMO_STARRED])
    for name in DEMO_STARRED:
        record_feedback(storage, github.repository(name).full_name, "starred")
    for name in DEMO_SAVED:
        record_feedback(storage, github.repository(name).full_name, "interested", github.repository(name))
    for name in DEMO_BLOCKED:
        record_feedback(storage, github.repository(name).full_name, "blocked")
    for name in DEMO_DISMISSED:
        record_feedback(storage, github.repository(name).full_name, "not interested")
    storage.save_imported_profile(
        ImportedProfile(
            DEMO_LOGIN,
            4,
            repositories=[
                ImportedRepository("workflow-kit", pinned=True, language="Python", topics=["automation", "cli"]),
                ImportedRepository("issue-notes", pinned=True, language="TypeScript", topics=["developer-tools"]),
                ImportedRepository("dotfiles", language="Shell", topics=["terminal"]),
                ImportedRepository("old-fork", language="Python", is_fork=True),
            ],
        )
    )
    last_sync = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    storage.save_status({"authenticated_user": DEMO_LOGIN, "last_sync": last_sync})
    return storage


def _free_port() -> int:
    """
    reserve an unused localhost port
    :returns: port number
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextlib.contextmanager
def serve(github: FixtureGitHub, data_dir: Path) -> Iterator[str]:
    """
    run the real application with only its GitHub boundary replaced
    :param github: fixture GitHub state
    :param data_dir: data directory the application reads and writes
    :returns: base URL of the running server
    """

    def fake_import(username: str, storage: Storage) -> ImportedProfile:
        """
        import a deterministic public profile without contacting GitProfileLens
        :param username: requested GitHub username
        :param storage: local storage manager
        :returns: imported profile
        """
        github._enter("import")
        profile = ImportedProfile(
            username,
            2,
            repositories=[
                ImportedRepository("cli-tools", pinned=True, language="Go", topics=["cli", "developer-tools"]),
                ImportedRepository("site", language="TypeScript", topics=["web"]),
            ],
        )
        storage.save_imported_profile(profile)
        return profile

    patched = {
        "GitHubClient": github.client_class(),
        "import_profile": fake_import,
        "token_configured": lambda: True,
    }
    original = {name: getattr(web, name) for name in patched}
    previous_data_dir = os.environ.get("REPO_RADAR_DATA_DIR")
    for name, value in patched.items():
        setattr(web, name, value)
    os.environ["REPO_RADAR_DATA_DIR"] = str(data_dir)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(web.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("UI test server did not start")
            time.sleep(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for name, value in original.items():
            setattr(web, name, value)
        if previous_data_dir is None:
            os.environ.pop("REPO_RADAR_DATA_DIR", None)
        else:
            os.environ["REPO_RADAR_DATA_DIR"] = previous_data_dir
