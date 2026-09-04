import numpy as np
import pandas as pd
import pytest

from src.core.logic.shadow_mode import (
    SCORE_VERSION_V2, SCORE_VERSION_V3, SCORE_VERSION_V3_1, SCORE_VERSION_V3_2,
    compute_shadow_observation, compute_shadow_observations,
    update_shadow_states, update_shadow_states_v2, update_shadow_states_v3,
    update_shadow_states_v3_1, update_shadow_states_v3_2,
)
from src.core.models import StockRule


def _rule(**overrides):
    values = dict(
        ticker="AAPL", buy_threshold_pct=-5, sell_threshold_pct=10,
        buy_amount=500, regime_enabled=True, regime_algo="channel",
        channel_lookback=63, long_channel_lookback=252,
        shadow_mode_enabled=True,
    )
    values.update(overrides)
    return StockRule(**values)


def _frame(closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "High": closes * 1.01, "Low": closes * 0.99, "Close": closes,
    }, index=pd.bdate_range("2025-01-01", periods=len(closes)))


def test_steady_rise_scores_as_trend():
    row = compute_shadow_observation(
        _rule(), _frame(np.geomspace(50, 150, 320)), "2026-01-02", 150,
    )
    assert row["data_ready"]
    assert row["candidate_state"] == "trend"
    assert row["trend_score"] > row["range_score"]


def test_oscillation_scores_as_range():
    x = np.arange(320)
    row = compute_shadow_observation(
        _rule(), _frame(100 + 8 * np.sin(x * np.pi / 8)), "2026-01-02", 100,
    )
    assert row["candidate_state"] == "range"
    assert row["range_score"] > row["trend_score"]


def test_steady_decline_scores_as_risk_off():
    row = compute_shadow_observation(
        _rule(), _frame(np.geomspace(150, 50, 320)), "2026-01-02", 50,
    )
    assert row["candidate_state"] == "risk_off"


def test_insufficient_data_is_neutral():
    row = compute_shadow_observation(_rule(), _frame(np.arange(100) + 50), "2026-01-02", 100)
    assert not row["data_ready"]
    assert row["candidate_state"] == "neutral"


def test_v1_and_v2_reuse_identical_features_with_distinct_versions():
    rows = compute_shadow_observations(
        _rule(shadow_mode_v2_enabled=True),
        _frame(np.geomspace(50, 150, 320)), "2026-01-02", 150,
        include_v2=True,
    )
    assert [row["score_version"] for row in rows] == ["price_action_v1", SCORE_VERSION_V2]
    assert rows[0]["trend_score"] == rows[1]["trend_score"]
    assert rows[0]["efficiency_ratio_252"] == rows[1]["efficiency_ratio_252"]


def test_all_shadow_versions_reuse_identical_features():
    rows = compute_shadow_observations(
        _rule(shadow_mode_v2_enabled=True, shadow_mode_v3_enabled=True),
        _frame(np.geomspace(50, 150, 320)), "2026-01-02", 150,
        include_v2=True, include_v3=True,
        include_v3_1=True, include_v3_2=True,
    )
    assert [row["score_version"] for row in rows] == [
        "price_action_v1", SCORE_VERSION_V2, SCORE_VERSION_V3, SCORE_VERSION_V3_1,
        SCORE_VERSION_V3_2,
    ]
    assert len({row["trend_score"] for row in rows}) == 1
    assert all("signal_atr" in row for row in rows)


def _observation(day, state):
    return {
        "date": day, "ticker": "AAPL", "price": 100.0,
        "score_version": "price_action_v1", "data_ready": True,
        "trend_score": 80.0 if state == "trend" else 10.0,
        "range_score": 80.0 if state == "range" else 10.0,
        "risk_score": 80.0 if state == "risk_off" else 10.0,
        "candidate_state": state, "reason_code": state,
    }


def _v2_observation(day, state, *, risk_score=None):
    row = _observation(day, state)
    row["score_version"] = SCORE_VERSION_V2
    if risk_score is not None:
        row["risk_score"] = risk_score
    return row


def _v3_observation(
    day, state="neutral", *, risk_score=55, above_ema=True,
    short_slope=1.0, new_low=False, signal_low=100.0,
    above_midline=True, signal_high=106.0, signal_close=105.0,
    ema_distance=0.0, price_gap_ok=True,
):
    row = _v2_observation(day, state, risk_score=risk_score)
    row.update({
        "score_version": SCORE_VERSION_V3,
        "short_slope_pct": short_slope,
        "above_ema20": above_ema,
        "above_short_midline": above_midline,
        "is_new_20d_low": new_low,
        "signal_close": signal_close,
        "signal_high": signal_high,
        "signal_low": signal_low,
        "signal_atr": 5.0,
        "ema20_distance_atr": ema_distance,
        "price_gap_guard_passed": price_gap_ok,
    })
    return row


def _v3_state(effective_state, **overrides):
    state = {
        "effective_state": effective_state,
        "state_start_date": "2026-01-01", "state_start_price": 100.0,
        "state_days": 20, "candidate_history": [],
        "risk_score_history": [], "above_ema_history": [],
        "new_low_history": [], "risk_entry_days": 0,
        "risk_below_60_days": 0, "last_evaluation_month": "2026-01",
        "probe_days": 0,
        "turning_probe_days": 0, "turning_cooldown_days": 0,
        "turning_trailing_active": False, "turning_trailing_stop": None,
        "risk_anchor_low": None, "risk_anchor_atr": None,
        "risk_anchor_date": None, "risk_anchor_age": 0,
        "turning_lows": [], "turning_highs": [],
        "turning_ema_distances": [], "turning_risk_scores": [],
    }
    state.update(overrides)
    return {"AAPL": state}


def test_risk_off_enters_after_two_days_immediately():
    state = {}
    _, state, _ = update_shadow_states(state, [_observation("2026-01-02", "risk_off")], "2026-01-02")
    events, state, rows = update_shadow_states(
        state, [_observation("2026-01-05", "risk_off")], "2026-01-05",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert [event["event"] for event in events] == ["mode_end", "mode_start"]


def test_normal_transition_requires_confirmation_month_and_dwell():
    state = {}
    days = pd.bdate_range("2026-01-02", periods=70)
    transitioned = None
    for value in days:
        day = value.strftime("%Y-%m-%d")
        events, state, rows = update_shadow_states(state, [_observation(day, "trend")], day)
        if rows[0]["effective_state"] == "trend":
            transitioned = day
            break
    assert transitioned is not None
    assert transitioned.endswith("-01") or pd.Timestamp(transitioned).day <= 3


def test_risk_off_exit_uses_confirmation_and_monthly_gate_without_dwell():
    state = {
        "AAPL": {
            "effective_state": "risk_off", "state_start_date": "2026-01-15",
            "state_start_price": 100.0, "state_days": 0,
            "candidate_state": None, "candidate_days": 0,
            "last_evaluation_month": "2026-01",
        }
    }
    transitioned = None
    for value in pd.bdate_range("2026-01-16", "2026-03-03"):
        day = value.strftime("%Y-%m-%d")
        _, state, rows = update_shadow_states(state, [_observation(day, "trend")], day)
        if rows[0]["effective_state"] == "trend":
            transitioned = day
            break

    # 20일 확인을 마친 뒤 다음 달 첫 평가일에 해제하며 63일 체류는 요구하지 않는다.
    assert transitioned == "2026-03-02"
    assert state["AAPL"]["state_days"] == 0


def test_v2_normal_transition_uses_ten_votes_in_fifteen_not_consecutive_days():
    state = {
        "AAPL": {
            "effective_state": "neutral", "state_start_date": "2025-10-01",
            "state_start_price": 100.0, "state_days": 62,
            "candidate_history": ["trend"] * 5 + ["neutral"] + ["trend"] * 4,
            "risk_clear_history": [], "risk_entry_days": 0,
            "last_evaluation_month": "2026-01",
        }
    }
    events, state, rows = update_shadow_states_v2(
        state, [_v2_observation("2026-02-02", "trend")], "2026-02-02",
    )
    assert rows[0]["effective_state"] == "trend"
    assert rows[0]["confirmation_votes"] == 10
    assert [event["event"] for event in events] == ["mode_end", "mode_start"]


def test_v2_risk_entry_still_requires_two_consecutive_days():
    state = {}
    _, state, _ = update_shadow_states_v2(
        state, [_v2_observation("2026-01-02", "risk_off", risk_score=75)], "2026-01-02",
    )
    events, state, rows = update_shadow_states_v2(
        state, [_v2_observation("2026-01-05", "risk_off", risk_score=75)], "2026-01-05",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert events[-1]["trigger"] == "risk_confirmed"


def test_v2_risk_exit_requires_seven_clear_days_in_full_ten_day_window():
    state = {
        "AAPL": {
            "effective_state": "risk_off", "state_start_date": "2026-01-01",
            "state_start_price": 100.0, "state_days": 20,
            "candidate_history": [], "risk_clear_history": [],
            "risk_entry_days": 0, "last_evaluation_month": "2026-01",
        }
    }
    events = []
    for index, value in enumerate([45, 45, 55, 45, 55, 45, 45, 55, 45, 45]):
        day = pd.Timestamp("2026-02-02") + pd.offsets.BDay(index)
        events, state, rows = update_shadow_states_v2(
            state, [_v2_observation(day.strftime("%Y-%m-%d"), "neutral", risk_score=value)],
            day.strftime("%Y-%m-%d"),
        )
    assert rows[0]["effective_state"] == "neutral"
    assert events[-1]["trigger"] == "risk_recovered"


def test_v3_enters_recovery_probe_with_three_of_four_evidence_signals():
    state = _v3_state(
        "risk_off", risk_score_history=[85.0] * 10,
        above_ema_history=[True] * 4, new_low_history=[False] * 4,
    )
    events, state, rows = update_shadow_states_v3(
        state, [_v3_observation("2026-02-02", risk_score=65)], "2026-02-02",
    )
    assert rows[0]["effective_state"] == "recovery_probe"
    assert rows[0]["recovery_evidence_count"] == 4
    assert events[-1]["trigger"] == "recovery_probe_entered"
    assert state["AAPL"]["probe_days"] == 1


def test_v3_does_not_probe_while_risk_score_is_still_seventy():
    state = _v3_state(
        "risk_off", risk_score_history=[90.0] * 10,
        above_ema_history=[True] * 4, new_low_history=[False] * 4,
    )
    events, state, rows = update_shadow_states_v3(
        state, [_v3_observation("2026-02-02", risk_score=70)], "2026-02-02",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert not events


def test_v3_1_preserves_initial_risk_history_and_probes_on_next_recovery_day():
    state = _v3_state(
        "neutral", risk_entry_days=1,
        risk_score_history=[90.0] * 10,
        above_ema_history=[True] * 4,
        new_low_history=[False] * 4,
    )
    _, state, rows = update_shadow_states_v3_1(
        state, [_v3_observation("2026-02-02", state="risk_off", risk_score=75)],
        "2026-02-02",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert len(state["AAPL"]["risk_score_history"]) == 10

    events, state, rows = update_shadow_states_v3_1(
        state, [_v3_observation("2026-02-03", risk_score=65)], "2026-02-03",
    )
    assert rows[0]["effective_state"] == "recovery_probe"
    assert events[-1]["score_version"] == SCORE_VERSION_V3_1


def test_v3_still_resets_history_on_initial_risk_entry():
    state = _v3_state(
        "neutral", risk_entry_days=1,
        risk_score_history=[90.0] * 10,
        above_ema_history=[True] * 4,
        new_low_history=[False] * 4,
    )
    _, state, _ = update_shadow_states_v3(
        state, [_v3_observation("2026-02-02", state="risk_off", risk_score=75)],
        "2026-02-02",
    )
    assert state["AAPL"]["risk_score_history"] == []


@pytest.mark.parametrize(
    "observation,trigger",
    [
        ({"signal_low": 94.0}, "probe_failed_atr_floor"),
        ({"new_low": True}, "probe_failed_new_20d_low"),
    ],
)
def test_v3_probe_hard_failures_return_to_risk_immediately(observation, trigger):
    state = _v3_state(
        "recovery_probe", probe_entry_low=100.0, probe_entry_atr=5.0,
        probe_days=3,
    )
    events, state, rows = update_shadow_states_v3(
        state, [_v3_observation("2026-02-02", **observation)], "2026-02-02",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert events[-1]["trigger"] == trigger
    assert state["AAPL"]["risk_score_history"] == []


def test_v3_probe_returns_to_risk_after_two_risk_days_and_requires_fresh_history():
    state = _v3_state(
        "recovery_probe", probe_entry_low=100.0, probe_entry_atr=5.0,
        probe_days=3, risk_entry_days=1,
    )
    events, state, rows = update_shadow_states_v3(
        state, [_v3_observation("2026-02-02", risk_score=75)], "2026-02-02",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert events[-1]["trigger"] == "probe_failed_risk_reconfirmed"

    events, state, rows = update_shadow_states_v3(
        state, [_v3_observation("2026-02-03", risk_score=55)], "2026-02-03",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert rows[0]["recovery_evidence"]["risk_improving"] is False
    assert not events


def test_v3_probe_success_goes_directly_to_confirmed_trend():
    state = _v3_state(
        "recovery_probe", probe_entry_low=100.0, probe_entry_atr=5.0,
        probe_days=5, risk_below_60_days=4,
        candidate_history=["trend"] * 9,
    )
    events, state, rows = update_shadow_states_v3(
        state, [_v3_observation("2026-02-02", state="trend", risk_score=55)],
        "2026-02-02",
    )
    assert rows[0]["effective_state"] == "trend"
    assert events[-1]["trigger"] == "probe_confirmed_trend"


def test_v3_probe_success_without_votes_goes_to_neutral():
    state = _v3_state(
        "recovery_probe", probe_entry_low=100.0, probe_entry_atr=5.0,
        probe_days=5, risk_below_60_days=4,
    )
    events, _, rows = update_shadow_states_v3(
        state, [_v3_observation("2026-02-02", risk_score=55)], "2026-02-02",
    )
    assert rows[0]["effective_state"] == "neutral"
    assert events[-1]["trigger"] == "probe_confirmed_neutral"


def test_v3_probe_times_out_to_neutral_on_twentieth_day():
    state = _v3_state(
        "recovery_probe", probe_entry_low=100.0, probe_entry_atr=5.0,
        probe_days=19,
    )
    events, _, rows = update_shadow_states_v3(
        state, [_v3_observation(
            "2026-02-02", risk_score=65, short_slope=-1, above_midline=False,
        )], "2026-02-02",
    )
    assert rows[0]["effective_state"] == "neutral"
    assert events[-1]["trigger"] == "probe_timeout_neutral"


def test_v3_2_enters_ten_percent_turning_probe_without_clearing_structural_risk():
    state = _v3_state(
        "risk_off", risk_anchor_low=100.0, risk_anchor_atr=5.0,
        risk_anchor_date="2026-01-30", risk_anchor_age=1,
        turning_lows=[100.0], turning_highs=[103.0],
        turning_ema_distances=[-2.0, -1.0], turning_risk_scores=[82.0, 78.0],
    )
    events, state, rows = update_shadow_states_v3_2(
        state,
        [_v3_observation(
            "2026-02-02", state="risk_off", risk_score=75,
            signal_low=101.0, signal_high=106.0, signal_close=105.0,
            ema_distance=0.0,
        )],
        "2026-02-02",
    )
    assert rows[0]["effective_state"] == "turning_probe"
    assert rows[0]["shadow_exposure_pct"] == 10.0
    assert rows[0]["turning_evidence_count"] == 4
    assert events[-1]["trigger"] == "turning_probe_entered"
    assert state["AAPL"]["risk_anchor_low"] == 100.0


def test_v3_2_turning_probe_fails_on_new_anchor_low_and_starts_cooldown():
    state = _v3_state(
        "turning_probe", risk_anchor_low=100.0, risk_anchor_atr=5.0,
        risk_anchor_date="2026-01-30", turning_probe_days=3,
    )
    events, state, rows = update_shadow_states_v3_2(
        state, [_v3_observation("2026-02-02", risk_score=75, signal_low=99.0)],
        "2026-02-02",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert rows[0]["shadow_exposure_pct"] == 0.0
    assert events[-1]["trigger"] == "turning_probe_failed_new_anchor_low"
    assert state["AAPL"]["turning_cooldown_days"] == 5
    assert state["AAPL"]["risk_anchor_low"] == 99.0


def test_v3_2_promotes_turning_probe_to_existing_recovery_probe():
    state = _v3_state(
        "turning_probe", risk_anchor_low=100.0, risk_anchor_atr=5.0,
        risk_anchor_date="2026-01-30", turning_probe_days=3,
        risk_score_history=[85.0] * 10,
        above_ema_history=[True] * 4, new_low_history=[False] * 4,
    )
    events, _, rows = update_shadow_states_v3_2(
        state, [_v3_observation("2026-02-02", risk_score=65)], "2026-02-02",
    )
    assert rows[0]["effective_state"] == "recovery_probe"
    assert rows[0]["shadow_exposure_pct"] == 25.0
    assert events[-1]["trigger"] == "turning_probe_confirmed_recovery"


def test_v3_2_records_late_recovery_chase_without_blocking_structural_recovery():
    state = _v3_state(
        "risk_off", risk_anchor_low=100.0, risk_anchor_atr=5.0,
        risk_anchor_date="2026-01-20", risk_anchor_age=10,
        risk_score_history=[85.0] * 10,
        above_ema_history=[True] * 4, new_low_history=[False] * 4,
    )
    events, _, rows = update_shadow_states_v3_2(
        state,
        [_v3_observation(
            "2026-02-02", risk_score=65, signal_low=112.0,
            signal_close=115.0, signal_high=116.0,
        )],
        "2026-02-02",
    )
    assert rows[0]["effective_state"] == "recovery_probe"
    assert rows[0]["late_recovery_chase_signal"] is True
    assert events[-1]["trigger"] == "recovery_probe_entered"


def test_v3_2_turning_failure_keeps_structural_recovery_history():
    state = _v3_state(
        "turning_probe", risk_anchor_low=100.0, risk_anchor_atr=5.0,
        risk_anchor_date="2026-01-30", turning_probe_days=3,
        risk_score_history=[80.0, 75.0], above_ema_history=[True, False],
        new_low_history=[False, False],
    )
    _, state, rows = update_shadow_states_v3_2(
        state, [_v3_observation("2026-02-02", risk_score=72, signal_low=99.0)],
        "2026-02-02",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert state["AAPL"]["risk_score_history"] == [80.0, 75.0, 72.0]
    assert state["AAPL"]["above_ema_history"] == [True, False, True]


def test_v3_2_timeout_activates_breakeven_three_atr_trailing_without_selling():
    state = _v3_state(
        "turning_probe", risk_anchor_low=100.0, risk_anchor_atr=5.0,
        risk_anchor_date="2026-01-01", turning_probe_days=19,
        turning_probe_entry_price=100.0,
    )
    events, state, rows = update_shadow_states_v3_2(
        state,
        [_v3_observation(
            "2026-02-02", state="risk_off", risk_score=75,
            signal_low=100.0, signal_close=110.0,
        )],
        "2026-02-02",
    )
    assert rows[0]["effective_state"] == "turning_probe"
    assert rows[0]["shadow_exposure_pct"] == 10.0
    assert rows[0]["turning_trailing_active"] is True
    assert rows[0]["turning_trailing_stop"] == 100.0
    assert events[-1]["event"] == "turning_trailing_start"
    assert events[-1]["trigger"] == "turning_probe_timeout_trailing_activated"


def test_v3_2_turning_trailing_ratchets_up_and_exits_without_lowering_stop():
    state = _v3_state(
        "turning_probe", risk_anchor_low=100.0, risk_anchor_atr=5.0,
        risk_anchor_date="2026-01-01", turning_probe_days=20,
        turning_probe_entry_price=100.0,
        turning_trailing_active=True, turning_trailing_stop=100.0,
    )
    _, state, rows = update_shadow_states_v3_2(
        state,
        [_v3_observation(
            "2026-02-02", state="risk_off", risk_score=75,
            signal_low=105.0, signal_close=120.0,
        )],
        "2026-02-02",
    )
    assert rows[0]["turning_trailing_stop"] == 105.0

    _, state, rows = update_shadow_states_v3_2(
        state,
        [_v3_observation(
            "2026-02-03", state="risk_off", risk_score=75,
            signal_low=106.0, signal_close=115.0,
        )],
        "2026-02-03",
    )
    assert rows[0]["turning_trailing_stop"] == 105.0

    events, state, rows = update_shadow_states_v3_2(
        state,
        [_v3_observation(
            "2026-02-04", state="risk_off", risk_score=75,
            signal_low=104.0, signal_close=104.0,
        )],
        "2026-02-04",
    )
    assert rows[0]["effective_state"] == "risk_off"
    assert events[-1]["trigger"] == "turning_probe_trailing_stop"
    assert events[-2]["price"] == 104.0
