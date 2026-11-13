"""library collections, feedback history, preferences, and the overview"""

from __future__ import annotations

import pytest

from repo_radar.models import SeedPreferences
from repo_radar.storage import Storage

expect = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed").expect


def test_overview_reports_only_local_counts(page, demo_data) -> None:
    """
    overview numbers are the counts the local API reports
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/")
    stats = page.locator("#overview-stats .stat")
    expect(stats).to_have_count(5)
    values = dict(
        zip(stats.locator("dt").all_inner_texts(), stats.locator(".stat-value").all_inner_texts(), strict=True)
    )
    assert values == {
        "Saved repositories": "3",
        "Starred repositories": "8",
        "Imported repositories": "4",
        "Manual interests": "10",
        "Feedback records": "14",
    }
    expect(page.locator("#overview-latest")).to_contain_text("No searches yet in this session")


def test_library_tabs_sort_filter_and_expand(page, demo_data) -> None:
    """
    saved and starred collections switch by tab and support sorting, filtering, and See more
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#library")
    expect(page.locator('[data-count="saved"]')).to_have_text("3")
    expect(page.locator("#interested-repositories .entry")).to_have_count(3)
    expect(page.locator("#interested-repositories .influence").first).to_contain_text("at weight 0.70")

    page.locator("#library-tab-saved").focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#library-tab-starred")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#library-tab-starred")).to_be_focused()
    starred = page.locator("#starred-repositories .entry")
    expect(starred).to_have_count(6)
    toggle = page.locator("#starred-repositories [data-collection-toggle]")
    expect(toggle).to_have_text("See more (2)")
    toggle.click()
    expect(starred).to_have_count(8)
    expect(page.locator("#starred-repositories [data-collection-toggle]")).to_have_text("See less")
    expect(page.locator("#starred-repositories [data-collection-toggle]")).to_be_focused()

    page.locator("#library-sort").select_option("stars")
    stars = [
        int(text.replace(",", ""))
        for text in page.locator("#starred-repositories .meta li[title$='stars']").evaluate_all(
            "items => items.map(item => item.title.split(' ')[0])"
        )
    ]
    assert stars == sorted(stars, reverse=True)

    page.locator("#library-filter").fill("sentry")
    expect(starred).to_have_count(1)
    expect(starred.first).to_contain_text("getsentry/sentry")
    page.locator("#library-filter").fill("no-such-repository")
    expect(page.locator("#starred-repositories .state")).to_contain_text("No repositories match that filter")


def test_removing_a_saved_repository_needs_confirmation(page, demo_data) -> None:
    """
    removal asks first, cancel keeps the repository, and confirm removes only local data
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#library")
    entry = page.locator("#interested-repositories .entry", has_text="josh-project/josh")
    entry.locator(".button", has_text="Remove").click()
    expect(page.locator("#confirm-title")).to_have_text("Remove josh-project/josh from Saved?")
    page.locator("#confirm-cancel").click()
    expect(page.locator("#interested-repositories .entry")).to_have_count(3)

    entry.locator(".button", has_text="Remove").click()
    page.locator("#confirm-accept").click()
    expect(page.locator("#interested-repositories .entry")).to_have_count(2)
    assert "josh-project/josh" not in {
        repository.full_name for repository in Storage(demo_data).load_interested_repositories()
    }


def test_bulk_star_lists_every_repository_before_starring(page, demo_data, fixture_github) -> None:
    """
    the bulk star confirmation names each repository, and success appears after GitHub confirms
    :param page: Playwright page
    :param demo_data: seeded data directory
    :param fixture_github: fake GitHub state
    :returns: nothing
    """
    page.goto("/#library")
    page.locator("#star-all-saved").click()
    expect(page.locator("#confirm-title")).to_have_text("Star 3 saved repositories on GitHub?")
    expect(page.locator("#confirm-details li")).to_have_count(3)
    expect(page.locator("#confirm-accept")).to_have_text("Star 3 repositories")
    page.locator("#confirm-cancel").click()
    assert fixture_github.star_requests == []

    page.locator("#star-all-saved").click()
    page.locator("#confirm-accept").click()
    expect(page.locator(".toast")).to_contain_text("Starred 3 repositories on GitHub.")
    assert sorted(fixture_github.star_requests) == ["Skyvern-AI/skyvern", "josh-project/josh", "vadimdemedes/ink"]
    expect(page.locator('[data-count="saved"]')).to_have_text("0")
    expect(page.locator("#star-all-saved")).to_be_disabled()


def test_remove_all_is_confirmed_as_destructive(page, demo_data) -> None:
    """
    clearing Saved uses the destructive confirmation style and empties the list
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#library")
    page.locator("#clear-saved").click()
    expect(page.locator("#confirm-accept")).to_have_class("button button--danger")
    expect(page.locator("#confirm-body")).to_contain_text("This can't be undone.")
    page.locator("#confirm-accept").click()
    expect(page.locator("#interested-repositories .state")).to_contain_text("Nothing saved yet")


def test_history_filters_and_explains_each_classification(page, demo_data) -> None:
    """
    history groups records by classification with counts and a plain explanation of each
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#history")
    groups = page.locator("#feedback-records > section")
    expect(groups).to_have_count(4)
    expect(page.locator("#history-filter .segment", has_text="Blocked")).to_contain_text("1")
    page.locator("#history-filter .segment", has_text="Blocked").click()
    expect(groups).to_have_count(1)
    expect(groups.first).to_contain_text("Left out of repository and contribution discovery")
    expect(groups.first.locator(".history-row")).to_have_count(1)


def test_clearing_a_starred_record_never_touches_github(page, demo_data, fixture_github) -> None:
    """
    clearing a starred record says the GitHub star stays, and no GitHub mutation happens
    :param page: Playwright page
    :param demo_data: seeded data directory
    :param fixture_github: fake GitHub state
    :returns: nothing
    """
    page.goto("/#history")
    row = page.locator(".history-row", has_text="getsentry/sentry")
    row.locator(".button", has_text="Clear").click()
    expect(page.locator("#confirm-body")).to_contain_text("The repository stays starred on GitHub")
    page.locator("#confirm-accept").click()
    expect(page.locator(".toast")).to_contain_text("Cleared the starred record for getsentry/sentry.")
    assert "getsentry/sentry" not in Storage(demo_data).load_feedback()
    assert fixture_github.star_requests == []
    assert "getsentry/sentry" in fixture_github.starred
    assert "getsentry/sentry" in {repository.full_name for repository in Storage(demo_data).load_repositories()}


def test_preferences_save_and_report_unsaved_changes(page, demo_data) -> None:
    """
    manual interests keep their comma separated semantics and persistence
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#preferences")
    expect(page.locator("#languages")).to_have_value("Python, TypeScript")
    page.locator("#topics").fill("Developer-Tools, testing")
    expect(page.locator("#preferences-state")).to_have_text("Unsaved changes")
    page.locator("#save-preferences").click()
    expect(page.locator(".toast")).to_contain_text("Interests saved")
    expect(page.locator("#preferences-state")).to_have_text("")
    assert Storage(demo_data).load_seed_preferences() == SeedPreferences(
        ["Python", "TypeScript"], ["developer-tools", "testing"], ["terminal", "browser", "api"]
    )
    weights = page.locator("#source-weights tr")
    expect(weights).to_have_count(5)
    expect(weights.first).to_contain_text("Starred repository")
    expect(weights.first).to_contain_text("1.00")


def test_profile_import_validates_and_reports_the_result(page, demo_data) -> None:
    """
    an invalid username is caught before a request, and a valid import reports its counts
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#preferences")
    page.locator("#import-username").fill("not a user")
    page.locator("#import-submit").click()
    expect(page.locator("#import-username-error")).to_contain_text("GitHub usernames use letters, numbers")
    page.locator("#import-username").fill("octo-dev")
    page.locator("#import-submit").click()
    expect(page.locator("#import-message")).to_have_text("Imported 2 repositories: 1 pinned, 2 languages, 3 topics.")
    expect(page.locator("#import-current")).to_contain_text("octo-dev, 2 repositories")
