"""local JSON persistence"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ImportedProfile, PreferenceProfile, Repository, SeedPreferences


class Storage:
    """manage private user data in a local directory"""

    def __init__(self, data_dir: Path | str = "data") -> None:
        """
        initialize local storage paths
        :param data_dir: directory for local JSON data
        :returns: nothing
        """
        self.data_dir = Path(data_dir)

    def _read_json(self, name: str, default: Any) -> Any:
        """
        read a JSON value or return a default
        :param name: file name inside the data directory
        :param default: value returned when the file does not exist
        :returns: decoded JSON data
        """
        path = self.data_dir / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read {path}: {error}") from error

    def _write_json(self, name: str, value: Any) -> None:
        """
        atomically write a JSON value
        :param name: file name inside the data directory
        :param value: JSON compatible value
        :returns: nothing
        """
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def load_repositories(self, name: str = "starred.json") -> list[Repository]:
        """
        load repositories from local storage
        :param name: repository cache file name
        :returns: stored repositories
        """
        return [Repository.from_dict(item) for item in self._read_json(name, [])]

    def save_repositories(self, repositories: list[Repository], name: str = "starred.json") -> None:
        """
        save repositories to local storage
        :param repositories: repositories to persist
        :param name: repository cache file name
        :returns: nothing
        """
        self._write_json(name, [repository.to_dict() for repository in repositories])

    def load_interested_repositories(self) -> list[Repository]:
        """
        load repositories marked as interesting
        :returns: stored interested repositories
        """
        return self.load_repositories("interested.json")

    def save_interested_repositories(self, repositories: list[Repository]) -> None:
        """
        save repositories marked as interesting
        :param repositories: interested repositories to persist
        :returns: nothing
        """
        self.save_repositories(repositories, "interested.json")

    def load_profile(self) -> PreferenceProfile | None:
        """
        load the stored preference profile
        :returns: stored profile or none
        """
        value = self._read_json("profile.json", None)
        return PreferenceProfile.from_dict(value) if value else None

    def save_profile(self, profile: PreferenceProfile) -> None:
        """
        save a preference profile
        :param profile: profile to persist
        :returns: nothing
        """
        self._write_json("profile.json", profile.to_dict())

    def load_seed_preferences(self) -> SeedPreferences:
        """
        load manually entered seed preferences
        :returns: stored seed preferences
        """
        value = self._read_json("seed_preferences.json", {})
        return SeedPreferences.from_dict(value)

    def save_seed_preferences(self, preferences: SeedPreferences) -> None:
        """
        save manually entered seed preferences
        :param preferences: seed preferences to persist
        :returns: nothing
        """
        self._write_json("seed_preferences.json", preferences.to_dict())

    def load_status(self) -> dict[str, Any]:
        """
        load local synchronization status
        :returns: stored status values
        """
        return dict(self._read_json("status.json", {}))

    def save_status(self, status: dict[str, Any]) -> None:
        """
        save local synchronization status
        :param status: synchronization status values
        :returns: nothing
        """
        self._write_json("status.json", status)

    def load_imported_profile(self) -> ImportedProfile | None:
        """
        load the last valid GitProfileLens profile
        :returns: imported profile or none
        """
        value = self._read_json("gitprofilelens_profile.json", None)
        return ImportedProfile.from_dict(value) if value else None

    def save_imported_profile(self, profile: ImportedProfile) -> None:
        """
        save a parsed GitProfileLens profile
        :param profile: imported profile to persist
        :returns: nothing
        """
        self._write_json("gitprofilelens_profile.json", profile.to_dict())

    def load_feedback(self) -> dict[str, str]:
        """
        load repository feedback
        :returns: mapping from repository name to classification
        """
        return dict(self._read_json("feedback.json", {}))

    def save_feedback(self, feedback: dict[str, str]) -> None:
        """
        save repository feedback
        :param feedback: repository classifications
        :returns: nothing
        """
        self._write_json("feedback.json", feedback)
