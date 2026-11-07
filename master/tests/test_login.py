"""The web app's token login (ADR 0013): nothing but the login page answers without the token, the login sets a
cookie the views and the graph page then carry, and the MCP endpoint never follows `--bind`."""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest

from conftest import seed
from hustlemaster.server import COOKIE, serve
from hustlemaster.store import Store

MASTER = Path(__file__).resolve().parent.parent
TOKEN = "s3cret-token-for-the-tests"


@pytest.fixture
def app():
    store = Store()
    ids = seed(store)
    server, _ = serve(store, 0, token=TOKEN)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port, ids, store
    server.shutdown()
    server.server_close()
    store.close()


def request(port, method, path, body=None, headers=None, host="127.0.0.1"):
    """One request, redirects not followed: the tests look at the 303s themselves."""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    conn.request(method, path, body=data, headers=headers or {})
    res = conn.getresponse()
    answer = res.status, dict(res.getheaders()), res.read()
    conn.close()
    return answer


def login(port, token=TOKEN, next="/", headers=None):
    form = urlencode({"token": token, "next": next}).encode()
    return request(port, "POST", "/login", form,
                   {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})


def cookie_of(port):
    status, headers, _ = login(port)
    assert status == 303
    return headers["Set-Cookie"].split(";", 1)[0]


def test_without_the_token_the_api_answers_401_and_pages_go_to_the_login(app):
    port, ids, store = app
    for path in ("/api/state", f"/api/hustles/{ids['master']}/graph.json"):
        status, _, body = request(port, "GET", path)
        assert status == 401 and json.loads(body) == {"error": "login required"}
    for path, back in [("/", "%2F"), ("/cinema", "%2Fcinema"), ("/app.js", "%2Fapp.js"), ("/engine/", "%2Fengine%2F"),
                       (f"/hustles/{ids['master']}?x=1", f"%2Fhustles%2F{ids['master']}%3Fx%3D1")]:
        status, headers, _ = request(port, "GET", path)
        assert (status, headers["Location"]) == (303, f"/login?next={back}"), path
    before = store.settings()
    for path, body in [("/api/settings", {"total_points": 3}), (f"/api/hustles/{ids['docs']}", {"active": True}),
                       (f"/api/hustles/{ids['sync']}/edit", {"node": "B", "action": "pause"}),
                       ("/api/approvals/1", {"decision": "yes"})]:
        assert request(port, "POST", path, body)[0] == 401, path
    assert store.settings() == before
    assert not store.hustle(ids["docs"]).active
    assert store.approvals(ids["master"], states=("pending",))
    assert store.hustle(ids["sync"]).increments[1].held != "paused"


def test_a_wrong_or_malformed_credential_is_refused(app):
    port, _, _ = app
    for headers in [{"Cookie": f"{COOKIE}=nope"}, {"Cookie": f"{COOKIE}={TOKEN}x"}, {"Cookie": 'a="\\'},
                    {"Cookie": f"other={TOKEN}"}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}]:
        assert request(port, "GET", "/api/state", headers=headers)[0] == 401, headers
    status, headers, body = login(port, "nope")
    assert status == 401 and "Set-Cookie" not in headers and b"Wrong token." in body


def test_the_login_sets_a_cookie_that_opens_every_route(app):
    port, ids, store = app
    status, headers, body = request(port, "GET", "/login?next=%2Fcinema")
    assert status == 200 and b'name="token"' in body and b'value="/cinema"' in body
    status, headers, _ = login(port, next="/cinema")
    assert (status, headers["Location"]) == (303, "/cinema")
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"{COOKIE}={TOKEN}; Path=/;")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" not in cookie
    with_cookie = {"Cookie": cookie.split(";", 1)[0]}
    for path in ("/", "/cinema", "/app.js", "/engine/", f"/hustles/{ids['master']}", "/api/state",
                 f"/api/hustles/{ids['master']}/graph.json"):
        assert request(port, "GET", path, headers=with_cookie)[0] == 200, path
    status, _, body = request(port, "POST", "/api/settings", {"total_points": 7}, with_cookie)
    assert status == 200 and store.settings().total_points == 7
    status, _, body = request(port, "POST", f"/api/hustles/{ids['sync']}/edit", {"node": "B", "action": "pause"},
                              with_cookie)
    assert status == 200 and store.hustle(ids["sync"]).increments[1].held == "paused"
    # scripts may send the token as a bearer header instead
    assert request(port, "GET", "/api/state", headers={"Authorization": f"Bearer {TOKEN}"})[0] == 200


def test_behind_a_tls_proxy_the_cookie_is_secure(app):
    port, _, _ = app
    _, headers, _ = login(port, headers={"X-Forwarded-Proto": "https"})
    assert headers["Set-Cookie"].endswith("; Secure")


@pytest.mark.parametrize("target", ["//evil.example/", "https://evil.example/", "/\\evil.example", "evil", ""])
def test_the_login_only_sends_the_browser_to_this_server(app, target):
    port, _, _ = app
    status, headers, _ = login(port, next=target)
    assert (status, headers["Location"]) == (303, "/")


def test_the_login_page_escapes_what_it_echoes(app):
    port, _, _ = app
    _, _, body = request(port, "GET", '/login?next=%2F%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E')
    assert b"<script>" not in body and b"&quot;&gt;&lt;script&gt;" in body


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def outward_address() -> str | None:
    """An address of this machine other than loopback (a UDP connect sends nothing), or None offline."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address = s.getsockname()[0]
    return None if address.startswith("127.") else address


def wait_for(port, host="127.0.0.1"):
    for _ in range(80):
        try:
            return request(port, "GET", "/login", host=host)
        except OSError:
            time.sleep(0.1)
    pytest.fail("the server did not start")


def test_the_service_binds_where_told_and_keeps_mcp_on_localhost(tmp_path):
    outward = outward_address()
    if outward is None:
        pytest.skip("no address besides loopback")
    db = tmp_path / "hustles.db"
    seed(Store(db))
    port, mcp = free_port(), free_port()
    proc = subprocess.Popen([sys.executable, "-m", "hustlemaster.server", "--db", str(db), "--port", str(port),
                             "--mcp-port", str(mcp), "--bind", "0.0.0.0"], cwd=MASTER)
    try:
        wait_for(port)
        token = (tmp_path / "dashboard-token").read_text().strip()
        assert len(token) > 30 and token != (tmp_path / "interactive-token").read_text().strip()
        bearer = {"Authorization": f"Bearer {token}"}
        assert request(port, "GET", "/api/state", headers=bearer, host=outward)[0] == 200
        assert request(port, "GET", "/api/state", host=outward)[0] == 401
        # the MCP endpoint answers on loopback (401 without a session token) and not at all on the outward address
        assert request(mcp, "POST", "/mcp", {}, host="127.0.0.1")[0] == 401
        with pytest.raises(ConnectionRefusedError):
            request(mcp, "POST", "/mcp", {}, host=outward)
    finally:
        proc.terminate()
        proc.wait(5)


def test_the_default_bind_is_loopback_only(tmp_path):
    outward = outward_address()
    if outward is None:
        pytest.skip("no address besides loopback")
    db = tmp_path / "hustles.db"
    seed(Store(db))
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "hustlemaster.server", "--db", str(db), "--port", str(port),
                             "--mcp-port", str(free_port())], cwd=MASTER)
    try:
        wait_for(port)
        with pytest.raises(ConnectionRefusedError):
            request(port, "GET", "/login", host=outward)
    finally:
        proc.terminate()
        proc.wait(5)
