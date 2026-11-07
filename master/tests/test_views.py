"""Both views render against the real server on a seeded store in the system Chromium, with sessions reported the
way the service loop reports them. Screenshots land in $HM_SHOTS (default: the test's tmp dir) so a human can look
at them."""

from __future__ import annotations

import math
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import seed
from hustlemaster.server import serve
from hustlemaster.store import Store
from hustlemaster.usage import Reading, Window

pytestmark = pytest.mark.views
playwright = pytest.importorskip("playwright.sync_api")

CHROMIUM = "/usr/bin/chromium"


@pytest.fixture(scope="module")
def server():
    store = Store()
    ids = seed(store)
    # port 0: never 8765, where the demo unit lives
    httpd, live = serve(store, 0)
    live.started(ids["master"], "B", "sonnet")
    live.started(ids["master"], "D", "haiku")
    live.started(ids["sync"], "event-1", "opus")
    now = datetime.now(timezone.utc)
    # four days into the week: the target line stands at 95 * 4/7 ≈ 54 %
    live.usage.observe(Reading(now, {"session": Window(62.0, now + timedelta(hours=2)),
                                     "week": Window(40.0, now + timedelta(days=3))}, "screen"))
    for _ in range(20):
        live.step()
    live.run_forever()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", ids
    httpd.shutdown()
    httpd.server_close()
    store.close()


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
    url, _ = server
    page.goto(url + "/")
    page.wait_for_selector("article.hustle")
    cards = page.locator("article.hustle")
    assert cards.count() == 3
    assert "Hustlemaster v2" in page.locator("#hustles").inner_text()
    first = cards.nth(0)
    assert first.locator(".share").inner_text() == "3/13.3 pts"  # 2 fr of 20 beside the 1 fr hustle its event overseer keeps in the pool
    assert first.locator(".count").inner_text() == "1 / 6 increments"
    assert "36 %" in page.locator("#approvals").inner_text()
    assert page.locator("h2", has_text="Hustles").count() == 1
    # the mini graph of the first card loads its document from the renamed route
    frame = page.frame_locator("article.hustle >> nth=0 >> .preview iframe")
    frame.locator("#stage canvas").first.wait_for(timeout=5000)
    page.wait_for_timeout(1500)  # a few polls, so the load strips and beams have history to draw
    shot(page, "control", tmp_path)
    assert page.errors == []


def test_graph_route_opens_the_hustle_full_screen(server, page, tmp_path):
    url, ids = server
    page.goto(f"{url}/hustles/{ids['master']}")
    page.wait_for_selector("#overlay:not([hidden])")
    assert page.locator("#overlayTitle").inner_text() == "hustlebot · Hustlemaster v2"
    page.frame_locator("#overlayFrame").locator("#stage canvas").first.wait_for(timeout=5000)
    shot(page, "graph", tmp_path)
    page.keyboard.press("Escape")
    assert page.locator("#overlay").is_hidden()
    assert page.errors == []


def test_cinema_view_draws_lanes(server, page, tmp_path):
    url, _ = server
    page.goto(url + "/cinema")
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


def thresholds(page, url):
    return page.request.get(url + "/api/state").json()["thresholds"]


def test_threshold_knobs_drag_around_the_ring_and_answer_the_keys(server, page, tmp_path):
    url, _ = server
    page.goto(url + "/")
    knob = page.locator(".knob.session")
    knob.wait_for()
    assert knob.get_attribute("aria-valuenow") == "85"
    assert page.locator("#thresholdText").inner_text() == "5h stop 85 % · week 95 % at reset, line now 54 %"
    shot(page, "thresholds", tmp_path)
    box = page.locator("svg.thresholds").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    k = knob.bounding_box()
    page.mouse.move(k["x"] + k["width"] / 2, k["y"] + k["height"] / 2)
    page.mouse.down()
    # anticlockwise along the inner ring from 85 % to 60 %
    for pct in range(85, 59, -5):
        a = -math.pi / 2 + 2 * math.pi * pct / 100
        page.mouse.move(cx + 61 * math.cos(a), cy + 61 * math.sin(a))
    shot(page, "threshold-drag", tmp_path)
    assert thresholds(page, url)["session"] == 85  # nothing is sent while the knob is held
    page.mouse.up()
    page.wait_for_function("() => document.querySelector('.knob.session').getAttribute('aria-valuenow') === '60'")
    page.wait_for_timeout(300)
    assert thresholds(page, url)["session"] == 60
    week = page.locator(".knob.week")
    week.focus()
    for _ in range(3):
        page.keyboard.press("ArrowDown")
    page.wait_for_timeout(900)
    assert thresholds(page, url) == {"session": 60, "week": 92}
    assert page.locator("#thresholdText").inner_text() == "5h stop 60 % · week 92 % at reset, line now 53 %"
    shot(page, "thresholds-set", tmp_path)
    page.request.post(url + "/api/settings", data={"session_threshold": 85, "week_target": 95})
    assert page.errors == []


def test_cinema_view_shows_only_active_hustles(server, page, tmp_path):
    url, _ = server
    # the scene is one canvas: record what it writes
    page.add_init_script("""(() => {
      window.drawn = new Set();
      const fill = CanvasRenderingContext2D.prototype.fillText;
      CanvasRenderingContext2D.prototype.fillText = function (text, ...rest) {
        window.drawn.add(String(text));
        return fill.call(this, text, ...rest);
      };
    })()""")
    page.goto(url + "/cinema")
    page.wait_for_timeout(1500)
    drawn = set(page.evaluate("() => [...window.drawn]"))
    assert {"Hustlemaster v2", "Workout sync API"} <= drawn
    assert "Docs search" not in drawn  # inactive
    shot(page, "cinema-active", tmp_path)
    assert page.errors == []
