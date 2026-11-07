"""The hustlemaster's MCP endpoint (ADR 0014): plans are read, proposed, changed and submitted here.

Streamable HTTP on localhost only, in the hustlemaster process. Each request carries a bearer token; an unknown
token gets a 401 before MCP sees it. The tools a caller may not use are hidden from its tool list and refused
if called anyway, so a worker's model never even sees `change_plan`.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, Field

from .access import ACTIONS, Caller, Gate, Refused, Tokens
from .model import Increment
from .plan import PlanError
from .store import Store

PATH = "/mcp"
# The session's token reaches the header through this variable, so the `--mcp-config` file holds no secret.
# Our own name: Claude Code blanks `ANTHROPIC_*` variables in headers (ADR 0014).
TOKEN_ENV = "HUSTLEMASTER_TOKEN"


class IncrementSpec(BaseModel):
    id: str = Field(description="Short, unique within the plan; dependencies refer to it")
    title: str
    prompt: str = Field(description="Self-contained instructions for the hustlebot, including how to verify")
    after: list[str] = Field(default_factory=list, description="Ids of the increments this one depends on")
    model: str = Field(description="haiku, sonnet, opus or fable")
    mode: str = Field("worker", description="worker, or overseer for acceptance and milestone checks")

    def increment(self) -> Increment:
        return Increment(id=self.id, title=self.title, model=self.model, instructions=[], after=self.after,
                         prompt=self.prompt, mode=self.mode)


def bearer(headers: Any) -> str | None:
    value = headers.get("authorization", "") if headers is not None else ""
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


def build(store: Store, tokens: Tokens, root: Path | None = None) -> MCPServer:
    def gate(ctx: Context) -> Gate:
        caller = tokens.caller(bearer(ctx.headers))
        if caller is None:  # the ASGI guard already refused it; this only covers a token revoked mid-request
            raise ToolError("unknown or revoked token")
        return Gate(store, caller, root)

    def run(call):
        try:
            return call()
        except PlanError as err:
            raise ToolError("plan rejected:\n- " + "\n- ".join(err.problems)) from None
        except (Refused, KeyError, ValueError) as err:
            raise ToolError(str(err.args[0] if isinstance(err, KeyError) else err)) from None

    server = MCPServer(
        "hustlemaster",
        instructions="Plans of the hustles this machine runs. Your token decides which hustle and which tools.",
        middleware=[RoleFilter(tokens)],
    )

    @server.tool(description="All hustles on this machine with their status and plan version.")
    def list_hustles(ctx: Context) -> str:
        return json.dumps(run(lambda: gate(ctx).list_hustles()))

    @server.tool(description="A hustle's brief and current plan with each increment's state. Workers and "
                             "overseers read their own hustle and leave hustle_id empty.")
    def read_hustle(ctx: Context, hustle_id: str = "") -> str:
        return json.dumps(run(lambda: gate(ctx).read_hustle(hustle_id or None)))

    @server.tool(description="Ask for a plan change (split, reorder, new increment, different model). It "
                             "changes nothing by itself; an overseer decides.")
    def propose_change(ctx: Context, text: str) -> str:
        return json.dumps({"proposal": run(lambda: gate(ctx).propose_change(text))})

    @server.tool(description="Replace the plan with a new version. Pass every increment, also the unchanged "
                             "ones; progress carries over by id. Overseers leave hustle_id empty. If the "
                             "answer names an approval, the increments it lists wait until the user decides.")
    def change_plan(ctx: Context, increments: list[IncrementSpec], note: str = "", hustle_id: str = "") -> str:
        incs = [i.increment() for i in increments]
        return json.dumps(run(lambda: gate(ctx).change_plan(incs, note, hustle_id or None)))

    @server.tool(description="Submit a new hustle. It arrives paused; the user activates it in the dashboard. "
                             "project is the project's name on the hustlemaster's host; its repository is created "
                             "there on first use (cloned from remote, or empty for the user to push into). commit "
                             "is the commit the plan was made against; it must already be in that repository's "
                             "main. remote, push (off, branch, main), verify and required (ignored files the "
                             "project needs, e.g. .env) are the project's settings; leave them out to keep what "
                             "the project has. The answer names the repository and any missing required files.")
    def submit_hustle(ctx: Context, project: str, title: str, brief: str, increments: list[IncrementSpec],
                      commit: str = "", remote: str | None = None, push: str | None = None,
                      verify: str | None = None, required: list[str] | None = None) -> str:
        incs = [i.increment() for i in increments]
        return json.dumps(run(lambda: gate(ctx).submit_hustle(project, title, brief, incs, commit=commit or None,
                                                              remote=remote, push=push, verify=verify,
                                                              required=required)))

    @server.tool(description="The hustlemaster's settings: total points, thresholds, allow_fable, auto-approve.")
    def read_settings(ctx: Context) -> str:
        return json.dumps(run(lambda: gate(ctx).read_settings()))

    missing = ACTIONS - {"list_hustles", "read_hustle", "propose_change", "change_plan", "submit_hustle",
                         "read_settings"}
    assert not missing, f"rights without a tool: {missing}"
    return server


class RoleFilter:
    """Hides and refuses the tools outside the caller's rights before any handler runs."""

    def __init__(self, tokens: Tokens) -> None:
        self.tokens = tokens

    def _caller(self, ctx: ServerRequestContext) -> Caller | None:
        return self.tokens.caller(bearer(getattr(ctx.request, "headers", None)))

    async def __call__(self, ctx: ServerRequestContext, call_next):
        if ctx.method == "tools/call":
            caller = self._caller(ctx)
            name = (ctx.params or {}).get("name")
            if caller is None or not Gate(None, caller).allowed(name):
                role = caller.role if caller else "unknown"
                raise MCPError(code=-32601, message=f"tool {name!r} is not available to {role} sessions")
        result = await call_next(ctx)
        if ctx.method == "tools/list":
            caller = self._caller(ctx)
            gate = Gate(None, caller) if caller else None
            # the runner hands middleware the serialised result on some paths and the model on others
            if isinstance(result, dict):
                result["tools"] = [t for t in result["tools"] if gate and gate.allowed(t["name"])]
            else:
                result.tools = [t for t in result.tools if gate and gate.allowed(t.name)]
        return result


class TokenGuard:
    """ASGI wrapper: no MCP traffic without a known bearer token — of one of `roles`, if given. The endpoint the
    network reaches takes only the user's interactive token (ADR 0016): a hustlebot's session token is no use
    outside its own machine."""

    def __init__(self, app, tokens: Tokens, roles: frozenset[str] | None = None) -> None:
        self.app = app
        self.tokens = tokens
        self.roles = roles

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            caller = self.tokens.caller(bearer(headers))
            if caller is None or (self.roles is not None and caller.role not in self.roles):
                body = b'{"error": "unknown or missing bearer token"}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def app(store: Store, tokens: Tokens, root: Path | None = None, roles: frozenset[str] | None = None,
        names: list[str] | None = None):
    """`names` are the Host headers the endpoint answers besides its own address, e.g. the name a reverse proxy
    passes on; the SDK refuses any other (DNS rebinding protection), and without them only localhost works."""
    security = None
    if names:
        security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=list(names),
                                             allowed_origins=[f"https://{n}" for n in names])
    inner = build(store, tokens, root).streamable_http_app(streamable_http_path=PATH, transport_security=security)
    return TokenGuard(inner, tokens, roles)


class Endpoint:
    """The MCP endpoint running in a thread of the hustlemaster process. The hustlebots' one is on 127.0.0.1
    (ADR 0013); a second one on `host` serves the user's sessions on other machines, interactive tokens only."""

    def __init__(self, store: Store, tokens: Tokens, port: int = 0, *, host: str = "127.0.0.1",
                 root: Path | None = None, roles: frozenset[str] | None = None, names: list[str] | None = None) -> None:
        self.tokens = tokens
        self.host = host
        self._sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self.port = self._sock.getsockname()[1]
        if names:
            names = [*names, f"{host}:{self.port}"]
        config = uvicorn.Config(app(store, tokens, root, roles, names), log_level="warning", lifespan="on")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [self._sock]}, daemon=True)

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}{PATH}"

    def mcp_config(self) -> dict:
        """What a hustlebot gets as `--mcp-config`; its environment carries the token in `TOKEN_ENV`."""
        return {"mcpServers": {"hustlemaster": {"type": "http", "url": self.url,
                                                "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"}}}}

    def start(self) -> Endpoint:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("the MCP endpoint did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        if not self._thread.is_alive():
            self._sock.close()  # bound in the constructor even if it never served
            return
        self._server.should_exit = True
        self._thread.join(timeout=10)
