"""Plan editing from the graph view (`editing.py`, ADR 0013): prompt, model, delete, pause — the user's own changes."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import seed
from hustlemaster import editing
from hustlemaster.graph import graph_doc
from hustlemaster.plan import PlanError
from hustlemaster.server import serve
from hustlemaster.store import Store


@pytest.fixture
def store():
    store = Store()
    yield store
    store.close()


@pytest.fixture
def ids(store):
    return seed(store)


def inc(store, hustle, id):
    return next(i for i in store.hustle(hustle).increments if i.id == id)


def test_a_prompt_edit_is_a_new_version_by_the_user_and_confirms_the_plan(store, ids):
    sync = ids["sync"]
    before = store.hustle(sync).version
    editing.apply(store, sync, {"node": "ok", "field": "prompt", "value": "Check everything."})
    hustle = store.hustle(sync)
    assert hustle.version == before + 1 and inc(store, sync, "ok").prompt == "Check everything."
    assert store.versions(sync)[-1].author == "user" and store.versions(sync)[-1].note == "prompt of ok edited"
    assert hustle.confirmed_version == hustle.version


def test_the_users_own_upgrade_needs_no_approval_of_their_own(store, ids):
    sync = ids["sync"]
    editing.apply(store, sync, {"node": "B", "field": "model", "value": "opus"})  # 2 → 4 points, beyond any share
    assert store.approvals(sync) == [] and inc(store, sync, "B").held == ""
    assert store.hustle(sync).confirmed_version == store.hustle(sync).version


def test_an_edit_while_an_overseers_change_awaits_approval_does_not_settle_it(store, ids):
    master = ids["master"]
    [pending] = store.approvals(master, states=("pending",))
    confirmed = store.hustle(master).confirmed_version
    editing.apply(store, master, {"node": "C", "field": "prompt", "value": "Do C differently."})
    assert store.hustle(master).confirmed_version == confirmed
    [again] = store.approvals(master, states=("pending",))
    assert again.id != pending.id and again.version == store.hustle(master).version
    assert store.approval(pending.id).state == "superseded"
    assert inc(store, master, "E").held == "provisional"


def test_before_the_first_activation_an_edit_leaves_the_confirmation_to_the_activation(store, ids):
    docs = ids["docs"]
    editing.apply(store, docs, {"node": "A", "field": "model", "value": "sonnet"})
    assert store.hustle(docs).confirmed_version is None
    store.set_active(docs, True)
    assert store.hustle(docs).confirmed_version == store.hustle(docs).version


def test_editing_a_blocked_increment_gives_it_a_fresh_start(store, ids):
    sync = ids["sync"]
    store.count_attempt(sync, "B")
    store.count_attempt(sync, "B")
    editing.apply(store, sync, {"node": "B", "field": "prompt", "value": "Try it this way."})
    b = inc(store, sync, "B")
    assert (b.state, b.attempts) == ("open", 0) and b in store.hustle(sync).ready()


def test_refused_edits_change_nothing(store, ids):
    sync, master = ids["sync"], ids["master"]
    version = store.hustle(sync).version
    refused = [
        (sync, {"node": "A", "field": "prompt", "value": "again"}, ValueError, "done"),
        (sync, {"node": "B", "field": "prompt", "value": "  "}, ValueError, "empty"),
        (sync, {"node": "B", "field": "model", "value": "gpt"}, ValueError, "unknown model"),
        (sync, {"node": "B", "field": "model", "value": "fable"}, PlanError, "allow_fable"),
        (sync, {"node": "B", "field": "title", "value": "x"}, ValueError, "field must be"),
        (sync, {"node": "B", "field": "prompt", "value": 3}, TypeError, "string"),
        (sync, {"node": "B", "action": "explode"}, ValueError, "action must be"),
        (sync, {"node": "Z", "action": "pause"}, KeyError, "unknown increment"),
        (sync, {"node": "ok", "action": "delete"}, PlanError, "acceptance"),
        (master, {"node": "B", "action": "delete"}, ValueError, "running"),
    ]
    for hustle, edit, error, words in refused:
        with pytest.raises(error, match=words):
            editing.apply(store, hustle, edit)
    assert store.hustle(sync).version == version and inc(store, sync, "B").state == "blocked"
    with pytest.raises(KeyError):
        editing.apply(store, "nope", {"node": "A", "action": "pause"})


def test_deleting_an_increment_hands_its_dependencies_to_its_dependants(store, ids):
    master = ids["master"]
    store.set_state(master, "B", "open")
    editing.apply(store, master, {"node": "B", "action": "delete"})
    plan = {i.id: i.after for i in store.hustle(master).increments}
    assert "B" not in plan and plan["C"] == ["A"]
    assert store.versions(master)[-1].note == "B deleted"
    editing.apply(store, master, {"node": "C", "action": "delete"})
    assert store.hustle(master).increments[-1].after == ["A", "D", "E"]  # in the place C stood


def test_a_paused_increment_does_not_start_until_resumed_and_stays_paused_across_plan_changes(store, ids):
    docs = ids["docs"]
    store.set_active(docs, True)
    editing.apply(store, docs, {"node": "A", "action": "pause"})
    hustle = store.hustle(docs)
    assert inc(store, docs, "A").held == "paused" and hustle.ready() == [] and hustle.version == 1
    editing.apply(store, docs, {"node": "A", "field": "prompt", "value": "Changed while paused."})
    assert inc(store, docs, "A").held == "paused"
    editing.apply(store, docs, {"node": "A", "action": "resume"})
    assert [i.id for i in store.hustle(docs).ready()] == ["A"]


def test_an_overseer_hold_outranks_a_pause(store, ids):
    sync = ids["sync"]
    store.set_state(sync, "B", "open")
    editing.apply(store, sync, {"node": "B", "action": "pause"})
    store.open_event(sync, "failed", increment="B")
    assert inc(store, sync, "B").held == "overseer"


def test_the_graph_offers_what_each_state_allows(store, ids):
    master = ids["master"]
    store.set_paused(master, "C", True)
    doc = graph_doc(store.hustle(master), allow_fable=False)
    nodes = {n["id"]: n for n in doc["nodes"]}
    assert doc["edit_url"] == f"/api/hustles/{master}/edit"
    assert "actions" not in nodes["A"] and all("edit" not in d for d in nodes["A"]["details"])  # done
    assert [a["id"] for a in nodes["B"]["actions"]] == ["pause"]  # running: no delete
    assert [a["id"] for a in nodes["C"]["actions"]] == ["resume", "delete"] and nodes["C"]["tags"] == ["paused"]
    assert nodes["C"]["title"] == "⏸ C · Step C" and nodes["C"]["details"][2] == {"label": "Waits", "lines": ["paused"]}
    assert nodes["E"]["tags"] == ["provisional, awaits approval"]
    prompt, model, _ = nodes["C"]["details"]
    assert prompt["edit"] == {"field": "prompt", "value": "Implement step C.\nVerify with the tests, then commit."}
    assert model["edit"]["choices"] == ["haiku", "sonnet", "opus"]
    assert graph_doc(store.hustle(master))["nodes"][2]["details"][1]["edit"]["choices"][-1] == "fable"
    from graph_engine import problems

    assert problems(doc) == []


def post(url, body):
    request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_edit_route_answers_with_the_graph_or_the_reason(store, ids):
    httpd, _ = serve(store, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_port}/api/hustles"
    try:
        status, doc = post(f"{url}/{ids['sync']}/edit", {"node": "B", "field": "model", "value": "haiku"})
        assert status == 200 and next(n for n in doc["nodes"] if n["id"] == "B")["type"] == "haiku"
        status, answer = post(f"{url}/{ids['sync']}/edit", {"node": "ok", "action": "delete"})
        assert status == 409 and "acceptance" in answer["error"]
        assert post(f"{url}/nope/edit", {"node": "A", "action": "pause"})[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
