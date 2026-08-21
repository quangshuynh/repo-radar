"""local FastAPI application for Repo Radar"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .discovery import generate_recommendations
from .feedback import record_feedback
from .github_client import GitHubClient, GitHubError
from .gitprofilelens import GitProfileLensError, import_profile
from .models import Recommendation, Repository, SeedPreferences
from .profile import build_profile
from .storage import Storage

STATIC_DIRECTORY = Path(__file__).parent / "static"


class PreferencesRequest(BaseModel):
    """manual preference update payload"""

    languages: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    """local recommendation feedback payload"""

    repository: str
    classification: str
    description: str | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    stars: int = 0
    url: str = ""


class StarRequest(BaseModel):
    """GitHub repository star payload"""

    repository: str
    description: str | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    stars: int = 0
    url: str = ""


class ImportProfileRequest(BaseModel):
    """GitProfileLens import payload"""

    username: str


def _storage() -> Storage:
    """
    create storage for the configured private data directory
    :returns: local storage manager
    """
    return Storage(os.environ.get("REPO_RADAR_DATA_DIR", "data"))


def _clean_values(values: list[str], lowercase: bool = False) -> list[str]:
    """
    clean and deduplicate preference values
    :param values: submitted preference values
    :param lowercase: whether to normalize values to lowercase
    :returns: cleaned unique values
    """
    cleaned = [value.strip() for value in values if value.strip()]
    if lowercase:
        cleaned = [value.lower() for value in cleaned]
    unique: dict[str, str] = {}
    for value in cleaned:
        unique.setdefault(value.lower(), value)
    return list(unique.values())


def _safe_error(error: Exception) -> str:
    """
    remove configured credentials from an error message
    :param error: application error
    :returns: safe user facing error message
    """
    message = str(error)
    token = os.environ.get("GITHUB_TOKEN")
    return message.replace(token, "[redacted]") if token else message


def _recommendation_data(recommendation: Recommendation) -> dict[str, object]:
    """
    serialize a recommendation for the local API
    :param recommendation: ranked repository recommendation
    :returns: public recommendation fields
    """
    repository = recommendation.repository
    return {
        "full_name": repository.full_name,
        "score": round(recommendation.score, 4),
        "description": repository.description,
        "language": repository.language,
        "stars": repository.stars,
        "url": repository.url,
        "topics": repository.topics,
        "explanation": recommendation.explanation,
    }


def _repository_from_payload(payload: FeedbackRequest | StarRequest) -> Repository:
    """
    create repository metadata from a web action payload
    :param payload: interested or star action payload
    :returns: normalized repository metadata
    """
    owner = payload.repository.split("/", maxsplit=1)[0] if "/" in payload.repository else ""
    return Repository(
        full_name=payload.repository,
        description=payload.description,
        language=payload.language,
        topics=payload.topics,
        stars=payload.stars,
        owner=owner,
        url=payload.url,
    )


def _save_star(storage: Storage, repository: Repository) -> None:
    """
    persist one confirmed GitHub star locally
    :param storage: local storage manager
    :param repository: repository confirmed as starred
    :returns: nothing
    """
    starred = {item.full_name.lower(): item for item in storage.load_repositories()}
    starred[repository.full_name.lower()] = repository
    storage.save_repositories(list(starred.values()))
    record_feedback(storage, repository.full_name, "starred")


def _remove_interested_feedback(storage: Storage, repositories: set[str]) -> None:
    """
    remove interested classifications for repositories leaving the saved list
    :param storage: local storage manager
    :param repositories: case insensitive repository names to clear
    :returns: nothing
    """
    names = {repository.lower() for repository in repositories}
    feedback = storage.load_feedback()
    remaining = {
        repository: classification
        for repository, classification in feedback.items()
        if repository.lower() not in names or classification != "interested"
    }
    if len(remaining) != len(feedback):
        storage.save_feedback(remaining)


def create_app() -> FastAPI:
    """
    create the local Repo Radar FastAPI application
    :returns: configured FastAPI application
    """
    application = FastAPI(title="Repo Radar", docs_url=None, redoc_url=None)
    application.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """
        serve the local web interface
        :returns: frontend HTML response
        """
        return FileResponse(STATIC_DIRECTORY / "index.html")

    @application.get("/api/profile")
    def get_profile() -> dict[str, object]:
        """
        return the current combined preference profile
        :returns: profile and local preference counts
        """
        storage = _storage()
        starred = storage.load_repositories()
        seeds = storage.load_seed_preferences()
        imported = storage.load_imported_profile()
        interested = storage.load_interested_repositories()
        profile = build_profile(starred, seeds, imported, interested)
        storage.save_profile(profile)
        return {
            **profile.to_dict(),
            "starred_count": len(starred),
            "seed_count": len(seeds.languages) + len(seeds.topics) + len(seeds.keywords),
            "imported_count": len(imported.repositories) if imported else 0,
            "feedback_count": len(storage.load_feedback()),
            "interested_count": len(interested),
        }

    @application.get("/api/preferences")
    def get_preferences() -> dict[str, object]:
        """
        return stored manual seed preferences
        :returns: seed preference fields
        """
        return _storage().load_seed_preferences().to_dict()

    @application.post("/api/preferences")
    def save_preferences(payload: PreferencesRequest) -> dict[str, object]:
        """
        replace stored manual seed preferences
        :param payload: submitted seed preferences
        :returns: saved seed preference fields
        """
        preferences = SeedPreferences(
            languages=_clean_values(payload.languages),
            topics=_clean_values(payload.topics, lowercase=True),
            keywords=_clean_values(payload.keywords, lowercase=True),
        )
        _storage().save_seed_preferences(preferences)
        return preferences.to_dict()

    @application.post("/api/import-profile")
    def import_public_profile(payload: ImportProfileRequest) -> dict[str, object]:
        """
        import a GitProfileLens public repository profile
        :param payload: GitHub username import payload
        :returns: imported profile summary
        """
        try:
            profile = import_profile(payload.username, _storage())
        except (GitProfileLensError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=502, detail=_safe_error(error)) from error
        active = [
            repository for repository in profile.repositories if not repository.archived and not repository.is_fork
        ]
        return {
            "username": profile.username,
            "repository_count": len(profile.repositories),
            "pinned_count": sum(repository.pinned for repository in profile.repositories),
            "language_count": len({repository.language for repository in active if repository.language}),
            "topic_count": len({topic for repository in active for topic in repository.topics}),
        }

    @application.post("/api/feedback")
    def save_feedback(payload: FeedbackRequest) -> dict[str, str]:
        """
        persist one recommendation classification
        :param payload: repository feedback payload
        :returns: saved feedback confirmation
        """
        try:
            storage = _storage()
            repository_data = _repository_from_payload(payload) if payload.classification == "interested" else None
            record_feedback(storage, payload.repository, payload.classification, repository_data)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=_safe_error(error)) from error
        return {"repository": payload.repository, "classification": payload.classification}

    @application.get("/api/interested")
    def get_interested() -> dict[str, object]:
        """
        return repositories saved for later
        :returns: stored interested repositories
        """
        repositories = _storage().load_interested_repositories()
        return {"repositories": [repository.to_dict() for repository in repositories]}

    @application.delete("/api/interested/{owner}/{name}")
    def remove_interested(owner: str, name: str) -> dict[str, object]:
        """
        remove one repository from the saved list
        :param owner: repository owner
        :param name: repository name
        :returns: removal confirmation
        """
        repository = f"{owner}/{name}"
        storage = _storage()
        removed = storage.remove_interested_repository(repository)
        if not removed:
            raise HTTPException(status_code=404, detail="Saved repository was not found")
        _remove_interested_feedback(storage, {repository})
        return {"repository": repository, "removed": True}

    @application.delete("/api/interested")
    def clear_interested() -> dict[str, int]:
        """
        remove every repository from the saved list
        :returns: number of removed repositories
        """
        storage = _storage()
        repositories = storage.load_interested_repositories()
        removed_count = storage.clear_interested_repositories()
        _remove_interested_feedback(storage, {repository.full_name for repository in repositories})
        return {"removed_count": removed_count}

    @application.post("/api/star")
    def star_repository(payload: StarRequest) -> dict[str, object]:
        """
        star one repository through the authenticated GitHub API
        :param payload: repository star request
        :returns: successful star confirmation
        """
        try:
            client = GitHubClient()
            client.star_repository(payload.repository)
            storage = _storage()
            repository = _repository_from_payload(payload)
            _save_star(storage, repository)
        except (GitHubError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=502, detail=_safe_error(error)) from error
        return {"repository": payload.repository, "starred": True}

    @application.post("/api/interested/star-all")
    def star_all_interested() -> dict[str, int]:
        """
        star every saved repository through GitHub
        :returns: number of repositories successfully starred
        """
        storage = _storage()
        repositories = storage.load_interested_repositories()
        if not repositories:
            return {"starred_count": 0}
        try:
            client = GitHubClient()
            for repository in repositories:
                client.star_repository(repository.full_name)
                _save_star(storage, repository)
        except (GitHubError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=502, detail=_safe_error(error)) from error
        return {"starred_count": len(repositories)}

    @application.post("/api/sync")
    def sync_repositories() -> dict[str, object]:
        """
        refresh the local starred repository cache
        :returns: synchronization result
        """
        try:
            client = GitHubClient()
            owner = client.get_authenticated_user()
            repositories = client.get_starred_repositories()
            storage = _storage()
            storage.save_repositories(repositories)
            last_sync = datetime.now(timezone.utc).isoformat()
            storage.save_status({"authenticated_user": owner, "last_sync": last_sync})
            return {"authenticated_user": owner, "starred_count": len(repositories), "last_sync": last_sync}
        except (GitHubError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=502, detail=_safe_error(error)) from error

    @application.get("/api/status")
    def get_status() -> dict[str, object]:
        """
        return useful local synchronization state
        :returns: local status without credentials
        """
        storage = _storage()
        status = storage.load_status()
        seeds = storage.load_seed_preferences()
        imported = storage.load_imported_profile()
        return {
            "authenticated_user": status.get("authenticated_user"),
            "last_sync": status.get("last_sync"),
            "starred_count": len(storage.load_repositories()),
            "has_seed_preferences": seeds.has_signals(),
            "imported_username": imported.username if imported else None,
            "imported_count": len(imported.repositories) if imported else 0,
        }

    @application.get("/api/recommendations")
    def get_recommendations(
        limit: int = Query(default=10, ge=1, le=50),
        language: str | None = None,
        min_stars: int = Query(default=0, ge=0),
        max_stars: int | None = Query(default=None, ge=0),
        hidden_gems: bool = False,
    ) -> dict[str, object]:
        """
        discover and return filtered ranked recommendations
        :param limit: maximum results to return
        :param language: optional primary language filter
        :param min_stars: minimum repository star count
        :param max_stars: optional maximum repository star count
        :param hidden_gems: whether to limit results to smaller repositories
        :returns: recommendation results and empty state
        """
        storage = _storage()
        starred = storage.load_repositories()
        imported = storage.load_imported_profile()
        interested = storage.load_interested_repositories()
        profile = build_profile(starred, storage.load_seed_preferences(), imported, interested)
        if not profile.languages and not profile.topics and not profile.keywords:
            return {"recommendations": [], "message": "No preference signals are available yet"}
        try:
            client = GitHubClient()
            owner = client.get_authenticated_user()
            recommendations = generate_recommendations(
                client, profile, starred, owner, storage.load_feedback(), 50, imported
            )
        except (GitHubError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=502, detail=_safe_error(error)) from error
        maximum = 1000 if hidden_gems and max_stars is None else max_stars
        filtered = [
            recommendation
            for recommendation in recommendations
            if (not language or recommendation.repository.language == language)
            and recommendation.repository.stars >= min_stars
            and (maximum is None or recommendation.repository.stars <= maximum)
        ][:limit]
        message = None if filtered else "No eligible recommendations found"
        return {"recommendations": [_recommendation_data(item) for item in filtered], "message": message}

    return application


app = create_app()
