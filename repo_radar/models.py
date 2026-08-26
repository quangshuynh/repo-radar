"""typed domain models for Repo Radar"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Repository:
    """repository metadata used by profiling and ranking"""

    full_name: str
    description: str | None = None
    language: str | None = None
    topics: list[str] = field(default_factory=list)
    stars: int = 0
    forks: int = 0
    archived: bool = False
    is_fork: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    pushed_at: str | None = None
    owner: str = ""
    url: str = ""
    # recorded so held-out evaluation snapshots can prove a repository is public before
    # committing its identity; ranking and discovery deliberately ignore it
    private: bool = False

    @classmethod
    def from_github(cls, value: dict[str, Any]) -> Repository:
        """
        create a repository from a GitHub API object
        :param value: GitHub repository response object
        :returns: normalized repository
        """
        owner = value.get("owner") or {}
        return cls(
            full_name=str(value.get("full_name", "")),
            description=value.get("description"),
            language=value.get("language"),
            topics=list(value.get("topics") or []),
            stars=int(value.get("stargazers_count") or 0),
            forks=int(value.get("forks_count") or 0),
            archived=bool(value.get("archived", False)),
            is_fork=bool(value.get("fork", False)),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
            pushed_at=value.get("pushed_at"),
            owner=str(owner.get("login", "")),
            url=str(value.get("html_url", "")),
            private=bool(value.get("private", False)),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Repository:
        """
        create a repository from persisted data
        :param value: persisted repository dictionary
        :returns: repository instance
        """
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        """
        convert the repository to JSON compatible data
        :returns: repository dictionary
        """
        return asdict(self)


@dataclass(slots=True)
class PreferenceProfile:
    """normalized preference signals derived from starred repositories"""

    languages: dict[str, float] = field(default_factory=dict)
    topics: dict[str, float] = field(default_factory=dict)
    keywords: dict[str, float] = field(default_factory=dict)
    median_stars: float = 0.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PreferenceProfile:
        """
        create a profile from persisted data
        :param value: persisted profile dictionary
        :returns: preference profile
        """
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        """
        convert the profile to JSON compatible data
        :returns: profile dictionary
        """
        return asdict(self)


@dataclass(slots=True)
class SeedPreferences:
    """manual interests used when building a preference profile"""

    languages: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SeedPreferences:
        """
        create seed preferences from persisted data
        :param value: persisted seed preference dictionary
        :returns: seed preferences
        """
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        """
        convert seed preferences to JSON compatible data
        :returns: seed preference dictionary
        """
        return asdict(self)

    def has_signals(self) -> bool:
        """
        determine whether any manual preferences are present
        :returns: whether at least one signal is present
        """
        return bool(self.languages or self.topics or self.keywords)


@dataclass(slots=True)
class ImportedRepository:
    """public owned repository imported from GitProfileLens"""

    name: str
    description: str | None = None
    url: str = ""
    pinned: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    pushed_at: str | None = None
    language: str | None = None
    topics: list[str] = field(default_factory=list)
    stars: int = 0
    forks: int = 0
    archived: bool = False
    is_fork: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ImportedRepository:
        """
        create an imported repository from persisted data
        :param value: persisted imported repository dictionary
        :returns: imported repository
        """
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        """
        convert an imported repository to JSON compatible data
        :returns: imported repository dictionary
        """
        return asdict(self)


@dataclass(slots=True)
class ImportedProfile:
    """structured GitProfileLens public repository profile"""

    username: str
    public_repository_count: int = 0
    fetched_at: str = ""
    source_url: str = ""
    repositories: list[ImportedRepository] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ImportedProfile:
        """
        create an imported profile from persisted data
        :param value: persisted imported profile dictionary
        :returns: imported profile
        """
        data = dict(value)
        data["repositories"] = [ImportedRepository.from_dict(repository) for repository in data.get("repositories", [])]
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """
        convert an imported profile to JSON compatible data
        :returns: imported profile dictionary
        """
        return {
            "username": self.username,
            "public_repository_count": self.public_repository_count,
            "fetched_at": self.fetched_at,
            "source_url": self.source_url,
            "repositories": [repository.to_dict() for repository in self.repositories],
        }


@dataclass(slots=True)
class Recommendation:
    """ranked repository with a transparent explanation"""

    repository: Repository
    score: float
    explanation: str


def _issue_repository(value: dict[str, Any]) -> str:
    """
    derive the owner and name of the repository owning a GitHub issue
    :param value: GitHub issue response object
    :returns: repository full name or an empty string
    """
    api_url = str(value.get("repository_url") or "")
    if api_url:
        parts = api_url.rstrip("/").split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
    # issue search results carry repository_url; the html_url fallback keeps partial
    # payloads usable instead of discarding an otherwise complete issue
    html_url = str(value.get("html_url") or "")
    parts = html_url.split("/")
    if len(parts) >= 5 and parts[2].endswith("github.com"):
        return f"{parts[3]}/{parts[4]}"
    return ""


def _issue_labels(value: dict[str, Any]) -> list[str]:
    """
    normalize GitHub issue labels to unique lowercase names
    :param value: GitHub issue response object
    :returns: normalized label names preserving declaration order
    """
    names: list[str] = []
    for label in value.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        cleaned = str(name or "").strip().lower()
        if cleaned:
            names.append(cleaned)
    return list(dict.fromkeys(names))


def _issue_assignee_count(value: dict[str, Any]) -> int:
    """
    count the people currently assigned to a GitHub issue
    :param value: GitHub issue response object
    :returns: number of distinct assignees
    """
    logins = {
        str(assignee.get("login") or "")
        for assignee in value.get("assignees") or []
        if isinstance(assignee, dict) and assignee.get("login")
    }
    assignee = value.get("assignee")
    if isinstance(assignee, dict) and assignee.get("login"):
        logins.add(str(assignee["login"]))
    return len(logins)


@dataclass(slots=True)
class Issue:
    """open GitHub issue considered as a contribution opportunity"""

    repository: str
    number: int
    title: str
    url: str = ""
    body: str | None = None
    labels: list[str] = field(default_factory=list)
    assignee_count: int = 0
    comments: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    state: str = "open"
    is_pull_request: bool = False

    @classmethod
    def from_github(cls, value: dict[str, Any]) -> Issue:
        """
        create an issue from a GitHub API object
        :param value: GitHub issue response object
        :returns: normalized issue
        """
        return cls(
            repository=_issue_repository(value),
            number=int(value.get("number") or 0),
            title=str(value.get("title") or "").strip(),
            url=str(value.get("html_url") or ""),
            body=value.get("body"),
            labels=_issue_labels(value),
            assignee_count=_issue_assignee_count(value),
            comments=int(value.get("comments") or 0),
            created_at=value.get("created_at"),
            updated_at=value.get("updated_at"),
            state=str(value.get("state") or "").strip().lower() or "open",
            is_pull_request="pull_request" in value,
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Issue:
        """
        create an issue from persisted data
        :param value: persisted issue dictionary
        :returns: issue instance
        """
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        """
        convert the issue to JSON compatible data
        :returns: issue dictionary
        """
        return asdict(self)

    def is_identifiable(self) -> bool:
        """
        determine whether the issue carries the identity the pipeline requires
        :returns: whether the repository, number, and title are usable
        """
        owner, _, name = self.repository.partition("/")
        return bool(owner and name and self.number > 0 and self.title)


@dataclass(slots=True)
class IssueRecommendation:
    """ranked contribution opportunity with the evidence that produced it"""

    issue: Issue
    repository: Repository
    score: float
    reasons: list[str] = field(default_factory=list)
    scope_signal: str = "Unclear"
    scope_evidence: list[str] = field(default_factory=list)
