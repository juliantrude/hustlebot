"""How full the subscription's windows are (ADR 0012): three sources, combined.

1. **The `/usage` screen.** Headless `/usage` does not report the windows reliably, so `read_screen` starts an
   invisible interactive Claude Code session in a pseudo-terminal, types `/usage` and renders what it shows with a
   terminal emulator. No prompt reaches the model; the reading is free and can be repeated at will.
2. **Own accounting.** Every finished session reports its `total_cost_usd`, and its stream carries the windows'
   utilization as the API saw them near its start. Between two readings of the same window the hustlemaster
   learns how many percent a dollar of usage costs, and extrapolates from the last reading with what finished
   since. Cost in dollars weighs the models the way the subscription does closely enough; the readings correct
   the drift.
3. **Safety net.** A session rejected for a limit blocks new starts until the reset it names, or, when it names
   none, for an exponential backoff (15 min doubling to 4 h, as `hustle-session.sh` does). A fresh screen reading
   below the limits lifts the block early: it costs nothing, unlike a probing session.

The screen is a layout, not an API, and breaks when Claude Code changes it; the stream readings and the
accounting keep the hustlemaster informed meanwhile, and the safety net keeps it from hammering a limit.
"""

from __future__ import annotations

import os
import signal
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SESSION, WEEK = "session", "week"  # the 5h window and the weekly window across all models

COLS, ROWS = 160, 50
STARTUP_TIMEOUT = 45.0
SCREEN_TIMEOUT = 20.0
QUIET = 1.0  # the prompt is drawn before the input accepts keys; typing waits until the screen settles

BACKOFF_FIRST, BACKOFF_MAX = 900, 14400
RESET_GRACE = timedelta(minutes=2)  # a reset time on the screen is rounded; starting right on it can still bounce
SAME_WINDOW = timedelta(hours=1)  # two readings whose resets are this close describe the same window
MIN_SPEND = 0.5  # dollars between two readings before their difference says anything about the rate
RATE_SMOOTHING = 0.3

TRUST_MARK = "trust this folder"
_PROMPT = re.compile(r"^\s*❯", re.MULTILINE)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)% used")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_DATE = re.compile(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\b,?(?:\s+at\b)?")
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_ZONE = re.compile(r"\(([^()]+)\)\s*$")
_LIMIT = re.compile(r"usage limit|rate limit|session limit|weekly limit|hit your limit|limit reached", re.IGNORECASE)
_EPOCH = re.compile(r"\|(\d{10})\b")
_ISO = re.compile(r"reset[^0-9]{0,14}(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})", re.IGNORECASE)
_RESETS_TEXT = re.compile(r"resets?\s+(?:at\s+)?([^·\n]+)", re.IGNORECASE)


class UsageError(Exception):
    """The screen could not be read: Claude Code did not start, asked about trust, or changed its layout."""


@dataclass(frozen=True)
class Window:
    percent: float
    resets: datetime | None  # unknown when the screen shows none, e.g. for an unused window


@dataclass(frozen=True)
class Reading:
    at: datetime  # when the numbers were true
    windows: dict[str, Window]  # `session`, `week`, and per-model weekly windows under their screen name
    source: str  # `screen` or `stream`


@dataclass(frozen=True)
class Estimate:
    percent: float  # the last reading, plus what finished since at the learned rate
    resets: datetime | None
    measured: float  # the last reading itself; 0 once its window has reset
    measured_at: datetime
    spent: float  # dollars finished since the reading (or since the reset)
    extrapolated: bool  # False while no rate is learned yet: `percent` is then the reading alone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_reset(text: str, now: datetime) -> datetime | None:
    """`6:30am (Europe/Berlin)`, `Nov 7, 10pm (Europe/Berlin)`, `Nov 10 at 14:00` → the next such moment.

    A time without a date is the next time the clock shows it; a date without a year is this year's, or next
    year's when this year's lies more than a day back (a December screen naming a January reset)."""
    text = text.strip()
    zone = now.tzinfo
    if m := _ZONE.search(text):
        try:
            zone = ZoneInfo(m.group(1).strip())
        except (ZoneInfoNotFoundError, ValueError):
            return None
        text = text[:m.start()].strip()
    local = now.astimezone(zone)
    date = None
    if (m := _DATE.search(text)) and m.group(1).lower() in _MONTHS:
        date = (_MONTHS[m.group(1).lower()], int(m.group(2)))
        text = text[:m.start()] + " " + text[m.end():]
    m = _TIME.search(text)
    if not m:
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        if date:
            at = datetime(local.year, *date, hour, minute, tzinfo=zone)
            if at < local - timedelta(days=1):
                at = at.replace(year=local.year + 1)
            return at
        at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None
    return at if at > local else at + timedelta(days=1)


def parse_screen(text: str, now: datetime | None = None) -> Reading:
    """Every "Current …" block of the `/usage` screen with its percent and reset."""
    now = now or _now()
    if TRUST_MARK in text:
        raise UsageError("Claude Code asks whether to trust the folder; read the usage in a trusted project")
    windows: dict[str, Window] = {}
    name, percent = None, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Current "):
            if name and percent is not None:
                windows[name] = Window(percent, None)
            label = line[len("Current "):].strip()
            name = SESSION if label == "session" else WEEK if label == "week (all models)" else label
            percent = None
        elif name and percent is None and (m := _PERCENT.search(line)):
            percent = float(m.group(1))
        elif name and percent is not None and line.startswith("Resets "):
            windows[name] = Window(percent, parse_reset(line[len("Resets "):], now))
            name = None
    if name and percent is not None:
        windows[name] = Window(percent, None)
    missing = [w for w in (SESSION, WEEK) if w not in windows]
    if missing:
        raise UsageError(f"the /usage screen shows no {' and no '.join(missing)} window; did its layout change?")
    return Reading(now, windows, "screen")


def from_rate_limit(info: dict | None, at: datetime) -> Reading | None:
    """A session stream's `rate_limit_info` as a reading; `utilization` is a fraction there, not a percent."""
    names = {"five_hour": SESSION, "seven_day": WEEK}
    windows = {}
    for key, w in ((info or {}).get("unifiedWindows") or {}).items():
        if key in names and isinstance(w.get("utilization"), (int, float)):
            resets = datetime.fromtimestamp(w["resetsAt"], timezone.utc) if w.get("resetsAt") else None
            windows[names[key]] = Window(round(w["utilization"] * 100, 1), resets)
    return Reading(at, windows, "stream") if windows else None


def limit_hit(outcome, now: datetime | None = None) -> tuple[bool, datetime | None]:
    """Whether a finished session was turned away by a usage limit, and the reset it named if it named one."""
    now = now or _now()
    info = outcome.rate_limit or {}
    if info.get("status") == "rejected":
        rejected = [w["resetsAt"] for w in (info.get("unifiedWindows") or {}).values()
                    if w.get("status") == "rejected" and w.get("resetsAt")]
        reset = max(rejected) if rejected else info.get("resetsAt")
        return True, datetime.fromtimestamp(reset, timezone.utc) if reset else None
    text = outcome.error or ""
    if outcome.api_error_status != 429 and not _LIMIT.search(text):
        return False, None
    if m := _EPOCH.search(text):
        return True, datetime.fromtimestamp(int(m.group(1)), timezone.utc)
    if m := _ISO.search(text):
        return True, datetime.fromisoformat(m.group(1).replace("T", " ")).astimezone()
    if m := _RESETS_TEXT.search(text):
        return True, parse_reset(m.group(1), now)
    return True, None


def backoff(failures: int) -> int:
    return min(BACKOFF_FIRST * 2 ** (max(failures, 1) - 1), BACKOFF_MAX)


@dataclass
class SafetyNet:
    failures: int = 0
    until: datetime | None = None
    hit_at: datetime | None = None

    def hit(self, now: datetime, reset: datetime | None = None) -> datetime:
        self.failures += 1
        self.hit_at = now
        self.until = reset + RESET_GRACE if reset and reset > now else now + timedelta(seconds=backoff(self.failures))
        return self.until

    def clear(self) -> None:
        self.failures, self.until, self.hit_at = 0, None, None

    def blocked_until(self, now: datetime) -> datetime | None:
        return self.until if self.until and self.until > now else None


@dataclass
class Usage:
    """The windows as the hustlemaster believes them to be. Thread-safe: sessions finish on their own threads."""

    reading: Reading | None = None
    rates: dict[str, float] = field(default_factory=dict)  # percent per dollar, per window
    net: SafetyNet = field(default_factory=SafetyNet)
    _spend: list[tuple[datetime, float]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def observe(self, reading: Reading) -> None:
        with self._lock:
            old = self.reading
            if old is not None and reading.at <= old.at:
                return  # a stream reading taken when that session started, older than what is known
            if old is not None:
                self._learn(old, reading)
                # a stream reading has session and week only; the per-model weeks of the screen stay
                reading = Reading(reading.at, {**old.windows, **reading.windows}, reading.source)
            self.reading = reading
            below = all(reading.windows[w].percent < 100 for w in (SESSION, WEEK) if w in reading.windows)
            if self.net.hit_at and reading.at > self.net.hit_at and reading.source == "screen" and below:
                self.net.clear()
            cutoff = reading.at - timedelta(days=8)
            self._spend = [(t, c) for t, c in self._spend if t > cutoff]

    def _learn(self, old: Reading, new: Reading) -> None:
        spent = sum(c for t, c in self._spend if old.at < t <= new.at)
        if spent < MIN_SPEND:
            return
        for name in (SESSION, WEEK):
            a, b = old.windows.get(name), new.windows.get(name)
            if not (a and b and a.resets and b.resets) or abs(a.resets - b.resets) > SAME_WINDOW:
                continue  # across a reset the difference measures nothing
            if b.percent < a.percent:
                continue  # an older reading arriving late, or rounding
            sample = (b.percent - a.percent) / spent
            prev = self.rates.get(name)
            self.rates[name] = sample if prev is None else prev + RATE_SMOOTHING * (sample - prev)

    def record(self, outcome, finished: datetime | None = None) -> None:
        """Account a finished session: its cost, its stream reading, and whether a limit turned it away."""
        finished = finished or _now()
        with self._lock:
            if outcome.cost_usd:
                self._spend.append((finished, float(outcome.cost_usd)))
            # the stream reports the windows after the session's first call, not at its end
            reading = from_rate_limit(outcome.rate_limit, finished - timedelta(milliseconds=outcome.duration_ms or 0))
            if reading is not None:
                self.observe(reading)
            hit, reset = limit_hit(outcome, finished)
            if hit:
                self.net.hit(finished, reset)
            elif outcome.session_id:
                self.net.clear()

    def estimate(self, name: str, now: datetime | None = None) -> Estimate | None:
        now = now or _now()
        with self._lock:
            w = self.reading.windows.get(name) if self.reading else None
            if w is None:
                return None
            base, since, resets = w.percent, self.reading.at, w.resets
            if resets and now >= resets:
                base, since, resets = 0.0, resets, None
            spent = sum(c for t, c in self._spend if since < t <= now)
            rate = self.rates.get(name)
            percent = base + rate * spent if rate is not None else base
            return Estimate(percent, resets, base, self.reading.at, spent, rate is not None and spent > 0)

    def blocked_until(self, now: datetime | None = None) -> datetime | None:
        with self._lock:
            return self.net.blocked_until(now or _now())

    def refresh(self, cwd: Path | str, **kwargs) -> Reading:
        """Read the screen now and take it in."""
        reading = parse_screen(read_screen(cwd, **kwargs))
        self.observe(reading)
        return reading


def read_screen(cwd: Path | str, *, command: tuple[str, ...] = ("claude",), env: dict | None = None,
                startup_timeout: float = STARTUP_TIMEOUT, screen_timeout: float = SCREEN_TIMEOUT) -> str:
    """The `/usage` screen of an interactive Claude Code session in `cwd`, as text (blank lines dropped).

    `cwd` must be a folder the user trusts in Claude Code; the trust question is never answered here, because
    answering it changes the user's configuration."""
    import pexpect
    import pyte

    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.ByteStream(screen)
    child = pexpect.spawn(command[0], list(command[1:]), cwd=str(cwd), dimensions=(ROWS, COLS),
                          env={**(env if env is not None else os.environ), "TERM": "xterm-256color"})

    def text() -> str:
        return "\n".join(line.rstrip() for line in screen.display if line.strip())

    def pump(seconds: float) -> bool:
        """Feed output for `seconds`; whether anything arrived. Raises on EOF: the session is gone."""
        got, end = False, time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                stream.feed(child.read_nonblocking(65536, timeout=min(0.1, max(end - time.monotonic(), 0.01))))
                got = True
            except pexpect.TIMEOUT:
                pass
            except pexpect.EOF:
                raise UsageError(f"Claude Code ended before showing its usage:\n{text()[-800:]}") from None
        return got

    def wait(done, timeout: float, what: str) -> None:
        end = time.monotonic() + timeout
        while not done(text()):
            if time.monotonic() > end:
                raise UsageError(f"no {what} within {timeout:.0f}s:\n{text()[-800:]}")
            pump(0.2)

    def complete(shown: str) -> bool:
        try:
            parse_screen(shown)
            return True
        except UsageError:
            return False

    try:
        wait(lambda t: TRUST_MARK in t or _PROMPT.search(t), startup_timeout, "prompt")
        if TRUST_MARK in text():
            parse_screen(text())  # raises the trust error
        end = time.monotonic() + startup_timeout
        while pump(QUIET) and time.monotonic() < end:
            pass
        child.send("/usage")
        pump(0.5)  # the command menu opens; Enter before it settles can pick nothing
        child.send("\r")
        wait(complete, screen_timeout, "usage screen")
        pump(0.3)  # the last lines may follow the first paint
        return text()
    finally:
        try:
            child.send("\x1b")
            child.sendcontrol("c")
            child.sendcontrol("c")
        except OSError:
            pass
        _close(child)


def _close(child) -> None:
    """End the hidden session for good. A busy `claude` can outlive pexpect's SIGHUP/SIGINT/SIGKILL rounds by a
    moment; that must not throw away the reading this attempt already has, nor leave the process behind."""
    try:
        child.close(force=True)
    except Exception:  # ptyprocess.PtyProcessError: "Could not terminate the child."
        try:
            os.kill(child.pid, signal.SIGKILL)
            os.waitpid(child.pid, 0)
        except (OSError, ChildProcessError):
            pass
