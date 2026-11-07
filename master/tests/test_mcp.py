"""The MCP endpoint per caller (ADR 0014): each role sees and may use only its rights, over real HTTP."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from hustlemaster.access import Caller, Gate, Refused, Tokens, interactive_token
from hustlemaster.mcp_server import TOKEN_ENV, Endpoint
from hustlemaster.store import Store

from test_store import inc, valid_plan

DASHBOARD_ONLY = ["approve", "set_weight", "set_active", "activate", "update_settings", "set_total_points",
                  "set_allow_fable", "confirm"]


def spec(increments):
    return [{k: v for k, v in asdict(i).items() if k in ("id", "title", "prompt", "after", "model", "mode")}
            for i in increments]


@pytest.fixture(scope="module")
def world():
    store = Store()
    tokens = Tokens(interactive="interactive-token")
    mine = store.submit("/repo/a", "Mine", "brief A", valid_plan(), author="user")
    other = store.submit("/repo/b", "Other", "brief B", valid_plan(), author="user")
    endpoint = Endpoint(store, tokens).start()
    yield store, tokens, endpoint, mine, other
    endpoint.stop()
    store.close()


def session(endpoint, token, *steps):
    """Run (tool, arguments) steps in one MCP session; each step yields its parsed result or the error."""

    async def go():
        out = []
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx2.AsyncClient(headers=headers) as http:
            async with Client(streamable_http_client(endpoint.url, http_client=http), mode="legacy") as client:
                for tool, args in steps:
                    if tool == "list":
                        out.append(sorted(t.name for t in (await client.list_tools()).tools))
                        continue
                    try:
                        result = await client.call_tool(tool, args)
                    except MCPError as err:
                        out.append(("refused", err.message))
                        continue
                    text = result.content[0].text
                    # the SDK prefixes tool errors with "Error executing tool <name>: "
                    error = text.removeprefix(f"Error executing tool {tool}: ")
                    out.append(("error", error) if result.is_error else json.loads(text))
        return out

    return anyio.run(go)


def test_each_role_lists_only_its_tools(world):
    store, tokens, endpoint, mine, _ = world
    worker = tokens.issue("worker", mine, "A")
    overseer = tokens.issue("overseer", mine, "ok")
    assert session(endpoint, worker, ("list", None)) == [["propose_change", "read_hustle"]]
    assert session(endpoint, overseer, ("list", None)) == [["change_plan", "read_hustle", "read_settings"]]
    assert session(endpoint, "interactive-token", ("list", None)) == [
        ["change_plan", "list_hustles", "read_hustle", "read_settings", "submit_hustle"]
    ]


def test_worker_reads_its_hustle_and_proposes_but_changes_nothing(world):
    store, tokens, endpoint, mine, other = world
    worker = tokens.issue("worker", mine, "B")
    read, proposed, own_id, foreign, change, submit, settings = session(
        endpoint, worker,
        ("read_hustle", {}),
        ("propose_change", {"text": "split B: the parser and the CLI are separate"}),
        ("read_hustle", {"hustle_id": mine}),
        ("read_hustle", {"hustle_id": other}),
        ("change_plan", {"increments": spec(valid_plan())}),
        ("submit_hustle", {"project": "/x", "title": "t", "brief": "b", "increments": spec(valid_plan())}),
        ("read_settings", {}),
    )
    assert (read["id"], read["brief"], [i["id"] for i in read["increments"]]) == (mine, "brief A", ["A", "B", "C", "ok"])
    assert own_id["id"] == mine
    assert foreign == ("error", "worker sessions may only touch their own hustle")
    for refused in (change, submit, settings):
        assert refused[0] == "refused" and "not available to worker sessions" in refused[1]
    [proposal] = store.proposals(mine)
    assert proposed == {"proposal": proposal.id}
    assert (proposal.author, proposal.increment, proposal.state) == ("worker:B", "B", "open")
    assert store.hustle(mine).version == 1


def test_overseer_changes_only_its_own_plan(world):
    store, tokens, endpoint, mine, other = world
    overseer = tokens.issue("overseer", mine, "ok")
    split = [inc("A"), inc("B1", ["A"]), inc("B2", ["B1"]), inc("C", model="haiku"),
             inc("ok", ["B2", "C"], model="opus", mode="overseer")]
    before = store.hustle(other).version
    changed, foreign, broken, settings, propose = session(
        endpoint, overseer,
        ("change_plan", {"increments": spec(split), "note": "split B"}),
        ("change_plan", {"increments": spec(split), "hustle_id": other}),
        ("change_plan", {"increments": spec(split[:-1])}),
        ("read_settings", {}),
        ("propose_change", {"text": "x"}),
    )
    version = store.hustle(mine).version
    assert changed == {"version": version} and version >= 2
    assert [i.id for i in store.hustle(mine).increments] == ["A", "B1", "B2", "C", "ok"]
    assert store.versions(mine)[-1].author == "overseer:ok"
    assert foreign == ("error", "overseer sessions may only touch their own hustle")
    assert store.hustle(other).version == before
    assert broken[0] == "error" and "no acceptance increment" in broken[1]
    assert settings["allow_fable"] is False and settings["total_points"] == 20
    assert propose[0] == "refused"


def test_interactive_submits_reads_all_and_changes_any_plan(world):
    store, tokens, endpoint, mine, other = world
    fable = [inc("A", model="fable"), inc("ok", ["A"], model="opus", mode="overseer")]
    submitted, listed, read, changed, rejected, relative = session(
        endpoint, "interactive-token",
        ("submit_hustle", {"project": "/repo/c", "title": "New", "brief": "b", "increments": spec(valid_plan())}),
        ("list_hustles", {}),
        ("read_hustle", {"hustle_id": other}),
        ("change_plan", {"increments": spec(valid_plan()), "hustle_id": other, "note": "by hand"}),
        ("submit_hustle", {"project": "/repo/d", "title": "F", "brief": "b", "increments": spec(fable)}),
        ("submit_hustle", {"project": "repo", "title": "R", "brief": "b", "increments": spec(valid_plan())}),
    )
    new = store.hustle(submitted["hustle"])
    assert (new.project, new.title, new.active) == ("/repo/c", "New", False)
    assert store.versions(new.id)[0].author == "interactive"
    assert {h["id"] for h in listed} >= {mine, other}
    assert read["id"] == other
    assert changed["version"] == store.hustle(other).version
    assert rejected[0] == "error" and "Fable is not allowed" in rejected[1]
    assert relative == ("error", "project must be an absolute path to the repository")


@pytest.mark.parametrize("role", ["worker", "overseer", "interactive"])
def test_dashboard_only_actions_are_refused_to_every_role(world, role):
    store, tokens, endpoint, mine, _ = world
    token = "interactive-token" if role == "interactive" else tokens.issue(role, mine)
    before = (store.settings(), store.hustle(mine).weight, store.hustle(mine).active)
    results = session(endpoint, token, *[(name, {"hustle_id": mine, "weight": 9}) for name in DASHBOARD_ONLY])
    assert all(r[0] == "refused" for r in results)
    assert (store.settings(), store.hustle(mine).weight, store.hustle(mine).active) == before


def test_unknown_missing_and_revoked_tokens_get_401(world):
    store, tokens, endpoint, mine, _ = world
    token = tokens.issue("worker", mine)

    def status(headers):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        return httpx2.post(endpoint.url, json=body, headers=headers).status_code

    assert status({}) == 401
    assert status({"Authorization": "Bearer guessed"}) == 401
    assert status({"Authorization": f"Basic {token}"}) == 401
    assert status({"Authorization": f"Bearer {token}"}) != 401
    tokens.revoke(token)
    assert status({"Authorization": f"Bearer {token}"}) == 401
    tokens.revoke("interactive-token")  # the fixed entry survives revocation of sessions
    assert tokens.caller("interactive-token") == Caller("interactive")


def test_endpoint_listens_on_localhost_and_hands_out_a_token_free_config(world):
    _, _, endpoint, _, _ = world
    assert endpoint.url.startswith("http://127.0.0.1:")
    server = endpoint.mcp_config()["mcpServers"]["hustlemaster"]
    assert server == {"type": "http", "url": endpoint.url, "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"}}


def test_gate_without_transport_binds_sessions_to_their_hustle():
    store = Store()
    a = store.submit("/a", "a", "b", valid_plan(), author="user")
    b = store.submit("/b", "b", "b", valid_plan(), author="user")
    worker = Gate(store, Caller("worker", a, "A"))
    assert worker.read_hustle()["id"] == a
    with pytest.raises(Refused):
        worker.read_hustle(b)
    with pytest.raises(Refused):
        worker.change_plan(valid_plan())
    with pytest.raises(Refused):
        worker.list_hustles()
    with pytest.raises(ValueError, match="hustle_id is required"):
        Gate(store, Caller("interactive")).read_hustle()
    with pytest.raises(ValueError, match="session tokens"):
        Tokens().issue("interactive", a)


def test_interactive_token_is_created_once_and_private(tmp_path):
    path = tmp_path / "state" / "interactive-token"
    token = interactive_token(path)
    assert len(token) > 30 and interactive_token(path) == token
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
