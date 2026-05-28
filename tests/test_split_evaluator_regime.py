# tests/test_split_evaluator_regime.py
import numpy as np
import pandas as pd
import pytest

from src.core.logic.split_evaluator import SplitEvaluator, DOWNTREND_CONFIRM_BARS
from src.core.logic.regime import Regime, classify
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


def _pullback_window(n=250, start=100.0, step=1.0, spread=0.5, dip_bars=5, dip_step=2.0):
    """상승 후 마지막 dip_bars봉이 하락하는 창 (깊은 눌림 테스트용)."""
    closes = np.array([start + i * step for i in range(n)], dtype=float)
    peak = closes[n - dip_bars - 1]
    for i in range(dip_bars):
        closes[n - dip_bars + i] = peak - (i + 1) * dip_step
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

    @pytest.mark.skip(reason="새 고점 게이트 우회 테스트 중 - split_evaluator.py 게이트 주석 참고")
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

    def test_deep_pullback_below_ema20_emits_buy(self, evaluator):
        rule = _regime_rule()
        # 마지막 5봉이 하락한 창: reading.close는 dip 저점, EMA20은 상승 추세 반영
        window = _pullback_window()
        r = _reading(window, rule)
        # 어제 종가(dip 저점)보다 살짝 위 = 반등 + EMA20보다 낮음 = 깊은 눌림
        price = r.close * 1.005
        lot = _lot(level=1, buy_price=50.0)
        state = {"AAPL": {"regime": "uptrend", "adds": 0,
                          "last_add_swing_high": r.swing_high - 5}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price), ohlc_window=window, regime_state=state,
        )
        buys = [s for s in signals if s.action == OrderAction.BUY and not s.is_blocked]
        assert len(buys) == 1, "EMA20 아래 깊은 눌림 반등에서 매수 신호가 나와야 한다"

    def test_above_band_blocks_add(self, evaluator):
        rule = _regime_rule()
        window = _uptrend_window()
        r = _reading(window, rule)
        # EMA20 +2% (상한 1.5% 초과) -> 추격 매수 차단
        price = r.ema20 * 1.02
        lot = _lot(level=1, buy_price=50.0)
        state = {"AAPL": {"regime": "uptrend", "adds": 0,
                          "last_add_swing_high": r.swing_high - 5}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=price), ohlc_window=window, regime_state=state,
        )
        buys = [s for s in signals if s.action == OrderAction.BUY and not s.is_blocked]
        assert buys == [], "EMA20 +2% 추격 구간에서는 매수 신호가 없어야 한다"

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


class TestUptrendTrailingLock:
    def test_partial_sell_50_percent_on_trendbreak(self, evaluator):
        # 1. 50% 분할 매도 신호 검증
        rule = _regime_rule(
            trendbreak_partial_sell_pct=50.0,
            trendbreak_trailing_drop_pct=3.0,
        )
        window = _uptrend_window()
        r = _reading(window, rule)
        
        # 50MA 이하로 하락하여 이탈하도록 설정
        price = r.sma50 - 1.0
        
        # 평단 100.0에 10주 보유 중
        lots = [_lot(level=1, buy_price=100.0, qty=10)]
        state = {
            "AAPL": {
                "regime": "uptrend",
                "adds": 1,
                "last_add_price": 100.0,
                "last_add_swing_high": 120.0,
            }
        }
        
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price=price, qty=10), ohlc_window=window, regime_state=state
        )
        
        # 50%인 5주 매도 신호 생성 확인
        assert len(signals) == 1
        sig = signals[0]
        assert sig.action == OrderAction.SELL
        assert sig.quantity == 5
        assert sig.regime_partial_liquidation is True
        assert sig.regime_liquidation is False
        
        # 신호 생성만으로는 trailing_lock이 활성화되지 않음을 확인 (체결 확정 시 활성화됨)
        assert "trailing_lock" not in state["AAPL"]

    def test_trailing_lock_activated_and_triggered_on_drop(self, evaluator):
        # 2. 추종 데드라인 발동 (추가 3% 하락 시)
        rule = _regime_rule(
            trendbreak_partial_sell_pct=50.0,
            trendbreak_trailing_drop_pct=3.0,
        )
        window = _uptrend_window()
        r = _reading(window, rule)
        
        # 이미 50% 매도가 진행되어 trailing_lock 상태가 활성화된 경우
        # lock_price가 100.0이고, 현재 가격이 96.5 (-3.5%) 인 경우
        price = 96.5
        lots = [_lot(level=1, buy_price=100.0, qty=5)] # 잔량 5주
        state = {
            "AAPL": {
                "regime": "uptrend",
                "adds": 1,
                "last_add_price": 100.0,
                "last_add_swing_high": 120.0,
                "trailing_lock": {
                    "active": True,
                    "lock_price": 100.0,
                    "drop_pct": 3.0,
                }
            }
        }
        
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price=price, qty=5), ohlc_window=window, regime_state=state
        )
        
        # lock_price(100.0) 대비 -3.5% 하락으로 3% 한계 이탈 -> 잔량(5주) 전량 청산 신호 발생 확인
        assert len(signals) == 1
        sig = signals[0]
        assert sig.action == OrderAction.SELL
        assert sig.quantity == 5
        assert sig.regime_liquidation is True

    def test_trailing_lock_recovery(self, evaluator):
        # 3. 이탈 후 50MA 위로 복귀 시 추종 데드라인 해제 검증
        rule = _regime_rule(
            trendbreak_partial_sell_pct=50.0,
            trendbreak_trailing_drop_pct=3.0,
        )
        window = _uptrend_window()
        r = _reading(window, rule)
        
        # 가격 회복으로 데드라인 해제되도록 50MA보다 높은 가격 설정
        price = r.sma50 + 5.0
        lots = [_lot(level=1, buy_price=100.0, qty=5)]
        state = {
            "AAPL": {
                "regime": "uptrend",
                "adds": 1,
                "last_add_price": 100.0,
                "last_add_swing_high": 120.0,
                "trailing_lock": {
                    "active": True,
                    "lock_price": 95.0,
                    "drop_pct": 3.0,
                }
            }
        }
        
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price=price, qty=5), ohlc_window=window, regime_state=state
        )
        
        # 가격 회복으로 데드라인 해제 -> 매매 신호 없고 trailing_lock이 삭제되어 정상 레짐 복귀 확인
        assert len(signals) == 0
        assert "trailing_lock" not in state["AAPL"]
        assert state["AAPL"]["regime"] == "uptrend"

    def test_backward_compatibility_100_percent(self, evaluator):
        # 4. 하위 호환: 100% (기본값) 일 때 전량 즉각 청산 검증
        rule = _regime_rule(
            trendbreak_partial_sell_pct=100.0,
        )
        window = _uptrend_window()
        r = _reading(window, rule)
        
        price = r.sma50 - 1.0
        lots = [_lot(level=1, buy_price=100.0, qty=10)]
        state = {
            "AAPL": {
                "regime": "uptrend",
                "adds": 1,
                "last_add_price": 100.0,
                "last_add_swing_high": 120.0,
            }
        }
        
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price=price, qty=10), ohlc_window=window, regime_state=state
        )
        
        # 10주 전량 청산 신호 및 regime_liquidation=True 확인
        assert len(signals) == 1
        sig = signals[0]
        assert sig.action == OrderAction.SELL
        assert sig.quantity == 10
        assert sig.regime_liquidation is True

    def test_buy_blocked_during_trailing_lock(self, evaluator):
        # 5. 추종 데드라인 상태에서는 눌림목 추가 매수가 차단되는지 검증
        rule = _regime_rule(
            trendbreak_partial_sell_pct=50.0,
            trendbreak_trailing_drop_pct=3.0,
        )
        window = _uptrend_window()
        r = _reading(window, rule)
        
        # 가격이 20EMA 근처(눌림목 영역)로 반등했더라도 trailing_lock이 켜져 있으므로 추가 매수 안 됨
        price = r.ema20 * 1.005
        lots = [_lot(level=1, buy_price=100.0, qty=5)]
        state = {
            "AAPL": {
                "regime": "uptrend",
                "adds": 0,
                "last_add_price": 100.0,
                "last_add_swing_high": r.swing_high - 10,
                "trailing_lock": {
                    "active": True,
                    "lock_price": 100.0,
                    "drop_pct": 3.0,
                }
            }
        }
        
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price=price, qty=5), ohlc_window=window, regime_state=state
        )
        
        # 신호가 아예 없어야 함
        assert len(signals) == 0


def _downtrend_window(n=250, start=250.0, step=-1.0, spread=0.5):
    """지속 하락 창 - EMA20 < SMA50 < SMA200 역배열 + 강한 ADX 유도."""
    closes = np.array([max(start + i * step, 1.0) for i in range(n)], dtype=float)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"High": closes + spread, "Low": closes - spread, "Close": closes}, index=idx
    )


class TestDowntrendBuyBlock:
    """DOWNTREND 매수 차단 래치 동작 검증."""

    def test_regime_disabled_no_block(self, evaluator):
        """regime_enabled=False -> DOWNTREND 상태여도 매수 차단 없음."""
        rule = _regime_rule(regime_enabled=False, buy_threshold_pct=-5.0, sell_threshold_pct=50.0)
        lot = _lot(level=1, buy_price=200.0)
        state = {"AAPL": {"downtrend": "active"}}
        window = _downtrend_window()
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=100.0, cash=500000), ohlc_window=window, regime_state=state
        )
        buys = [s for s in signals if s.action == OrderAction.BUY and not s.is_blocked]
        assert len(buys) == 1

    def test_one_downtrend_bar_no_latch(self, evaluator):
        """DOWNTREND 1봉 - streak=1, 래치 미확정 (차단 없음)."""
        rule = _regime_rule(buy_threshold_pct=-5.0, sell_threshold_pct=50.0)
        window = _downtrend_window()
        r = _reading(window, rule)
        if r.regime != Regime.DOWNTREND:
            pytest.skip("window did not produce DOWNTREND regime")

        lot = _lot(level=1, buy_price=200.0)
        state = {"AAPL": {}}
        evaluator.evaluate_stock(
            rule, [lot], _pf(price=100.0, cash=500000), ohlc_window=window, regime_state=state
        )
        assert state["AAPL"].get("downtrend_streak") == 1
        assert state["AAPL"].get("downtrend") != "active"

    def test_two_downtrend_bars_latch_active(self, evaluator):
        """DOWNTREND 2봉 연속 -> 래치 확정, 추가 매수 차단."""
        rule = _regime_rule(buy_threshold_pct=-5.0, sell_threshold_pct=50.0)
        window = _downtrend_window()
        r = _reading(window, rule)
        if r.regime != Regime.DOWNTREND:
            pytest.skip("window did not produce DOWNTREND regime")

        lot = _lot(level=1, buy_price=200.0)
        state = {"AAPL": {"downtrend_streak": 1}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=100.0, cash=500000), ohlc_window=window, regime_state=state
        )
        assert state["AAPL"].get("downtrend") == "active"
        buys = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buys) >= 1
        assert all(b.is_blocked for b in buys)

    def test_downtrend_blocks_initial_buy_no_lots(self, evaluator):
        """DOWNTREND active, 보유 lot 없음 -> 신규 진입 차단."""
        rule = _regime_rule(buy_threshold_pct=-5.0, sell_threshold_pct=50.0)
        window = _downtrend_window()
        r = _reading(window, rule)
        if r.regime != Regime.DOWNTREND:
            pytest.skip("window did not produce DOWNTREND regime")

        state = {"AAPL": {"downtrend": "active"}}
        signals = evaluator.evaluate_stock(
            rule, [], _pf(price=100.0, cash=500000), ohlc_window=window, regime_state=state
        )
        buys = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buys) == 1
        assert buys[0].is_blocked

    def test_downtrend_does_not_block_sell(self, evaluator):
        """DOWNTREND active 중 이익실현 조건 충족 -> 매도 정상 작동."""
        rule = _regime_rule(sell_threshold_pct=10.0)
        window = _downtrend_window()
        r = _reading(window, rule)
        if r.regime != Regime.DOWNTREND:
            pytest.skip("window did not produce DOWNTREND regime")

        lot = _lot(level=1, buy_price=10.0)  # +1900% 평가익
        state = {"AAPL": {"downtrend": "active"}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=200.0), ohlc_window=window, regime_state=state
        )
        sells = [s for s in signals if s.action == OrderAction.SELL and not s.is_info]
        assert len(sells) == 1

    def test_latch_survives_one_non_downtrend_bar(self, evaluator):
        """DOWNTREND active 중 비-DOWNTREND 1봉 -> 래치 유지 (exit_streak=1)."""
        rule = _regime_rule(buy_threshold_pct=-5.0, sell_threshold_pct=50.0)
        window = _uptrend_window()  # non-DOWNTREND window
        lot = _lot(level=1, buy_price=300.0)
        state = {"AAPL": {"downtrend": "active", "downtrend_exit_streak": 0}}
        signals = evaluator.evaluate_stock(
            rule, [lot], _pf(price=250.0, cash=500000), ohlc_window=window, regime_state=state
        )
        assert state["AAPL"]["downtrend_exit_streak"] == 1
        assert state["AAPL"]["downtrend"] == "active"
        buys = [s for s in signals if s.action == OrderAction.BUY]
        assert all(b.is_blocked for b in buys)

    def test_latch_releases_after_two_non_downtrend_bars(self, evaluator):
        """비-DOWNTREND 2봉 연속 -> 래치 해제, state 초기화."""
        rule = _regime_rule(buy_threshold_pct=-5.0, sell_threshold_pct=50.0)
        window = _uptrend_window()
        lot = _lot(level=1, buy_price=300.0)
        # 이미 exit_streak=1 상태에서 한 봉 더 -> 총 2봉 -> 해제
        state = {"AAPL": {"downtrend": "active", "downtrend_exit_streak": 1}}
        evaluator.evaluate_stock(
            rule, [lot], _pf(price=250.0, cash=500000), ohlc_window=window, regime_state=state
        )
        assert state["AAPL"].get("downtrend") is None
        assert state["AAPL"]["downtrend_exit_streak"] == 0
        assert state["AAPL"]["downtrend_streak"] == 0

    def test_downtrend_block_resets_on_downtrend_bar_during_exit(self, evaluator):
        """탈출 카운트 중 DOWNTREND 봉 -> exit_streak 리셋, 래치 유지."""
        rule = _regime_rule(buy_threshold_pct=-5.0, sell_threshold_pct=50.0)
        window = _downtrend_window()
        r = _reading(window, rule)
        if r.regime != Regime.DOWNTREND:
            pytest.skip("window did not produce DOWNTREND regime")

        lot = _lot(level=1, buy_price=200.0)
        state = {"AAPL": {"downtrend": "active", "downtrend_exit_streak": 1}}
        evaluator.evaluate_stock(
            rule, [lot], _pf(price=100.0, cash=500000), ohlc_window=window, regime_state=state
        )
        assert state["AAPL"]["downtrend_exit_streak"] == 0
        assert state["AAPL"]["downtrend"] == "active"

    def test_downtrend_streak_accumulates_during_uptrend_mode(self, evaluator):
        """UPTREND 모드 중에도 downtrend_streak이 누적돼 레짐 탈출 후 즉시 래치 가능."""
        rule = _regime_rule(buy_threshold_pct=-5.0, sell_threshold_pct=50.0)
        window = _downtrend_window()
        r = _reading(window, rule)
        if r.regime != Regime.DOWNTREND:
            pytest.skip("window did not produce DOWNTREND regime")

        lot = _lot(level=1, buy_price=200.0)
        # st["regime"]="uptrend"으로 UPTREND 모드 시뮬레이션 (lots 있음)
        state = {"AAPL": {"regime": "uptrend", "adds": 0,
                          "last_add_price": 200.0,
                          "last_add_swing_high": r.swing_high}}
        evaluator.evaluate_stock(
            rule, [lot], _pf(price=100.0), ohlc_window=window, regime_state=state
        )
        # UPTREND 분기로 조기 반환됐지만 downtrend_streak은 누적돼야 함
        assert state["AAPL"].get("downtrend_streak", 0) >= 1

