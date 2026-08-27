import pytest

from repo_radar.cli import build_parser, run_contribute, run_import_profile, run_init, run_profile, run_recommend
from repo_radar.contribution import ContributionFilters
from repo_radar.models import ImportedProfile, ImportedRepository, Issue, Repository, SeedPreferences
from repo_radar.storage import Storage


class ColdStartRecommendationClient:
    """GitHub client returning cold-start discovery candidates"""

    def __init__(self) -> None:
        """
        initialize search query tracking
        :returns: nothing
        """
        self.queries: list[str] = []

    def get_authenticated_user(self) -> str:
        """
        return the mocked authenticated login
        :returns: authenticated login
        """
        return "empty-user"

    def search_repositories(self, query: str, limit: int) -> list[Repository]:
        """
        return candidates for each generated seed query
        :param query: generated repository query
        :param limit: requested result limit
        :returns: mocked repository candidates
        """
        self.queries.append(query)
        return [
            Repository(
                "useful/tool",
                "automation cli",
                "Python",
                ["automation"],
                50,
                owner="useful",
                url="https://github.com/useful/tool",
            ),
            Repository("empty-user/owned", language="Python", owner="empty-user"),
            Repository("old/archived", language="Python", archived=True, owner="old"),
            Repository("blocked/repo", language="Python", owner="blocked"),
        ]


def test_profile_succeeds_with_no_starred_repositories(tmp_path, capsys) -> None:
    """
    an empty starred cache produces a readable empty profile
    :param tmp_path: pytest temporary directory
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)

    assert run_profile(storage) == 0
    assert storage.load_profile() is not None
    assert "no signals yet" in capsys.readouterr().out


def test_recommend_succeeds_without_preference_signals(tmp_path, capsys) -> None:
    """
    empty stars and seeds produce a helpful recommendation message
    :param tmp_path: pytest temporary directory
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    assert run_recommend(storage, 10) == 0
    assert "No preference signals are available yet" in capsys.readouterr().out


def test_init_replaces_and_persists_seed_preferences(tmp_path) -> None:
    """
    repeated setup replaces existing locally persisted seed preferences
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    storage = Storage(tmp_path)
    storage.save_seed_preferences(SeedPreferences(["Go"], ["systems"], ["networking"]))
    answers = iter(["Python, TypeScript, python", "Automation, backend", "CLI, APIs"])

    def fake_input(prompt: str) -> str:
        """
        return the next setup response
        :param prompt: interactive input prompt
        :returns: next mocked answer
        """
        return next(answers)

    assert run_init(storage, fake_input) == 0
    assert storage.load_seed_preferences() == SeedPreferences(
        ["Python", "TypeScript"], ["automation", "backend"], ["cli", "apis"]
    )


def test_recommend_uses_only_manual_preferences_and_filters_candidates(tmp_path, monkeypatch, capsys) -> None:
    """
    seed-only discovery uses existing ranking and filtering behavior
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    storage.save_seed_preferences(SeedPreferences(["Python"], ["automation"], ["cli"]))
    storage.save_feedback({"blocked/repo": "blocked"})
    client = ColdStartRecommendationClient()

    def make_client() -> ColdStartRecommendationClient:
        """
        return the shared mocked GitHub client
        :returns: mocked GitHub client
        """
        return client

    monkeypatch.setattr("repo_radar.cli.GitHubClient", make_client)
    assert run_recommend(storage, 10) == 0
    output = capsys.readouterr().out
    assert client.queries
    assert "useful/tool" in output
    assert "empty-user/owned" not in output
    assert "old/archived" not in output
    assert "blocked/repo" not in output


def test_cli_import_profile_uses_shared_importer(tmp_path, monkeypatch, capsys) -> None:
    """
    the CLI imports and summarizes a GitProfileLens profile
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)

    def fake_import(username: str, target_storage: Storage) -> ImportedProfile:
        """
        save and return a mocked imported profile
        :param username: requested username
        :param target_storage: local storage manager
        :returns: mocked imported profile
        """
        profile = ImportedProfile(
            username,
            2,
            repositories=[ImportedRepository("one", pinned=True), ImportedRepository("two")],
        )
        target_storage.save_imported_profile(profile)
        return profile

    monkeypatch.setattr("repo_radar.cli.import_profile", fake_import)
    assert run_import_profile(storage, "example") == 0
    assert storage.load_imported_profile() is not None
    assert "Imported 2 public repositories" in capsys.readouterr().out


class ContributionClient:
    """GitHub client returning deterministic contribution candidates"""

    def __init__(self) -> None:
        """
        initialize issue query tracking
        :returns: nothing
        """
        self.queries: list[str] = []

    def get_authenticated_user(self) -> str:
        """
        return the mocked authenticated login
        :returns: authenticated login
        """
        return "example"

    def search_issues(self, query: str, limit: int = 50) -> list[Issue]:
        """
        return mocked issue candidates for one grouped query
        :param query: generated issue search query
        :param limit: requested result limit
        :returns: mocked issue candidates
        """
        self.queries.append(query)
        return [
            Issue(
                repository="acme/service",
                number=7,
                title="Improve backend automation retry handling",
                url="https://github.com/acme/service/issues/7",
                body="Steps to reproduce: call the client twice and inspect src/retry.py.",
                labels=["help wanted"],
                updated_at="2026-01-01T00:00:00Z",
            ),
            Issue(
                repository="blocked/repo",
                number=1,
                title="Unrelated work in a rejected repository",
                url="https://github.com/blocked/repo/issues/1",
                updated_at="2026-01-01T00:00:00Z",
            ),
        ]


def test_contribute_requires_local_repository_evidence(tmp_path, capsys) -> None:
    """
    an empty saved and starred state prints scope guidance instead of searching
    :param tmp_path: pytest temporary directory
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    assert run_contribute(Storage(tmp_path), 10, scope="saved_starred") == 0
    assert "No saved or starred repositories are available yet" in capsys.readouterr().out


def test_contribute_ranks_and_explains_issues_from_followed_repositories(tmp_path, monkeypatch, capsys) -> None:
    """
    the CLI shares the ranking pipeline and prints the evidence behind each result
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    storage.save_repositories(
        [
            Repository("acme/service", "backend automation service", "Python", ["backend"], 900, owner="acme"),
            Repository("blocked/repo", language="Python", owner="blocked"),
        ]
    )
    storage.save_feedback({"blocked/repo": "blocked"})
    client = ContributionClient()
    monkeypatch.setattr("repo_radar.cli.GitHubClient", lambda: client)

    assert run_contribute(storage, 5, scope="saved_starred") == 0
    output = capsys.readouterr().out
    assert client.queries
    assert "blocked/repo" not in client.queries[0]
    assert "acme/service#7" in output
    assert "Why recommended:" in output
    assert "Label: help wanted" in output
    assert "Scope signal: Focused" in output
    assert "blocked/repo#1" not in output


def test_contribute_parses_repeatable_category_labels_and_the_friendly_flag() -> None:
    """
    the filter flags parse into a normalized filter alongside the existing options
    :returns: nothing
    """
    parsed = build_parser().parse_args(
        [
            "contribute",
            "--label",
            "documentation",
            "--label",
            "bug",
            "--label",
            "bug",
            "--contributor-friendly",
            "--scope",
            "saved-starred",
            "--unassigned-only",
        ]
    )
    filters = ContributionFilters.create(parsed.labels, parsed.contributor_friendly)
    assert parsed.labels == ["documentation", "bug", "bug"]
    assert parsed.scope == "saved-starred"
    assert parsed.unassigned_only is True
    # repetition and click order are normalized away before a query is built
    assert filters == ContributionFilters(categories=("bug", "documentation"), contributor_friendly=True)


def test_contribute_without_filter_flags_keeps_its_previous_behavior() -> None:
    """
    the existing command form parses to no filters and the established defaults
    :returns: nothing
    """
    parsed = build_parser().parse_args(["contribute"])
    assert parsed.labels == []
    assert parsed.contributor_friendly is False
    assert parsed.scope == "discover"
    assert parsed.unassigned_only is False
    assert ContributionFilters.create(parsed.labels, parsed.contributor_friendly).qualifiers == ()


def test_contribute_rejects_an_unsupported_category() -> None:
    """
    an unsupported category fails at argument parsing rather than reaching GitHub
    :returns: nothing
    """
    with pytest.raises(SystemExit) as failure:
        build_parser().parse_args(["contribute", "--label", "security"])
    assert failure.value.code == 2


def test_contribute_filters_reach_the_issue_query(tmp_path, monkeypatch, capsys) -> None:
    """
    selected filters become label qualifiers on the same single grouped search
    :param tmp_path: pytest temporary directory
    :param monkeypatch: pytest monkeypatch fixture
    :param capsys: pytest output capture fixture
    :returns: nothing
    """
    storage = Storage(tmp_path)
    storage.save_repositories(
        [Repository("acme/service", "backend automation service", "Python", ["backend"], 900, owner="acme")]
    )
    client = ContributionClient()
    monkeypatch.setattr("repo_radar.cli.GitHubClient", lambda: client)

    exit_code = run_contribute(
        storage,
        5,
        scope="saved_starred",
        filters=ContributionFilters.create(["bug", "documentation"], contributor_friendly=True),
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert len(client.queries) == 1
    assert 'label:"bug","documentation"' in client.queries[0]
    assert 'label:"good first issue","help wanted","contributions welcome","up for grabs"' in client.queries[0]
    # the mocked issue carries help wanted but no category label, so nothing survives retrieval
    assert "No open contribution opportunities were found" in output
