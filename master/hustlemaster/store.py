"""The hustlemaster's database: hustles, versioned plans, increment progress and settings (ADR 0014).

A plan version is a full snapshot of the increments' definitions, so any version can be shown or exported as
it was. Progress (state, attempts, peak context) belongs to the increment id, not to a version: an edit
applies to the next attempt and must not reset what already happened.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from . import plan
from .model import STATES, Hustle, Increment

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
    path TEXT PRIMARY KEY,
    push INTEGER NOT NULL DEFAULT 0,
    copy_excludes TEXT  -- JSON list overriding the default excludes for gitignored files (ADR 0011); NULL = default
);
CREATE TABLE IF NOT EXISTS hustles (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL REFERENCES projects(path),
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    weight INTEGER NOT NULL DEFAULT 1 CHECK (weight >= 1),
    confirmed_version INTEGER,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_versions (
    hustle TEXT NOT NULL REFERENCES hustles(id),
    version INTEGER NOT NULL,
    author TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    PRIMARY KEY (hustle, version)
);
CREATE TABLE IF NOT EXISTS plan_increments (
    hustle TEXT NOT NULL,
    version INTEGER NOT NULL,
    position INTEGER NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    after TEXT NOT NULL,  -- JSON list of increment ids
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    PRIMARY KEY (hustle, version, id),
    FOREIGN KEY (hustle, version) REFERENCES plan_versions(hustle, version)
);
CREATE TABLE IF NOT EXISTS progress (
    hustle TEXT NOT NULL REFERENCES hustles(id),
    increment TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    attempts INTEGER NOT NULL DEFAULT 0,
    peak_context INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hustle, increment)
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    hustle TEXT NOT NULL REFERENCES hustles(id),
    increment TEXT,  -- the increment the proposing worker runs, if any
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',  -- open until an event overseer has handled it (ADR 0015)
    created REAL NOT NULL
);
"""

AUTO_APPROVE = ("off", "limited", "all")


@dataclass
class Settings:
    total_points: int = 20
    session_threshold: int = 85  # percent of the 5h window above which nothing new starts (ADR 0008)
    week_target: int = 95  # percent the week should reach at its reset; the target line ends here
    allow_fable: bool = False
    auto_approve: str = "off"
    auto_approve_growth: int = 25  # percent of growth approved automatically in "limited" mode, plus one model step


@dataclass
class Project:
    path: str
    push: bool = False
    copy_excludes: list[str] | None = None


@dataclass
class Proposal:
    id: int
    hustle: str
    increment: str | None
    author: str
    text: str
    state: str
    created: float


@dataclass
class PlanVersion:
    version: int
    author: str
    note: str
    created: float
    increments: list[Increment] = field(default_factory=list)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        # One connection shared by the dashboard, the MCP endpoint and the runner threads; the lock keeps their
        # transactions from interleaving on it.
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._lock = threading.RLock()
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # settings

    def settings(self) -> Settings:
        stored = {row["key"]: json.loads(row["value"]) for row in self.db.execute("SELECT key, value FROM settings")}
        return Settings(**{f.name: stored[f.name] for f in fields(Settings) if f.name in stored})

    def update_settings(self, **changes) -> Settings:
        new = replace(self.settings(), **changes)
        if new.total_points < 1:
            raise ValueError("total_points must be at least 1")
        if not (0 <= new.session_threshold <= 100 and 0 <= new.week_target <= 100):
            raise ValueError("thresholds are percentages between 0 and 100")
        if new.auto_approve not in AUTO_APPROVE:
            raise ValueError(f"auto_approve must be one of {AUTO_APPROVE}")
        with self.db:
            self.db.execute("BEGIN")
            for key, value in changes.items():
                self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(getattr(new, key))))
        return new

    # projects

    def project(self, path: str) -> Project | None:
        row = self.db.execute("SELECT * FROM projects WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        excludes = json.loads(row["copy_excludes"]) if row["copy_excludes"] is not None else None
        return Project(row["path"], bool(row["push"]), excludes)

    def set_project(self, path: str, *, push: bool | None = None, copy_excludes: list[str] | None = None) -> Project:
        current = self.project(path) or Project(path)
        if push is not None:
            current.push = push
        if copy_excludes is not None:
            current.copy_excludes = copy_excludes
        self.db.execute(
            "INSERT OR REPLACE INTO projects VALUES (?, ?, ?)",
            (path, int(current.push), json.dumps(current.copy_excludes) if current.copy_excludes is not None else None),
        )
        return current

    # hustles and plans

    def submit(self, project: str, title: str, brief: str, increments: list[Increment], *, author: str) -> str:
        """Store a new hustle. It arrives inactive; the first activation confirms it (ADR 0009)."""
        self._check(increments)
        hustle_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute("BEGIN")
            if self.project(project) is None:
                self.db.execute("INSERT INTO projects (path) VALUES (?)", (project,))
            self.db.execute(
                "INSERT INTO hustles (id, project, title, brief, created) VALUES (?, ?, ?, ?, ?)",
                (hustle_id, project, title, brief, time.time()),
            )
            self._write_version(hustle_id, 1, increments, author, "submitted")
        return hustle_id

    def change_plan(self, hustle_id: str, increments: list[Increment], *, author: str, note: str = "") -> int:
        """Store a new plan version. Progress of increments that keep their id carries over."""
        self._require(hustle_id)
        self._check(increments)
        with self.db:
            self.db.execute("BEGIN")
            version = self._version(hustle_id) + 1
            self._write_version(hustle_id, version, increments, author, note)
        return version

    def hustle(self, hustle_id: str, version: int | None = None) -> Hustle | None:
        row = self.db.execute("SELECT * FROM hustles WHERE id = ?", (hustle_id,)).fetchone()
        if row is None:
            return None
        current = self._version(hustle_id)
        return Hustle(
            id=row["id"],
            project=row["project"],
            title=row["title"],
            active=bool(row["active"]),
            weight=row["weight"],
            increments=self._increments(hustle_id, version or current),
            brief=row["brief"],
            version=version or current,
            confirmed_version=row["confirmed_version"],
        )

    def hustles(self) -> list[Hustle]:
        ids = [row["id"] for row in self.db.execute("SELECT id FROM hustles ORDER BY created, id")]
        return [self.hustle(i) for i in ids]

    def versions(self, hustle_id: str) -> list[PlanVersion]:
        rows = self.db.execute(
            "SELECT * FROM plan_versions WHERE hustle = ? ORDER BY version", (hustle_id,)
        ).fetchall()
        return [
            PlanVersion(r["version"], r["author"], r["note"], r["created"], self._increments(hustle_id, r["version"]))
            for r in rows
        ]

    def set_active(self, hustle_id: str, active: bool) -> None:
        self._require(hustle_id)
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("UPDATE hustles SET active = ? WHERE id = ?", (int(active), hustle_id))
            if active:
                self.db.execute(
                    "UPDATE hustles SET confirmed_version = ? WHERE id = ? AND confirmed_version IS NULL",
                    (self._version(hustle_id), hustle_id),
                )

    def confirm(self, hustle_id: str) -> None:
        """Make the current plan the baseline that growth is measured against (an approval, ADR 0015)."""
        self._require(hustle_id)
        self.db.execute("UPDATE hustles SET confirmed_version = ? WHERE id = ?", (self._version(hustle_id), hustle_id))

    def set_weight(self, hustle_id: str, weight: int) -> None:
        self._require(hustle_id)
        if weight < 1:
            raise ValueError("a weight is at least 1")
        self.db.execute("UPDATE hustles SET weight = ? WHERE id = ?", (weight, hustle_id))

    # progress

    def set_state(self, hustle_id: str, increment: str, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        self._progress(hustle_id, increment, "state = ?", state)

    def count_attempt(self, hustle_id: str, increment: str) -> None:
        self._progress(hustle_id, increment, "attempts = attempts + 1")

    def note_peak_context(self, hustle_id: str, increment: str, tokens: int) -> None:
        self._progress(hustle_id, increment, "peak_context = MAX(peak_context, ?)", tokens)

    # proposals

    def propose(self, hustle_id: str, text: str, *, author: str, increment: str | None = None) -> int:
        """A worker's plan change request; it changes nothing until an overseer acts on it (ADR 0015)."""
        self._require(hustle_id)
        if not text.strip():
            raise ValueError("a proposal needs a text")
        cursor = self.db.execute(
            "INSERT INTO proposals (hustle, increment, author, text, created) VALUES (?, ?, ?, ?, ?)",
            (hustle_id, increment, author, text, time.time()),
        )
        return cursor.lastrowid

    def proposals(self, hustle_id: str, *, state: str | None = None) -> list[Proposal]:
        rows = self.db.execute(
            "SELECT * FROM proposals WHERE hustle = ? AND (? IS NULL OR state = ?) ORDER BY id",
            (hustle_id, state, state),
        ).fetchall()
        return [Proposal(**dict(r)) for r in rows]

    # internals

    def _check(self, increments: list[Increment]) -> None:
        settings = self.settings()
        plan.check(increments, allow_fable=settings.allow_fable, total_points=settings.total_points)

    def _require(self, hustle_id: str) -> None:
        if self.db.execute("SELECT 1 FROM hustles WHERE id = ?", (hustle_id,)).fetchone() is None:
            raise KeyError(f"unknown hustle {hustle_id!r}")

    def _version(self, hustle_id: str) -> int:
        row = self.db.execute("SELECT MAX(version) FROM plan_versions WHERE hustle = ?", (hustle_id,)).fetchone()
        return row[0] or 0

    def _write_version(self, hustle_id: str, version: int, increments: list[Increment], author: str, note: str) -> None:
        self.db.execute(
            "INSERT INTO plan_versions VALUES (?, ?, ?, ?, ?)", (hustle_id, version, author, note, time.time())
        )
        for position, inc in enumerate(increments):
            self.db.execute(
                "INSERT INTO plan_increments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hustle_id, version, position, inc.id, inc.title, inc.prompt, json.dumps(inc.after), inc.model, inc.mode),
            )
            self.db.execute("INSERT OR IGNORE INTO progress (hustle, increment) VALUES (?, ?)", (hustle_id, inc.id))

    def _increments(self, hustle_id: str, version: int) -> list[Increment]:
        rows = self.db.execute(
            """SELECT p.*, g.state, g.attempts, g.peak_context FROM plan_increments p
               JOIN progress g ON g.hustle = p.hustle AND g.increment = p.id
               WHERE p.hustle = ? AND p.version = ? ORDER BY p.position""",
            (hustle_id, version),
        ).fetchall()
        return [
            Increment(
                id=r["id"],
                title=r["title"],
                model=r["model"],
                instructions=[],
                after=json.loads(r["after"]),
                state=r["state"],
                prompt=r["prompt"],
                mode=r["mode"],
                attempts=r["attempts"],
                peak_context=r["peak_context"],
            )
            for r in rows
        ]

    def _progress(self, hustle_id: str, increment: str, assignment: str, *args) -> None:
        cursor = self.db.execute(
            f"UPDATE progress SET {assignment} WHERE hustle = ? AND increment = ?", (*args, hustle_id, increment)
        )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown increment {increment!r} in hustle {hustle_id!r}")


def _locked(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


for _name, _member in list(vars(Store).items()):
    if callable(_member) and not _name.startswith("_"):
        setattr(Store, _name, _locked(_member))
