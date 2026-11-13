"""contribution discovery: scopes, filters, evidence display, and degraded results"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

expect = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed").expect


def contribution_requests(page) -> list[dict[str, list[str]]]:
    """
    start recording contribution requests
    :param page: Playwright page
    :returns: list that receives parsed query parameters
    """
    requests: list[dict[str, list[str]]] = []
    page.on(
        "request",
        lambda request: requests.append(parse_qs(urlparse(request.url).query))
        if "/api/contributions" in request.url
        else None,
    )
    return requests


def find_issues(page) -> None:
    """
    run the issue search and wait for ranked results
    :param page: Playwright page
    :returns: nothing
    """
    page.locator("#refresh-contributions").click()
    expect(page.locator("#contribute-results .entry").first).to_be_visible()


def test_default_search_covers_github_and_explains_every_result(page, demo_data) -> None:
    """
    each result separates repository from issue facts and shows grouped scoring evidence
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    requests = contribution_requests(page)
    page.goto("/#contribute")
    find_issues(page)

    assert requests == [
        {"limit": ["10"], "unassigned_only": ["false"], "scope": ["discover"], "contributor_friendly": ["false"]}
    ]
    expect(page.locator("#contribute-results .results-title")).to_have_text("10 open issues")
    first = page.locator("#contribute-results .entry").first
    expect(first.locator(".entry-repo .repo-name")).to_be_visible()
    expect(first.locator(".entry-repo .badge")).to_have_text(["Starred"])
    expect(first.locator(".entry-title a")).to_have_attribute("href", "https://github.com/apache/airflow/issues/34048")
    expect(first.locator(".entry-title a")).to_have_attribute("target", "_blank")
    headings = first.locator(".ledger dt").all_inner_texts()
    assert headings[0] == "Repository fit"
    assert "Scope readiness" in headings
    expect(first.locator(".ledger")).to_contain_text("Label: good first issue")
    # no difficulty or effort claims, which the ranking does not measure
    text = page.locator("#contribute-results").inner_text().lower()
    assert "difficulty:" not in text and "estimated" not in text
    new_badges = page.locator("#contribute-results .entry-repo .badge", has_text="New to you")
    assert new_badges.count() > 0


def test_assigned_issues_carry_a_caution_row(page, demo_data) -> None:
    """
    an assigned issue states that it may already be in progress
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#contribute")
    find_issues(page)
    caution = page.locator("#contribute-results .ledger-row--caution")
    expect(caution.first).to_contain_text("Assigned to")
    expect(caution.first).to_contain_text("may already be in progress")


def test_filters_are_sent_echoed_and_reset(page, demo_data) -> None:
    """
    scope, issue types, and toggles reach the API, the result header restates what was applied
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    requests = contribution_requests(page)
    page.goto("/#contribute")
    page.locator("label.segment", has_text="Saved and starred").click()
    page.locator("label.chip-option", has_text="Bug").click()
    page.locator("label.chip-option", has_text="Documentation").click()
    page.locator("#contribution-friendly").check()
    expect(page.locator("#contribution-filter-summary")).to_have_text(
        "Saved and starred repositories, Bug or Documentation, contributor-friendly only"
    )
    page.locator("#refresh-contributions").click()
    expect(page.locator("#contribute-results .results-meta")).to_contain_text(
        "Saved and starred repositories, Bug or Documentation, contributor-friendly only"
    )
    assert requests[0]["scope"] == ["saved_starred"]
    assert requests[0]["label"] == ["bug", "documentation"]
    assert requests[0]["contributor_friendly"] == ["true"]

    page.locator("#contribute-reset").click()
    expect(page.locator('input[name="contribution-scope"][value="discover"]')).to_be_checked()
    expect(page.locator('input[name="contribution-label"]:checked')).to_have_count(0)
    expect(page.locator("#contribution-friendly")).not_to_be_checked()
    expect(page.locator("#contribute-results .notice")).to_contain_text("Your filters changed since this search")


def test_filters_persist_across_a_reload(page, demo_data) -> None:
    """
    the chosen scope and filters are remembered by this browser
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#contribute")
    page.locator("label.segment", has_text="Saved and starred").click()
    page.locator("label.chip-option", has_text="Accessibility").click()
    page.locator("#contribution-unassigned").check()
    page.reload()
    expect(page.locator('input[name="contribution-scope"][value="saved_starred"]')).to_be_checked()
    expect(page.locator('input[name="contribution-label"][value="accessibility"]')).to_be_checked()
    expect(page.locator("#contribution-unassigned")).to_be_checked()


def test_issue_type_options_are_keyboard_operable(page, demo_data) -> None:
    """
    the styled scope and issue type controls stay native radios and checkboxes
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#contribute")
    page.locator('input[name="contribution-label"][value="bug"]').focus()
    page.keyboard.press("Space")
    expect(page.locator('input[name="contribution-label"][value="bug"]')).to_be_checked()
    page.locator('input[name="contribution-scope"][value="discover"]').focus()
    page.keyboard.press("ArrowDown")
    expect(page.locator('input[name="contribution-scope"][value="saved_starred"]')).to_be_checked()


def test_saved_scope_without_a_library_says_what_to_do(page, data_dir) -> None:
    """
    the saved and starred scope explains that it needs saved or synced repositories
    :param page: Playwright page
    :param data_dir: empty data directory
    :returns: nothing
    """
    page.goto("/#contribute")
    page.locator("label.segment", has_text="Saved and starred").click()
    page.locator("#refresh-contributions").click()
    expect(page.locator("#contribute-results .state--empty")).to_contain_text(
        "Save a repository or sync your GitHub stars to find contribution opportunities"
    )


def test_issue_search_failure_is_reported_with_a_retry(page, demo_data, fixture_github) -> None:
    """
    a failed issue search reports why and recovers through Try again
    :param page: Playwright page
    :param demo_data: seeded data directory
    :param fixture_github: fake GitHub state
    :returns: nothing
    """
    fixture_github.failures["user"] = "Bad credentials"
    page.goto("/#contribute")
    page.locator("#refresh-contributions").click()
    error = page.locator("#contribute-results .state--error")
    expect(error).to_contain_text("Bad credentials")
    fixture_github.failures.clear()
    error.locator(".button", has_text="Try again").click()
    expect(page.locator("#contribute-results .entry").first).to_be_visible()
