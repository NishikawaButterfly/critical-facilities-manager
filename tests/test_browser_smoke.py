"""The three web views, executed by a real browser.

Every other frontend check in this suite reads the files rather than running
them. :mod:`tests.test_web` labels them so a browser will execute them, parses
each module with ``node --check``, and pins the board's action table to the
API's own state machine -- but nothing there evaluates a single line of the
page. Two defects walked straight through all of it and were caught by hand:
the board referenced an identifier it never declared and threw on its first
paint, and the board's mount carried the view's own grid class, so the header
and the columns were laid out beside each other instead of stacked.

This module opens the page automatically. A seeded demo database is served by
the real application over a real socket, Chromium loads the real pages, and
anything the browser reports -- an uncaught exception, a console error -- fails
the test. Console errors include the browser's own note about a request that
came back with an error status, so a view that asks for a URL the server does
not serve fails here too.

On top of that, each view is held to the figures the API returned, and that is
what catches the first defect: a view that throws is caught by the shell and
painted as an error message, so nothing reaches the browser as an uncaught
exception and only the missing content gives it away.

What this does not do is judge appearance. It asserts that the board's columns
are laid out as columns and that the counts on screen are the API's own; it
says nothing about spacing, colour, or whether the result looks good.

The tests are marked ``browser`` and left out of the default run (see
``addopts`` in ``pyproject.toml``): they need the Chromium that
``playwright install`` downloads, which is not something a plain
``pip install -e ".[dev]"`` should imply. Run them with ``pytest -m browser``.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from cfm.config import Settings
from cfm.demo import SEED_ENGINEER, SEED_VIEWER, seed_demo
from cfm.main import create_app
from cfm.web import WEB_DIR

REQUIRE_BROWSER = os.environ.get("CFM_REQUIRE_BROWSER") == "1"
"""Set by the one CI job that exists to run this module.

Skipping is the right answer for a contributor who never installed the extra.
It is the wrong answer in a job whose only purpose is this check, where a skip
would report success without having run anything, so there the missing import
is raised instead.
"""

try:
    from playwright.sync_api import Browser, ConsoleMessage, Error, Locator, Page, sync_playwright
except ImportError:  # pragma: no cover - the browser extra is optional
    if REQUIRE_BROWSER:
        raise
    pytest.skip(
        "playwright is not installed; install the 'browser' extra to run the browser smoke",
        allow_module_level=True,
    )

pytestmark = pytest.mark.browser

TOKEN_KEY = "cfm.api-token"  # noqa: S105 - the storage key, not a secret
"""Where ``api.js`` keeps the token; the coupling is asserted below."""

VIEW_ROOTS = {"tree": "tree-root", "board": "board-root", "audit": "audit-root"}
"""The element each view paints into, keyed by its route."""

VIEW_LANDMARKS = {
    "tree": "#tree-root details > summary",
    "board": "#board-root .board-root > section.column",
    "audit": "#audit-root .timeline > li.entry",
}
"""Something only a view that actually rendered its data can produce."""

DRAFT_ORDER = "PDU thermal scan"
"""A seeded order left in ``draft``, so ``Schedule`` is offered and needs no note."""

VIEWPORT = {"width": 1280, "height": 900}
"""Wide enough for the board's five columns to sit side by side."""

STARTUP_TIMEOUT = 30.0
"""Seconds to wait for the test server to come up, and to go back down."""

RENDER_TIMEOUT_MS = 15_000
"""Milliseconds a view gets to paint before the test gives up on it."""


# --------------------------------------------------------------------------
# A real server, on a real port
# --------------------------------------------------------------------------


@contextmanager
def serve(application: FastAPI) -> Iterator[str]:
    """Serve ``application`` on a loopback port for the life of the block.

    The listening socket is bound here and handed to uvicorn already bound, so
    nothing can take the port between choosing it and serving on it. uvicorn
    runs in a daemon thread; it declines to install signal handlers off the
    main thread, which is exactly what is wanted here.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(application, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("the test server thread died before it started serving")
        if time.monotonic() > deadline:
            raise RuntimeError(f"the test server did not start within {STARTUP_TIMEOUT}s")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=STARTUP_TIMEOUT)
        listener.close()


@dataclass(frozen=True)
class Demo:
    """A served demo installation: where it is, who may talk to it, what is in it."""

    base_url: str
    tokens: dict[str, str]
    counts: dict[str, int]


@pytest.fixture(scope="session")
def chromium() -> Iterator[Browser]:
    """One browser for the whole module; each test still gets a clean context."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def demo(tmp_path: Path) -> Iterator[Demo]:
    """A freshly seeded demo database, served by the real application.

    ``seed_demo`` hands back the plaintext token secrets it minted -- the only
    time they exist outside a hash -- which is how a test reaches the seeded
    API as a named crew member without inventing fixtures of its own.
    """
    database_url = f"sqlite:///{(tmp_path / 'browser-smoke.db').as_posix()}"
    seeded = seed_demo(database_url)
    application = create_app(
        Settings(
            database_url=database_url,
            docs_enabled=False,
            evidence_dir=tmp_path / "evidence",
        )
    )
    with serve(application) as base_url:
        yield Demo(base_url=base_url, tokens=seeded.tokens, counts=seeded.counts)


# --------------------------------------------------------------------------
# A page, and everything the browser said about it
# --------------------------------------------------------------------------


@dataclass
class Screen:
    """A loaded page together with every complaint the browser made about it."""

    page: Page
    uncaught: list[str] = field(default_factory=list)
    logged: list[str] = field(default_factory=list)

    def assert_browser_stayed_quiet(self) -> None:
        """No uncaught exception and nothing logged as an error. The whole point."""
        assert self.uncaught == [], f"the page threw: {'; '.join(self.uncaught)}"
        assert self.logged == [], f"the page logged errors: {'; '.join(self.logged)}"


def wait_for_view(page: Page, view: str) -> None:
    """Block until the view has replaced its ``Loading...`` placeholder.

    ``aria-busy`` alone is not a signal: it reads ``false`` in the markup
    before the first render starts. The pair -- not busy, and something
    painted -- is only true once a render has finished, whether it finished
    with content or with an error message.
    """
    page.wait_for_function(
        """(rootId) => {
             const root = document.getElementById(rootId);
             return root !== null
               && root.getAttribute("aria-busy") === "false"
               && root.childElementCount > 0;
           }""",
        arg=VIEW_ROOTS[view],
        timeout=RENDER_TIMEOUT_MS,
    )


@contextmanager
def open_page(
    browser: Browser, demo: Demo, *, view: str, token: str | None = None
) -> Iterator[Screen]:
    """Load one view in a clean browser context, watching for browser complaints.

    ``token`` is written to storage before the first document runs, so the
    page's first paint is the authenticated one -- the path a returning
    operator takes, and the one both known defects appeared on. Pass ``None``
    to load the page with no token at all.
    """
    context = browser.new_context(viewport=VIEWPORT)
    # A missing element means the page is broken, not that the machine is
    # slow: fail on the same clock the renders are given rather than on
    # Playwright's much longer default.
    context.set_default_timeout(RENDER_TIMEOUT_MS)
    try:
        if token is not None:
            context.add_init_script(
                f"window.localStorage.setItem({json.dumps(TOKEN_KEY)}, {json.dumps(token)});"
            )
        page = context.new_page()
        screen = Screen(page=page)

        def record_uncaught(error: Error) -> None:
            screen.uncaught.append(f"{error.name}: {error.message}")

        def record_console(message: ConsoleMessage) -> None:
            if message.type == "error":
                screen.logged.append(message.text)

        page.on("pageerror", record_uncaught)
        page.on("console", record_console)
        page.goto(f"{demo.base_url}/ui/#/{view}", wait_until="load")
        wait_for_view(page, view)
        yield screen
    finally:
        context.close()


def column(page: Page, status: str) -> Locator:
    """The board column for one workflow status, found by its heading."""
    return page.locator("section.column").filter(has=page.locator(f"#column-{status}"))


def api_summary(view: str, counts: dict[str, int]) -> str:
    """The sentence each view prints from the API's own totals."""
    return {
        "tree": f"{counts['locations']} locations and {counts['assets']} assets",
        "board": f"{counts['maintenance_orders']} maintenance orders",
        "audit": f"of {counts['audit_entries']} entries",
    }[view]


# --------------------------------------------------------------------------
# The views
# --------------------------------------------------------------------------


def test_the_token_key_seeded_here_is_the_one_the_page_reads() -> None:
    """This module writes the token straight into storage; keep that honest.

    If ``api.js`` ever renames the key, every test below would load an
    unauthenticated page and assert against an empty one. Fail here instead.
    """
    source = (WEB_DIR / "api.js").read_text(encoding="utf-8")
    assert f'const TOKEN_KEY = "{TOKEN_KEY}"' in source


@pytest.mark.parametrize("view", ["tree", "board", "audit"])
def test_each_view_paints_with_the_browser_reporting_nothing(
    chromium: Browser, demo: Demo, view: str
) -> None:
    """Load each view with an engineer's token and hold it to three things.

    Nothing thrown, nothing logged, and the API's own figures on screen. The
    last one carries the weight: a view that throws is caught by the shell and
    painted as an error message, so the browser stays silent and only the
    missing content gives it away.
    """
    with open_page(chromium, demo, view=view, token=demo.tokens[SEED_ENGINEER]) as screen:
        screen.assert_browser_stayed_quiet()
        root = screen.page.locator(f"#{VIEW_ROOTS[view]}")
        painted = root.inner_text()
        assert root.locator(".level-error").count() == 0, painted
        assert api_summary(view, demo.counts) in painted
        assert screen.page.locator(VIEW_LANDMARKS[view]).count() > 0, painted
        # /me answered, so the page knows whose token this is.
        assert "role engineer" in screen.page.locator("#identity").inner_text()


def test_a_viewer_is_offered_no_actions_and_told_why(chromium: Browser, demo: Demo) -> None:
    """The board reads for everyone and offers buttons only to a role that may write."""
    with open_page(chromium, demo, view="board", token=demo.tokens[SEED_VIEWER]) as screen:
        screen.assert_browser_stayed_quiet()
        board = screen.page.locator("#board-root")
        assert board.locator("article.card").count() > 0, "the viewer sees no orders at all"
        assert board.locator(".card__actions").count() == 0
        assert board.locator("button").count() == 0
        painted = board.inner_text()
        assert "This token may read but not write" in painted
        assert "the API would refuse every workflow action with 403" in painted
        assert "may read only" in screen.page.locator("#identity").inner_text()


def test_a_rejected_token_raises_the_401_banner(chromium: Browser, demo: Demo) -> None:
    """A token the API refuses is reported as a 401 about the token itself.

    The token goes in through the form rather than through storage, so this
    also covers the path where an operator pastes one into a loaded page.
    """
    with open_page(chromium, demo, view="tree") as screen:
        page = screen.page
        page.fill("#token-input", "not-a-real-token")
        page.click("#token-form button[type='submit']")
        banner = page.locator("#banner")
        banner.wait_for(state="visible", timeout=RENDER_TIMEOUT_MS)
        reported = banner.inner_text()
        assert "Not authenticated (401)" in reported
        assert "the API rejected this token" in reported
        assert "auth.invalid_token" in reported
        # The refusal is reported, never thrown. The browser's own note about
        # the refused request is the only thing it logged.
        assert screen.uncaught == [], screen.uncaught
        assert all("401 (Unauthorized)" in line for line in screen.logged), screen.logged


def test_an_action_moves_an_order_and_the_trail_records_it(chromium: Browser, demo: Demo) -> None:
    """The whole loop the manual pass walked: click, move, and find it in the trail."""
    with open_page(chromium, demo, view="board", token=demo.tokens[SEED_ENGINEER]) as screen:
        page = screen.page
        assert column(page, "draft").locator("article.card", has_text=DRAFT_ORDER).count() == 1

        page.locator("article.card", has_text=DRAFT_ORDER).get_by_role(
            "button", name="Schedule"
        ).click()

        moved = column(page, "scheduled").locator("article.card", has_text=DRAFT_ORDER)
        moved.locator(".message").filter(
            has_text="The API moved this order to scheduled."
        ).wait_for(timeout=RENDER_TIMEOUT_MS)
        assert column(page, "draft").locator("article.card", has_text=DRAFT_ORDER).count() == 0

        page.click("a[data-view='audit']")
        wait_for_view(page, "audit")
        newest = page.locator("#audit-root .timeline > li.entry").first.inner_text()
        assert "status_changed" in newest
        assert "maintenance_order" in newest
        assert SEED_ENGINEER in newest
        trail = page.locator("#audit-root").inner_text()
        assert f"of {demo.counts['audit_entries'] + 1} entries" in trail
        screen.assert_browser_stayed_quiet()


def test_the_board_lays_its_columns_out_side_by_side(chromium: Browser, demo: Demo) -> None:
    """Regression guard for the mount that carried the board's own grid class.

    Two grids nested one inside the other put the board's header into one
    track and all of its columns into the next, so the board rendered as a
    narrow strip beside its own summary line. Nothing static could see it: the
    class was spelled correctly, every file parsed, and the page threw nothing.
    Geometry is the only witness.

    Three assertions, in the order a reader should think about them: the board
    owns the only grid on its way up to ``<main>`` (the defect itself), its
    header sits above its columns rather than beside them (what that defect
    looked like), and the columns really are laid out as columns (the wider
    regression of a board collapsing into one track on a desktop viewport).
    """
    with open_page(chromium, demo, view="board", token=demo.tokens[SEED_ENGINEER]) as screen:
        page = screen.page
        grid = page.locator("#board-root > .board-root")

        nested_in = grid.evaluate(
            """(node) => {
                 const grids = [];
                 let parent = node.parentElement;
                 while (parent !== null) {
                   if (getComputedStyle(parent).display.includes("grid")) {
                     grids.push(parent.id || parent.className || parent.tagName);
                   }
                   if (parent.id === "main") {
                     break;
                   }
                   parent = parent.parentElement;
                 }
                 return grids;
               }"""
        )
        assert nested_in == [], f"the board grid is nested inside another grid: {nested_in}"

        header = page.locator("#board-root > div:not(.board-root)").bounding_box()
        columns = grid.bounding_box()
        assert header is not None and columns is not None
        assert header["y"] + header["height"] <= columns["y"] + 1, (
            f"the board's header is beside its columns, not above them: {header} {columns}"
        )

        tracks = str(grid.evaluate("(node) => getComputedStyle(node).gridTemplateColumns")).split()
        assert len(tracks) > 1, f"the board resolved to a single column track: {tracks}"
        offsets = grid.evaluate(
            """(node) => [...node.querySelectorAll(':scope > section.column')]
                 .map((column) => Math.round(column.getBoundingClientRect().left))"""
        )
        assert len(set(offsets)) > 1, f"every column starts at the same offset: {offsets}"
        screen.assert_browser_stayed_quiet()
