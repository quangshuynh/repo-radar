"""local recommendation feedback"""

from __future__ import annotations

from .storage import Storage

VALID_CLASSIFICATIONS = {"interested", "not interested", "starred", "blocked"}


def record_feedback(storage: Storage, repository: str, classification: str) -> None:
    """
    record one local feedback classification
    :param storage: local storage manager
    :param repository: repository full name
    :param classification: supported feedback value
    :returns: nothing
    """
    normalized = classification.lower().replace("-", " ")
    if normalized not in VALID_CLASSIFICATIONS:
        choices = ", ".join(sorted(VALID_CLASSIFICATIONS))
        raise ValueError(f"Invalid classification. Choose one of: {choices}")
    feedback = storage.load_feedback()
    feedback[repository] = normalized
    storage.save_feedback(feedback)
