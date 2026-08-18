import numpy as np
import pandas as pd

from src.backtest.ohlc_validation import validate_raw_ohlc


def _ohlc(ticker="005930", periods=5):
    index = pd.bdate_range("2024-01-02", periods=periods)
    columns = pd.MultiIndex.from_product([["High", "Low", "Close"], [ticker]])
    values = np.tile([101.0, 99.0, 100.0], (periods, 1))
    frame = pd.DataFrame(values, index=index, columns=columns)
    return frame


def test_complete_raw_ohlc_passes():
    frame = _ohlc()
    result = validate_raw_ohlc(
        frame, ["005930"], required_start="2024-01-01", required_end="2024-01-08",
    )
    assert result[0]["status"] == "PASS"
    assert result[0]["missing_sessions"] == 0


def test_late_history_fails_warmup_requirement():
    frame = _ohlc()
    result = validate_raw_ohlc(
        frame, ["005930"], required_start="2023-12-01", required_end="2024-01-08",
        date_tolerance_days=0,
    )
    assert result[0]["status"] == "FAIL"
    assert "insufficient warm-up history" in result[0]["reasons"]


def test_middle_gap_is_not_hidden_by_forward_fill():
    frame = _ohlc(periods=5)
    frame.loc[frame.index[2], ("Close", "005930")] = np.nan
    result = validate_raw_ohlc(
        frame, ["005930"], required_start="2024-01-01", required_end="2024-01-08",
    )
    assert result[0]["status"] == "FAIL"
    assert result[0]["missing_sessions"] == 1
    assert result[0]["longest_missing_gap"] == 1


def test_missing_ticker_column_fails():
    result = validate_raw_ohlc(
        _ohlc(), ["005930", "000660"], required_start="2024-01-01", required_end="2024-01-08",
    )
    assert result[1]["status"] == "FAIL"
    assert result[1]["reasons"] == "ticker column missing"
