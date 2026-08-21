"""local recommendation feedback"""

from __future__ import annotations

from .models import Repository
from .storage import Storage

VALID_CLASSIFICATIONS = {"interested", "not interested", "starred", "blocked"}


def record_feedback(
    storage: Storage,
    repository: str,
    classification: str,
    repository_data: Repository | None = None,
) -> None:
    """
    record one local feedback classification
    :param storage: local storage manager
    :param repository: repository full name
    :param classification: supported feedback value
    :param repository_data: optional repository metadata for positive feedback
    :returns: nothing
    """
    normalized = classification.lower().replace("-", " ")
    if normalized not in VALID_CLASSIFICATIONS:
        choices = ", ".join(sorted(VALID_CLASSIFICATIONS))
        raise ValueError(f"Invalid classification. Choose one of: {choices}")
    feedback = storage.load_feedback()
    feedback[repository] = normalized
    storage.save_feedback(feedback)
    if normalized == "interested" and repository_data:
        interested = storage.load_interested_repositories()
        repositories = {item.full_name.lower(): item for item in interested}
        repositories[repository_data.full_name.lower()] = repository_data
        storage.save_interested_repositories(list(repositories.values()))
    elif normalized in {"not interested", "starred", "blocked"}:
        interested = storage.load_interested_repositories()
        remaining = [item for item in interested if item.full_name.lower() != repository.lower()]
        if len(remaining) != len(interested):
            storage.save_interested_repositories(remaining)


def reconcile_starred_repositories(storage: Storage, starred: list[Repository]) -> int:
    """
    remove newly starred repositories from the local interested list
    :param storage: local storage manager
    :param starred: repositories returned by GitHub starred synchronization
    :returns: number of interested repositories reconciled
    """
    starred_names = {repository.full_name.lower() for repository in starred}
    interested = storage.load_interested_repositories()
    reconciled = [repository for repository in interested if repository.full_name.lower() in starred_names]
    if not reconciled:
        return 0
    remaining = [repository for repository in interested if repository.full_name.lower() not in starred_names]
    storage.save_interested_repositories(remaining)
    feedback = storage.load_feedback()
    for repository in reconciled:
        feedback[repository.full_name] = "starred"
    storage.save_feedback(feedback)
    return len(reconciled)
