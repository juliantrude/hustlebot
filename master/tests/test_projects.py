"""Projects on the hustle host (ADR 0016): the repository per project, the optional remote, the hand-over check,
non-blocking pushes, and the MCP endpoint the user's other machines reach."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx2
import pytest

from hustlemaster import projects
from hustlemaster.access import Caller, Gate, Tokens
from hustlemaster.mcp_server import Endpoint
from hustlemaster.service import Service
from hustlemaster.store import Store

from test_mcp import session, spec
from test_store import valid_plan


def sh(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, text: str = "x") -> str:
    (repo / name).write_text(text)
    sh(repo, "add", name)
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
    return sh(repo, "rev-parse", "HEAD")


@pytest.fixture
def laptop(tmp_path):
    """The user's checkout, and a bare remote it pushes to."""
    remote = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "laptop"
    sh(tmp_path, "init", "-q", "-b", "main", str(work))
    commit(work, "README")
    sh(work, "remote", "add", "origin", str(remote))
    sh(work, "push", "-q", "origin", "main")
    return work, remote


def test_names_are_plain():
    for bad in ("", "../etc", "a/b", ".hidden", "x" * 65):
        with pytest.raises(projects.ProjectError):
            projects.path_for("/r", bad)
    assert projects.path_for("/r", "my-app.v2") == Path("/r/my-app.v2")


def test_with_a_remote_the_repository_is_cloned_and_follows_it(laptop, tmp_path):
    work, remote = laptop
    here = tmp_path / "host" / "app"
    projects.prepare(here, str(remote))
    first = sh(work, "rev-parse", "HEAD")
    assert projects.contains(here, first)
    later = commit(work, "more")
    assert not projects.contains(here, later)
    sh(work, "push", "-q", "origin", "main")
    assert projects.sync(here) is None and projects.contains(here, later)
    assert (here / "more").is_file()  # main is checked out; its files moved with it


def test_without_a_remote_the_user_pushes_straight_in(laptop, tmp_path):
    work, _ = laptop
    here = tmp_path / "host" / "app"
    projects.prepare(here, None)
    head = sh(work, "rev-parse", "HEAD")
    assert not projects.contains(here, head)
    sh(work, "push", "-q", str(here), "HEAD:main")
    assert projects.contains(here, head)
    assert (here / "README").is_file()  # updateInstead: the checkout follows the push
    commit(here, "by-the-hustlemaster")
    sh(work, "fetch", "-q", str(here), "main")
    assert sh(work, "log", "-1", "--format=%s", "FETCH_HEAD") == "by-the-hustlemaster"


def test_a_diverged_remote_leaves_main_alone(laptop, tmp_path):
    work, remote = laptop
    here = tmp_path / "host" / "app"
    projects.prepare(here, str(remote))
    mine = commit(here, "here")
    commit(work, "there")
    sh(work, "push", "-q", "origin", "main")
    assert "diverged" in projects.sync(here)
    assert projects.head(here) == mine


def test_pushes_follow_the_mode(laptop, tmp_path):
    work, remote = laptop
    here = tmp_path / "host" / "app"
    projects.prepare(here, str(remote))
    head = commit(here, "result")
    assert projects.push(here, "off", "h1") is None
    assert sh(remote, "rev-parse", "main") != head
    assert projects.push(here, "branch", "h1") is None
    assert sh(remote, "rev-parse", "hustle/h1") == head and sh(remote, "rev-parse", "main") != head
    assert projects.push(here, "main", "h1") is None
    assert sh(remote, "rev-parse", "main") == head
    projects.prepare(here, None)
    assert projects.push(here, "main", "h1") is None  # no remote, nothing to push to


def test_required_files():
    assert projects.missing(Path("/"), ["etc", "no-such-file"]) == ["no-such-file"]


def test_submit_checks_the_hand_over(laptop, tmp_path):
    work, remote = laptop
    store = Store()
    gate = Gate(store, Caller("interactive"), tmp_path / "hustles")
    head = commit(work, "plan-base")
    with pytest.raises(ValueError, match="push it to the project's remote"):
        gate.submit_hustle("app", "T", "b", valid_plan(), commit=head, remote=str(remote), push="branch",
                           required=[".env"])
    sh(work, "push", "-q", "origin", "main")
    answer = gate.submit_hustle("app", "T", "b", valid_plan(), commit=head, remote=str(remote), push="branch",
                                required=[".env"])
    assert answer["repository"] == str(tmp_path / "hustles" / "app")
    assert (answer["remote"], answer["push"], answer["missing_files"]) == (str(remote), "branch", [".env"])
    # without a remote the first try creates the repository and names it
    with pytest.raises(ValueError, match="push it here"):
        gate.submit_hustle("solo", "T", "b", valid_plan(), commit=head, remote="")
    sh(work, "push", "-q", str(tmp_path / "hustles" / "solo"), "HEAD:main")
    assert gate.submit_hustle("solo", "T", "b", valid_plan(), commit=head)["remote"] is None


def test_a_failed_push_is_retried_and_the_button_pushes_now(laptop, tmp_path):
    work, remote = laptop
    store = Store()
    root = tmp_path / "hustles"
    answer = Gate(store, Caller("interactive"), root).submit_hustle(
        "app", "T", "b", valid_plan(), remote=str(remote), push="branch")
    hid, here = answer["hustle"], Path(answer["repository"])
    service = Service(store, tmp_path / "home", projects_root=root)
    head = commit(here, "result")
    store.push_wanted(hid, head)
    remote.rename(tmp_path / "gone.git")  # the remote is unreachable
    service._retry_pushes()
    service.drain()
    failed = store.push_state(hid)
    assert failed.pending == head and failed.error and failed.tries == 1
    service._retry_pushes()  # inside the backoff: no new try
    service.drain()
    assert store.push_state(hid).tries == 1
    (tmp_path / "gone.git").rename(remote)
    state = service.push_now(hid)
    assert (state["pending"], state["error"]) == (None, None) and state["last_ok"]
    assert sh(remote, "rev-parse", f"hustle/{hid}") == head
    service.endpoint.stop()


def test_the_external_endpoint_takes_only_the_interactive_token(tmp_path):
    store = Store()
    tokens = Tokens("user-token")
    worker = tokens.issue("worker", "h1")
    outside = Endpoint(store, tokens, host="127.0.0.1", roles=frozenset({"interactive"}),
                       names=["hustle.example.org"]).start()
    try:
        def status(token, host=None):
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                       "Content-Type": "application/json"}
            if host:
                headers["Host"] = host
            body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "t", "version": "1"}}}
            return httpx2.post(outside.url, headers=headers, json=body, timeout=10).status_code

        assert status(worker) == 401
        assert status("user-token") == 200
        assert status("user-token", "hustle.example.org") == 200  # the name the proxy passes on
        assert status("user-token", "evil.example.org") != 200
        assert "submit_hustle" in session(outside, "user-token", ("list", None))[0]
    finally:
        outside.stop()


def test_project_env_files(tmp_path):
    from hustlemaster.runner import read_env

    env = tmp_path / "app.env"
    env.write_text("# test user\nexport E2E_USER=e2e\nE2E_PASSWORD='p w=1'\nnot a line\n1BAD=x\n")
    assert read_env(env) == {"E2E_USER": "e2e", "E2E_PASSWORD": "p w=1"}
    assert read_env(tmp_path / "missing.env") == {}
