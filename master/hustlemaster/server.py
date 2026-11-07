"""The hustlemaster: the service loop (`service.py`) and its web app — the dashboard, its JSON API and the
graph-engine page, behind a token login on a configurable address.

    python3 -m hustlemaster.server --db ~/.local/share/hustlemaster/hustles.db     # from master/
"""

from __future__ import annotations

import argparse
import hmac
import html
import json
import logging
import re
import signal
import socket
import sys
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from . import editing
from .access import token_file
from .graph import graph_doc
from .live import Live
from .service import Service
from .store import Store

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
STATIC = {"/": "index.html", "/app.js": "app.js", "/load.js": "load.js", "/flow.js": "flow.js", "/app.css": "app.css",
          "/thresholds.js": "thresholds.js", "/cinema": "cinema.html", "/cinema.js": "cinema.js"}
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
COOKIE = "hustlemaster"
# long, because the cinema view runs unattended on a display for months; a new token file logs everyone out
COOKIE_AGE = 400 * 24 * 3600


def _flag(value) -> bool:
    # bool("false") is True: only a JSON boolean switches Fable on
    if not isinstance(value, bool):
        raise TypeError("must be true or false")
    return value


def _word(value) -> str:
    if not isinstance(value, str):
        raise TypeError("must be a string")
    return value


# what the dashboard may write, each with how a JSON value becomes the setting; the store checks the ranges
SETTINGS = {"total_points": int, "session_threshold": int, "week_target": int, "allow_fable": _flag,
            "auto_approve": _word, "auto_approve_growth": int}


def engine_page() -> Path:
    """The bundled graph-engine page: from the installed package, else from a sibling checkout."""
    try:
        from graph_engine import page_path

        return page_path()
    except ImportError:
        return Path(__file__).resolve().parents[3] / "graph-engine/src/graph_engine/page/index.html"


def _local(target: str) -> str:
    """Where a login may send the browser: a path on this server only, never another host."""
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def make_handler(live: Live, engine: Path, token: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _send(self, status: int, body: bytes, kind: str, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, status: int = 200) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json")

        def _redirect(self, target: str, headers: dict[str, str] | None = None) -> None:
            self._send(303, b"", "text/plain", {"Location": target, **(headers or {})})

        def _authorized(self) -> bool:
            if token is None:
                return True
            given = None
            try:
                if morsel := SimpleCookie(self.headers.get("Cookie") or "").get(COOKIE):
                    given = morsel.value
            except CookieError:
                pass
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                given = auth.removeprefix("Bearer ")
            return given is not None and hmac.compare_digest(given.encode(), token.encode())

        def _refuse(self) -> None:
            # the API answers scripts and the views' polls; a page is sent to the login and comes back after it
            if self.path.startswith("/api/"):
                return self._json({"error": "login required"}, 401)
            self._redirect("/login?next=" + quote(self.path, safe=""))

        def _login_page(self, target: str, error: str = "", status: int = 200) -> None:
            page = (DASHBOARD / "login.html").read_text()
            page = page.replace("{{next}}", html.escape(target)).replace(
                "{{error}}", f'<p class="error">{html.escape(error)}</p>' if error else "")
            self._send(status, page.encode(), "text/html")

        def _login(self, body: bytes) -> None:
            form = parse_qs(body.decode(errors="replace"))
            given = (form.get("token") or [""])[0].strip()
            target = _local((form.get("next") or ["/"])[0])
            if token is None or not hmac.compare_digest(given.encode(), token.encode()):
                return self._login_page(target, "Wrong token.", 401)
            cookie = f"{COOKIE}={token}; Path=/; Max-Age={COOKIE_AGE}; HttpOnly; SameSite=Lax"
            # behind a TLS-terminating proxy the browser talks https; on the bare address it does not
            if self.headers.get("X-Forwarded-Proto") == "https":
                cookie += "; Secure"
            self._redirect(target, {"Set-Cookie": cookie})

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/login":
                target = (parse_qs(urlsplit(self.path).query).get("next") or ["/"])[0]
                return self._login_page(_local(target))
            if not self._authorized():
                return self._refuse()
            if re.fullmatch(r"/hustles/[\w-]+", path):
                path = "/"  # a route of the dashboard: the page itself, app.js shows the graph
            if path in STATIC:
                file = DASHBOARD / STATIC[path]
                return self._send(200, file.read_bytes(), TYPES[file.suffix])
            if path in ("/engine/", "/engine/index.html"):
                return self._send(200, engine.read_bytes(), "text/html")
            if path == "/api/state":
                return self._json(live.summary())
            match = re.fullmatch(r"/api/hustles/([\w-]+)/graph\.json", path)
            if match and (hustle := live.store.hustle(match.group(1))):
                return self._json(graph_doc(hustle, allow_fable=live.store.settings().allow_fable))
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if self.path == "/login":
                return self._login(raw)
            if not self._authorized():
                return self._refuse()
            body = json.loads(raw or b"{}")
            store = live.store
            try:
                if self.path == "/api/settings":
                    changes = {key: convert(body[key]) for key, convert in SETTINGS.items() if key in body}
                    if "total_points" in changes:
                        changes["total_points"] = max(1, changes["total_points"])
                    # one call, so an invalid value rejects the whole request
                    store.update_settings(**changes)
                    return self._json(live.summary())
                match = re.fullmatch(r"/api/hustles/([\w-]+)", self.path)
                if match and store.hustle(match.group(1)):
                    if "active" in body:
                        store.set_active(match.group(1), bool(body["active"]))
                    if "weight" in body:
                        store.set_weight(match.group(1), max(1, int(body["weight"])))
                    return self._json(live.summary())
                match = re.fullmatch(r"/api/hustles/([\w-]+)/edit", self.path)
                if match and store.hustle(match.group(1)):
                    # from the graph view's panel (graph-engine's edit_url); answers with the graph as it is now
                    editing.apply(store, match.group(1), body)
                    hustle = store.hustle(match.group(1))
                    return self._json(graph_doc(hustle, allow_fable=store.settings().allow_fable))
                match = re.fullmatch(r"/api/approvals/(\d+)", self.path)
                if match:
                    decisions = {"yes": store.approve, "no": store.reject, "revoke": store.revoke}
                    decide = decisions.get(body.get("decision"))
                    if decide is None:
                        return self._json({"error": f"decision must be one of {', '.join(decisions)}"}, 400)
                    decide(int(match.group(1)))
                    return self._json(live.summary())
            except (KeyError, ValueError, TypeError) as e:
                return self._json({"error": str(e)}, 409)
            self._json({"error": "not found"}, 404)

    return Handler


class _Server6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(store: Store, port: int, engine: Path | None = None, live: Live | None = None, *,
          bind: str = "127.0.0.1", token: str | None = None) -> tuple[ThreadingHTTPServer, Live]:
    """The web app on `bind`:`port` (0 picks a free one), not yet serving; the caller runs `serve_forever`.
    `live` is the service's; without one the views show the store with nothing running. With a `token` every
    route but the login needs it, as the login's cookie or a bearer header; without one (tests) nothing does."""
    live = live or Live(store)
    kind = _Server6 if ":" in bind else ThreadingHTTPServer
    server = kind((bind, port), make_handler(live, engine or engine_page(), token))
    return server, live


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="the hustle store (SQLite), created if missing")
    parser.add_argument("--home", help="runs, worktrees and the interactive token; default: beside the store")
    parser.add_argument("--port", type=int, default=8770)  # 8765 is the prototype demo, 8786+ the old loop's monitors
    parser.add_argument("--bind", default="127.0.0.1",
                        help="the web app's address, e.g. a private interface; the MCP endpoint stays on 127.0.0.1")
    # fixed, because the interactive sessions' MCP entry names it
    parser.add_argument("--mcp-port", type=int, default=8771)
    parser.add_argument("--usage-cwd", help="a folder Claude Code trusts, for reading `/usage`; default: a project")
    parser.add_argument("--claude", default="claude")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = engine_page()
    if not engine.is_file():
        parser.error(f"graph-engine page not found at {engine}")
    # without the installer nothing has created the home yet; it holds the tokens, so only the user may enter it
    Path(args.db).resolve().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = Store(args.db)
    home = Path(args.home or Path(args.db).resolve().parent)
    service = Service(store, home, claude=args.claude, mcp_port=args.mcp_port, usage_cwd=args.usage_cwd,
                      interactive=token_file(home / "interactive-token"))
    login = home / "dashboard-token"
    server, _ = serve(store, args.port, engine, service.live, bind=args.bind, token=token_file(login))
    service.start()
    host = f"[{args.bind}]" if ":" in args.bind else args.bind
    print(f"hustlemaster on http://{host}:{server.server_port}/ (login token in {login}), MCP on "
          f"{service.endpoint.url}", flush=True)
    # systemd stops with SIGTERM; leaving through `finally` lets merges under way finish before the process ends
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
