from repo_radar.discovery import filter_candidates
from repo_radar.feedback import record_feedback
from repo_radar.models import Repository
from repo_radar.storage import Storage


def test_negative_feedback_is_persisted_and_filtered(tmp_path) -> None:
    """
    rejected repositories do not appear in later candidate sets
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    storage = Storage(tmp_path)
    record_feedback(storage, "owner/repo", "not-interested")
    candidates = filter_candidates([Repository("owner/repo", owner="owner")], set(), "me", storage.load_feedback())
    assert candidates == []
