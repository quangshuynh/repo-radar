"""navigation, responsive layout, keyboard access, and motion preferences"""

from __future__ import annotations

import pytest
from conftest import DESKTOP, PHONE, TABLET, open_page

expect = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed").expect

ROUTES = {
    "overview": "Overview",
    "discover": "Discover",
    "contribute": "Contribute",
    "library": "Library",
    "preferences": "Preferences",
    "history": "Feedback history",
}


def visible_view(page) -> str:
    """
    name the section currently shown
    :param page: Playwright page
    :returns: view name
    """
    return page.locator("[data-view]:not([hidden])").get_attribute("data-view")


def expect_view(page, route: str) -> None:
    """
    wait until one section is the only one shown
    :param page: Playwright page
    :param route: expected view name
    :returns: nothing
    """
    expect(page.locator(f'[data-view="{route}"]')).to_be_visible()
    expect(page.locator("[data-view]:not([hidden])")).to_have_count(1)


def test_every_section_is_reachable_from_the_sidebar(page, demo_data) -> None:
    """
    each navigation item shows its own section, marks itself current, and titles the page
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/")
    assert visible_view(page) == "overview"
    for route, heading in ROUTES.items():
        page.locator(f'.nav-link[data-route="{route}"]').click()
        expect_view(page, route)
        expect(page.locator(f'.nav-link[data-route="{route}"]')).to_have_attribute("aria-current", "page")
        expect(page.locator(".nav-link[aria-current]")).to_have_count(1)
        expect(page).to_have_title(f"{heading} | Repo Radar")
        # focus moves to the new heading so keyboard and screen reader users land in the section
        expect(page.locator("h1:focus")).to_have_text(heading)


def test_browser_back_returns_to_the_previous_section(page, demo_data) -> None:
    """
    sections are history entries, so back and forward navigation behave predictably
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#discover")
    page.locator('.nav-link[data-route="library"]').click()
    page.locator('.nav-link[data-route="history"]').click()
    expect_view(page, "history")
    page.go_back()
    expect_view(page, "library")
    page.go_back()
    expect_view(page, "discover")
    page.go_forward()
    expect_view(page, "library")


def test_links_from_the_previous_layout_still_resolve(page, demo_data) -> None:
    """
    the old single page anchors open the section that now holds that content
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#starred")
    expect_view(page, "library")
    expect(page.locator("#library-tab-starred")).to_have_attribute("aria-selected", "true")
    page.goto("/#feedback")
    expect_view(page, "history")
    page.goto("/#profile")
    expect_view(page, "preferences")


def test_status_indicators_report_github_and_local_state(page, demo_data) -> None:
    """
    the sidebar states the synced account and that data is local, without exposing a token
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/")
    expect(page.locator("#github-dot")).to_have_attribute("data-tone", "positive")
    expect(page.locator("#github-state")).to_have_text("Synced as radar-demo")
    expect(page.locator("#github-detail")).to_contain_text("8 starred repositories")
    expect(page.locator("#local-state")).to_have_text("Local data stays on this machine")


def test_skip_link_and_slash_shortcut(page, demo_data) -> None:
    """
    the first tab stop skips to content, and slash jumps to the repository search
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip-link"
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "main"
    page.keyboard.press("/")
    expect(page.locator("#filter-search")).to_be_focused()
    expect_view(page, "discover")


@pytest.mark.parametrize("viewport", [DESKTOP, TABLET, PHONE], ids=["desktop", "tablet", "phone"])
def test_no_section_scrolls_horizontally(browser, ui_server, demo_data, viewport) -> None:
    """
    populated sections fit the viewport at desktop, tablet, and 375px phone widths
    :param browser: Playwright browser
    :param ui_server: application URL
    :param demo_data: seeded data directory
    :param viewport: viewport size
    :returns: nothing
    """
    context, page, errors = open_page(browser, ui_server, viewport)
    page.goto("/#discover")
    page.locator("#refresh").click()
    page.wait_for_selector("#discover-results .entry")
    page.goto("/#contribute")
    page.locator("#refresh-contributions").click()
    page.wait_for_selector("#contribute-results .entry")
    for route in ROUTES:
        page.goto(f"/#{route}")
        page.wait_for_timeout(100)
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, f"{route} overflows by {overflow}px at {viewport['width']}px"
    context.close()
    assert errors == []


def test_tablet_uses_a_labelled_icon_rail(browser, ui_server, demo_data) -> None:
    """
    the tablet rail keeps visible section labels and an accessible sync control
    :param browser: Playwright browser
    :param ui_server: application URL
    :param demo_data: seeded data directory
    :returns: nothing
    """
    context, page, errors = open_page(browser, ui_server, TABLET)
    page.goto("/")
    rail = page.locator("#sidebar").bounding_box()
    assert rail["width"] < 100
    assert page.locator('.nav-link[data-route="contribute"]').is_visible()
    assert page.locator('.nav-link[data-route="contribute"] .nav-label').is_visible()
    assert page.locator("#sync").get_attribute("aria-label") == "Sync stars from GitHub"
    context.close()
    assert errors == []


def test_phone_navigation_drawer_traps_and_restores_focus(phone_page, demo_data) -> None:
    """
    the phone drawer opens from the menu button, isolates the page, and closes predictably
    :param phone_page: phone sized Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page = phone_page
    page.goto("/#discover")
    assert (
        not page.locator("#sidebar").is_visible()
        or page.locator("#sidebar").evaluate("element => getComputedStyle(element).visibility") == "hidden"
    )
    toggle = page.locator("#nav-toggle")
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert page.evaluate("document.activeElement.dataset.route") == "discover"
    assert page.evaluate("document.querySelector('#main').inert") is True
    page.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.id") == "nav-toggle"
    toggle.click()
    page.locator('.nav-link[data-route="contribute"]').click()
    expect_view(page, "contribute")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    assert page.evaluate("document.querySelector('#main').inert") is False


def test_phone_controls_are_touch_sized(phone_page, demo_data) -> None:
    """
    buttons and navigation links meet a comfortable touch size on a touch phone
    :param phone_page: phone sized Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page = phone_page
    page.goto("/#discover")
    page.locator("#refresh").click()
    page.wait_for_selector("#discover-results .entry")
    heights = page.eval_on_selector_all(
        "#discover-results .entry-actions .button, #refresh, #nav-toggle",
        "elements => elements.map(element => element.getBoundingClientRect().height)",
    )
    assert heights and min(heights) >= 40


def test_reduced_motion_disables_transitions(browser, ui_server, demo_data) -> None:
    """
    a reduced motion preference zeroes the shared motion tokens
    :param browser: Playwright browser
    :param ui_server: application URL
    :param demo_data: seeded data directory
    :returns: nothing
    """
    context, page, errors = open_page(browser, ui_server, DESKTOP, reduced_motion="reduce")
    page.goto("/")
    duration = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--duration-base').trim()")
    assert duration == "0ms"
    context.close()
    assert errors == []


def test_every_rendered_icon_exists_in_the_sprite(page, demo_data) -> None:
    """
    each icon used by a populated view resolves to a symbol in the bundled sprite
    :param page: Playwright page
    :param demo_data: seeded data directory
    :returns: nothing
    """
    page.goto("/#discover")
    page.locator("#refresh").click()
    expect(page.locator("#discover-results .entry").first).to_be_visible()
    page.goto("/#contribute")
    page.locator("#refresh-contributions").click()
    expect(page.locator("#contribute-results .entry").first).to_be_visible()
    symbols = set(
        page.evaluate(
            """async () => {
              const text = await (await fetch('/static/icons.svg')).text();
              return [...text.matchAll(/<symbol id="([^"]+)"/g)].map((match) => match[1]);
            }"""
        )
    )
    used: set[str] = set()
    for route in ROUTES:
        page.goto(f"/#{route}")
        expect(page.locator(f'[data-view="{route}"]')).to_be_visible()
        used |= set(
            page.eval_on_selector_all(
                "use",
                "uses => uses.map(use => use.getAttribute('href')).filter(href => href.includes('icons.svg#'))",
            )
        )
    names = {href.split("#", 1)[1] for href in used}
    assert names, "no icons were rendered"
    assert names <= symbols, f"icons missing from the sprite: {sorted(names - symbols)}"
