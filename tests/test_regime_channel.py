# tests/test_regime_channel.py
"""회귀 채널 레짐 분류기(classify_channel) 테스트.

give-me-the-money simulate_trend의 calcLinearRegressionChannel 수식과의
수치 패리티, 기울기 밴드 3분류, 외삽 채널선 값을 검증한다.
"""
import numpy as np
import pandas as pd
import pytest

from src.core.logic.regime import (
    Regime,
    classify_channel,
    linreg_channel,
)


def _ohlc(closes, spread=0.5):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"High": closes + spread, "Low": closes - spread, "Close": closes},
        index=idx,
    )


def _geometric_trend(n, start, daily_pct, noise=None):
    """일 daily_pct% 복리 시계열 (로그 공간에서 완전 선형)."""
    vals = [start * (1 + daily_pct / 100) ** i for i in range(n)]
    if noise is not None:
        rng = np.random.default_rng(42)
        vals = [v * (1 + rng.uniform(-noise, noise) / 100) for v in vals]
    return vals


class TestLinregChannel:
    def test_exact_line_zero_sigma(self):
        # 완전 직선 -> 잔차 0
        y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        m, c, sigma = linreg_channel(y)
        assert m == pytest.approx(1.0)
        assert c == pytest.approx(1.0)
        assert sigma == pytest.approx(0.0, abs=1e-12)

    def test_matches_numpy_polyfit(self):
        rng = np.random.default_rng(7)
        y = 100 + 0.3 * np.arange(60) + rng.normal(0, 2.0, 60)
        m, c, sigma = linreg_channel(y)
        exp_m, exp_c = np.polyfit(np.arange(60), y, 1)
        assert m == pytest.approx(exp_m)
        assert c == pytest.approx(exp_c)
        # 소스와 동일: 모집단 표준편차 (n으로 나눔)
        resid = y - (m * np.arange(60) + c)
        assert sigma == pytest.approx(np.sqrt((resid**2).sum() / 60))

    def test_single_point_no_crash(self):
        m, c, sigma = linreg_channel(np.array([5.0]))
        # 분모 0 -> 소스와 동일하게 1로 대체 -> m=0, c=y
        assert m == pytest.approx(0.0)
        assert c == pytest.approx(5.0)
        assert sigma == pytest.approx(0.0)

    def test_empty_no_crash(self):
        assert linreg_channel(np.array([])) == (0.0, 0.0, 0.0)


class TestClassifyChannel:
    def test_uptrend_when_slope_above_band(self):
        # 일 +0.2% 복리 -> 63봉간 약 +13.2% > 밴드 5%
        df = _ohlc(_geometric_trend(63, 100.0, 0.2))
        r = classify_channel(df, lookback=63, slope_band_pct=5.0)
        assert r.regime == Regime.UPTREND
        assert r.channel_slope_pct == pytest.approx(
            ((1.002**62) - 1) * 100, rel=1e-6
        )

    def test_downtrend_when_slope_below_band(self):
        df = _ohlc(_geometric_trend(63, 100.0, -0.2))
        r = classify_channel(df, lookback=63, slope_band_pct=5.0)
        assert r.regime == Regime.DOWNTREND
        assert r.channel_slope_pct < -5.0

    def test_sideways_within_band(self):
        # 일 +0.05% -> 63봉간 약 +3.1% < 밴드 5%
        df = _ohlc(_geometric_trend(63, 100.0, 0.05))
        r = classify_channel(df, lookback=63, slope_band_pct=5.0)
        assert r.regime == Regime.SIDEWAYS

    def test_band_is_inclusive_boundary_sideways(self):
        # 평탄 시계열은 미세 밴드에서도 횡보 (밴드 '초과'일 때만 상승/하락)
        df = _ohlc(_geometric_trend(63, 100.0, 0.0))
        r = classify_channel(df, lookback=63, slope_band_pct=0.001)
        assert r.regime == Regime.SIDEWAYS
        assert r.channel_slope_pct == pytest.approx(0.0, abs=1e-9)

    def test_channel_lines_extrapolated_to_today(self):
        # 잡음 없는 복리 직선: sigma=0 -> support == mid == resistance
        # == 다음 봉(오늘) 예상 가격
        daily = 0.1
        df = _ohlc(_geometric_trend(63, 100.0, daily))
        r = classify_channel(df, lookback=63, stddev_k=2.0)
        expected_today = 100.0 * (1 + daily / 100) ** 63
        assert r.channel_mid == pytest.approx(expected_today, rel=1e-9)
        assert r.channel_support == pytest.approx(expected_today, rel=1e-9)
        assert r.channel_resistance == pytest.approx(expected_today, rel=1e-9)

    def test_channel_width_scales_with_k(self):
        rng = np.random.default_rng(1)
        closes = [100 * np.exp(0.001 * i + rng.normal(0, 0.02)) for i in range(63)]
        df = _ohlc(closes)
        r1 = classify_channel(df, lookback=63, stddev_k=1.0)
        r2 = classify_channel(df, lookback=63, stddev_k=2.0)
        # 로그 공간 대칭 채널: resistance/mid == mid/support
        assert r1.channel_resistance / r1.channel_mid == pytest.approx(
            r1.channel_mid / r1.channel_support, rel=1e-9
        )
        # k=2 채널이 k=1보다 넓다
        assert (r2.channel_resistance - r2.channel_support) > (
            r1.channel_resistance - r1.channel_support
        )
        # 폭 비율: exp(2s)-exp(-2s) 구조 확인 (로그폭 2배)
        log_w1 = np.log(r1.channel_resistance / r1.channel_support)
        log_w2 = np.log(r2.channel_resistance / r2.channel_support)
        assert log_w2 == pytest.approx(2 * log_w1, rel=1e-9)

    def test_insufficient_bars_unknown(self):
        df = _ohlc(_geometric_trend(50, 100.0, 0.2))
        r = classify_channel(df, lookback=63)
        assert r.regime == Regime.UNKNOWN
        assert np.isnan(r.channel_support)

    def test_lookback_uses_tail_only(self):
        # 앞 100봉 급락 + 뒤 63봉 상승: tail(63)만 봐야 UPTREND
        closes = _geometric_trend(100, 300.0, -1.0) + _geometric_trend(63, 100.0, 0.3)
        df = _ohlc(closes)
        r = classify_channel(df, lookback=63, slope_band_pct=5.0)
        assert r.regime == Regime.UPTREND

    def test_non_positive_price_unknown(self):
        closes = _geometric_trend(63, 100.0, 0.1)
        closes[10] = 0.0  # 로그 불가
        r = classify_channel(_ohlc(closes), lookback=63)
        assert r.regime == Regime.UNKNOWN

    def test_nan_price_unknown(self):
        closes = _geometric_trend(63, 100.0, 0.1)
        closes[5] = float("nan")
        r = classify_channel(_ohlc(closes), lookback=63)
        assert r.regime == Regime.UNKNOWN

    def test_auxiliary_indicators_filled(self):
        df = _ohlc(_geometric_trend(63, 100.0, 0.2))
        r = classify_channel(df, lookback=63)
        # 상승 레짐 로직이 소비하는 지표들이 채워져 있어야 한다
        assert np.isfinite(r.ema20)
        assert np.isfinite(r.atr)
        assert np.isfinite(r.chandelier_stop)
        assert np.isfinite(r.swing_high)
        assert np.isfinite(r.sma50)  # 63봉 >= 50
        # 채널 분류에서 미사용 -> NaN
        assert np.isnan(r.sma200)
        assert np.isnan(r.adx)

    def test_sma50_nan_when_short_history(self):
        df = _ohlc(_geometric_trend(40, 100.0, 0.2))
        r = classify_channel(df, lookback=30)
        assert r.regime != Regime.UNKNOWN
        assert np.isnan(r.sma50)

    def test_ma_adx_reading_has_nan_channel_fields(self):
        # 기존 classify 결과에는 channel 필드가 NaN (하위 호환)
        from src.core.logic.regime import classify

        df = _ohlc(_geometric_trend(250, 100.0, 0.2))
        r = classify(df)
        assert np.isnan(r.channel_slope_pct)
        assert np.isnan(r.channel_support)
