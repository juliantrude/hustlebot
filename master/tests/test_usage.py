"""Reading the subscription's windows (ADR 0012): the recorded `/usage` screens, the readings in session streams,
the accounting between readings and the safety net after a limit."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hustlemaster import usage
from hustlemaster.runner import Outcome, read_stream
from hustlemaster.usage import (SESSION, WEEK, Reading, Usage, UsageError, Window, backoff, from_rate_limit,
                                limit_hit, parse_reset, parse_screen, read_screen)

from conftest import FIXTURES, TESTS

BERLIN = ZoneInfo("Europe/Berlin")
RECORDED = datetime(2026, 11, 7, 4, 28, tzinfo=BERLIN)  # when screen-ready.txt was recorded
SCREENS = FIXTURES / "usage"


def screen(name: str) -> str:
    return (SCREENS / name).read_text()


def outcome(cost=0.0, rate_limit=None, error=None, api_error_status=None, duration_ms=0, session_id="s"):
    return Outcome("failed" if error else "done", None, error, 0 if not error else 1, {}, 0, session_id=session_id,
                   duration_ms=duration_ms, api_error_status=api_error_status, cost_usd=cost,
                   rate_limit=rate_limit)


def reading(at, session, week=10.0, resets=None, source="screen"):
    resets = resets or datetime(2026, 11, 7, 6, 30, tzinfo=BERLIN)
    return Reading(at, {SESSION: Window(session, resets), WEEK: Window(week, resets + timedelta(days=3))}, source)


# --- the screen ----------------------------------------------------------------------------------------------


def test_recorded_screen_gives_both_windows_with_their_resets():
    r = parse_screen(screen("screen-ready.txt"), RECORDED)
    assert r.source == "screen" and r.at == RECORDED
    assert r.windows[SESSION] == Window(69.0, datetime(2026, 11, 7, 6, 30, tzinfo=BERLIN))
    assert r.windows[WEEK] == Window(49.0, datetime(2026, 11, 7, 22, 0, tzinfo=BERLIN))
    assert set(r.windows) == {SESSION, WEEK}


def test_screen_at_the_limit_with_other_formats_and_a_per_model_week():
    r = parse_screen(screen("screen-limit.txt"), RECORDED)
    assert r.windows[SESSION] == Window(100.0, datetime(2026, 11, 7, 6, 0, tzinfo=BERLIN))
    week = datetime(2026, 11, 10, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    assert r.windows[WEEK] == Window(49.0, week)
    assert r.windows["week (Sonnet)"] == Window(3.0, week)


def test_trust_question_is_an_error_not_a_reading():
    with pytest.raises(UsageError, match="trust"):
        parse_screen(screen("screen-trust.txt"), RECORDED)


def test_a_screen_without_the_windows_says_the_layout_may_have_changed():
    with pytest.raises(UsageError, match="layout"):
        parse_screen(screen("screen-startup.txt"), RECORDED)
    without_week = screen("screen-ready.txt").replace("Current week (all models)", "Current fortnight")
    with pytest.raises(UsageError, match="no week"):
        parse_screen(without_week, RECORDED)


def test_a_window_without_a_reset_line_keeps_its_percent():
    text = screen("screen-ready.txt").replace("   Resets Nov 7, 10pm (Europe/Berlin)\n", "")
    assert parse_screen(text, RECORDED).windows[WEEK] == Window(49.0, None)


@pytest.mark.parametrize("text, expected", [
    ("6:30am (Europe/Berlin)", datetime(2026, 11, 7, 6, 30, tzinfo=BERLIN)),
    ("3am (Europe/Berlin)", datetime(2026, 11, 8, 3, 0, tzinfo=BERLIN)),  # already past today: tomorrow
    ("12am (Europe/Berlin)", datetime(2026, 11, 8, 0, 0, tzinfo=BERLIN)),
    ("12pm (Europe/Berlin)", datetime(2026, 11, 7, 12, 0, tzinfo=BERLIN)),
    ("Nov 7, 10pm (Europe/Berlin)", datetime(2026, 11, 7, 22, 0, tzinfo=BERLIN)),
    ("November 9 at 1:59pm (Europe/Berlin)", datetime(2026, 11, 9, 13, 59, tzinfo=BERLIN)),
    ("Nov 10 at 14:00 (UTC)", datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)),
    ("Jan 2, 1am (Europe/Berlin)", datetime(2027, 1, 2, 1, 0, tzinfo=BERLIN)),  # next year's
    ("Nov 6, 11pm (Europe/Berlin)", datetime(2026, 11, 6, 23, 0, tzinfo=BERLIN)),  # just past: not next year's
    ("10pm", datetime(2026, 11, 7, 22, 0, tzinfo=BERLIN)),  # no zone: the clock's own
    ("soon (Europe/Berlin)", None),
    ("10pm (Mars/Olympus)", None),
    ("13pm (Europe/Berlin)", None),
])
def test_reset_times(text, expected):
    assert parse_reset(text, RECORDED) == expected


# --- the stream ----------------------------------------------------------------------------------------------


def test_stream_reading_is_a_fraction_turned_into_percent():
    out = read_stream((FIXTURES / "streams" / "haiku-tools.jsonl").read_text().splitlines(), 0)
    assert out.cost_usd == pytest.approx(0.003817)
    r = from_rate_limit(out.rate_limit, RECORDED)
    assert r.source == "stream"
    assert r.windows[SESSION] == Window(47.0, datetime.fromtimestamp(1794029400, timezone.utc))
    assert r.windows[WEEK].resets == datetime(2026, 11, 7, 22, 0, tzinfo=BERLIN)
    assert from_rate_limit(None, RECORDED) is None
    assert from_rate_limit({"status": "allowed", "resetsAt": 1794029400}, RECORDED) is None


def test_stream_reading_is_dated_at_the_session_start():
    u = Usage()
    end = RECORDED + timedelta(minutes=30)
    info = {"unifiedWindows": {"five_hour": {"utilization": 0.5, "resetsAt": 1794029400}}}
    u.record(outcome(rate_limit=info, duration_ms=20 * 60 * 1000), end)
    assert u.reading.at == end - timedelta(minutes=20)


def test_older_reading_does_not_replace_a_newer_one():
    u = Usage()
    u.observe(reading(RECORDED, 60))
    u.observe(reading(RECORDED - timedelta(minutes=1), 50, source="stream"))
    assert u.reading.windows[SESSION].percent == 60


def test_stream_reading_keeps_the_per_model_week_of_the_screen():
    u = Usage()
    u.observe(parse_screen(screen("screen-limit.txt"), RECORDED))
    u.observe(reading(RECORDED + timedelta(minutes=1), 20, source="stream"))
    assert u.reading.windows[SESSION].percent == 20
    assert u.reading.windows["week (Sonnet)"].percent == 3


# --- accounting ----------------------------------------------------------------------------------------------


def test_rate_is_learned_between_readings_of_the_same_window_and_extrapolates():
    u = Usage()
    t = RECORDED
    u.observe(reading(t, 40, week=10))
    assert u.estimate(SESSION, t).extrapolated is False
    u.record(outcome(cost=2.0), t + timedelta(minutes=10))
    u.observe(reading(t + timedelta(minutes=20), 50, week=11))
    assert u.rates == {SESSION: pytest.approx(5.0), WEEK: pytest.approx(0.5)}
    u.record(outcome(cost=1.0), t + timedelta(minutes=30))
    e = u.estimate(SESSION, t + timedelta(minutes=31))
    assert (e.percent, e.measured, e.spent, e.extrapolated) == (pytest.approx(55.0), 50, 1.0, True)
    assert u.estimate(WEEK, t + timedelta(minutes=31)).percent == pytest.approx(11.5)
    # a second sample moves the rate part of the way, not all of it
    u.observe(reading(t + timedelta(minutes=40), 51))
    assert u.rates[SESSION] == pytest.approx(5.0 + usage.RATE_SMOOTHING * (1.0 - 5.0))


def test_nothing_is_learned_across_a_reset_or_from_too_little_spend():
    u = Usage()
    t = RECORDED
    u.observe(reading(t, 90))
    u.record(outcome(cost=5.0), t + timedelta(minutes=10))
    u.observe(reading(t + timedelta(minutes=20), 5, resets=datetime(2026, 11, 7, 11, 30, tzinfo=BERLIN)))
    assert SESSION not in u.rates
    u.record(outcome(cost=usage.MIN_SPEND / 2), t + timedelta(minutes=30))
    u.observe(reading(t + timedelta(minutes=40), 7, resets=datetime(2026, 11, 7, 11, 30, tzinfo=BERLIN)))
    assert u.rates == {}


def test_after_the_reset_the_estimate_starts_from_zero():
    u = Usage(rates={SESSION: 4.0})
    resets = datetime(2026, 11, 7, 6, 30, tzinfo=BERLIN)
    u.observe(reading(RECORDED, 80, resets=resets))
    u.record(outcome(cost=3.0), resets - timedelta(minutes=5))  # still in the old window
    u.record(outcome(cost=1.0), resets + timedelta(minutes=5))
    e = u.estimate(SESSION, resets + timedelta(minutes=10))
    assert (e.percent, e.measured, e.resets, e.spent) == (4.0, 0.0, None, 1.0)


def test_no_estimate_without_a_reading():
    assert Usage().estimate(SESSION) is None


# --- safety net ----------------------------------------------------------------------------------------------


def test_backoff_doubles_from_fifteen_minutes_to_four_hours_like_the_old_loop():
    assert [backoff(n) for n in range(1, 8)] == [900, 1800, 3600, 7200, 14400, 14400, 14400]


def test_rejected_stream_blocks_until_its_reset():
    reset = int((RECORDED + timedelta(hours=2)).timestamp())
    info = {"status": "rejected", "resetsAt": reset, "unifiedWindows": {
        "five_hour": {"utilization": 1.0, "resetsAt": reset, "status": "rejected"},
        "seven_day": {"utilization": 0.4, "resetsAt": reset + 86400, "status": "allowed"}}}
    u = Usage()
    u.record(outcome(rate_limit=info, error="limit"), RECORDED)
    assert u.blocked_until(RECORDED) == datetime.fromtimestamp(reset, timezone.utc) + usage.RESET_GRACE
    assert u.blocked_until(RECORDED + timedelta(hours=3)) is None


@pytest.mark.parametrize("error, reset", [
    ("Claude AI usage limit reached|1794029400", datetime.fromtimestamp(1794029400, timezone.utc)),
    ("You've hit your session limit · resets 6:30am (Europe/Berlin)", datetime(2026, 11, 7, 6, 30, tzinfo=BERLIN)),
    ("usage limit, reset at 2026-11-07 09:00", datetime(2026, 11, 7, 9, 0).astimezone()),
])
def test_limit_text_names_the_reset(error, reset):
    assert limit_hit(outcome(error=error), RECORDED) == (True, reset)


def test_limit_without_a_reset_backs_off_and_a_normal_run_clears_it():
    u = Usage()
    u.record(outcome(error="API Error: 429", api_error_status=429), RECORDED)
    assert u.blocked_until(RECORDED) == RECORDED + timedelta(minutes=15)
    later = RECORDED + timedelta(minutes=16)
    u.record(outcome(error="429", api_error_status=429), later)
    assert u.blocked_until(later) == later + timedelta(minutes=30)
    u.record(outcome(error="no result (exit 1)", session_id=None), later)  # a crash before any result proves nothing
    assert u.net.failures == 2
    u.record(outcome(cost=0.1), later)
    assert u.blocked_until(later) is None and u.net.failures == 0


def test_free_screen_reading_below_the_limits_lifts_the_block_early():
    u = Usage()
    u.record(outcome(error="usage limit reached", api_error_status=429), RECORDED)
    u.observe(parse_screen(screen("screen-limit.txt"), RECORDED + timedelta(minutes=1)))  # session still 100 %
    assert u.blocked_until(RECORDED + timedelta(minutes=1)) is not None
    u.observe(reading(RECORDED + timedelta(minutes=2), 99, source="stream"))  # a stream is no proof
    assert u.blocked_until(RECORDED + timedelta(minutes=2)) is not None
    u.observe(parse_screen(screen("screen-ready.txt"), RECORDED + timedelta(minutes=3)))
    assert u.blocked_until(RECORDED + timedelta(minutes=3)) is None


def test_a_session_that_failed_for_another_reason_still_proves_the_limit_is_gone():
    u = Usage()
    u.record(outcome(error="usage limit reached", api_error_status=429), RECORDED)
    u.record(outcome(error="session ended with an error (error_max_turns)"), RECORDED)
    assert u.blocked_until(RECORDED) is None


# --- the pseudo-terminal -------------------------------------------------------------------------------------


def fake(tmp_path, mode="ready", **env):
    log = tmp_path / "typed.log"
    return {"command": (sys.executable, str(TESTS / "fake_tui.py")),
            "env": {**os.environ, "FAKE_TUI_MODE": mode, "FAKE_TUI_LOG": str(log), **env}}, log


def test_reads_the_usage_screen_through_a_pseudo_terminal(tmp_path):
    kwargs, log = fake(tmp_path)
    shown = read_screen(tmp_path, **kwargs, startup_timeout=10)
    assert "/usage" in log.read_text()
    r = parse_screen(shown, RECORDED)
    assert (r.windows[SESSION].percent, r.windows[WEEK].percent) == (69.0, 49.0)
    u = Usage()
    assert u.refresh(tmp_path, **kwargs, startup_timeout=10).windows[SESSION].percent == 69.0
    assert u.reading.source == "screen"


def test_pseudo_terminal_draws_the_given_screen(tmp_path):
    kwargs, _ = fake(tmp_path, FAKE_TUI_USAGE="screen-limit.txt")
    assert parse_screen(read_screen(tmp_path, **kwargs), RECORDED).windows[SESSION].percent == 100.0


def test_trust_question_is_reported_and_never_answered(tmp_path):
    kwargs, log = fake(tmp_path, "trust")
    with pytest.raises(UsageError, match="trust"):
        read_screen(tmp_path, **kwargs, startup_timeout=10)
    assert not log.exists()


def test_a_session_that_ends_is_an_error(tmp_path):
    kwargs, _ = fake(tmp_path, "die")
    with pytest.raises(UsageError, match="not logged in"):
        read_screen(tmp_path, **kwargs, startup_timeout=10)


def test_a_screen_that_never_comes_times_out(tmp_path):
    kwargs, log = fake(tmp_path, "mute")
    with pytest.raises(UsageError, match="no usage screen"):
        read_screen(tmp_path, **kwargs, startup_timeout=10, screen_timeout=1.5)
    assert "/usage" in log.read_text()
