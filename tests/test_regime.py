# tests/test_regime.py
import numpy as np
import pandas as pd
import pytest

from src.core.logic.regime import (
    Regime,
    RegimeReading,
    classify,
    true_range,
    atr,
    ema,
    adx,
    swing_high,
)


def _ohlc(closes, spread=0.5):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"High": closes + spread, "Low": closes - spread, "Close": closes},
        index=idx,
    )


def _ohlc_hl(highs, lows, closes):
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"High": highs, "Low": lows, "Close": closes}, index=idx
    ).astype(float)


def _uptrend(n=250, start=100.0, step=1.0):
    return _ohlc([start + i * step for i in range(n)])


def _downtrend(n=250, start=400.0, step=1.0):
    return _ohlc([start - i * step for i in range(n)])


def _sideways(n=250, base=100.0):
    # 매 봉 방향이 뒤집히는 지그재그 -> 방향성(ADX) 거의 0, MA 정렬 안 됨
    return _ohlc([base + (i % 2) * 2.0 for i in range(n)])


class TestIndicators:
    def test_true_range_exact(self):
        df = _ohlc_hl(
            highs=[10, 12, 11], lows=[8, 9, 7], closes=[9, 11, 8]
        )
        tr = true_range(df)
        # bar0: prev_close NaN -> H-L=2; bar1: max(3,3,0)=3; bar2: max(4,0,4)=4
        assert list(tr.values) == [2.0, 3.0, 4.0]

    def test_atr_wilder_exact(self):
        df = _ohlc_hl(highs=[10, 12, 11], lows=[8, 9, 7], closes=[9, 11, 8])
        a = atr(df, period=2)
        # TR=[2,3,4], ewm(alpha=0.5,adjust=False): 2 -> 2.5 -> 3.25
        assert a.iloc[-1] == pytest.approx(3.25)

    def test_ema_matches_pandas(self):
        df = _uptrend(30)
        expected = df["Close"].ewm(span=20, adjust=False).mean()
        pd.testing.assert_series_equal(ema(df["Close"], 20), expected)

    def test_adx_high_in_strong_uptrend(self):
        df = _uptrend()
        val = adx(df).iloc[-1]
        assert np.isfinite(val)
        assert val >= 25.0

    def test_adx_low_in_choppy(self):
        df = _sideways()
        assert adx(df).iloc[-1] < 25.0

    def test_swing_high(self):
        df = _ohlc([100, 105, 103, 108, 101])
        # 고가 = close+0.5; 최근 3봉 고가 max = 108.5
        assert swing_high(df, lookback=3) == pytest.approx(108.5)


class TestClassify:
    def test_uptrend(self):
        r = classify(_uptrend())
        assert r.regime == Regime.UPTREND
        assert r.aligned_up is True
        assert r.n_bars == 250
        assert r.adx >= 25.0

    def test_downtrend(self):
        r = classify(_downtrend())
        assert r.regime == Regime.DOWNTREND
        assert r.aligned_up is False

    def test_sideways(self):
        r = classify(_sideways())
        assert r.regime == Regime.SIDEWAYS

    def test_unknown_when_too_few_bars(self):
        r = classify(_uptrend(150))  # default min_bars=200
        assert r.regime == Regime.UNKNOWN
        assert np.isnan(r.ema20)
        assert r.n_bars == 150

    def test_unknown_when_ma_nan(self):
        # 봉수 게이트는 통과하지만 sma200 산출 불가(100봉) -> UNKNOWN
        r = classify(_uptrend(100), min_bars=50)
        assert r.regime == Regime.UNKNOWN
        assert np.isnan(r.sma200)
        assert not np.isnan(r.ema20)

    def test_aligned_but_not_trending_is_sideways(self):
        # 정렬 상승이지만 ADX 임계가 비현실적으로 높으면 비추세 -> SIDEWAYS
        r = classify(_uptrend(), adx_trend_threshold=200.0)
        assert r.aligned_up is True
        assert r.regime == Regime.SIDEWAYS

    def test_reading_fields(self):
        df = _uptrend()
        r = classify(df, chandelier_k=3.0, chandelier_lookback=22)
        assert r.close == pytest.approx(float(df["Close"].iloc[-1]))
        assert r.prev_close == pytest.approx(float(df["Close"].iloc[-2]))
        highest_high = float(df["High"].tail(22).max())
        assert r.chandelier_stop == pytest.approx(highest_high - 3.0 * r.atr)

    def test_prev_close_single_bar(self):
        r = classify(_uptrend(1))
        assert r.regime == Regime.UNKNOWN
        assert r.prev_close == r.close


class TestRegimeEnum:
    def test_str(self):
        assert str(Regime.UPTREND) == "uptrend"
        assert str(Regime.SIDEWAYS) == "sideways"
