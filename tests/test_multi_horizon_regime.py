"""장·단기 3층 레짐 정책의 핵심 회귀 테스트."""
import numpy as np
import pandas as pd
from types import SimpleNamespace

from src.core.logic.regime import Regime
from src.core.logic.split_evaluator import SplitEvaluator, classify_for_rule
from src.core.logic.chart_builder import build_chart_series
from src.core.models import OrderAction, Portfolio, PositionLot, StockRule


def _window(closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"High": closes + 1, "Low": closes - 1, "Close": closes},
        index=pd.date_range("2024-01-01", periods=len(closes), freq="D"),
    )


def _trend(n, start, daily_pct):
    return [start * (1 + daily_pct / 100) ** i for i in range(n)]


def _rule(**overrides):
    values = dict(
        ticker="AAPL", buy_threshold_pct=-5, sell_threshold_pct=10, buy_amount=100,
        max_lots=10, max_exposure_pct=20, regime_enabled=True, regime_algo="channel",
        multi_horizon_regime_enabled=True, long_channel_lookback=252,
    )
    values.update(overrides)
    return StockRule(**values)


def _portfolio(price, qty=0, cash=1000):
    return Portfolio(total_cash=cash, holdings={"AAPL": qty}, current_prices={"AAPL": price})


def _lot(price=100, qty=5):
    return PositionLot("lot-1", "AAPL", price, qty, "2024-01-01", level=1)


def test_long_up_short_sideways_uses_relaxed_sell_threshold():
    # 장기 상승(+), 최근 63봉 횡보(→): +12%는 기본 +10%보다 크지만 1.5배 목표에는 미달.
    closes = _trend(189, 100, 0.35)
    closes += [closes[-1] * (1 + (i % 2) * 0.002) for i in range(63)]
    window = _window(closes)
    rule = _rule()
    evaluator = SplitEvaluator()
    st = {}
    signals = evaluator.evaluate_stock(rule, [_lot(100)], _portfolio(112, qty=5), ohlc_window=window, regime_state=st)
    assert not [s for s in signals if s.action == OrderAction.SELL]
    assert st["AAPL"]["long_trend"] == str(Regime.UPTREND)
    assert st["AAPL"]["short_trend"] == str(Regime.SIDEWAYS)


def test_long_downtrend_locks_initial_buy():
    window = _window(_trend(252, 200, -0.25))
    rule = _rule()
    signals = SplitEvaluator().evaluate_stock(rule, [], _portfolio(100), ohlc_window=window, regime_state={})
    assert len(signals) == 1
    assert signals[0].is_blocked
    assert "장기 하락" in signals[0].reason


def test_aligned_downtrend_liquidates_all_remaining_lots_after_confirmation():
    window = _window(_trend(252, 200, -0.25))
    rule = _rule()
    evaluator = SplitEvaluator()
    st = {}
    signals = evaluator.evaluate_stock(rule, [_lot()], _portfolio(100, qty=5), ohlc_window=window, regime_state=st)
    assert len(signals) == 1
    assert signals[0].action == OrderAction.SELL
    assert signals[0].regime_liquidation
    assert signals[0].quantity == 5
    assert st["AAPL"]["aligned_downtrend_reentry_lock"] is True


def test_long_sideways_reduces_effective_exposure_limit():
    # 최근 상승이지만 252봉 전체는 횡보: 초기 매수 뒤 비중 10%가 7% 한도를 넘는다.
    prefix = _trend(189, 120, -0.05)
    closes = prefix + _trend(63, prefix[-1], 0.25)
    window = _window(closes)
    rule = _rule(buy_amount=200, max_exposure_pct=10)
    signals = SplitEvaluator().evaluate_stock(rule, [], _portfolio(window.Close.iloc[-1], cash=1000), ohlc_window=window, regime_state={})
    assert len(signals) == 1
    assert signals[0].is_blocked
    assert "상한 7.0%" in signals[0].reason


def test_long_sideways_short_up_blocks_existing_position_adds():
    prefix = _trend(189, 120, -0.05)
    window = _window(prefix + _trend(63, prefix[-1], 0.25))
    rule = _rule(buy_amount=200, max_exposure_pct=100)
    signals = SplitEvaluator().evaluate_stock(
        rule, [_lot(price=130, qty=1)], _portfolio(window.Close.iloc[-1], qty=1),
        ohlc_window=window, regime_state={},
    )
    assert signals == []


def test_long_downtrend_lock_releases_only_after_long_non_downtrend_and_short_uptrend():
    prefix = _trend(189, 120, -0.05)
    window = _window(prefix + _trend(63, prefix[-1], 0.25))
    rule = _rule()
    st = {"AAPL": {"long_downtrend_lock": True}}
    SplitEvaluator().evaluate_stock(rule, [], _portfolio(window.Close.iloc[-1]), ohlc_window=window, regime_state=st)
    assert "long_downtrend_lock" not in st["AAPL"]


def test_full_liquidation_reentry_requires_short_midline_recovery():
    prefix = _trend(189, 120, -0.05)
    window = _window(prefix + _trend(63, prefix[-1], 0.25))
    rule = _rule(buy_amount=500, max_exposure_pct=100)
    short = classify_for_rule(rule, window)
    st = {"AAPL": {"post_liquidation": True, "aligned_downtrend_reentry_lock": True}}
    blocked = SplitEvaluator().evaluate_stock(
        rule, [], _portfolio(short.channel_mid), ohlc_window=window, regime_state=st,
    )
    assert blocked[0].is_blocked
    assert "채널 중심선 회복" in blocked[0].reason

    allowed = SplitEvaluator().evaluate_stock(
        rule, [], _portfolio(short.channel_mid + 1), ohlc_window=window, regime_state=st,
    )
    assert not allowed[0].is_blocked
    assert "post_liquidation" not in st["AAPL"]
    assert "aligned_downtrend_reentry_lock" not in st["AAPL"]


def test_full_liquidation_reentry_waits_when_long_history_is_insufficient():
    window = _window(_trend(63, 100, 0.25))
    rule = _rule()
    st = {"AAPL": {"post_liquidation": True}}
    signals = SplitEvaluator().evaluate_stock(
        rule, [], _portfolio(window.Close.iloc[-1]), ohlc_window=window, regime_state=st,
    )
    assert signals[0].is_blocked
    assert "장기 추세 데이터 부족" in signals[0].reason


def test_relaxed_sell_multiplier_does_not_change_active_trailing_lot():
    evaluator = SplitEvaluator()
    evaluator._active_sell_multiplier = 1.5
    active_lot = _lot()
    active_lot.trailing_highest_price = 120
    assert evaluator._effective_sell_threshold(_rule(), active_lot) == 10


def test_disabled_mode_preserves_existing_chart_contract_and_trading_path():
    window = _window(_trend(63, 100, 0.25))
    rule = _rule(multi_horizon_regime_enabled=False, long_channel_lookback=21)
    evaluator = SplitEvaluator()
    signals = evaluator.evaluate_stock(
        rule, [_lot(price=130, qty=1)], _portfolio(window.Close.iloc[-1], qty=1),
        ohlc_window=window, regime_state={},
    )
    assert not [s for s in signals if s.is_blocked and "장기" in s.reason]
    chart = build_chart_series(rule, window, lambda d: classify_for_rule(rule, d), [], window.Close.iloc[-1])
    assert "multi_horizon_regime_enabled" not in chart["params"]
    assert "long_trend" not in chart["state"]


def test_trend_only_initial_entry_requires_two_aligned_uptrend_days():
    window = _window(_trend(252, 100, 0.25))
    price = window.Close.iloc[-1]
    rule = _rule(
        trend_only_enabled=True, buy_amount=1000, max_exposure_pct=100,
    )
    evaluator = SplitEvaluator()
    state = {}

    first = evaluator.evaluate_stock(
        rule, [], _portfolio(price, cash=10000), ohlc_window=window,
        regime_state=state, evaluation_date="2025-01-02",
    )
    second = evaluator.evaluate_stock(
        rule, [], _portfolio(price, cash=10000), ohlc_window=window,
        regime_state=state, evaluation_date="2025-01-03",
    )

    assert first[0].is_blocked
    assert "장·단기 상승 정렬" in first[0].reason
    assert second[0].action == OrderAction.BUY
    assert not second[0].is_blocked


def test_trend_only_blocks_long_sideways_short_uptrend_initial_entry():
    prefix = _trend(189, 120, -0.05)
    window = _window(prefix + _trend(63, prefix[-1], 0.25))
    rule = _rule(trend_only_enabled=True, buy_amount=1000, max_exposure_pct=100)
    state = {}
    evaluator = SplitEvaluator()

    for day in ("2025-01-02", "2025-01-03"):
        signals = evaluator.evaluate_stock(
            rule, [], _portfolio(window.Close.iloc[-1], cash=10000),
            ohlc_window=window, regime_state=state, evaluation_date=day,
        )

    assert signals[0].is_blocked
    assert "장·단기 상승 정렬" in signals[0].reason


def test_trend_only_blocks_long_uptrend_short_sideways_initial_entry():
    closes = _trend(189, 100, 0.35)
    closes += [closes[-1] * (1 + (i % 2) * 0.002) for i in range(63)]
    window = _window(closes)
    rule = _rule(trend_only_enabled=True, buy_amount=1000, max_exposure_pct=100)
    signals = SplitEvaluator().evaluate_stock(
        rule, [], _portfolio(window.Close.iloc[-1], cash=10000),
        ohlc_window=window, regime_state={}, evaluation_date="2025-01-02",
    )
    assert signals[0].is_blocked
    assert "장·단기 상승 정렬" in signals[0].reason


def test_trend_only_blocks_initial_entry_when_long_history_is_missing():
    window = _window(_trend(63, 100, 0.25))
    rule = _rule(trend_only_enabled=True, buy_amount=1000, max_exposure_pct=100)
    signals = SplitEvaluator().evaluate_stock(
        rule, [], _portfolio(window.Close.iloc[-1], cash=10000),
        ohlc_window=window, regime_state={}, evaluation_date="2025-01-02",
    )
    assert signals[0].is_blocked
    assert signals[0].reason == "추세 데이터 부족"


def test_trend_only_holds_existing_position_during_sideways_regime():
    closes = [100 + (i % 2) * 0.2 for i in range(252)]
    window = _window(closes)
    current = window.Close.iloc[-1]
    rule = _rule(
        trend_only_enabled=True, buy_amount=1000, max_exposure_pct=100,
        trailing_drop_pct=5,
    )
    signals = SplitEvaluator().evaluate_stock(
        rule, [_lot(price=50, qty=1)], _portfolio(current, qty=1, cash=10000),
        ohlc_window=window, regime_state={}, evaluation_date="2025-01-02",
    )
    assert signals == []


def test_trend_only_holds_existing_position_when_regime_data_is_missing():
    rule = _rule(trend_only_enabled=True)
    signals = SplitEvaluator().evaluate_stock(
        rule, [_lot(price=50, qty=1)], _portfolio(100, qty=1, cash=10000),
        ohlc_window=None, regime_state={}, evaluation_date="2025-01-02",
    )
    assert signals == []


def test_trend_only_chart_exposes_enabled_parameter():
    window = _window(_trend(252, 100, 0.25))
    rule = _rule(trend_only_enabled=True)
    chart = build_chart_series(
        rule, window, lambda d: classify_for_rule(rule, d), [],
        window.Close.iloc[-1],
    )
    assert chart["params"]["trend_only_enabled"] is True


def test_uptrend_sideways_transition_sells_half_only_on_second_distinct_day():
    closes = _trend(189, 100, 0.35)
    closes += [closes[-1] * (1 + (i % 2) * 0.002) for i in range(63)]
    window = _window(closes)
    price = window.Close.iloc[-1]
    rule = _rule(
        uptrend_sideways_transition_partial_sell_pct=50,
        uptrend_sideways_transition_confirm_bars=2,
    )
    state = {"AAPL": {
        "previous_long_regime": str(Regime.UPTREND),
        "previous_short_regime": str(Regime.UPTREND),
    }}
    evaluator = SplitEvaluator()
    lots = [_lot(price=price, qty=5)]

    first = evaluator.evaluate_stock(
        rule, lots, _portfolio(price, qty=5), ohlc_window=window,
        regime_state=state, evaluation_date="2025-01-02",
    )
    duplicate = evaluator.evaluate_stock(
        rule, lots, _portfolio(price, qty=5), ohlc_window=window,
        regime_state=state, evaluation_date="2025-01-02",
    )
    second = evaluator.evaluate_stock(
        rule, lots, _portfolio(price, qty=5), ohlc_window=window,
        regime_state=state, evaluation_date="2025-01-03",
    )

    assert first == []
    assert duplicate == []
    assert len(second) == 1
    assert second[0].quantity == 3
    assert second[0].transition_partial_liquidation is True
    assert second[0].exit_trigger == "uptrend_sideways_transition"
    assert second[0].exit_long_regime == "uptrend"
    assert second[0].exit_short_regime == "sideways"


def test_uptrend_sideways_transition_does_not_start_without_observed_up_up_pair():
    closes = _trend(189, 100, 0.35)
    closes += [closes[-1] * (1 + (i % 2) * 0.002) for i in range(63)]
    window = _window(closes)
    price = window.Close.iloc[-1]
    rule = _rule(uptrend_sideways_transition_partial_sell_pct=50)
    state = {}
    evaluator = SplitEvaluator()

    for day in ("2025-01-02", "2025-01-03", "2025-01-06"):
        signals = evaluator.evaluate_stock(
            rule, [_lot(price=price, qty=5)], _portfolio(price, qty=5),
            ohlc_window=window, regime_state=state, evaluation_date=day,
        )

    assert not [s for s in signals if s.transition_partial_liquidation]


def test_transition_chart_exposes_policy_parameters():
    window = _window(_trend(252, 100, 0.25))
    rule = _rule(
        uptrend_sideways_transition_partial_sell_pct=50,
        uptrend_sideways_transition_confirm_bars=2,
    )
    chart = build_chart_series(
        rule, window, lambda d: classify_for_rule(rule, d), [],
        window.Close.iloc[-1],
    )
    assert chart["params"]["uptrend_sideways_transition_partial_sell_pct"] == 50
    assert chart["params"]["uptrend_sideways_transition_confirm_bars"] == 2


def test_rebound_entry_requires_observed_transition_and_two_distinct_days():
    rule = _rule(trend_only_enabled=True, trend_entry_mode="rebound")
    evaluator = SplitEvaluator()
    reading = SimpleNamespace(channel_mid=100.0)
    state = {}
    transition = {
        "long": Regime.UPTREND, "short": Regime.UPTREND,
        "previous_long": "uptrend", "previous_short": "sideways",
    }
    assert not evaluator._resolve_rebound_entry(
        rule, reading, state, transition, 101.0, "2025-01-02",
    )
    assert evaluator._resolve_rebound_entry(
        rule, reading, state, transition, 101.0, "2025-01-03",
    )

    startup = {}
    already_up = {**transition, "previous_short": "uptrend"}
    assert not evaluator._resolve_rebound_entry(
        rule, reading, startup, already_up, 101.0, "2025-01-02",
    )


def test_rebound_entry_integrates_with_full_stock_evaluation():
    sideways_closes = _trend(189, 100, 0.35)
    sideways_closes += [sideways_closes[-1] * (1 + (i % 2) * 0.002) for i in range(63)]
    sideways = _window(sideways_closes)
    uptrend = _window(_trend(252, 100, 0.25))
    rule = _rule(
        trend_only_enabled=True, trend_entry_mode="rebound",
        buy_amount=1000, max_exposure_pct=100,
    )
    state = {}
    evaluator = SplitEvaluator()
    uptrend_price = classify_for_rule(rule, uptrend).channel_mid + 1

    waiting = evaluator.evaluate_stock(
        rule, [], _portfolio(sideways.Close.iloc[-1], cash=10000),
        ohlc_window=sideways, regime_state=state, evaluation_date="2025-01-02",
    )
    first = evaluator.evaluate_stock(
        rule, [], _portfolio(uptrend_price, cash=10000),
        ohlc_window=uptrend, regime_state=state, evaluation_date="2025-01-03",
    )
    second = evaluator.evaluate_stock(
        rule, [], _portfolio(uptrend_price, cash=10000),
        ohlc_window=uptrend, regime_state=state, evaluation_date="2025-01-06",
    )

    assert waiting[0].is_blocked
    assert first[0].is_blocked
    assert second[0].action == OrderAction.BUY
    assert not second[0].is_blocked, second[0].reason
    assert second[0].entry_trigger == "rebound_initial_entry"


def test_rebound_pullback_add_waits_for_next_day_ema_recovery():
    rule = _rule(
        trend_only_enabled=True, trend_entry_mode="rebound",
        uptrend_add_amount=1000, max_exposure_pct=100,
    )
    evaluator = SplitEvaluator()
    state = {"adds": 0}
    lot = _lot(price=100, qty=5)
    first_reading = SimpleNamespace(ema20=100.0, close=100.0, swing_high=110.0)
    first = evaluator._evaluate_uptrend_add(
        rule, [lot], lot, 99.0, first_reading, state,
        _portfolio(99.0, qty=5, cash=10000), evaluation_date="2025-01-02",
    )
    assert first is None
    assert state["pullback_rebound_armed"] is True

    second_reading = SimpleNamespace(ema20=100.0, close=100.0, swing_high=110.0)
    second = evaluator._evaluate_uptrend_add(
        rule, [lot], lot, 101.0, second_reading, state,
        _portfolio(101.0, qty=5, cash=10000), evaluation_date="2025-01-03",
    )
    assert second is not None
    assert second.entry_trigger == "pullback_rebound_add"


def test_rebound_chart_exposes_entry_policy():
    window = _window(_trend(252, 100, 0.25))
    rule = _rule(trend_only_enabled=True, trend_entry_mode="rebound")
    chart = build_chart_series(
        rule, window, lambda d: classify_for_rule(rule, d), [], window.Close.iloc[-1],
    )
    assert chart["params"]["trend_entry_mode"] == "rebound"
    assert chart["params"]["rebound_entry_confirm_bars"] == 2
