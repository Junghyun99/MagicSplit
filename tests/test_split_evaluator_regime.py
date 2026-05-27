# tests/test_split_evaluator_regime.py
import numpy as np
import pandas as pd
import pytest

from src.core.logic.split_evaluator import SplitEvaluator
from src.core.logic.regime import classify
from src.core.models import StockRule, PositionLot, Portfolio, OrderAction


@pytest.fixture
def evaluator():
    return SplitEvaluator()


def _uptrend_window(n=250, start=100.0, step=1.0, spread=0.5):
    closes = np.array([start + i * step for i in range(n)], dtype=float)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"High": closes + spread, "Low": closes - spread, "Close": closes}, index=idx
    )


def _regime_rule(**over):
    base = dict(
        ticker="AAPL", buy_threshold_pct=-5.0, sell_threshold_pct=10.0,
        buy_amount=500, max_lots=10, regime_enabled=True,
    )
    base.update(over)
    return StockRule(**base)


def _reading(window, rule):
    return classify(
        window,
        adx_trend_threshold=rule.regime_adx_trend,
        adx_range_threshold=rule.regime_adx_range,
        chandelier_k=rule.trendbreak_chandelier_k,
        chandelier_lookback=rule.trendbreak_chandelier_lookback,
        swing_lookback=rule.uptrend_swing_lookback,
        min_bars=rule.regime_min_bars,
    )


def _lot(level=1, buy_price=100.0, qty=5, lot_id=None):
    return PositionLot(
        lot_id=lot_id or f"lot_{level:03d}",
        ticker="AAPL", buy_price=buy_price, quantity=qty,
        buy_date="2024-01-01", level=level,
    )


def _pf(price, cash=100000.0, qty=5):
    return Portfolio(total_cash=cash, holdings={"AAPL": qty}, current_prices={"AAPL": price})


class TestRegimeBranchGating:
    def test_disabled_falls_through_to_normal_harvest(self, evaluator):
        # regime_enabled=False면 윈도우가 있어도 평균회귀 경로 -> 익절 매도 발생
        rule = _regime_rule(regime_enabled=False)
        window = _uptrend_window()
        lot = _lot(level=1, buy_price=10.0)  # 큰 평가익
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=300.0), ohlc_window=window,
            regime_state={"AAPL": {"regime": "uptrend"}},
        )
        assert any(s.action == OrderAction.SELL for s in signals)

    def test_no_window_falls_through(self, evaluator):
        rule = _regime_rule()
        lot = _lot(level=1, buy_price=10.0)
        signals = evaluator.evaluate_stock(rule, [lot], _pf(price=300.0))
        assert any(s.action == OrderAction.SELL for s in signals)


class TestUptrendLockSells:
    def test_sells_locked_in_uptrend(self, evaluator):
        rule = _regime_rule()
        window = _uptrend_window()
        r = _reading(window, rule)
        price = r.ema20 * 1.10  # 밴드 밖(눌림 아님) -> 추가도 없음
        lot = _lot(level=1, buy_price=10.0)  # 평균회귀라면 익절했을 큰 이익
        state = {"AAPL": {"regime": "uptrend", "adds": 0, "last_add_swing_high": r.swing_high}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price), ohlc_window=window, regime_state=state,
        )
        assert all(s.action != OrderAction.SELL for s in signals)


class TestUptrendPullbackAdd:
    def test_pullback_add_emits_buy(self, evaluator):
        rule = _regime_rule()
        window = _uptrend_window()
        r = _reading(window, rule)
        price = r.ema20 * 1.005  # 밴드 내 + 20EMA 위 -> 반등 확인
        lot = _lot(level=1, buy_price=50.0)
        state = {"AAPL": {"regime": "uptrend", "adds": 0,
                          "last_add_swing_high": r.swing_high - 5}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price), ohlc_window=window, regime_state=state,
        )
        buys = [s for s in signals if s.action == OrderAction.BUY and not s.is_blocked]
        assert len(buys) == 1
        assert buys[0].level == 2
        assert buys[0].quantity >= 1
        # 신호에 스윙고점이 실리고(체결 시 엔진이 커밋), 평가 시점엔 adds 미변경
        assert buys[0].regime_add_swing_high == r.swing_high
        assert state["AAPL"]["adds"] == 0

    def test_new_high_gate_blocks_add(self, evaluator):
        rule = _regime_rule()
        window = _uptrend_window()
        r = _reading(window, rule)
        price = r.ema20 * 1.005
        lot = _lot(level=1, buy_price=50.0)
        # 새 고점이 없으면(직전 add 고점 == 현재 스윙고점) 추가 차단
        state = {"AAPL": {"regime": "uptrend", "adds": 0,
                          "last_add_swing_high": r.swing_high}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price), ohlc_window=window, regime_state=state,
        )
        assert signals == []

    def test_add_blocked_by_exposure(self, evaluator):
        rule = _regime_rule(max_exposure_pct=0.001)  # 사실상 어떤 매수도 비중 초과
        window = _uptrend_window()
        r = _reading(window, rule)
        price = r.ema20 * 1.005
        lot = _lot(level=1, buy_price=50.0)
        state = {"AAPL": {"regime": "uptrend", "adds": 0,
                          "last_add_swing_high": r.swing_high - 5}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price), ohlc_window=window, regime_state=state,
        )
        assert len(signals) == 1 and signals[0].is_blocked

    def test_add_blocked_by_cash(self, evaluator):
        rule = _regime_rule(uptrend_add_amount=100000)
        window = _uptrend_window()
        r = _reading(window, rule)
        price = r.ema20 * 1.005
        lot = _lot(level=1, buy_price=50.0)
        state = {"AAPL": {"regime": "uptrend", "adds": 0,
                          "last_add_swing_high": r.swing_high - 5}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price, cash=50.0), ohlc_window=window, regime_state=state,
        )
        assert len(signals) == 1 and signals[0].is_blocked

    def test_add_qty_zero_skips(self, evaluator):
        rule = _regime_rule(uptrend_add_amount=1.0)  # 1주도 못 사는 금액
        window = _uptrend_window()
        r = _reading(window, rule)
        price = r.ema20 * 1.005
        lot = _lot(level=1, buy_price=50.0)
        state = {"AAPL": {"regime": "uptrend", "adds": 0,
                          "last_add_swing_high": r.swing_high - 5}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price), ohlc_window=window, regime_state=state,
        )
        assert signals == []

    def test_max_adds_caps(self, evaluator):
        rule = _regime_rule(uptrend_max_adds=2)
        window = _uptrend_window()
        r = _reading(window, rule)
        price = r.ema20 * 1.005
        lot = _lot(level=1, buy_price=50.0)
        state = {"AAPL": {"regime": "uptrend", "adds": 2,
                          "last_add_swing_high": r.swing_high - 5}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price), ohlc_window=window, regime_state=state,
        )
        assert signals == []


class TestTrendBreakLiquidation:
    def test_full_liquidation_emits_bulk_sell(self, evaluator):
        rule = _regime_rule()
        window = _uptrend_window()
        r = _reading(window, rule)
        price = r.sma50 * 0.9  # 50MA 하향 이탈 -> 전량 청산
        lots = [
            _lot(level=1, buy_price=50.0, qty=5, lot_id="lotA"),
            _lot(level=2, buy_price=60.0, qty=5, lot_id="lotB"),
            _lot(level=3, buy_price=70.0, qty=5, lot_id="lotC"),
        ]
        state = {"AAPL": {"regime": "uptrend", "adds": 2, "last_add_swing_high": 999}}
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price=price, qty=15), ohlc_window=window, regime_state=state,
        )
        # 통합 매도 1건: 총 보유 수량, lot_id 없음, 청산 표식
        assert len(signals) == 1
        s = signals[0]
        assert s.action == OrderAction.SELL
        assert s.lot_id is None
        assert s.quantity == 15  # 5+5+5
        assert s.regime_liquidation is True
        # 리셋은 체결 시 엔진이 수행 -> 평가 시점엔 상승 유지
        assert state["AAPL"]["regime"] == "uptrend"


class TestNanGuard:
    def test_nan_indicator_holds_and_warns(self):
        from unittest.mock import MagicMock
        logger = MagicMock()
        ev = SplitEvaluator(logger=logger)
        rule = _regime_rule()
        window = _uptrend_window(50)  # < min_bars(200) -> UNKNOWN -> sma50 NaN
        lot = _lot(level=1, buy_price=50.0)
        # 이미 상승 래치 상태라 _resolve_regime은 UPTREND 유지 -> _evaluate_uptrend 진입
        state = {"AAPL": {"regime": "uptrend", "adds": 0, "last_add_swing_high": 0.0}}
        signals = ev.evaluate_stock(
            rule, [lot], _pf(price=100.0), ohlc_window=window, regime_state=state,
        )
        # 지표 NaN -> 이탈 판단 불가 -> 평가 보류(빈 신호) + 경고 로그
        assert signals == []
        assert logger.warning.called


class TestResolveRegimeHysteresis:
    def test_requires_confirm_bars_to_enter_uptrend(self, evaluator):
        rule = _regime_rule()
        window = _uptrend_window()
        r = _reading(window, rule)
        lot = _lot(level=1, buy_price=50.0)
        state = {}
        # 1회차: 아직 확정 전 -> 상승 미진입 (state에 uptrend 미기록)
        evaluator.evaluate_stock(
            rule, [lot], _pf(price=r.ema20 * 1.005), ohlc_window=window, regime_state=state,
        )
        assert state["AAPL"].get("regime") != "uptrend"
        assert state["AAPL"]["uptrend_streak"] == 1
        # 2회차: 연속 확정 -> 상승 진입
        evaluator.evaluate_stock(
            rule, [lot], _pf(price=r.ema20 * 1.005), ohlc_window=window, regime_state=state,
        )
        assert state["AAPL"]["regime"] == "uptrend"


class TestUptrendAddReset:
    def test_adds_reset_on_price_levelup(self, evaluator):
        rule = _regime_rule(uptrend_max_adds=3, uptrend_add_reset_pct=20.0)
        window = _uptrend_window()
        r = _reading(window, rule)
        lot = _lot(level=1, buy_price=100.0)

        # 1. adds가 3회 꽉 차있고, last_add_price가 100.0인 상태
        state = {
            "AAPL": {
                "regime": "uptrend",
                "adds": 3,
                "last_add_price": 100.0,
                "last_add_swing_high": r.swing_high - 10
            }
        }

        # 주가가 110.0 (+10%) 으로 상승 -> 아직 20% 상승 기준에 미달하여 리셋되지 않음
        evaluator.evaluate_stock(
            rule, [lot], _pf(price=110.0), ohlc_window=window, regime_state=state,
        )
        assert state["AAPL"]["adds"] == 3

        # 주가가 120.0 (+20%) 으로 상승 -> 리셋 트리거 발동해야 함
        evaluator.evaluate_stock(
            rule, [lot], _pf(price=120.0), ohlc_window=window, regime_state=state,
        )
        assert state["AAPL"]["adds"] == 0
        assert state["AAPL"]["last_add_price"] == 120.0
        assert state["AAPL"]["last_add_swing_high"] is None  # [리뷰 1 반영] 게이트 열림 확인!

    def test_adds_reset_fallback_from_lots(self, evaluator):
        rule = _regime_rule(uptrend_max_adds=3, uptrend_add_reset_pct=20.0)
        window = _uptrend_window()
        r = _reading(window, rule)
        # 2차수 최고 차수 매수가 80.0
        lots = [
            _lot(level=1, buy_price=50.0),
            _lot(level=2, buy_price=80.0),
        ]

        # 2. st에 last_add_price가 없는 레거시 상태 (adds = 3)
        state = {
            "AAPL": {
                "regime": "uptrend",
                "adds": 3,
                "last_add_swing_high": r.swing_high - 10
            }
        }

        # 주가가 96.0 (+20% of 80.0) 으로 상승 -> 리셋 트리거 발동 (last_add_price 폴백 복구 후 20% 상승 판단)
        evaluator.evaluate_stock(
            rule, lots, _pf(price=96.0, qty=10), ohlc_window=window, regime_state=state,
        )
        assert state["AAPL"]["adds"] == 0
        assert state["AAPL"]["last_add_price"] == 96.0
        assert state["AAPL"]["last_add_swing_high"] is None

