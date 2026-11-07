"""The start gate (ADR 0008): the 5h stop, the weekly target line, the fit of one more session, and parking an
increment the limit cut off until the next window."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from hustlemaster import budget
from hustlemaster.budget import PARKED_NOTE, Budget, catches_up, target
from hustlemaster.runner import Outcome
from hustlemaster.store import Settings
from hustlemaster.usage import RESET_GRACE, SESSION, WEEK, Reading, Usage, Window

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 7, 4, 30, tzinfo=BERLIN)
SESSION_RESETS = datetime(2026, 11, 7, 6, 30, tzinfo=BERLIN)
WEEK_RESETS = datetime(2026, 11, 10, 22, 0, tzinfo=BERLIN)  # the week started Nov 3, 22:00
WEEK_START = WEEK_RESETS - timedelta(days=7)
DEFAULTS = Settings()  # threshold 85 %, week target 95 %


def outcome(cost=0.0, error=None, api_error_status=None, rate_limit=None, session_id="s"):
    return Outcome("failed" if error else "done", None, error, 1 if error else 0, {}, 0, session_id=session_id,
                   api_error_status=api_error_status, cost_usd=cost, rate_limit=rate_limit)


def gate(session, week, at=NOW, rates=None, extra=None) -> Budget:
    windows = {SESSION: Window(session, SESSION_RESETS), WEEK: Window(week, WEEK_RESETS), **(extra or {})}
    return Budget(Usage(reading=Reading(at, windows, "screen"), rates=dict(rates or {})))


# --- the target line -----------------------------------------------------------------------------------------


def test_target_line_runs_from_zero_at_the_week_start_to_the_target_at_the_reset():
    assert target(WEEK_RESETS, 95, WEEK_START) == 0
    assert target(WEEK_RESETS, 95, WEEK_START + timedelta(days=3, hours=12)) == pytest.approx(47.5)
    assert target(WEEK_RESETS, 95, WEEK_RESETS) == 95
    assert target(WEEK_RESETS, 95, WEEK_START - timedelta(hours=1)) == 0
    assert target(WEEK_RESETS, 95, WEEK_RESETS + timedelta(hours=1)) == 95


def test_the_line_catches_up_when_it_reaches_the_usage_or_at_the_reset():
    assert catches_up(WEEK_RESETS, 95, 47.5) == WEEK_START + timedelta(days=3, hours=12)
    assert catches_up(WEEK_RESETS, 95, 95) == WEEK_RESETS
    assert catches_up(WEEK_RESETS, 80, 90) == WEEK_RESETS
    assert catches_up(WEEK_RESETS, 0, 10) == WEEK_RESETS


# --- the checks ------------------------------------------------------------------------------------------------


def test_room_in_both_windows_starts():
    v = gate(50, 30).check("opus", DEFAULTS, now=NOW)
    assert (v.ok, v.reason) == (True, "ok")


def test_the_5h_threshold_stops_new_starts_until_the_window_resets():
    v = gate(85, 30).check("haiku", DEFAULTS, now=NOW)
    assert (v.ok, v.reason, v.until) == (False, "session", SESSION_RESETS)
    assert gate(84, 30).check("haiku", DEFAULTS, now=NOW).ok
    assert gate(84, 30).check("haiku", Settings(session_threshold=80), now=NOW).reason == "session"


def test_the_5h_estimate_counts_what_finished_since_the_reading():
    b = gate(80, 30, rates={SESSION: 2.0})
    b.usage.record(outcome(cost=2.5), NOW + timedelta(minutes=1))  # 80 + 2 × 2.5 = 85
    assert b.check("haiku", DEFAULTS, now=NOW + timedelta(minutes=2)).reason == "session"


def test_after_the_5h_reset_starts_resume_from_zero():
    b = gate(95, 30)
    assert b.check("haiku", DEFAULTS, now=SESSION_RESETS - timedelta(minutes=1)).reason == "session"
    assert b.check("haiku", DEFAULTS, now=SESSION_RESETS + timedelta(minutes=1)).ok


def test_above_the_weekly_target_line_waits_until_the_line_has_caught_up():
    line = target(WEEK_RESETS, 95, NOW)  # about 44 % on Saturday morning
    v = gate(10, line + 5).check("haiku", DEFAULTS, now=NOW)
    assert (v.ok, v.reason) == (False, "week")
    assert v.until == catches_up(WEEK_RESETS, 95, line + 5) and NOW < v.until < WEEK_RESETS
    assert gate(10, line - 1).check("haiku", DEFAULTS, now=NOW).ok
    # the same usage is fine later in the week: the line has moved past it
    assert gate(10, line + 5, at=v.until).check("haiku", DEFAULTS, now=v.until + timedelta(minutes=1)).ok


def test_a_lower_week_target_holds_back_earlier():
    line = target(WEEK_RESETS, 95, NOW)
    assert gate(10, line - 5).check("haiku", Settings(week_target=60), now=NOW).reason == "week"


def test_a_usage_limit_blocks_before_anything_else():
    b = gate(10, 10)
    b.usage.record(outcome(error="usage limit reached · resets 6:30am (Europe/Berlin)"), NOW)
    v = b.check("haiku", DEFAULTS, now=NOW)
    assert (v.ok, v.reason, v.until) == (False, "limit", SESSION_RESETS + RESET_GRACE)


def test_without_any_reading_only_the_safety_net_holds_back():
    b = Budget()
    assert b.check("opus", DEFAULTS, now=NOW).ok
    b.usage.record(outcome(error="429", api_error_status=429), NOW)
    assert b.check("opus", DEFAULTS, now=NOW).reason == "limit"


# --- the fit ---------------------------------------------------------------------------------------------------


def test_expected_cost_is_the_models_recent_mean_else_scaled_by_points_else_unknown():
    b = Budget()
    assert b.expected_cost("opus") is None
    for cost in (0.2, 0.4):
        b.finished("h", "a", "haiku", outcome(cost=cost), NOW)
    assert b.expected_cost("haiku") == pytest.approx(0.3)
    assert b.expected_cost("opus") == pytest.approx(1.2)  # 0.3 per point × 4 points
    b.finished("h", "b", "opus", outcome(cost=2.0), NOW)
    assert b.expected_cost("opus") == pytest.approx(2.0)
    for _ in range(budget.HISTORY):
        b.finished("h", "c", "haiku", outcome(cost=1.0), NOW)
    assert b.expected_cost("haiku") == pytest.approx(1.0)  # only the recent sessions count


def test_a_session_that_would_cross_the_limit_waits_for_the_reset():
    b = gate(70, 30, rates={SESSION: 5.0, WEEK: 0.5})
    b.finished("h", "a", "opus", outcome(cost=4.0), NOW - timedelta(hours=1))  # before the reading: history only
    v = b.check("opus", DEFAULTS, now=NOW)  # 70 + 4 × 5 = 90: fits
    assert v.ok
    v = b.check("opus", DEFAULTS, running=["opus", "opus"], now=NOW)  # 70 + 12 × 5 = 130
    assert (v.ok, v.reason, v.until) == (False, "fit", SESSION_RESETS)
    assert "130" in v.detail


def test_the_fit_also_applies_to_the_week():
    b = gate(10, 20, rates={SESSION: 0.1, WEEK: 10.0})
    b.finished("h", "a", "opus", outcome(cost=9.0), NOW - timedelta(hours=1))
    v = b.check("opus", Settings(week_target=100), now=NOW)  # week 20 + 90 = 110
    assert (v.ok, v.reason, v.until) == (False, "fit", WEEK_RESETS)


def test_without_a_learned_rate_or_cost_history_a_session_may_start():
    assert gate(80, 30).check("fable", DEFAULTS, running=["fable"] * 5, now=NOW).ok
    b = gate(80, 30, rates={SESSION: 50.0})
    assert b.check("fable", DEFAULTS, now=NOW).ok  # no session finished yet, nothing to multiply


def test_a_used_up_model_week_holds_back_that_model_only():
    extra = {"week (Sonnet)": Window(100.0, WEEK_RESETS)}
    b = gate(10, 10, extra=extra)
    v = b.check("sonnet", DEFAULTS, now=NOW)
    assert (v.ok, v.reason, v.until) == (False, "fit", WEEK_RESETS)
    assert b.check("opus", DEFAULTS, now=NOW).ok
    assert gate(10, 10, extra={"week (Sonnet)": Window(99.0, WEEK_RESETS)}).check("sonnet", DEFAULTS, now=NOW).ok
    assert b.check("sonnet", DEFAULTS, now=WEEK_RESETS + timedelta(minutes=1)).ok  # reset, reading not yet renewed


# --- parking ---------------------------------------------------------------------------------------------------


def test_a_session_cut_off_at_the_limit_is_parked_and_resumed_with_a_note_after_the_reset():
    b = gate(84, 30)
    until = b.finished("h", "a", "opus", outcome(cost=3.0, error="You've hit your session limit · resets 6:30am "
                                                                   "(Europe/Berlin)"), NOW)
    assert until == SESSION_RESETS + RESET_GRACE
    assert b.is_parked("h", "a") and not b.is_parked("h", "b")
    assert b.costs == {}  # a cut-off session says nothing about what a whole one costs
    assert b.check("opus", DEFAULTS, now=NOW + timedelta(minutes=30)).reason == "limit"
    assert b.check("opus", DEFAULTS, now=until + timedelta(minutes=1)).ok  # the next window
    assert b.resume("h", "a") == PARKED_NOTE
    assert not b.is_parked("h", "a") and b.resume("h", "a") is None


def test_a_normal_failure_is_not_parked():
    b = gate(10, 10)
    assert b.finished("h", "a", "haiku", outcome(cost=0.3, error="no report block"), NOW) is None
    assert not b.is_parked("h", "a") and b.resume("h", "a") is None
    assert b.expected_cost("haiku") == pytest.approx(0.3)


def test_a_limit_without_a_named_reset_parks_until_the_backoff_ends():
    b = Budget()
    until = b.finished("h", "a", "haiku", outcome(error="API Error: 429", api_error_status=429), NOW)
    assert until == NOW + timedelta(minutes=15) and b.is_parked("h", "a")
