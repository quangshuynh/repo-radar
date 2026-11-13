"""repository discovery: search semantics, validation, result actions, and GitHub confirmations"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from repo_radar.storage import Storage

expect = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed").expect


def search(page, query: str = "") -> None:
    """
    run a repository search and wait for ranked results
    :param page: Playwright page
    :param query: search text
    :returns: nothing
    """
    page.goto("/#discover")
    page.locator("#filter-search").fill(query)
    page.locator("#refresh").click()
    expect(page.locator("#discover-results .entry").first).to_be_visible()


def recommendation_requests(page) -> list[dict[str, list[str]]]:
    """
    start recording repository recommendation requests
    :param page: Playwright page
    :returns: list that receives parsed query parameters
    """
    requests: list[dict[str, list[str]]] = []
    page.on(
        "request",
        lambda request: requests.append(parse_qs(urlparse(request.url).query, keep_blank_values=True))
        if "/api/recommendations" in request.url
        else None,
    )
    return requests


def test_search_shows_the_language_it_resolves_to(page, demo_data) -> None:
    """
    the resolved language is visible before searching and matches the server parser
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#discover")
    resolution = page.locator("#search-resolution")
    expect(resolution).to_contain_text("Language: Python")
    expect(resolution).to_contain_text("Python is used when the search names no language")
    expect(resolution).not_to_contain_text("null")
    expect(resolution).not_to_contain_text("Topic terms")
    page.locator("#filter-search").fill("rust web")
    expect(resolution).to_contain_text("Language: Rust")
    expect(resolution).to_contain_text("Named in your search")
    expect(resolution).to_contain_text("Topic terms: web")
    # a common word only names a language in the leading position
    page.locator("#filter-search").fill("libraries that go fast")
    expect(resolution).to_contain_text("Language: Python")


def test_search_sends_the_query_unchanged_and_reports_the_language(page, demo_data) -> None:
    """
    the redesigned controls send the same parameters, so retrieval semantics are unchanged
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    requests = recommendation_requests(page)
    search(page, "typescript cli")

    assert requests == [{"limit": ["10"], "search": ["typescript cli"], "min_stars": ["0"], "hidden_gems": ["false"]}]
    expect(page.locator("#discover-results .results-title")).to_have_text('10 TypeScript repositories for "cli"')
    languages = page.locator("#discover-results .entry .meta li:first-child").all_inner_texts()
    assert set(languages) == {"TypeScript"}
    first = page.locator("#discover-results .entry").first
    expect(first.locator(".score-value")).to_contain_text("%")
    expect(first.locator(".why")).to_contain_text("Strong match for")


def test_invalid_filters_are_explained_and_never_sent(page, demo_data) -> None:
    """
    a maximum below the minimum is reported beside the field and blocks the request
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    requests = recommendation_requests(page)
    page.goto("/#discover")
    page.locator("#filter-min").fill("500")
    page.locator("#filter-max").fill("100")
    page.locator("#refresh").click()
    expect(page.locator("#filter-max-error")).to_have_text("Maximum stars can't be lower than minimum stars.")
    expect(page.locator("#filter-max")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#filter-max")).to_be_focused()
    page.locator("#filter-limit").fill("80")
    page.locator("#filter-max").fill("")
    page.locator("#refresh").click()
    expect(page.locator("#filter-limit-error")).to_have_text("A result count can be at most 50.")
    assert requests == []


def test_a_search_in_flight_cannot_be_submitted_twice(page, demo_data, fixture_github) -> None:
    """
    repeated clicks and Enter while a search runs send exactly one request
    :param page: Playwright page
    :param demo_data: seeded data directory
    :param fixture_github: fake GitHub state
    :returns: nothing
    """
    fixture_github.delays["user"] = 0.8
    requests = recommendation_requests(page)
    page.goto("/#discover")
    page.locator("#refresh").click()
    expect(page.locator("#refresh")).to_be_disabled()
    expect(page.locator("#discover-results .state--loading")).to_be_visible()
    page.locator("#refresh").click(force=True)
    page.locator("#filter-search").press("Enter")
    expect(page.locator("#discover-results .entry").first).to_be_visible()
    assert len(requests) == 1


def test_filters_and_results_survive_a_reload(page, demo_data) -> None:
    """
    search controls persist in this browser, and the latest results come back for the session
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#discover")
    page.locator("#filter-min").fill("100")
    page.locator("#filter-gems").check()
    search(page, "python automation")
    page.reload()
    expect(page.locator("#filter-search")).to_have_value("python automation")
    expect(page.locator("#filter-min")).to_have_value("100")
    expect(page.locator("#filter-gems")).to_be_checked()
    expect(page.locator("#discover-filter-count")).to_have_text("2 active")
    expect(page.locator("#discover-results .entry").first).to_be_visible()
    expect(page.locator("#discover-results .notice")).to_contain_text("from earlier in this session")
    page.goto("/#overview")
    expect(page.locator("#overview-latest .latest-group")).to_have_count(1)


def test_save_updates_the_library_and_can_be_undone(page, demo_data) -> None:
    """
    saving keeps the card, marks it saved, and Undo clears the record again
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    search(page, "typescript cli")
    first = page.locator("#discover-results .entry").first
    name = first.locator(".entry-title a .repo-name").inner_text()
    first.locator(".entry-actions .button", has_text="Save").click()
    expect(first.locator(".entry-actions .button").first).to_have_text("Saved")
    expect(first.locator(".entry-actions .button").first).to_be_disabled()
    expect(page.locator(".toast")).to_contain_text(f"Saved {name} to your Library.")
    assert Storage(demo_data).load_feedback()[name] == "interested"
    expect(page.locator('[data-count="saved"]')).to_have_text("4")

    page.locator(".toast .button", has_text="Undo").click()
    expect(page.locator(".toast").last).to_contain_text(f"Undone for {name}")
    expect(first.locator(".entry-actions .button").first).to_have_text("Save")
    assert name not in Storage(demo_data).load_feedback()


@pytest.mark.parametrize(
    ("label", "classification", "message"),
    [("Not for me", "not interested", "Hid"), ("Block", "blocked", "Blocked")],
)
def test_dismiss_and_block_remove_the_result_and_undo_restores_it(
    page, demo_data, label, classification, message
) -> None:
    """
    a hidden result leaves the list, focus moves on, and Undo puts it back in place
    :param page: Playwright page
    :param demo_data: seeded data directory
    :param label: action button label
    :param classification: stored feedback classification
    :param message: start of the confirmation toast
    :returns: nothing
    """
    search(page, "typescript cli")
    entries = page.locator("#discover-results .entry")
    count = entries.count()
    name = entries.first.locator(".entry-title a .repo-name").inner_text()
    entries.first.locator(".entry-actions .button", has_text=label).click()
    expect(entries).to_have_count(count - 1)
    expect(page.locator(".toast")).to_contain_text(f"{message} {name}")
    assert Storage(demo_data).load_feedback()[name] == classification
    # focus lands on the result that moved into the removed one's place
    expect(entries.first.locator(".entry-title a")).to_be_focused()

    page.locator(".toast .button", has_text="Undo").click()
    expect(entries).to_have_count(count)
    expect(entries.first.locator(".entry-title a .repo-name")).to_have_text(name)
    assert name not in Storage(demo_data).load_feedback()


def test_starring_needs_confirmation_and_cancel_changes_nothing(page, demo_data, fixture_github) -> None:
    """
    a GitHub star is only requested after the dialog is confirmed
    :param page: Playwright page
    :param demo_data: seeded data directory
    :param fixture_github: fake GitHub state
    :returns: nothing
    """
    search(page, "typescript cli")
    first = page.locator("#discover-results .entry").first
    name = first.locator(".entry-title a .repo-name").inner_text()
    star = first.locator(".entry-actions .button", has_text="Star on GitHub")
    star.click()
    dialog = page.locator("#confirm-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#confirm-title")).to_have_text(f"Star {name} on GitHub?")
    expect(dialog.locator("#confirm-body")).to_contain_text("(radar-demo)")
    expect(page.locator("#confirm-cancel")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(star).to_be_focused()
    assert fixture_github.star_requests == []

    star.click()
    page.locator("#confirm-accept").click()
    expect(page.locator(".toast")).to_contain_text(f"Starred {name} on GitHub.")
    assert fixture_github.star_requests == [name]
    assert Storage(demo_data).load_feedback()[name] == "starred"
    expect(page.locator(f'#discover-results .entry:has-text("{name}")')).to_have_count(0)


def test_a_failed_star_never_reports_success(page, demo_data, fixture_github) -> None:
    """
    when GitHub rejects the star, the result says so and no success notification appears
    :param page: Playwright page
    :param demo_data: seeded data directory
    :param fixture_github: fake GitHub state
    :returns: nothing
    """
    fixture_github.failures["star"] = "GitHub rejected the star with status 403"
    search(page, "typescript cli")
    first = page.locator("#discover-results .entry").first
    name = first.locator(".entry-title a .repo-name").inner_text()
    first.locator(".entry-actions .button", has_text="Star on GitHub").click()
    page.locator("#confirm-accept").click()
    expect(first.locator(".entry-status")).to_have_text(
        "GitHub didn't add the star: GitHub rejected the star with status 403"
    )
    expect(first.locator(".entry-actions .button", has_text="Star on GitHub")).to_be_enabled()
    expect(page.locator(".toast--positive")).to_have_count(0)
    assert Storage(demo_data).load_feedback().get(name) is None


def test_search_errors_offer_a_retry(page, demo_data, fixture_github) -> None:
    """
    a failed search shows the server's reason and a retry that works once GitHub recovers
    :param page: Playwright page
    :param demo_data: seeded data directory
    :param fixture_github: fake GitHub state
    :returns: nothing
    """
    fixture_github.failures["user"] = "GitHub API rate limit exceeded"
    page.goto("/#discover")
    page.locator("#refresh").click()
    error = page.locator("#discover-results .state--error")
    expect(error).to_contain_text("The search didn't finish")
    expect(error).to_contain_text("GitHub API rate limit exceeded")
    fixture_github.failures.clear()
    error.locator(".button", has_text="Try again").click()
    expect(page.locator("#discover-results .entry").first).to_be_visible()


def test_an_empty_profile_explains_what_to_add(page, data_dir) -> None:
    """
    with no preference signals, search and overview point to the profile sources
    :param page: Playwright page
    :param data_dir: empty data directory
    :returns: nothing
    """
    page.goto("/")
    expect(page.locator("#overview-setup .setup")).to_contain_text("Give Repo Radar something to go on")
    expect(page.locator("#overview-setup .setup-steps li")).to_have_count(3)
    page.goto("/#discover")
    page.locator("#refresh").click()
    empty = page.locator("#discover-results .state--empty")
    expect(empty).to_contain_text("Your profile has no signals yet")
    expect(empty.locator("a", has_text="Add interests")).to_have_attribute("href", "#preferences")
