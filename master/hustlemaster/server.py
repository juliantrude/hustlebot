"""The hustlemaster: the service loop (`service.py`) and its web app on 127.0.0.1 — the dashboard, its JSON API
and the graph-engine page.

    python3 -m hustlemaster.server --db ~/.local/share/hustlemaster/hustles.db     # from master/
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .access import interactive_token
from .graph import graph_doc
from .live import Live
from .service import Service
from .store import Store

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
STATIC = {"/": "index.html", "/app.js": "app.js", "/load.js": "load.js", "/flow.js": "flow.js", "/app.css": "app.css",
          "/thresholds.js": "thresholds.js", "/cinema": "cinema.html", "/cinema.js": "cinema.js"}
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


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


def make_handler(live: Live, engine: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, status: int = 200) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json")

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
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
                return self._json(graph_doc(hustle))
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
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


def serve(store: Store, port: int, engine: Path | None = None,
          live: Live | None = None) -> tuple[ThreadingHTTPServer, Live]:
    """The web app on 127.0.0.1:`port` (0 picks a free one), not yet serving; the caller runs `serve_forever`.
    `live` is the service's; without one the views show the store with nothing running."""
    live = live or Live(store)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(live, engine or engine_page()))
    return server, live


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="the hustle store (SQLite), created if missing")
    parser.add_argument("--home", help="runs, worktrees and the interactive token; default: beside the store")
    parser.add_argument("--port", type=int, default=8770)  # 8765 is the prototype demo, 8786+ the old loop's monitors
    # fixed, because the interactive sessions' MCP entry names it
    parser.add_argument("--mcp-port", type=int, default=8771)
    parser.add_argument("--usage-cwd", help="a folder Claude Code trusts, for reading `/usage`; default: a project")
    parser.add_argument("--claude", default="claude")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = engine_page()
    if not engine.is_file():
        parser.error(f"graph-engine page not found at {engine}")
    store = Store(args.db)
    home = Path(args.home or Path(args.db).resolve().parent)
    service = Service(store, home, claude=args.claude, mcp_port=args.mcp_port, usage_cwd=args.usage_cwd,
                      interactive=interactive_token(home / "interactive-token"))
    server, _ = serve(store, args.port, engine, service.live)
    service.start()
    print(f"hustlemaster on http://127.0.0.1:{server.server_port}/, MCP on {service.endpoint.url}", flush=True)
    try:
        server.serve_forever()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
