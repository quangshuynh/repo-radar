"""browser fixtures for the local interface tests

These tests drive the real application in Chromium through Playwright. They are skipped when
Playwright or its Chromium build is unavailable, unless REPO_RADAR_REQUIRE_UI_TESTS=1 is set,
which CI uses so a missing browser fails loudly instead of silently skipping.

Install locally with:

    python -m pip install playwright
    python -m playwright install chromium
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

REQUIRE_UI_TESTS = os.environ.get("REPO_RADAR_REQUIRE_UI_TESTS") == "1"

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    if REQUIRE_UI_TESTS:
        raise
    PlaywrightError = RuntimeError
    sync_playwright = None

from harness import FixtureGitHub, seed_demo_data, serve  # noqa: E402

DESKTOP = {"width": 1440, "height": 900}
TABLET = {"width": 820, "height": 1180}
PHONE = {"width": 375, "height": 812}


@pytest.fixture(scope="session")
def fixture_github() -> FixtureGitHub:
    """
    load the frozen GitHub fixtures once
    :returns: shared fixture GitHub state
    """
    return FixtureGitHub()


@pytest.fixture(scope="module")
def ui_server(fixture_github: FixtureGitHub, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """
    run the application for one test module; the GitHub boundary is restored afterwards
    :param fixture_github: shared fixture GitHub state
    :param tmp_path_factory: pytest temporary directory factory
    :returns: base URL of the running server
    """
    with serve(fixture_github, tmp_path_factory.mktemp("server")) as base_url:
        yield base_url


@pytest.fixture(scope="session")
def browser() -> Iterator[object]:
    """
    launch headless Chromium once per session
    :returns: Playwright browser
    """
    if sync_playwright is None:
        pytest.skip("Playwright is not installed")
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except PlaywrightError as error:
            if REQUIRE_UI_TESTS:
                raise
            pytest.skip(f"Chromium for Playwright is not installed ({error.message.splitlines()[0]})")
        yield launched
        launched.close()


@pytest.fixture
def data_dir(tmp_path: Path, fixture_github: FixtureGitHub, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    point the running server at an empty data directory and reset the fake GitHub
    :param tmp_path: pytest temporary directory
    :param fixture_github: shared fixture GitHub state
    :param monkeypatch: pytest monkeypatch fixture
    :returns: data directory
    """
    fixture_github.reset()
    monkeypatch.setenv("REPO_RADAR_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def demo_data(data_dir: Path, fixture_github: FixtureGitHub) -> Path:
    """
    seed the data directory with the demo profile
    :param data_dir: empty data directory
    :param fixture_github: shared fixture GitHub state
    :returns: seeded data directory
    """
    seed_demo_data(data_dir, fixture_github)
    return data_dir


def open_page(
    browser: object, base_url: str, viewport: dict[str, int], **options: object
) -> tuple[object, object, list]:
    """
    open a page in a fresh context and record script errors
    :param browser: Playwright browser
    :param base_url: application URL
    :param viewport: viewport size
    :param options: extra browser context options
    :returns: context, page, and the list collecting script errors
    """
    context = browser.new_context(viewport=viewport, base_url=base_url, **options)
    page = context.new_page()
    errors: list[str] = []

    def record_console(message: object) -> None:
        """
        keep console errors except failed API responses, which tests assert through the interface
        :param message: Playwright console message
        :returns: nothing
        """
        if message.type == "error" and "/api/" not in (message.location or {}).get("url", ""):
            errors.append(message.text)

    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", record_console)
    return context, page, errors


@pytest.fixture
def page(browser: object, ui_server: str, data_dir: Path) -> Iterator[object]:
    """
    open a desktop page and fail the test on any script or console error
    :param browser: Playwright browser
    :param ui_server: application URL
    :param data_dir: isolated data directory, so every page starts from known local data
    :returns: Playwright page
    """
    context, opened, errors = open_page(browser, ui_server, DESKTOP)
    yield opened
    context.close()
    assert errors == [], f"The page logged errors: {errors}"


@pytest.fixture
def phone_page(browser: object, ui_server: str, data_dir: Path) -> Iterator[object]:
    """
    open a phone sized page with touch input
    :param browser: Playwright browser
    :param ui_server: application URL
    :param data_dir: isolated data directory
    :returns: Playwright page
    """
    context, opened, errors = open_page(browser, ui_server, PHONE, has_touch=True, is_mobile=True)
    yield opened
    context.close()
    assert errors == [], f"The page logged errors: {errors}"
