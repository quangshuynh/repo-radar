"""capture README screenshots of the running interface

    python tests/ui/capture_screenshots.py

The real application runs through the UI test harness: the production frontend, API, and
ranking pipeline, with GitHub answered from the frozen public snapshots under `evaluation/`.
No token, network access, or personal `data/` directory is used, so the images never show
private data. Output goes to docs/screenshots/, and docs/screenshot.png is the README hero.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness import FixtureGitHub, seed_demo_data, serve  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "screenshots"


def populate(page) -> None:
    """
    run one repository search and one issue search so every view has real results
    :param page: Playwright page
    :returns: nothing
    """
    page.goto("/#discover")
    page.locator("#filter-search").fill("typescript cli")
    page.locator("#refresh").click()
    expect(page.locator("#discover-results .entry").first).to_be_visible()
    page.goto("/#contribute")
    page.locator("#refresh-contributions").click()
    expect(page.locator("#contribute-results .entry").first).to_be_visible()


def show(page, route: str) -> None:
    """
    open one section at the top of the page with focus rings cleared
    :param page: Playwright page
    :param route: section name
    :returns: nothing
    """
    page.goto(f"/#{route}")
    expect(page.locator(f'[data-view="{route}"]')).to_be_visible()
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(250)


def capture(browser, base_url: str, name: str, viewport: dict[str, int], shots, scale: int = 1) -> None:
    """
    capture a set of sections at one viewport size
    :param browser: Playwright browser
    :param base_url: application URL
    :param name: viewport name used in file names
    :param viewport: viewport size
    :param shots: pairs of section name and optional preparation callback
    :param scale: device pixel ratio
    :returns: nothing
    """
    context = browser.new_context(viewport=viewport, base_url=base_url, device_scale_factor=scale)
    page = context.new_page()
    populate(page)
    for route, prepare in shots:
        show(page, route)
        if prepare:
            prepare(page)
        page.screenshot(path=str(OUTPUT / f"{route}-{name}.png"))
    context.close()


def scroll_to_results(page) -> None:
    """
    scroll so the issue results fill the screenshot
    :param page: Playwright page
    :returns: nothing
    """
    top = page.locator("#contribute-results").evaluate(
        "element => element.getBoundingClientRect().top + window.scrollY"
    )
    page.evaluate(f"window.scrollTo(0, {top} - 16)")
    page.wait_for_timeout(150)


def open_drawer(page) -> None:
    """
    open the phone navigation drawer
    :param page: Playwright page
    :returns: nothing
    """
    page.locator("#nav-toggle").click()
    page.wait_for_timeout(300)
    page.evaluate("document.activeElement && document.activeElement.blur()")


def main() -> int:
    """
    write every screenshot
    :returns: process exit status
    """
    OUTPUT.mkdir(parents=True, exist_ok=True)
    github = FixtureGitHub()
    with tempfile.TemporaryDirectory() as directory:
        seed_demo_data(Path(directory), github)
        with serve(github, Path(directory)) as base_url, sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            desktop = [
                ("overview", None),
                ("discover", None),
                ("contribute", scroll_to_results),
                ("library", None),
                ("preferences", None),
                ("history", None),
            ]
            capture(browser, base_url, "desktop", {"width": 1440, "height": 900}, desktop)
            capture(
                browser, base_url, "tablet", {"width": 820, "height": 1180}, [("discover", None), ("contribute", None)]
            )
            capture(
                browser,
                base_url,
                "phone",
                {"width": 375, "height": 812},
                [("discover", None), ("contribute", scroll_to_results), ("overview", open_drawer)],
                scale=2,
            )
            browser.close()
    (OUTPUT / "overview-phone.png").replace(OUTPUT / "navigation-phone.png")
    shutil.copyfile(OUTPUT / "discover-desktop.png", ROOT / "docs" / "screenshot.png")
    print(f"Wrote screenshots to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
