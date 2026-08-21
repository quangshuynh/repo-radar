"""command line interface for Repo Radar"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import datetime, timezone

from .discovery import generate_recommendations
from .feedback import record_feedback
from .github_client import GitHubClient, GitHubError
from .gitprofilelens import GitProfileLensError, import_profile
from .models import ImportedProfile, PreferenceProfile, Recommendation, SeedPreferences
from .profile import build_profile
from .storage import Storage


def build_parser() -> argparse.ArgumentParser:
    """
    create the command line argument parser
    :returns: configured argument parser
    """
    parser = argparse.ArgumentParser(prog="repo-radar", description="Personalized GitHub repository discovery")
    parser.add_argument("--data-dir", default="data", help="local private data directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="set manual seed preferences")
    profile_import = subparsers.add_parser("import-profile", help="import public repositories from GitProfileLens")
    profile_import.add_argument("username", nargs="?", help="GitHub username to import")
    subparsers.add_parser("sync", help="refresh starred repository metadata")
    subparsers.add_parser("profile", help="build and display your preference profile")
    subparsers.add_parser("web", help="start the local web interface")
    recommend = subparsers.add_parser("recommend", help="discover and rank repositories")
    recommend.add_argument("--limit", type=int, default=10, help="number of recommendations")
    feedback = subparsers.add_parser("feedback", help="classify a recommendation locally")
    feedback.add_argument("repository", help="repository in owner/name form")
    feedback.add_argument("classification", help="interested, not-interested, starred, or blocked")
    return parser


def _parse_seed_values(value: str, lowercase: bool = False) -> list[str]:
    """
    parse and deduplicate comma separated preference values
    :param value: comma separated user input
    :param lowercase: whether to normalize values to lowercase
    :returns: cleaned preference values
    """
    values = [item.strip() for item in value.split(",") if item.strip()]
    if lowercase:
        values = [item.lower() for item in values]
    unique: dict[str, str] = {}
    for item in values:
        unique.setdefault(item.lower(), item)
    return list(unique.values())


def run_init(storage: Storage, input_function: Callable[[str], str] = input) -> int:
    """
    interactively replace manual seed preferences
    :param storage: local storage manager
    :param input_function: function used to collect user input
    :returns: process exit code
    """
    print("Repo Radar setup\n")
    print("Preferred languages")
    languages = input_function("Enter comma-separated values:\n> ")
    print("\nPreferred topics")
    topics = input_function("Enter comma-separated values:\n> ")
    print("\nOptional keywords")
    keywords = input_function("Enter comma-separated values:\n> ")
    preferences = SeedPreferences(
        languages=_parse_seed_values(languages),
        topics=_parse_seed_values(topics, lowercase=True),
        keywords=_parse_seed_values(keywords, lowercase=True),
    )
    storage.save_seed_preferences(preferences)
    print("\nSaved seed preferences")
    return 0


def run_import_profile(
    storage: Storage,
    username: str | None,
    input_function: Callable[[str], str] = input,
) -> int:
    """
    import a public repository profile from GitProfileLens
    :param storage: local storage manager
    :param username: optional GitHub username
    :param input_function: function used to collect a missing username
    :returns: process exit code
    """
    selected_username = username or input_function("GitHub username:\n> ")
    profile = import_profile(selected_username, storage)
    pinned_count = sum(repository.pinned for repository in profile.repositories)
    print(f"Imported {len(profile.repositories)} public repositories for {profile.username}")
    print(f"Pinned repositories: {pinned_count}")
    return 0


def _print_signals(title: str, values: dict[str, float], limit: int = 10) -> None:
    """
    print a section of normalized profile signals
    :param title: section title
    :param values: signal names and normalized scores
    :param limit: maximum rows to display
    :returns: nothing
    """
    print(f"\n{title}")
    if not values:
        print("  no signals yet")
        return
    for name, score in list(values.items())[:limit]:
        print(f"{name:<24} {score:.2f}")


def print_profile(profile: PreferenceProfile) -> None:
    """
    display an inferred preference profile
    :param profile: profile to display
    :returns: nothing
    """
    print("Your interests")
    _print_signals("Languages", profile.languages)
    _print_signals("Topics", profile.topics)
    _print_signals("Description keywords", profile.keywords)
    print(f"\nMedian stars             {profile.median_stars:.0f}")


def print_preference_sources(storage: Storage, imported: ImportedProfile | None) -> None:
    """
    display active preference source counts
    :param storage: local storage manager
    :param imported: optional GitProfileLens profile
    :returns: nothing
    """
    seeds = storage.load_seed_preferences()
    print("\nPreference sources\n")
    print(f"Starred repositories        {len(storage.load_repositories())}")
    print(f"GitProfileLens repositories {len(imported.repositories) if imported else 0}")
    print(f"Manual seed preferences     {'yes' if seeds.has_signals() else 'no'}")
    print(f"Feedback records            {len(storage.load_feedback())}")


def print_recommendations(recommendations: list[Recommendation]) -> None:
    """
    display ranked repository recommendations
    :param recommendations: recommendations to display
    :returns: nothing
    """
    if not recommendations:
        print("No eligible recommendations found.")
        return
    for rank, recommendation in enumerate(recommendations, start=1):
        repository = recommendation.repository
        print(f"{rank}. {repository.full_name}")
        print(f"   Score: {recommendation.score:.0%}")
        print(f"   {repository.language or 'Unknown'} | {repository.stars:,} stars")
        print(f"   {repository.description or 'No description provided'}")
        print(f"\n   Why: {recommendation.explanation}\n")
        print(f"   {repository.url}\n")


def run_sync(storage: Storage) -> int:
    """
    refresh the local starred repository cache
    :param storage: local storage manager
    :returns: process exit code
    """
    client = GitHubClient()
    owner = client.get_authenticated_user()
    repositories = client.get_starred_repositories()
    storage.save_repositories(repositories)
    storage.save_status({"authenticated_user": owner, "last_sync": datetime.now(timezone.utc).isoformat()})
    print(f"Cached {len(repositories)} starred repositories for {owner}")
    return 0


def run_profile(storage: Storage) -> int:
    """
    build and display the local preference profile
    :param storage: local storage manager
    :returns: process exit code
    """
    repositories = storage.load_repositories()
    imported = storage.load_imported_profile()
    profile = build_profile(repositories, storage.load_seed_preferences(), imported)
    storage.save_profile(profile)
    print_profile(profile)
    print_preference_sources(storage, imported)
    return 0


def run_recommend(storage: Storage, limit: int) -> int:
    """
    discover rank and display repository recommendations
    :param storage: local storage manager
    :param limit: maximum recommendations
    :returns: process exit code
    """
    starred = storage.load_repositories()
    imported = storage.load_imported_profile()
    profile = build_profile(starred, storage.load_seed_preferences(), imported)
    storage.save_profile(profile)
    if not profile.languages and not profile.topics and not profile.keywords:
        print("No preference signals are available yet.")
        print("Run `python -m repo_radar init`, import a public profile, or star some repositories and run sync.")
        return 0
    client = GitHubClient()
    owner = client.get_authenticated_user()
    recommendations = generate_recommendations(
        client, profile, starred, owner, storage.load_feedback(), limit, imported
    )
    print_recommendations(recommendations)
    return 0


def run_web() -> int:
    """
    start the local Repo Radar web interface
    :returns: process exit code
    """
    import uvicorn

    uvicorn.run("repo_radar.web:app", host="127.0.0.1", port=8000)
    return 0


def main(arguments: list[str] | None = None) -> int:
    """
    execute the Repo Radar command line interface
    :param arguments: optional command line arguments
    :returns: process exit code
    """
    parsed = build_parser().parse_args(arguments)
    storage = Storage(parsed.data_dir)
    try:
        if parsed.command == "init":
            return run_init(storage)
        if parsed.command == "import-profile":
            return run_import_profile(storage, parsed.username)
        if parsed.command == "sync":
            return run_sync(storage)
        if parsed.command == "profile":
            return run_profile(storage)
        if parsed.command == "recommend":
            return run_recommend(storage, parsed.limit)
        if parsed.command == "web":
            return run_web()
        record_feedback(storage, parsed.repository, parsed.classification)
        print(f"Recorded {parsed.classification.replace('-', ' ')} for {parsed.repository}")
        return 0
    except (GitHubError, GitProfileLensError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
