"""Both views render against the mock server in the system Chromium. Screenshots land in $HM_SHOTS (default: the
test's tmp dir) so a human can look at them."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.views
playwright = pytest.importorskip("playwright.sync_api")

MASTER = Path(__file__).resolve().parent.parent
CHROMIUM = "/usr/bin/chromium"


def free_port() -> int:
    # never 8765: the demo unit lives there
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "hustlemaster.server", "--mock", "--port", str(port)], cwd=MASTER)
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/api/state", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("mock server did not start")
    yield url
    proc.terminate()
    proc.wait(5)


@pytest.fixture
def page(tmp_path):
    if not Path(CHROMIUM).exists():
        pytest.skip("no system Chromium")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        # the server has no favicon; the browser asks anyway
        page.on("console", lambda m: m.type == "error" and not m.location["url"].endswith("/favicon.ico") and page.errors.append(m.text))
        yield page
        browser.close()


def shot(page, name, tmp_path):
    out = Path(os.environ.get("HM_SHOTS", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=out / f"{name}.png")


def test_control_view_shows_every_hustle_with_its_graph(server, page, tmp_path):
    page.goto(server + "/")
    page.wait_for_selector("article.hustle")
    cards = page.locator("article.hustle")
    assert cards.count() == 5
    assert "Hustlemaster v2" in page.locator("#hustles").inner_text()
    assert page.locator("h2", has_text="Hustles").count() == 1
    # the mini graph of the first card loads its document from the renamed route
    frame = page.frame_locator("article.hustle >> nth=0 >> .preview iframe")
    frame.locator("#stage canvas").first.wait_for(timeout=5000)
    page.wait_for_timeout(1500)  # a few polls, so the load strips and beams have history to draw
    shot(page, "control", tmp_path)
    assert page.errors == []


def test_graph_route_opens_the_hustle_full_screen(server, page, tmp_path):
    page.goto(server + "/hustles/hb-master")
    page.wait_for_selector("#overlay:not([hidden])")
    assert page.locator("#overlayTitle").inner_text() == "hustlebot · Hustlemaster v2"
    page.frame_locator("#overlayFrame").locator("#stage canvas").first.wait_for(timeout=5000)
    shot(page, "graph", tmp_path)
    page.keyboard.press("Escape")
    assert page.locator("#overlay").is_hidden()
    assert page.errors == []


def test_cinema_view_draws_lanes(server, page, tmp_path):
    page.goto(server + "/cinema")
    page.wait_for_timeout(2500)
    lit = page.evaluate(
        """() => {
          const c = document.getElementById('scene');
          const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
          let n = 0;
          for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 60) n++;
          return n / (d.length / 4);
        }"""
    )
    shot(page, "cinema", tmp_path)
    assert lit > 0.01, "the cinema canvas stayed black"
    assert page.errors == []
