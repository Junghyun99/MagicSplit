"""Price-action shadow classifier. It never participates in order decisions."""
from __future__ import annotations

import math
from collections import Counter
from typing import Iterable

import numpy as np
import pandas as pd

from src.core.logic.regime import classify_channel, ema

SCORE_VERSION = "price_action_v1"
SCORE_VERSION_V2 = "price_action_v2"
SCORE_VERSION_V3 = "price_action_v3"
SCORE_VERSION_V3_1 = "price_action_v3_1"
SCORE_VERSION_V3_2 = "price_action_v3_2"
STATES = {
    "trend", "range", "neutral", "risk_off", "turning_probe", "recovery_probe",
}

V2_POLICY = {
    "trend_threshold": 55.0,
    "range_threshold": 60.0,
    "risk_threshold": 70.0,
    "risk_ceiling": 50.0,
    "score_margin": 5.0,
    "trend_maintain_threshold": 45.0,
    "range_maintain_threshold": 50.0,
    "maintain_risk_ceiling": 60.0,
    "confirmation_window": 15,
    "confirmation_votes": 10,
    "minimum_dwell": 63,
    "risk_entry_bars": 2,
    "risk_exit_threshold": 50.0,
    "risk_exit_window": 10,
    "risk_exit_votes": 7,
}

V3_POLICY = {
    **V2_POLICY,
    "recovery_risk_drop": 12.0,
    "recovery_risk_history": 10,
    "recovery_ema_window": 5,
    "recovery_ema_votes": 3,
    "recovery_no_new_low_bars": 5,
    "recovery_evidence_required": 3,
    "probe_success_risk_threshold": 60.0,
    "probe_success_bars": 5,
    "probe_floor_atr": 1.0,
    "probe_timeout_bars": 20,
    "probe_shadow_exposure_pct": 25.0,
    "turning_no_new_low_bars": 2,
    "turning_evidence_required": 2,
    "turning_max_anchor_atr": 2.0,
    "turning_stop_atr": 0.5,
    "turning_timeout_bars": 20,
    "turning_cooldown_bars": 5,
    "turning_shadow_exposure_pct": 10.0,
    "turning_trailing_atr_multiplier": 3.0,
    "price_gap_limit_pct": 30.0,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _efficiency(values: np.ndarray) -> tuple[float, float]:
    logs = np.log(values)
    changes = np.abs(np.diff(logs)).sum()
    net = float(logs[-1] - logs[0])
    return (abs(net) / changes if changes > 0 else 0.0), net


def _neutral(
    date: str, ticker: str, price: float, reason: str,
    score_version: str = SCORE_VERSION,
) -> dict:
    return {
        "date": date, "ticker": ticker, "price": round(float(price), 6),
        "score_version": score_version, "data_ready": False,
        "trend_score": 0.0, "range_score": 0.0, "risk_score": 0.0,
        "candidate_state": "neutral", "reason_code": reason,
    }


def _classify_scores(
    trend_score: float, range_score: float, risk_score: float, *,
    trend_threshold: float, range_threshold: float, risk_threshold: float,
    risk_ceiling: float, score_margin: float,
) -> tuple[str, str]:
    if risk_score >= risk_threshold:
        return "risk_off", "risk_threshold"
    if (trend_score >= trend_threshold
            and trend_score - range_score >= score_margin
            and risk_score < risk_ceiling):
        return "trend", "trend_dominant"
    if (range_score >= range_threshold
            and range_score - trend_score >= score_margin
            and risk_score < risk_ceiling):
        return "range", "range_dominant"
    return "neutral", "ambiguous_scores"


def compute_shadow_observation(rule, df: pd.DataFrame, date: str, price: float) -> dict:
    """Compute explainable scores using only the supplied historical OHLC window."""
    needed = max(int(rule.long_channel_lookback), 253)
    if df is None or len(df) < needed:
        return _neutral(date, rule.ticker, price, "insufficient_data")
    closes = df["Close"].astype(float).to_numpy()
    if not np.all(np.isfinite(closes[-needed:])) or np.any(closes[-needed:] <= 0):
        return _neutral(date, rule.ticker, price, "invalid_price_data")

    short = classify_channel(
        df, lookback=rule.channel_lookback, stddev_k=rule.channel_stddev_k,
        slope_band_pct=rule.channel_slope_band_pct,
    )
    long = classify_channel(
        df, lookback=rule.long_channel_lookback, stddev_k=rule.channel_stddev_k,
        slope_band_pct=rule.channel_slope_band_pct,
    )
    if str(short.regime) == "unknown" or str(long.regime) == "unknown":
        return _neutral(date, rule.ticker, price, "insufficient_channel_data")

    er63, net63 = _efficiency(closes[-64:])
    er252, net252 = _efficiency(closes[-253:])
    ema20 = ema(df["Close"].astype(float), 20).to_numpy()
    signs = np.sign(closes[-63:] - ema20[-63:])
    crossings = int(np.sum((signs[1:] * signs[:-1]) < 0))
    band = max(float(rule.channel_slope_band_pct), 1e-9)
    long_up = _clamp(long.channel_slope_pct / (2 * band))
    short_up = _clamp(short.channel_slope_pct / (2 * band))
    long_down = _clamp(-long.channel_slope_pct / (2 * band))
    short_down = _clamp(-short.channel_slope_pct / (2 * band))
    positive_er = 0.5 * (er63 * (net63 > 0) + er252 * (net252 > 0))
    low_efficiency = 1 - 0.5 * (er63 + er252)
    flat_long = 1 - _clamp(abs(long.channel_slope_pct) / band)
    flat_short = 1 - _clamp(abs(short.channel_slope_pct) / band)
    upper_half = max(short.channel_resistance - short.channel_mid, 1e-9)
    above_mid = _clamp((short.close - short.channel_mid) / upper_half)
    atr_value = long.atr if math.isfinite(long.atr) and long.atr > 0 else 1e-9
    breakdown = _clamp((long.channel_support - long.close) / (3 * atr_value))
    drawdown252 = float(long.close / np.max(closes[-252:]) - 1)
    drawdown_score = _clamp(-drawdown252 / 0.30)
    lows = df["Low"].astype(float).to_numpy()
    signal_close = float(closes[-1])
    signal_high = float(df["High"].astype(float).to_numpy()[-1])
    signal_low = float(lows[-1])
    is_new_20d_low = signal_low < float(np.min(lows[-21:-1]))
    previous_close = float(closes[-2])
    price_gap_pct = (signal_close / previous_close - 1) * 100 if previous_close > 0 else math.inf
    ema20_distance_atr = (signal_close - float(ema20[-1])) / atr_value

    trend_score = 100 * (
        0.30 * long_up + 0.25 * short_up + 0.30 * positive_er + 0.15 * above_mid
    )
    range_score = 100 * (
        0.30 * flat_long + 0.25 * flat_short
        + 0.25 * low_efficiency + 0.20 * _clamp(crossings / 8)
    )
    risk_score = 100 * (
        0.35 * long_down + 0.25 * short_down
        + 0.20 * breakdown + 0.20 * drawdown_score
    )

    candidate, reason = _classify_scores(
        trend_score, range_score, risk_score,
        trend_threshold=rule.shadow_mode_trend_threshold,
        range_threshold=rule.shadow_mode_range_threshold,
        risk_threshold=rule.shadow_mode_risk_threshold,
        risk_ceiling=rule.shadow_mode_risk_ceiling,
        score_margin=rule.shadow_mode_score_margin,
    )

    return {
        "date": date, "ticker": rule.ticker, "price": round(float(price), 6),
        "score_version": SCORE_VERSION, "data_ready": True,
        "long_regime": str(long.regime), "short_regime": str(short.regime),
        "long_slope_pct": round(long.channel_slope_pct, 6),
        "short_slope_pct": round(short.channel_slope_pct, 6),
        "efficiency_ratio_63": round(er63, 6),
        "efficiency_ratio_252": round(er252, 6),
        "ema20_crossings_63": crossings,
        "short_channel_position": round(
            _clamp((short.close - short.channel_support)
                   / max(short.channel_resistance - short.channel_support, 1e-9)), 6),
        "long_breakdown_atr": round(max(long.channel_support - long.close, 0) / atr_value, 6),
        "drawdown_252": round(drawdown252, 6),
        "signal_close": round(signal_close, 6),
        "signal_high": round(signal_high, 6),
        "signal_low": round(signal_low, 6),
        "signal_atr": round(atr_value, 6),
        "ema20_distance_atr": round(ema20_distance_atr, 6),
        "price_gap_pct": round(price_gap_pct, 6),
        "price_gap_guard_passed": bool(
            math.isfinite(price_gap_pct)
            and abs(price_gap_pct) <= V3_POLICY["price_gap_limit_pct"]
        ),
        "above_ema20": bool(signal_close > float(ema20[-1])),
        "above_short_midline": bool(signal_close > float(short.channel_mid)),
        "is_new_20d_low": bool(is_new_20d_low),
        "trend_score": round(trend_score, 6),
        "range_score": round(range_score, 6),
        "risk_score": round(risk_score, 6),
        "candidate_state": candidate, "reason_code": reason,
    }


def compute_shadow_observations(
    rule, df: pd.DataFrame, date: str, price: float, *,
    include_v2: bool = False, include_v3: bool = False,
    include_v3_1: bool = False, include_v3_2: bool = False,
) -> list[dict]:
    """Calculate OHLC features once and classify them under each policy version."""
    v1 = compute_shadow_observation(rule, df, date, price)
    rows = [v1]
    if not include_v2 and not include_v3 and not include_v3_1 and not include_v3_2:
        return rows
    v2 = dict(v1)
    v2["score_version"] = SCORE_VERSION_V2
    if v2["data_ready"]:
        candidate, reason = _classify_scores(
            v2["trend_score"], v2["range_score"], v2["risk_score"],
            trend_threshold=V2_POLICY["trend_threshold"],
            range_threshold=V2_POLICY["range_threshold"],
            risk_threshold=V2_POLICY["risk_threshold"],
            risk_ceiling=V2_POLICY["risk_ceiling"],
            score_margin=V2_POLICY["score_margin"],
        )
        v2["candidate_state"], v2["reason_code"] = candidate, reason
    if include_v2:
        rows.append(v2)
    if include_v3:
        v3 = dict(v2)
        v3["score_version"] = SCORE_VERSION_V3
        rows.append(v3)
    if include_v3_1:
        v3_1 = dict(v2)
        v3_1["score_version"] = SCORE_VERSION_V3_1
        rows.append(v3_1)
    if include_v3_2:
        v3_2 = dict(v2)
        v3_2["score_version"] = SCORE_VERSION_V3_2
        rows.append(v3_2)
    return rows


def update_shadow_states(previous: dict, observations: Iterable[dict], date: str):
    """Apply confirmation, monthly hysteresis and episode accounting."""
    state = {ticker: dict(value) for ticker, value in (previous or {}).items()}
    events, scored = [], []
    month = date[:7]
    for item in observations:
        row = dict(item)
        ticker = row["ticker"]
        st = state.get(ticker)
        if st is None:
            st = {
                "effective_state": "neutral", "state_start_date": date,
                "state_start_price": row.get("price"), "state_days": 0,
                "candidate_state": None, "candidate_days": 0,
                "last_evaluation_month": None,
            }
            state[ticker] = st
            events.append({
                "date": date, "ticker": ticker, "event": "mode_start",
                "state": "neutral", "price": row.get("price"),
                "score_version": SCORE_VERSION, "trigger": "initial_state",
            })

        first_in_month = st.get("last_evaluation_month") != month
        st["last_evaluation_month"] = month
        st["state_days"] = int(st.get("state_days", 0)) + 1
        candidate = row["candidate_state"]
        if candidate == st.get("candidate_state"):
            st["candidate_days"] = int(st.get("candidate_days", 0)) + 1
        else:
            st["candidate_state"] = candidate
            st["candidate_days"] = 1

        current = st["effective_state"]
        transition = False
        if candidate == "risk_off" and current != "risk_off":
            transition = st["candidate_days"] >= 2
        elif candidate != current and candidate != "risk_off":
            confirmed = st["candidate_days"] >= 20
            dwell_ok = current == "risk_off" or st["state_days"] >= 63
            transition = confirmed and first_in_month and dwell_ok

        if transition:
            events.append({
                "date": date, "ticker": ticker, "event": "mode_end",
                "state": current, "start_date": st["state_start_date"],
                "start_price": st.get("state_start_price"),
                "price": row.get("price"), "trading_days": st["state_days"],
                "next_state": candidate, "score_version": SCORE_VERSION,
            })
            events.append({
                "date": date, "ticker": ticker, "event": "mode_start",
                "state": candidate, "price": row.get("price"),
                "score_version": SCORE_VERSION, "trigger": row.get("reason_code"),
                "trend_score": row.get("trend_score"),
                "range_score": row.get("range_score"),
                "risk_score": row.get("risk_score"),
            })
            st.update({
                "effective_state": candidate, "state_start_date": date,
                "state_start_price": row.get("price"), "state_days": 0,
                "candidate_days": 0,
            })
        st["last_date"] = date
        st["last_price"] = row.get("price")
        row["effective_state"] = st["effective_state"]
        row["candidate_days"] = st["candidate_days"]
        scored.append(row)
    return events, state, scored


def update_shadow_states_v2(previous: dict, observations: Iterable[dict], date: str):
    """Apply V2 voting, maintenance hysteresis and symmetric risk recovery."""
    state = {ticker: dict(value) for ticker, value in (previous or {}).items()}
    events, scored = [], []
    month = date[:7]
    for item in observations:
        row = dict(item)
        ticker = row["ticker"]
        st = state.get(ticker)
        if st is None:
            st = {
                "effective_state": "neutral", "state_start_date": date,
                "state_start_price": row.get("price"), "state_days": 0,
                "candidate_history": [], "risk_clear_history": [],
                "risk_entry_days": 0, "last_evaluation_month": None,
            }
            state[ticker] = st
            events.append({
                "date": date, "ticker": ticker, "event": "mode_start",
                "state": "neutral", "price": row.get("price"),
                "score_version": SCORE_VERSION_V2, "trigger": "initial_state",
            })

        first_in_month = st.get("last_evaluation_month") != month
        st["last_evaluation_month"] = month
        st["state_days"] = int(st.get("state_days", 0)) + 1
        current = st["effective_state"]
        candidate = row["candidate_state"]
        transition_candidate = candidate

        transition_reason = row.get("reason_code")

        # Once a normal mode is established, a softer threshold maintains it.
        if (current == "trend" and candidate == "neutral"
                and row["trend_score"] >= V2_POLICY["trend_maintain_threshold"]
                and row["risk_score"] < V2_POLICY["maintain_risk_ceiling"]):
            transition_candidate = "trend"
            transition_reason = "trend_maintained"
        elif (current == "range" and candidate == "neutral"
              and row["range_score"] >= V2_POLICY["range_maintain_threshold"]
              and row["risk_score"] < V2_POLICY["maintain_risk_ceiling"]):
            transition_candidate = "range"
            transition_reason = "range_maintained"

        history = list(st.get("candidate_history") or [])
        history.append(transition_candidate)
        history = history[-V2_POLICY["confirmation_window"]:]
        st["candidate_history"] = history
        vote_counts = Counter(history)

        risk_clear = list(st.get("risk_clear_history") or [])
        risk_clear.append(row["risk_score"] < V2_POLICY["risk_exit_threshold"])
        risk_clear = risk_clear[-V2_POLICY["risk_exit_window"]:]
        st["risk_clear_history"] = risk_clear
        risk_clear_votes = sum(risk_clear)

        if row["risk_score"] >= V2_POLICY["risk_threshold"]:
            st["risk_entry_days"] = int(st.get("risk_entry_days", 0)) + 1
        else:
            st["risk_entry_days"] = 0

        target = current
        trigger = transition_reason
        if (current != "risk_off"
                and st["risk_entry_days"] >= V2_POLICY["risk_entry_bars"]):
            target, trigger = "risk_off", "risk_confirmed"
        elif current == "risk_off":
            risk_exit_ready = (
                len(risk_clear) >= V2_POLICY["risk_exit_window"]
                and risk_clear_votes >= V2_POLICY["risk_exit_votes"]
            )
            if risk_exit_ready:
                target, trigger = "neutral", "risk_recovered"
        else:
            eligible = [
                mode for mode in ("trend", "range", "neutral")
                if mode != current
                and vote_counts[mode] >= V2_POLICY["confirmation_votes"]
            ]
            if eligible and first_in_month and st["state_days"] >= V2_POLICY["minimum_dwell"]:
                target = max(eligible, key=lambda mode: vote_counts[mode])
                trigger = f"rolling_vote_{vote_counts[target]}_of_{len(history)}"

        if target != current:
            events.append({
                "date": date, "ticker": ticker, "event": "mode_end",
                "state": current, "start_date": st["state_start_date"],
                "start_price": st.get("state_start_price"),
                "price": row.get("price"), "trading_days": st["state_days"],
                "next_state": target, "score_version": SCORE_VERSION_V2,
            })
            events.append({
                "date": date, "ticker": ticker, "event": "mode_start",
                "state": target, "price": row.get("price"),
                "score_version": SCORE_VERSION_V2, "trigger": trigger,
                "trend_score": row.get("trend_score"),
                "range_score": row.get("range_score"),
                "risk_score": row.get("risk_score"),
            })
            st.update({
                "effective_state": target, "state_start_date": date,
                "state_start_price": row.get("price"), "state_days": 0,
                "candidate_history": [], "risk_clear_history": [],
                "risk_entry_days": 0,
            })

        st["last_date"] = date
        st["last_price"] = row.get("price")
        row["effective_state"] = st["effective_state"]
        row["transition_candidate"] = transition_candidate
        row["confirmation_votes"] = vote_counts[transition_candidate]
        row["risk_clear_votes"] = risk_clear_votes
        scored.append(row)
    return events, state, scored


def _update_shadow_states_v3(
    previous: dict, observations: Iterable[dict], date: str, *,
    score_version: str, preserve_initial_risk_history: bool,
    enable_turning_probe: bool = False,
):
    """Apply V2 normal modes plus an evidence-based recovery probe from risk."""
    state = {ticker: dict(value) for ticker, value in (previous or {}).items()}
    events, scored = [], []
    month = date[:7]
    for item in observations:
        row = dict(item)
        ticker = row["ticker"]
        st = state.get(ticker)
        if st is None:
            st = {
                "effective_state": "neutral", "state_start_date": date,
                "state_start_price": row.get("price"), "state_days": 0,
                "candidate_history": [], "risk_score_history": [],
                "above_ema_history": [], "new_low_history": [],
                "risk_entry_days": 0, "risk_below_60_days": 0,
                "last_evaluation_month": None, "probe_days": 0,
                "turning_probe_days": 0, "turning_cooldown_days": 0,
                "turning_trailing_active": False,
                "turning_trailing_stop": None,
                "risk_anchor_low": None, "risk_anchor_atr": None,
                "risk_anchor_date": None, "risk_anchor_age": 0,
                "turning_lows": [], "turning_highs": [],
                "turning_ema_distances": [], "turning_risk_scores": [],
            }
            state[ticker] = st
            events.append({
                "date": date, "ticker": ticker, "event": "mode_start",
                "state": "neutral", "price": row.get("price"),
                "score_version": score_version, "trigger": "initial_state",
            })

        first_in_month = st.get("last_evaluation_month") != month
        st["last_evaluation_month"] = month
        st["state_days"] = int(st.get("state_days", 0)) + 1
        current = st["effective_state"]
        candidate = row["candidate_state"]
        transition_candidate = candidate

        signal_low = float(row.get("signal_low", row.get("price", 0)) or 0)
        signal_high = float(row.get("signal_high", row.get("price", 0)) or 0)
        signal_close = float(row.get("signal_close", row.get("price", 0)) or 0)
        signal_atr = max(float(row.get("signal_atr", 0) or 0), 1e-9)
        ema_distance = float(row.get("ema20_distance_atr", 0) or 0)

        prior_turning_lows = list(st.get("turning_lows") or [])
        prior_turning_highs = list(st.get("turning_highs") or [])
        prior_turning_ema = list(st.get("turning_ema_distances") or [])
        prior_turning_risk = list(st.get("turning_risk_scores") or [])
        st["turning_lows"] = (prior_turning_lows + [signal_low])[-3:]
        st["turning_highs"] = (prior_turning_highs + [signal_high])[-3:]
        st["turning_ema_distances"] = (prior_turning_ema + [ema_distance])[-3:]

        anchor_lowered_today = False
        if enable_turning_probe and current == "risk_off":
            cooldown = max(0, int(st.get("turning_cooldown_days", 0)) - 1)
            st["turning_cooldown_days"] = cooldown
            anchor = st.get("risk_anchor_low")
            if anchor is None or signal_low < float(anchor):
                st.update({
                    "risk_anchor_low": signal_low, "risk_anchor_atr": signal_atr,
                    "risk_anchor_date": date, "risk_anchor_age": 0,
                })
                anchor_lowered_today = True
            else:
                st["risk_anchor_age"] = int(st.get("risk_anchor_age", 0)) + 1

        if (current == "trend" and candidate == "neutral"
                and row.get("trend_score", 0) >= V3_POLICY["trend_maintain_threshold"]
                and row.get("risk_score", 100) < V3_POLICY["maintain_risk_ceiling"]):
            transition_candidate = "trend"
        elif (current == "range" and candidate == "neutral"
              and row.get("range_score", 0) >= V3_POLICY["range_maintain_threshold"]
              and row.get("risk_score", 100) < V3_POLICY["maintain_risk_ceiling"]):
            transition_candidate = "range"

        candidate_history = list(st.get("candidate_history") or [])
        candidate_history.append(transition_candidate)
        candidate_history = candidate_history[-V3_POLICY["confirmation_window"]:]
        st["candidate_history"] = candidate_history
        vote_counts = Counter(candidate_history)

        prior_risk_scores = list(st.get("risk_score_history") or [])
        risk_score = float(row.get("risk_score", 0))
        st["turning_risk_scores"] = (prior_turning_risk + [risk_score])[-3:]
        risk_improvement = (
            max(prior_risk_scores) - risk_score
            if len(prior_risk_scores) >= V3_POLICY["recovery_risk_history"] else 0.0
        )
        risk_scores = (prior_risk_scores + [risk_score])[-V3_POLICY["recovery_risk_history"]:]
        st["risk_score_history"] = risk_scores

        above_ema_history = list(st.get("above_ema_history") or [])
        above_ema_history.append(bool(row.get("above_ema20", False)))
        above_ema_history = above_ema_history[-V3_POLICY["recovery_ema_window"]:]
        st["above_ema_history"] = above_ema_history

        new_low_history = list(st.get("new_low_history") or [])
        new_low_history.append(bool(row.get("is_new_20d_low", False)))
        new_low_history = new_low_history[-V3_POLICY["recovery_no_new_low_bars"]:]
        st["new_low_history"] = new_low_history

        if risk_score >= V3_POLICY["risk_threshold"]:
            st["risk_entry_days"] = int(st.get("risk_entry_days", 0)) + 1
        else:
            st["risk_entry_days"] = 0
        if risk_score < V3_POLICY["probe_success_risk_threshold"]:
            st["risk_below_60_days"] = int(st.get("risk_below_60_days", 0)) + 1
        else:
            st["risk_below_60_days"] = 0
        if current == "recovery_probe":
            st["probe_days"] = int(st.get("probe_days", 1)) + 1
        if current == "turning_probe":
            st["turning_probe_days"] = int(st.get("turning_probe_days", 1)) + 1

        evidence = {
            "risk_improving": (
                len(prior_risk_scores) >= V3_POLICY["recovery_risk_history"]
                and risk_improvement >= V3_POLICY["recovery_risk_drop"]
            ),
            "ema_recovered": (
                len(above_ema_history) >= V3_POLICY["recovery_ema_window"]
                and sum(above_ema_history) >= V3_POLICY["recovery_ema_votes"]
            ),
            "short_slope_nonnegative": row.get("short_slope_pct", -math.inf) >= 0,
            "no_new_20d_low": (
                len(new_low_history) >= V3_POLICY["recovery_no_new_low_bars"]
                and not any(new_low_history)
            ),
        }
        evidence_count = sum(evidence.values())
        turning_evidence = {
            "higher_low": bool(prior_turning_lows and signal_low > prior_turning_lows[-1]),
            "close_above_previous_high": bool(
                prior_turning_highs and signal_close > prior_turning_highs[-1]
            ),
            "ema_distance_improving_2d": bool(
                len(prior_turning_ema) >= 2
                and prior_turning_ema[-2] < prior_turning_ema[-1] < ema_distance
            ),
            "risk_score_declining_2d": bool(
                len(prior_turning_risk) >= 2
                and prior_turning_risk[-2] > prior_turning_risk[-1] > risk_score
            ),
        }
        turning_evidence_count = sum(turning_evidence.values())
        anchor_low = float(st.get("risk_anchor_low") or signal_low)
        anchor_atr = max(float(st.get("risk_anchor_atr") or signal_atr), 1e-9)
        anchor_distance_atr = (signal_close - anchor_low) / anchor_atr
        turning_ready = (
            enable_turning_probe
            and bool(row.get("data_ready"))
            and int(st.get("turning_cooldown_days", 0)) == 0
            and not anchor_lowered_today
            and int(st.get("risk_anchor_age", 0)) >= V3_POLICY["turning_no_new_low_bars"]
            and turning_evidence_count >= V3_POLICY["turning_evidence_required"]
            and 0 <= anchor_distance_atr <= V3_POLICY["turning_max_anchor_atr"]
            and bool(row.get("price_gap_guard_passed", True))
        )
        target, trigger = current, None
        transition_price = row.get("price")

        late_recovery_chase_signal = False
        if current not in ("risk_off", "turning_probe", "recovery_probe"):
            if st["risk_entry_days"] >= V3_POLICY["risk_entry_bars"]:
                target, trigger = "risk_off", "risk_confirmed"
            else:
                eligible = [
                    mode for mode in ("trend", "range", "neutral")
                    if mode != current
                    and vote_counts[mode] >= V3_POLICY["confirmation_votes"]
                ]
                if (eligible and first_in_month
                        and st["state_days"] >= V3_POLICY["minimum_dwell"]):
                    target = max(eligible, key=lambda mode: vote_counts[mode])
                    trigger = f"rolling_vote_{vote_counts[target]}_of_{len(candidate_history)}"
        elif current == "risk_off":
            recovery_ready = (
                bool(row.get("data_ready"))
                and risk_score < V3_POLICY["risk_threshold"]
                and evidence_count >= V3_POLICY["recovery_evidence_required"]
            )
            late_recovery_chase_signal = bool(
                enable_turning_probe
                and recovery_ready
                and anchor_distance_atr > V3_POLICY["turning_max_anchor_atr"]
            )
            if recovery_ready:
                target, trigger = "recovery_probe", "recovery_probe_entered"
            elif turning_ready:
                target, trigger = "turning_probe", "turning_probe_entered"
        elif current == "turning_probe":
            turning_floor = (
                float(st.get("risk_anchor_low") or signal_low)
                - V3_POLICY["turning_stop_atr"]
                * float(st.get("risk_anchor_atr") or signal_atr)
            )
            recovery_ready = (
                bool(row.get("data_ready"))
                and risk_score < V3_POLICY["risk_threshold"]
                and evidence_count >= V3_POLICY["recovery_evidence_required"]
            )
            trailing_active = bool(st.get("turning_trailing_active", False))
            trailing_stop = float(st.get("turning_trailing_stop") or 0)
            if trailing_active:
                if signal_low < trailing_stop:
                    target, trigger = "risk_off", "turning_probe_trailing_stop"
                    transition_price = min(trailing_stop, signal_close)
                elif recovery_ready:
                    target, trigger = "recovery_probe", "turning_probe_confirmed_recovery"
                else:
                    st["turning_trailing_stop"] = max(
                        trailing_stop,
                        float(st.get("turning_probe_entry_price") or signal_close),
                        signal_close
                        - V3_POLICY["turning_trailing_atr_multiplier"] * signal_atr,
                    )
            elif signal_low < turning_floor:
                target, trigger = "risk_off", "turning_probe_failed_atr_floor"
            elif signal_low < float(st.get("risk_anchor_low") or signal_low):
                target, trigger = "risk_off", "turning_probe_failed_new_anchor_low"
            elif recovery_ready:
                target, trigger = "recovery_probe", "turning_probe_confirmed_recovery"
            elif st["turning_probe_days"] >= V3_POLICY["turning_timeout_bars"]:
                trailing_stop = max(
                    float(st.get("turning_probe_entry_price") or signal_close),
                    signal_close
                    - V3_POLICY["turning_trailing_atr_multiplier"] * signal_atr,
                )
                st.update({
                    "turning_trailing_active": True,
                    "turning_trailing_stop": trailing_stop,
                    "turning_trailing_start_date": date,
                    "turning_trailing_start_price": row.get("price"),
                })
                events.append({
                    "date": date, "ticker": ticker,
                    "event": "turning_trailing_start",
                    "state": "turning_probe", "price": row.get("price"),
                    "score_version": score_version,
                    "trigger": "turning_probe_timeout_trailing_activated",
                    "trading_days": st["turning_probe_days"],
                    "turning_start_date": st.get("state_start_date"),
                    "turning_entry_price": st.get("turning_probe_entry_price"),
                    "trailing_stop": round(trailing_stop, 6),
                    "atr_multiplier": V3_POLICY["turning_trailing_atr_multiplier"],
                })
        else:
            probe_floor = (
                float(st.get("probe_entry_low", 0))
                - V3_POLICY["probe_floor_atr"] * float(st.get("probe_entry_atr", 0))
            )
            if float(row.get("signal_low", math.inf)) < probe_floor:
                target, trigger = "risk_off", "probe_failed_atr_floor"
            elif row.get("is_new_20d_low", False):
                target, trigger = "risk_off", "probe_failed_new_20d_low"
            elif st["risk_entry_days"] >= V3_POLICY["risk_entry_bars"]:
                target, trigger = "risk_off", "probe_failed_risk_reconfirmed"
            else:
                success = (
                    st["risk_below_60_days"] >= V3_POLICY["probe_success_bars"]
                    and row.get("short_slope_pct", -math.inf) >= 0
                    and bool(row.get("above_short_midline", False))
                )
                if success:
                    confirmed = [
                        mode for mode in ("trend", "range")
                        if vote_counts[mode] >= V3_POLICY["confirmation_votes"]
                    ]
                    target = (
                        max(confirmed, key=lambda mode: vote_counts[mode])
                        if confirmed else "neutral"
                    )
                    trigger = f"probe_confirmed_{target}"
                elif st["probe_days"] >= V3_POLICY["probe_timeout_bars"]:
                    target, trigger = "neutral", "probe_timeout_neutral"

        if target != current:
            events.append({
                "date": date, "ticker": ticker, "event": "mode_end",
                "state": current, "start_date": st["state_start_date"],
                "start_price": st.get("state_start_price"),
                "price": transition_price, "trading_days": st["state_days"],
                "next_state": target, "score_version": score_version,
                "trigger": trigger,
            })
            start_event = {
                "date": date, "ticker": ticker, "event": "mode_start",
                "state": target, "price": row.get("price"),
                "score_version": score_version, "trigger": trigger,
                "trend_score": row.get("trend_score"),
                "range_score": row.get("range_score"),
                "risk_score": row.get("risk_score"),
            }
            if target == "recovery_probe":
                start_event.update({
                    "probe_entry_low": row.get("signal_low"),
                    "probe_entry_atr": row.get("signal_atr"),
                    "recovery_evidence": evidence,
                    "recovery_evidence_count": evidence_count,
                })
            elif target == "turning_probe":
                start_event.update({
                    "risk_anchor_date": st.get("risk_anchor_date"),
                    "risk_anchor_low": anchor_low,
                    "risk_anchor_atr": anchor_atr,
                    "anchor_distance_atr": round(anchor_distance_atr, 6),
                    "turning_evidence": turning_evidence,
                    "turning_evidence_count": turning_evidence_count,
                })
            events.append(start_event)
            st.update({
                "effective_state": target, "state_start_date": date,
                "state_start_price": row.get("price"), "state_days": 0,
            })
            if target == "recovery_probe":
                st.update({
                    "probe_entry_low": row.get("signal_low"),
                    "probe_entry_atr": row.get("signal_atr"),
                    "probe_days": 1,
                    "turning_probe_days": 0,
                    "turning_trailing_active": False,
                    "turning_trailing_stop": None,
                    "risk_below_60_days": 1 if risk_score < 60 else 0,
                })
            elif target == "turning_probe":
                st.update({
                    "turning_probe_days": 1,
                    "turning_probe_entry_price": row.get("price"),
                    "turning_trailing_active": False,
                    "turning_trailing_stop": None,
                    "turning_trailing_start_date": None,
                    "turning_trailing_start_price": None,
                    "probe_entry_low": None, "probe_entry_atr": None,
                    "probe_days": 0,
                })
            else:
                st.update({
                    "probe_entry_low": None, "probe_entry_atr": None,
                    "probe_days": 0, "turning_probe_days": 0,
                    "turning_trailing_active": False,
                    "turning_trailing_stop": None,
                })
            if (target == "risk_off" and current != "turning_probe" and (
                    not preserve_initial_risk_history
                    or current == "recovery_probe")):
                # Failed probes need ten fresh observations before another attempt.
                st.update({
                    "risk_score_history": [], "above_ema_history": [],
                    "new_low_history": [], "risk_below_60_days": 0,
                    "risk_entry_days": 0,
                })
            if enable_turning_probe and target == "risk_off":
                failed_turning = current == "turning_probe"
                st.update({
                    "risk_anchor_low": signal_low,
                    "risk_anchor_atr": signal_atr,
                    "risk_anchor_date": date,
                    "risk_anchor_age": 0,
                    "turning_cooldown_days": (
                        V3_POLICY["turning_cooldown_bars"] if failed_turning else 0
                    ),
                    "turning_lows": [signal_low],
                    "turning_highs": [signal_high],
                    "turning_ema_distances": [ema_distance],
                    "turning_risk_scores": [risk_score],
                    "turning_trailing_active": False,
                    "turning_trailing_stop": None,
                })
            elif enable_turning_probe and target not in (
                    "turning_probe", "recovery_probe"):
                st.update({
                    "risk_anchor_low": None, "risk_anchor_atr": None,
                    "risk_anchor_date": None, "risk_anchor_age": 0,
                    "turning_cooldown_days": 0,
                })

        st["last_date"] = date
        st["last_price"] = row.get("price")
        row.update({
            "effective_state": st["effective_state"],
            "transition_candidate": transition_candidate,
            "confirmation_votes": vote_counts[transition_candidate],
            "risk_improvement_10d": round(risk_improvement, 6),
            "recovery_evidence": evidence,
            "recovery_evidence_count": evidence_count,
            "turning_evidence": turning_evidence,
            "turning_evidence_count": turning_evidence_count,
            "risk_anchor_date": st.get("risk_anchor_date"),
            "risk_anchor_low": st.get("risk_anchor_low"),
            "risk_anchor_atr": st.get("risk_anchor_atr"),
            "risk_anchor_age": st.get("risk_anchor_age", 0),
            "anchor_distance_atr": round(anchor_distance_atr, 6),
            "late_recovery_chase_signal": late_recovery_chase_signal,
            "probe_days": st.get("probe_days", 0),
            "turning_probe_days": st.get("turning_probe_days", 0),
            "turning_trailing_active": bool(st.get("turning_trailing_active", False)),
            "turning_trailing_stop": st.get("turning_trailing_stop"),
            "shadow_exposure_pct": (
                V3_POLICY["turning_shadow_exposure_pct"]
                if st["effective_state"] == "turning_probe"
                else V3_POLICY["probe_shadow_exposure_pct"]
                if st["effective_state"] == "recovery_probe"
                else 0.0 if st["effective_state"] == "risk_off" else 100.0
            ),
        })
        scored.append(row)
    return events, state, scored


def update_shadow_states_v3(previous: dict, observations: Iterable[dict], date: str):
    return _update_shadow_states_v3(
        previous, observations, date,
        score_version=SCORE_VERSION_V3,
        preserve_initial_risk_history=False,
    )


def update_shadow_states_v3_1(previous: dict, observations: Iterable[dict], date: str):
    """V3.1 preserves recovery evidence on initial risk entry, not probe failure."""
    return _update_shadow_states_v3(
        previous, observations, date,
        score_version=SCORE_VERSION_V3_1,
        preserve_initial_risk_history=True,
    )


def update_shadow_states_v3_2(previous: dict, observations: Iterable[dict], date: str):
    """V3.2 keeps structural risk while allowing a 10% early turning probe."""
    return _update_shadow_states_v3(
        previous, observations, date,
        score_version=SCORE_VERSION_V3_2,
        preserve_initial_risk_history=False,
        enable_turning_probe=True,
    )
