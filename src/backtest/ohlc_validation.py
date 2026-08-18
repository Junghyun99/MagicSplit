"""Strict, pre-backtest validation for raw OHLC downloads.

The normal backtest cache deliberately adjusts its start date for late IPOs and
forward-fills remaining gaps.  Those behaviours are useful for an ordinary
single backtest, but would hide an incomplete constituent in a common-period
universe comparison.  This module therefore validates the *raw* yfinance
response before either adjustment is made.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


OHLC_FIELDS = ("High", "Low", "Close")


def validate_raw_ohlc(
    raw_ohlc: pd.DataFrame,
    tickers: Iterable[str],
    *,
    required_start: str | pd.Timestamp,
    required_end: str | pd.Timestamp,
    date_tolerance_days: int = 7,
    max_missing_sessions: int = 0,
    max_consecutive_missing: int = 0,
) -> list[dict]:
    """Return one strict validation result per ticker.

    ``raw_ohlc`` must have ``(field, ticker)`` MultiIndex columns and must not
    have been trimmed or forward-filled.  The DataFrame index is used as the
    source's trading-session calendar, so Korean holidays do not count as
    missing sessions.
    """
    if not isinstance(raw_ohlc.columns, pd.MultiIndex):
        raise ValueError("raw_ohlc must have MultiIndex columns: (field, ticker)")

    required_start_ts = pd.Timestamp(required_start)
    required_end_ts = pd.Timestamp(required_end)
    index = pd.DatetimeIndex(raw_ohlc.index).sort_values()
    available = set(raw_ohlc.columns.get_level_values(1))
    results: list[dict] = []

    for ticker in tickers:
        reasons: list[str] = []
        if ticker not in available:
            results.append(_result(ticker, reasons=["ticker column missing"]))
            continue

        columns = [(field, ticker) for field in OHLC_FIELDS]
        if not all(column in raw_ohlc.columns for column in columns):
            results.append(_result(ticker, reasons=["one or more OHLC fields missing"]))
            continue

        block = raw_ohlc.loc[:, columns].copy()
        block.columns = list(OHLC_FIELDS)
        numeric = block.apply(pd.to_numeric, errors="coerce").astype(float)
        valid = numeric.notna().all(axis=1)
        finite = pd.Series(np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1), index=index)
        valid &= finite
        valid &= (numeric > 0).all(axis=1)
        valid &= numeric["High"] >= numeric["Low"]
        valid &= numeric["High"] >= numeric["Close"]
        valid &= numeric["Close"] >= numeric["Low"]

        valid_dates = index[valid.to_numpy()]
        if valid_dates.empty:
            results.append(_result(ticker, reasons=["no valid High/Low/Close rows"]))
            continue

        first_valid = valid_dates.min()
        last_valid = valid_dates.max()
        if first_valid > required_start_ts + pd.Timedelta(days=date_tolerance_days):
            reasons.append("insufficient warm-up history")
        if last_valid < required_end_ts - pd.Timedelta(days=date_tolerance_days):
            reasons.append("data ends before requested end")

        # Only sessions in the required window are relevant.  Missing rows
        # before a later IPO are already caught by the warm-up test.
        in_window = (index >= required_start_ts) & (index <= required_end_ts)
        missing = (~valid.to_numpy()) & in_window
        missing_sessions = int(missing.sum())
        longest_gap = _longest_true_run(missing)
        if missing_sessions > max_missing_sessions:
            reasons.append(f"missing sessions {missing_sessions}>{max_missing_sessions}")
        if longest_gap > max_consecutive_missing:
            reasons.append(f"longest missing gap {longest_gap}>{max_consecutive_missing}")

        results.append(
            _result(
                ticker,
                first_valid=first_valid.date().isoformat(),
                last_valid=last_valid.date().isoformat(),
                valid_sessions=int(valid.sum()),
                missing_sessions=missing_sessions,
                longest_missing_gap=longest_gap,
                reasons=reasons,
            )
        )

    return results


def _longest_true_run(values: np.ndarray) -> int:
    longest = current = 0
    for value in values:
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _result(ticker: str, *, reasons: list[str], **values) -> dict:
    return {
        "ticker": ticker,
        "status": "PASS" if not reasons else "FAIL",
        "reasons": "; ".join(reasons),
        "first_valid": None,
        "last_valid": None,
        "valid_sessions": 0,
        "missing_sessions": None,
        "longest_missing_gap": None,
        **values,
    }
