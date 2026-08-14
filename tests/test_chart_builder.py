# tests/test_chart_builder.py
import json

import numpy as np
import pandas as pd
import pytest

from src.core.logic.chart_builder import (
    build_chart_series,
    build_current_channel,
    build_price_lines,
    build_regime_bands,
    build_state_lines,
)
from src.core.logic.regime import Regime, classify_series
from src.core.logic.split_evaluator import classify_for_rule
from src.core.models import PositionLot, StockRule


def _ohlc(closes, spread=0.5):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame(
        {"High": closes + spread, "Low": closes - spread, "Close": closes},
        index=idx,
    )


def _rule(**kwargs):
    base = dict(
        ticker="AAPL", market_type="overseas",
        buy_threshold_pct=-5.0, sell_threshold_pct=10.0, buy_amount=1000.0,
        max_lots=10,
    )
    base.update(kwargs)
    return StockRule(**base)


def _channel_rule(**kwargs):
    return _rule(regime_enabled=True, regime_algo="channel", **kwargs)


class TestClassifySeries:
    def test_returns_one_reading_per_bar_within_limit(self):
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 160, 80))

        out = classify_series(df, lambda d: classify_for_rule(rule, d), max_points=30)

        assert len(out) == 30
        dates = [d for d, _ in out]
        assert dates == sorted(dates)
        assert dates[-1] == df.index[-1].strftime("%Y-%m-%d")

    def test_skips_unknown_readings_when_history_is_short(self):
        # lookback 63봉이 필요한데 전체가 40봉 -> 전부 UNKNOWN -> 결과 없음
        rule = _channel_rule(channel_lookback=63)
        df = _ohlc(np.linspace(100, 120, 40))

        assert classify_series(df, lambda d: classify_for_rule(rule, d)) == []

    def test_last_reading_matches_live_classifier(self):
        """차트 말단 값은 라이브 판정과 동일해야 한다 (단일 진실 원천)."""
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 180, 60))

        _, last = classify_series(df, lambda d: classify_for_rule(rule, d))[-1]
        live = classify_for_rule(rule, df)

        assert last.regime == live.regime
        assert last.channel_support == pytest.approx(live.channel_support)
        assert last.channel_mid == pytest.approx(live.channel_mid)

    def test_empty_frame_returns_empty(self):
        rule = _channel_rule()
        empty = pd.DataFrame({"High": [], "Low": [], "Close": []})
        assert classify_series(empty, lambda d: classify_for_rule(rule, d)) == []


class TestBuildRegimeBands:
    def test_collapses_consecutive_same_regime_into_runs(self):
        class R:
            def __init__(self, regime):
                self.regime = regime

        readings = [
            ("2026-01-01", R(Regime.UPTREND)),
            ("2026-01-02", R(Regime.UPTREND)),
            ("2026-01-03", R(Regime.SIDEWAYS)),
            ("2026-01-04", R(Regime.DOWNTREND)),
            ("2026-01-05", R(Regime.DOWNTREND)),
        ]

        bands = build_regime_bands(readings)

        assert bands == [
            {"from": "2026-01-01", "to": "2026-01-02", "regime": "uptrend"},
            {"from": "2026-01-03", "to": "2026-01-03", "regime": "sideways"},
            {"from": "2026-01-04", "to": "2026-01-05", "regime": "downtrend"},
        ]

    def test_empty_input(self):
        assert build_regime_bands([]) == []


class TestBuildPriceLines:
    def test_no_lots_yields_no_lines(self):
        assert build_price_lines(_rule(), []) == []

    def test_emits_buy_and_sell_target_per_level(self):
        lots = [
            PositionLot("l1", "AAPL", 100.0, 1, "2026-01-01", 1),
            PositionLot("l2", "AAPL", 90.0, 1, "2026-02-01", 2),
        ]

        lines = build_price_lines(_rule(), lots)
        by_label = {l["label"]: l["value"] for l in lines}

        assert by_label["Lv1 매수가"] == 100.0
        assert by_label["Lv1 익절선"] == pytest.approx(110.0)
        assert by_label["Lv2 매수가"] == 90.0
        assert by_label["Lv2 익절선"] == pytest.approx(99.0)
        # 다음 차수 추가매수선은 마지막 차수(Lv2) 기준 -5%
        assert by_label["Lv3 추가매수선"] == pytest.approx(85.5)

    def test_trailing_stop_only_when_activated(self):
        rule = _rule(trailing_drop_pct=5.0)
        idle = PositionLot("l1", "AAPL", 100.0, 1, "2026-01-01", 1)
        active = PositionLot("l2", "AAPL", 100.0, 1, "2026-01-01", 2,
                             trailing_highest_price=120.0)

        labels = {l["label"] for l in build_price_lines(rule, [idle, active])}

        assert "Lv1 트레일링 스톱" not in labels
        assert "Lv2 트레일링 스톱" in labels
        # trailing이 켜지면 익절선은 '활성화선'으로 표기된다
        assert "Lv1 트레일링 활성화선" in labels

    def test_dynamic_reentry_reference_uses_last_sell_price(self):
        lots = [PositionLot("l1", "AAPL", 100.0, 1, "2026-01-01", 1)]

        lines = build_price_lines(_rule(), lots, last_sell_price=120.0)
        by_label = {l["label"]: l["value"] for l in lines}

        # 직전 매도가(120)가 매수가(100)보다 높으므로 그쪽이 기준
        assert by_label["Lv2 추가매수선(동적)"] == pytest.approx(114.0)
        assert "Lv2 추가매수선" not in by_label

    def test_no_add_line_at_max_lots(self):
        rule = _rule(max_lots=2)
        lots = [PositionLot("l2", "AAPL", 100.0, 1, "2026-01-01", 2)]

        labels = {l["label"] for l in build_price_lines(rule, lots)}

        assert not any("추가매수선" in label for label in labels)


class TestBuildStateLines:
    def test_trailing_lock_emits_reference_and_stop(self):
        state = {"trailing_lock": {"lock_price": 100.0, "drop_pct": 3.0}}

        by_label = {l["label"]: l["value"] for l in build_state_lines(_rule(), state)}

        assert by_label["추종 데드라인 기준가"] == 100.0
        assert by_label["추종 데드라인 청산선"] == pytest.approx(97.0)

    def test_no_lock_yields_nothing(self):
        assert build_state_lines(_rule(), {}) == []


class TestBuildChartSeries:
    def test_returns_none_without_data(self):
        rule = _channel_rule()
        assert build_chart_series(
            rule, None, lambda d: classify_for_rule(rule, d), [], 100.0
        ) is None

    def test_channel_mode_emits_channel_columns(self):
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 200, 80))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d),
            [PositionLot("l1", "AAPL", 150.0, 1, "2026-01-01", 1)],
            current_price=195.0, asof="2026-07-31",
        )

        assert chart["cols"] == ["date", "close", "mid", "support", "resistance", "ema20"]
        assert chart["algo"] == "channel"
        assert all(len(row) == len(chart["cols"]) for row in chart["rows"])
        assert chart["regime_bands"]
        assert chart["current_price"] == 195.0

    def test_ma_adx_mode_emits_moving_average_columns(self):
        rule = _rule(regime_enabled=True, regime_algo="ma_adx", regime_min_bars=60)
        df = _ohlc(np.linspace(100, 300, 260))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 300.0
        )

        assert chart["cols"] == ["date", "close", "ema20", "sma50", "sma200", "chandelier"]

    def test_regime_disabled_falls_back_to_close_only(self):
        rule = _rule()
        df = _ohlc(np.linspace(100, 120, 40))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 120.0
        )

        assert chart["cols"] == ["date", "close"]
        assert len(chart["rows"]) == 40
        assert chart["regime_bands"] == []
        assert chart["algo"] == "off"

    def test_short_history_still_produces_close_series(self):
        """레짐은 켜져 있지만 히스토리가 모자란 경우에도 주가는 보여야 한다."""
        rule = _channel_rule(channel_lookback=63)
        df = _ohlc(np.linspace(100, 110, 30))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 110.0
        )

        assert chart["cols"] == ["date", "close"]
        assert len(chart["rows"]) == 30

    def test_current_price_line_is_included(self):
        rule = _rule()
        df = _ohlc(np.linspace(100, 120, 30))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 118.5
        )

        current = [l for l in chart["lines"] if l["kind"] == "current"]
        assert len(current) == 1
        assert current[0]["value"] == 118.5

    def test_state_summary_is_exposed(self):
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 200, 60))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 200.0,
            regime_st={
                "regime": "uptrend", "downtrend": "active", "adds": 2,
                "breakdown_days": ["2026-07-30"],
                "post_liquidation": True,
                "post_liquidation_reentry_gate": "midline",
                "trailing_lock": {"lock_price": 180.0, "drop_pct": 3.0},
            },
        )

        assert chart["state"] == {
            "regime": "uptrend", "downtrend": "active", "trailing_lock": True,
            "post_liquidation": True, "reentry_gate": "midline",
            "adds": 2, "breakdown_count": 1,
        }

    def test_output_is_strict_json_without_nan(self):
        """NaN이 새어 나가면 프런트 JSON.parse가 깨진다."""
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 200, 60))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d),
            [PositionLot("l1", "AAPL", 150.0, 1, "2026-01-01", 1)],
            current_price=195.0,
            regime_st={"trailing_lock": {"lock_price": 180.0, "drop_pct": 3.0}},
        )

        json.dumps(chart, allow_nan=False)  # NaN/Infinity가 있으면 ValueError

    def test_ma_adx_below_min_history_falls_back_to_close_only(self):
        """ma_adx는 200MA가 필요해 히스토리가 짧으면 전부 UNKNOWN이 된다."""
        rule = _rule(regime_enabled=True, regime_algo="ma_adx", regime_min_bars=60)
        df = _ohlc(np.linspace(100, 300, 80))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 300.0
        )

        assert chart["cols"] == ["date", "close"]
        assert chart["regime_bands"] == []

    def test_rounding_keeps_precision_for_small_prices(self):
        rule = _rule()
        df = _ohlc(np.linspace(0.001234567, 0.002, 30), spread=0.00001)

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 0.00199999
        )

        # 저가 코인은 소수 6자리까지 유지되어야 0으로 뭉개지지 않는다
        assert chart["rows"][0][1] > 0
        assert chart["current_price"] == pytest.approx(0.002, abs=1e-6)


class TestBuildCurrentChannel:
    def test_none_when_regime_disabled(self):
        df = _ohlc(np.linspace(100, 200, 80))
        assert build_current_channel(_rule(), df) is None

    def test_none_for_ma_adx_mode(self):
        rule = _rule(regime_enabled=True, regime_algo="ma_adx")
        df = _ohlc(np.linspace(100, 200, 260))
        assert build_current_channel(rule, df) is None

    def test_none_when_history_shorter_than_lookback(self):
        rule = _channel_rule(channel_lookback=63)
        assert build_current_channel(rule, _ohlc(np.linspace(100, 110, 40))) is None

    def test_none_for_missing_window(self):
        assert build_current_channel(_channel_rule(), None) is None

    def test_emits_one_point_per_lookback_bar(self):
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 200, 80))

        ch = build_current_channel(rule, df)

        assert ch["cols"] == ["date", "mid", "support", "resistance"]
        assert len(ch["rows"]) == 21  # asof 미지정 -> 외삽 지점 없음
        assert ch["lookback"] == 21
        # 창의 마지막 봉 날짜와 일치해야 한다
        assert ch["rows"][-1][0] == df.index[-1].strftime("%Y-%m-%d")

    def test_appends_today_extrapolation_when_asof_given(self):
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 200, 80))

        ch = build_current_channel(rule, df, asof="2099-01-01")

        assert len(ch["rows"]) == 22
        assert ch["rows"][-1][0] == "2099-01-01"

    def test_asof_not_after_last_bar_is_not_appended(self):
        """sim_date가 마지막 봉과 같거나 이전이면 중복 지점을 만들지 않는다."""
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 200, 80))
        last = df.index[-1].strftime("%Y-%m-%d")

        assert len(build_current_channel(rule, df, asof=last)["rows"]) == 21

    def test_endpoint_matches_live_judgment_values(self):
        """직선의 끝점 = classify_channel이 오늘 판정에 쓴 값 (단일 진실 원천)."""
        rule = _channel_rule(channel_lookback=21, channel_stddev_k=2.0)
        df = _ohlc(np.linspace(100, 180, 60))

        ch = build_current_channel(rule, df, asof="2099-01-01")
        live = classify_for_rule(rule, df)
        _, mid, support, resistance = ch["rows"][-1]

        # 파일 크기를 줄이려고 소수 2자리로 반올림해 내보내므로 그 폭까지만 허용
        assert mid == pytest.approx(live.channel_mid, abs=0.01)
        assert support == pytest.approx(live.channel_support, abs=0.01)
        assert resistance == pytest.approx(live.channel_resistance, abs=0.01)
        assert ch["slope_pct"] == pytest.approx(live.channel_slope_pct, abs=0.01)

    def test_channel_is_symmetric_in_log_space(self):
        """로그 공간 대칭 -> mid/support 비율과 resistance/mid 비율이 같다."""
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 180, 60))

        for _, mid, support, resistance in build_current_channel(rule, df)["rows"]:
            assert mid / support == pytest.approx(resistance / mid, rel=1e-3)

    def test_straight_in_log_space(self):
        """로그를 취하면 정확한 직선이어야 한다 (가격축의 곡률은 exp 때문).

        롤링 채널선이 잘못 실리면 이 잔차가 눈에 띄게 커진다.
        """
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 180, 60))

        mids = np.log([row[1] for row in build_current_channel(rule, df)["rows"]])
        x = np.arange(len(mids))
        slope, intercept = np.polyfit(x, mids, 1)
        residual = np.abs(mids - (slope * x + intercept)).max()

        # 남는 오차는 출력 반올림(소수 2자리)뿐
        assert residual < 1e-3

    def test_included_in_chart_payload(self):
        rule = _channel_rule(channel_lookback=21)
        df = _ohlc(np.linspace(100, 200, 80))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 195.0,
            asof="2099-01-01",
        )

        assert chart["current_channel"] is not None
        assert len(chart["current_channel"]["rows"]) == 22

    def test_chart_payload_has_null_channel_when_disabled(self):
        rule = _rule()
        df = _ohlc(np.linspace(100, 120, 40))

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 120.0
        )

        assert chart["current_channel"] is None

    def test_multi_horizon_chart_includes_long_channel_only_when_enabled(self):
        df = _ohlc(np.linspace(100, 220, 300))
        rule = _channel_rule(
            channel_lookback=21,
            multi_horizon_regime_enabled=True,
            long_channel_lookback=252,
        )

        chart = build_chart_series(
            rule, df, lambda d: classify_for_rule(rule, d), [], 215.0,
        )

        assert chart["long_current_channel"]["lookback"] == 252
        assert len(chart["long_current_channel"]["rows"]) == 252

    def test_multi_horizon_chart_omits_long_channel_when_disabled_or_short(self):
        df = _ohlc(np.linspace(100, 220, 300))
        disabled = _channel_rule(channel_lookback=21, long_channel_lookback=252)
        enabled_short_history = _channel_rule(
            channel_lookback=21,
            multi_horizon_regime_enabled=True,
            long_channel_lookback=400,
        )

        disabled_chart = build_chart_series(
            disabled, df, lambda d: classify_for_rule(disabled, d), [], 215.0,
        )
        short_chart = build_chart_series(
            enabled_short_history, df,
            lambda d: classify_for_rule(enabled_short_history, d), [], 215.0,
        )

        assert "long_current_channel" not in disabled_chart
        assert "long_current_channel" not in short_chart
