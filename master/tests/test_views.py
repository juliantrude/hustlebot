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

from conftest import auto_approve, seed
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


@pytest.fixture
def approvals_server():
    # its own store: the test revokes and switches settings the other view tests read
    store = Store()
    ids = seed(store)
    auto_approve(store, ids["sync"], ("F",), ("G", "H", "I"))
    store.update_settings(auto_approve="off")
    httpd, _ = serve(store, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", ids, store
    httpd.shutdown()
    httpd.server_close()
    store.close()


def test_settings_and_approvals_in_the_control_view(approvals_server, page, tmp_path):
    url, ids, store = approvals_server
    page.goto(url + "/")
    page.locator("#autoApprove button").first.wait_for()
    assert page.locator("#approvalList li").count() == 1
    auto = page.locator("#autoApprovedList li")
    assert auto.count() == 2 and auto.nth(0).locator(".revoke").count() == 1 and auto.nth(1).locator(".revoke").count() == 0
    assert auto.nth(0).locator(".t").inner_text().startswith("final_lift · Workout sync API: the plan grew by")
    assert page.locator("#autoApprove [aria-checked=true]").inner_text() == "off" and page.locator("#growth").is_hidden()
    shot(page, "settings", tmp_path)

    page.locator(".setting .switch").click()
    page.wait_for_function("() => document.getElementById('allowFable').checked")
    assert store.settings().allow_fable is True
    page.locator("#autoApprove button[data-mode=limited]").click()
    page.locator("#growth").wait_for()
    assert store.settings().auto_approve == "limited"
    page.locator("[data-growth='5']").click()
    page.wait_for_function("() => document.getElementById('growthValue').textContent === '30 %'")
    assert store.settings().auto_approve_growth == 30
    shot(page, "settings-limited", tmp_path)

    auto.nth(0).locator(".revoke").click()
    page.wait_for_function("() => document.querySelectorAll('#approvalList li').length === 2")
    [revoked] = store.approvals(ids["sync"], states=("revoked",))
    assert store.hustle(ids["sync"]).confirmed_version == revoked.baseline
    # the earlier automatic approval is the latest again and can be taken back in turn
    assert auto.count() == 1 and auto.nth(0).locator(".revoke").count() == 1
    shot(page, "settings-revoked", tmp_path)
    page.locator("#approvalList li", has_text="Workout sync API").locator(".yes").click()
    page.wait_for_function("() => document.querySelectorAll('#approvalList li').length === 1")
    assert store.hustle(ids["sync"]).confirmed_version == store.hustle(ids["sync"]).version
    assert page.errors == []


def engine_frame(page):
    page.frame_locator("#overlayFrame").locator("#stage canvas").first.wait_for(timeout=5000)
    # the overlay's frame, not a card's preview behind it
    frame = page.locator("#overlayFrame").element_handle().content_frame()
    frame.wait_for_function("() => JSON.parse(document.getElementById('engine-state').textContent || '{}').ok")
    return frame


def engine_state(frame):
    return frame.evaluate("() => JSON.parse(document.getElementById('engine-state').textContent)")


def settled(frame, check: str):
    # after an edit the page loads the graph again and draws it anew; `check` reads the new engine state
    frame.wait_for_function(f"() => {{ const s = JSON.parse(document.getElementById('engine-state').textContent);"
                            f" return s.ok && ({check}); }}")


def test_plan_editing_in_the_graph_view(approvals_server, page, tmp_path):
    url, ids, store = approvals_server
    sync = ids["sync"]
    page.goto(f"{url}/hustles/{sync}")
    frame = engine_frame(page)
    frame.evaluate('GraphEngine.click("B")')
    panel = frame.locator("#panel")
    assert panel.locator("textarea[data-field=prompt]").input_value().startswith("Implement step B.")

    # the blocked increment gets a new prompt and with it a fresh start
    panel.locator("textarea[data-field=prompt]").fill("Implement step B with the retry the overseer asked for.")
    panel.locator(".editor", has=frame.locator("textarea")).locator("button").click()
    settled(frame, "s.last_edit && s.last_edit.ok && s.panel && s.panel.id === 'B'")
    b = next(i for i in store.hustle(sync).increments if i.id == "B")
    assert b.prompt == "Implement step B with the retry the overseer asked for." and b.state == "open"
    assert store.versions(sync)[-1].author == "user"
    assert engine_state(frame)["panel"]["details"][0]["lines"] == [b.prompt]
    shot(page, "graph-edit-prompt", tmp_path)

    panel.locator("select[data-field=model]").select_option("haiku")
    panel.locator(".editor", has=frame.locator("select")).locator("button").click()
    settled(frame, "s.nodes.some(n => n.id === 'B' && n.type === 'haiku')")
    assert next(i for i in store.hustle(sync).increments if i.id == "B").model == "haiku"

    panel.locator("[data-action=pause]").click()
    settled(frame, "s.nodes.some(n => n.id === 'B' && n.tag.includes('paused'))")
    assert next(i for i in store.hustle(sync).increments if i.id == "B").held == "paused"
    assert panel.locator("[data-action=resume]").count() == 1
    shot(page, "graph-paused", tmp_path)

    # the server refuses what would break the plan, and the page says why
    frame.evaluate('GraphEngine.click("ok")')
    panel.locator("[data-action=delete]").click()
    assert panel.locator("[data-action=delete]").inner_text() == "Delete ok? Click again"
    panel.locator("[data-action=delete]").click()
    panel.locator("#edit-error").wait_for()
    assert "no acceptance increment" in panel.locator("#edit-error").inner_text()
    shot(page, "graph-refused", tmp_path)

    frame.evaluate('GraphEngine.click("B")')
    panel.locator("[data-action=delete]").click()
    panel.locator("[data-action=delete]").click()
    settled(frame, "!s.nodes.some(n => n.id === 'B') && s.panel === null")
    plan = {i.id: i.after for i in store.hustle(sync).increments}
    assert "B" not in plan and plan["ok"][0] == "A"
    shot(page, "graph-deleted", tmp_path)
    # Chromium logs the refused delete's answer; that one was asked for
    assert [e for e in page.errors if "status of 409" not in e] == []


def test_the_token_login_leads_back_and_the_views_work_behind_it(page, tmp_path):
    store = Store()
    ids = seed(store)
    httpd, _ = serve(store, 0, token="view-token")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        page.goto(f"{url}/hustles/{ids['sync']}")
        page.wait_for_selector("input[name=token]")
        assert page.url.startswith(f"{url}/login?next=")
        page.fill("input[name=token]", "wrong")
        page.click("button[type=submit]")
        page.wait_for_selector(".error")
        assert page.locator(".error").inner_text() == "Wrong token."
        shot(page, "login-refused", tmp_path)

        page.fill("input[name=token]", "view-token")
        page.click("button[type=submit]")
        page.wait_for_url(f"{url}/hustles/{ids['sync']}")
        # the graph page in the iframe loads its document and posts its edits with the login's cookie
        frame = engine_frame(page)
        frame.evaluate('GraphEngine.click("B")')
        frame.locator("#panel [data-action=pause]").click()
        settled(frame, "s.nodes.some(n => n.id === 'B' && n.tag.includes('paused'))")
        assert next(i for i in store.hustle(ids["sync"]).increments if i.id == "B").held == "paused"
        page.keyboard.press("Escape")
        page.locator("article.hustle").first.wait_for()

        page.goto(f"{url}/cinema")
        page.wait_for_timeout(1500)
        assert page.url == f"{url}/cinema"
        shot(page, "cinema-logged-in", tmp_path)
        # a display whose login is gone shows the login, not its last picture forever
        page.context.clear_cookies()
        page.wait_for_url(f"{url}/login?next=%2Fcinema", timeout=5000)
        shot(page, "login", tmp_path)
        # the browser logs the refused poll; that one was asked for
        assert [e for e in page.errors if "status of 401" not in e] == []
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()
