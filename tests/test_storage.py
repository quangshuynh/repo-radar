from repo_radar.models import SeedPreferences
from repo_radar.storage import Storage


def test_seed_preferences_round_trip_through_storage(tmp_path) -> None:
    """
    seed preferences persist in the private data directory
    :param tmp_path: pytest temporary directory
    :returns: nothing
    """
    storage = Storage(tmp_path)
    preferences = SeedPreferences(["Python"], ["automation"], ["cli"])
    storage.save_seed_preferences(preferences)
    assert storage.load_seed_preferences() == preferences
    assert (tmp_path / "seed_preferences.json").exists()
