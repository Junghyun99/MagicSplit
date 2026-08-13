"""장·단기 3층 레짐 정책의 핵심 회귀 테스트."""
import numpy as np
import pandas as pd

from src.core.logic.regime import Regime
from src.core.logic.split_evaluator import SplitEvaluator, classify_for_rule
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


def test_aligned_downtrend_reentry_lock_requires_short_resistance_recovery():
    prefix = _trend(189, 120, -0.05)
    window = _window(prefix + _trend(63, prefix[-1], 0.25))
    rule = _rule()
    short = classify_for_rule(rule, window)
    st = {"AAPL": {"aligned_downtrend_reentry_lock": True}}
    SplitEvaluator().evaluate_stock(
        rule, [], _portfolio(short.channel_resistance + 1), ohlc_window=window, regime_state=st,
    )
    assert "aligned_downtrend_reentry_lock" not in st["AAPL"]


def test_relaxed_sell_multiplier_does_not_change_active_trailing_lot():
    evaluator = SplitEvaluator()
    evaluator._active_sell_multiplier = 1.5
    active_lot = _lot()
    active_lot.trailing_highest_price = 120
    assert evaluator._effective_sell_threshold(_rule(), active_lot) == 10
