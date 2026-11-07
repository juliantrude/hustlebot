"""`/api/state` and the dashboard's actions come from the hustle store and the sessions the service reports."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import seed
from hustlemaster import live as living
from hustlemaster.live import Live
from hustlemaster.server import serve
from hustlemaster.store import Store
from hustlemaster.usage import Reading, Usage, Window

MASTER = Path(__file__).resolve().parent.parent


@pytest.fixture
def store():
    s = Store()
    yield s
    s.close()


@pytest.fixture
def ids(store):
    return seed(store)


@pytest.fixture
def app(store, ids):
    server, live = serve(store, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", live
    server.shutdown()
    server.server_close()


def call(url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def by_id(state):
    return {h["id"]: h for h in state["hustles"]}


def test_state_lists_the_stored_hustles_active_first_then_by_weight(store, ids):
    state = Live(store).summary()
    assert [h["title"] for h in state["hustles"]] == ["Hustlemaster v2", "Workout sync API", "Docs search"]
    h = by_id(state)
    assert (h[ids["master"]]["done"], h[ids["master"]]["total"]) == (1, 6)
    assert (h[ids["master"]]["project"], h[ids["master"]]["path"]) == ("hustlebot", "/work/hustlebot") and h[ids["master"]]["weight"] == 2
    assert h[ids["sync"]]["status"] == "blocked"
    assert h[ids["docs"]]["status"] == "paused" and h[ids["docs"]]["active"] is False
    assert state["total_points"] == 20
    inc = {i["id"]: i for i in h[ids["master"]]["increments"]}
    assert inc["B"] == {"id": "B", "after": ["A"], "state": "running", "model": "sonnet"}


def test_points_and_shares_come_from_the_live_sessions(store, ids):
    live = Live(store)
    # nothing is ready (C waits for B, E for its approval) and no session is reported yet: no share
    assert by_id(live.summary())[ids["master"]]["share"] == 0
    live.started(ids["master"], "B", "sonnet")
    live.started(ids["master"], "D", "haiku")
    state = live.summary()
    master = by_id(state)[ids["master"]]
    assert state["points_in_use"] == master["points_in_use"] == 3
    assert master["share"] == 20
    assert [(s["increment"], s["points"], s["started"]) for s in master["sessions"]] == [("B", 2, 0), ("D", 1, 0)]
    # the blocked hustle has neither ready work nor a session, so it holds no share
    assert by_id(state)[ids["sync"]]["share"] == 0


def test_an_event_overseer_runs_a_blocked_hustle_and_its_points_count(store, ids):
    live = Live(store)
    live.started(ids["sync"], "event-1", "opus")
    state = live.summary()
    sync = by_id(state)[ids["sync"]]
    assert sync["status"] == "running" and sync["points_in_use"] == 4
    assert sync["share"] == 20  # back in the pool, alone: nothing of the others is ready or reported running
    store.set_active(ids["sync"], False)
    assert by_id(live.summary())[ids["sync"]]["status"] == "stopping"


def test_each_tick_samples_the_running_sessions_into_the_history(store, ids):
    live = Live(store)
    live.started(ids["master"], "B", "sonnet")
    live.step()
    live.started(ids["master"], "D", "haiku")
    live.step()
    live.finished(ids["master"], "B")
    live.step()
    history = by_id(live.summary())[ids["master"]]["history"]
    assert [[c["increment"] for c in col] for col in history] == [["B"], ["B", "D"], ["D"]]
    assert by_id(live.summary())[ids["sync"]]["history"] == []
    for _ in range(living.HISTORY + 5):
        live.step()
    assert len(by_id(live.summary())[ids["master"]]["history"]) == living.HISTORY


def test_started_and_finished_events_fade_after_a_few_ticks(store, ids):
    live = Live(store)
    live.started(ids["master"], "B", "sonnet")
    live.step()
    live.finished(ids["master"], "B", "failed")
    assert [(e["kind"], e["increment"], e["model"]) for e in live.summary()["events"]] == [
        ("started", "B", "sonnet"), ("failed", "B", "sonnet")]
    live.finished(ids["master"], "B")  # a second report of the same end changes nothing
    assert len(live.summary()["events"]) == 2
    for _ in range(living.EVENTS):
        live.step()
    assert live.summary()["events"] == []


def test_graph_version_changes_with_a_state_or_a_new_plan(store, ids):
    live = Live(store)
    first = by_id(live.summary())[ids["master"]]["graph_version"]
    store.set_state(ids["master"], "B", "done")
    second = by_id(live.summary())[ids["master"]]["graph_version"]
    store.change_plan(ids["sync"], store.hustle(ids["sync"]).increments, author="user")
    assert len({first, second}) == 2
    assert by_id(live.summary())[ids["sync"]]["graph_version"].startswith("2:")


def test_usage_is_unknown_until_read_then_the_estimate(store, ids):
    usage = Usage()
    live = Live(store, usage)
    assert live.summary()["usage"]["session"] == {"percent": 0, "resets": "unknown", "known": False}
    now = datetime.now(timezone.utc)
    usage.observe(Reading(now, {"session": Window(47.0, now + timedelta(hours=3)),
                                "week": Window(55.0, now + timedelta(days=2))}, "screen"))
    state = live.summary()["usage"]
    assert (state["session"]["percent"], state["week"]["percent"], state["week"]["known"]) == (47.0, 55.0, True)
    assert state["session"]["resets"] != "unknown"


def test_pending_approvals_are_listed_and_decided_over_the_api(app, store, ids):
    url, _ = app
    _, state = call(url + "/api/state")
    [approval] = state["approvals"]
    assert approval["hustle"] == ids["master"] and "36 %" in approval["text"]
    status, state = call(f"{url}/api/approvals/{approval['id']}", {"decision": "yes"})
    assert status == 200 and state["approvals"] == []
    assert store.hustle(ids["master"]).confirmed_version == 2
    assert store.approval(int(approval["id"])).state == "approved"
    assert call(f"{url}/api/approvals/{approval['id']}", {"decision": "yes"})[0] == 409  # no longer pending
    assert call(f"{url}/api/approvals/{approval['id']}", {"decision": "maybe"})[0] == 400


def test_rejecting_an_approval_blocks_what_waited_for_it(app, store, ids):
    url, _ = app
    [approval] = store.approvals(states=("pending",))
    call(f"{url}/api/approvals/{approval.id}", {"decision": "no"})
    assert {i.id: i.state for i in store.hustle(ids["master"]).increments}["E"] == "blocked"


def test_settings_activation_and_weight_are_stored(app, store, ids):
    url, _ = app
    status, state = call(url + "/api/settings", {"total_points": 12})
    assert status == 200 and state["total_points"] == 12 and store.settings().total_points == 12
    call(f"{url}/api/hustles/{ids['docs']}", {"active": True, "weight": 4})
    docs = store.hustle(ids["docs"])
    assert (docs.active, docs.weight, docs.confirmed_version) == (True, 4, 1)  # the first activation confirms
    assert call(url + "/api/hustles/nope", {"active": True})[0] == 404


def test_graph_document_shows_each_increments_prompt(app, ids):
    url, _ = app
    status, doc = call(f"{url}/api/hustles/{ids['master']}/graph.json")
    assert status == 200 and doc["title"] == "Hustlemaster v2"
    assert doc["subtitle"] == "hustlebot · 1/6 done"
    node = next(n for n in doc["nodes"] if n["id"] == "B")
    assert node["details"][0] == {"label": "Prompt", "lines": ["Implement step B.", "Verify with the tests, then commit."]}
    assert {"from": "A", "to": "B"} in doc["edges"]
    assert call(f"{url}/api/hustles/nope/graph.json")[0] == 404


def test_the_server_boots_on_a_store_file_and_serves_it(tmp_path):
    db = tmp_path / "hustles.db"
    ids = seed(Store(db))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "hustlemaster.server", "--db", str(db), "--port", str(port)],
                            cwd=MASTER)
    try:
        for _ in range(50):
            try:
                _, state = call(f"http://127.0.0.1:{port}/api/state")
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the server did not start")
        assert set(by_id(state)) == set(ids.values())
    finally:
        proc.terminate()
        proc.wait(5)
