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
