# tests/test_split_evaluator_channel.py
"""채널 레짐 모드(regime_algo="channel")의 SplitEvaluator 통합 테스트.

이탈 판정 = (하락 래치 확정) OR (상승/횡보 중 하단 채널선 하향 돌파).
청산 방식은 trendbreak_partial_sell_pct(50=절반+추종 데드라인, 100=전량)를 따른다.
"""
import numpy as np
import pandas as pd
import pytest

from src.core.logic.split_evaluator import (
    BREAKDOWN_CONFIRM_BARS,
    SplitEvaluator,
    classify_for_rule,
)
from src.core.logic.regime import Regime
from src.core.models import StockRule, PositionLot, Portfolio, OrderAction


@pytest.fixture
def evaluator():
    return SplitEvaluator()


def _window(closes, spread=0.5):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"High": closes + spread, "Low": closes - spread, "Close": closes}, index=idx
    )


def _geo(n, start, daily_pct):
    return [start * (1 + daily_pct / 100) ** i for i in range(n)]


def _uptrend_window(n=63, start=100.0, daily_pct=0.3):
    """63봉간 약 +20% -> 기울기 밴드(5%) 상향 돌파."""
    return _window(_geo(n, start, daily_pct))


def _downtrend_window(n=63, start=100.0, daily_pct=-0.3):
    return _window(_geo(n, start, daily_pct))


def _sideways_window(n=63, base=100.0, wobble=2.0):
    """지그재그 횡보: 기울기 ~0, 잔차 표준편차로 채널 폭 확보 (support ~ 99)."""
    return _window([base + (i % 2) * wobble for i in range(n)])


def _channel_rule(**over):
    base = dict(
        ticker="AAPL", buy_threshold_pct=-5.0, sell_threshold_pct=10.0,
        buy_amount=500, max_lots=10,
        regime_enabled=True, regime_algo="channel",
    )
    base.update(over)
    return StockRule(**base)


def _lot(level=1, buy_price=100.0, qty=5, lot_id=None):
    return PositionLot(
        lot_id=lot_id or f"lot_{level:03d}",
        ticker="AAPL", buy_price=buy_price, quantity=qty,
        buy_date="2024-01-01", level=level,
    )


def _pf(price, cash=100000.0, qty=5):
    return Portfolio(total_cash=cash, holdings={"AAPL": qty}, current_prices={"AAPL": price})


def _support(rule, window):
    return classify_for_rule(rule, window).channel_support


def _eval_until_confirmed(evaluator, rule, lots, pf, window, st):
    """이탈 확정 봉수만큼 반복 평가해 마지막 신호를 반환한다."""
    signals = []
    for _ in range(BREAKDOWN_CONFIRM_BARS):
        signals = evaluator.evaluate_stock(
            rule, lots, pf, ohlc_window=window, regime_state=st,
        )
    return signals


class TestChannelClassifierDispatch:
    def test_classify_for_rule_selects_channel(self):
        rule = _channel_rule()
        r = classify_for_rule(rule, _uptrend_window())
        assert r.regime == Regime.UPTREND
        assert np.isfinite(r.channel_support)
        assert np.isnan(r.adx)  # 채널 분류기는 ADX 미계산

    def test_classify_for_rule_selects_ma_adx(self):
        rule = _channel_rule(regime_algo="ma_adx")
        r = classify_for_rule(rule, _uptrend_window(n=250))
        assert np.isnan(r.channel_support)  # ma_adx는 채널 미계산


class TestChannelSidewaysBreakdown:
    def test_no_breakdown_falls_through_to_harvest(self, evaluator):
        # 횡보 + 지지선 위 -> 통상 익절 매도 동작 유지
        window = _sideways_window()
        rule = _channel_rule()
        lots = [_lot(buy_price=100.0)]
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(112.0), ohlc_window=window, regime_state={},
        )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL
        assert not signals[0].regime_liquidation
        assert not signals[0].regime_partial_liquidation

    def test_breakdown_partial_50_sells_half_and_locks(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule(trendbreak_partial_sell_pct=50.0)
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0, qty=10)]
        st = {}
        signals = _eval_until_confirmed(
            evaluator, rule, lots, _pf(support * 0.95, qty=10), window, st,
        )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL
        assert signals[0].quantity == 5
        assert signals[0].regime_partial_liquidation is True
        # 잔량 추종 데드라인은 매도 체결 확정 시 엔진이 활성화하므로
        # 평가 단계에서는 st를 오염시키지 않는다
        assert "trailing_lock" not in st.get("AAPL", {})

    def test_breakdown_requires_confirm_bars(self, evaluator):
        # 1봉째 이탈은 확정 대기 -> 청산 신호 없음
        window = _sideways_window()
        rule = _channel_rule()
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0, qty=10)]
        st = {}
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
        )
        assert all(
            not s.regime_liquidation and not s.regime_partial_liquidation
            for s in signals
        )
        assert st["AAPL"]["breakdown_streak"] == 1

    def test_breakdown_streak_resets_on_recovery(self, evaluator):
        # 이탈 1봉 -> 회복 1봉 -> 다시 이탈 1봉: 스파이크는 청산으로 이어지지 않음
        window = _sideways_window()
        rule = _channel_rule()
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0, qty=10)]
        st = {}
        evaluator.evaluate_stock(
            rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
        )
        evaluator.evaluate_stock(
            rule, lots, _pf(support * 1.02, qty=10), ohlc_window=window, regime_state=st,
        )
        assert st["AAPL"]["breakdown_streak"] == 0
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
        )
        assert all(
            not s.regime_liquidation and not s.regime_partial_liquidation
            for s in signals
        )

    def test_breakdown_full_100_liquidates_all(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule(trendbreak_partial_sell_pct=100.0)
        support = _support(rule, window)
        lots = [_lot(level=1, qty=5), _lot(level=2, buy_price=95.0, qty=5, lot_id="lot_002")]
        signals = _eval_until_confirmed(
            evaluator, rule, lots, _pf(support * 0.95, qty=10), window, {},
        )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL
        assert signals[0].quantity == 10
        assert signals[0].regime_liquidation is True

    def test_breakdown_tolerance_delays_trigger(self, evaluator):
        # 허용 오차 5%: 지지선 바로 아래로는 이탈 아님, 5% 넘게 뚫어야 이탈
        window = _sideways_window()
        rule = _channel_rule(channel_breakdown_tolerance_pct=5.0)
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0)]

        # 지지선 -2%: 이탈선(support*0.95) 위 -> 통상 흐름 (신호 없음: 익절/추매 조건 미충족)
        signals = _eval_until_confirmed(
            evaluator, rule, lots, _pf(support * 0.98), window, {},
        )
        assert all(not s.regime_liquidation and not s.regime_partial_liquidation for s in signals)

        # 지지선 -6%: 이탈선 아래 -> 이탈 청산
        signals = _eval_until_confirmed(
            evaluator, rule, lots, _pf(support * 0.94), window, {},
        )
        assert len(signals) == 1
        assert signals[0].regime_partial_liquidation is True


class TestChannelReentryBreakout:
    """채널 모드 재진입 게이트(고정 동작): 이탈 청산 후 재진입은 상단 저항선 돌파 시에만."""

    def test_below_resistance_blocked(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule()
        reading = classify_for_rule(rule, window)
        st = {"AAPL": {"post_liquidation": True}}
        signals = evaluator.evaluate_stock(
            rule, [], _pf(reading.channel_support * 1.01), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].is_blocked is True
        assert st["AAPL"]["post_liquidation"] is True  # 마커 유지

    def test_above_resistance_allows_entry_and_clears_marker(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule()
        reading = classify_for_rule(rule, window)
        st = {"AAPL": {"post_liquidation": True}}
        signals = evaluator.evaluate_stock(
            rule, [], _pf(reading.channel_resistance * 1.01), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert not signals[0].is_blocked
        assert signals[0].quantity > 0
        assert "post_liquidation" not in st["AAPL"]

    def test_gate_inactive_without_marker(self, evaluator):
        # 청산 이력이 없으면(첫 진입) 게이트 미적용
        window = _sideways_window()
        rule = _channel_rule()
        reading = classify_for_rule(rule, window)
        signals = evaluator.evaluate_stock(
            rule, [], _pf(reading.channel_support * 1.01), ohlc_window=window, regime_state={},
        )
        assert len(signals) == 1
        assert not signals[0].is_blocked

    def test_gate_inactive_when_option_off(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule()  # 기본 False
        reading = classify_for_rule(rule, window)
        st = {"AAPL": {"post_liquidation": True}}
        signals = evaluator.evaluate_stock(
            rule, [], _pf(reading.channel_support * 1.01), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert not signals[0].is_blocked

    def test_between_mid_and_resistance_still_blocked(self, evaluator):
        # 기준선은 상단 저항선 고정: 중심선~상단 사이 가격은 여전히 차단
        window = _sideways_window()
        rule = _channel_rule()
        reading = classify_for_rule(rule, window)
        price = (reading.channel_mid + reading.channel_resistance) / 2
        st = {"AAPL": {"post_liquidation": True}}
        signals = evaluator.evaluate_stock(
            rule, [], _pf(price), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].is_blocked is True


class TestChannelDowntrendLiquidation:
    def test_downtrend_latch_confirms_then_liquidates(self, evaluator):
        # 하락 기울기 확정(2봉 연속) -> 보유분 이탈 청산
        window = _downtrend_window()
        rule = _channel_rule(trendbreak_partial_sell_pct=100.0)
        support = _support(rule, window)
        price = support * 1.02  # 지지선 위 -> 하단 이탈 아닌 래치 트리거만 검증
        lots = [_lot(buy_price=150.0)]
        st = {}

        # 1봉째: 래치 미확정 -> 청산 없음
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price), ohlc_window=window, regime_state=st,
        )
        assert all(not s.regime_liquidation for s in signals)

        # 2봉째: 래치 확정 -> 전량 청산
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL
        assert signals[0].regime_liquidation is True

    def test_downtrend_latch_blocks_initial_buy(self, evaluator):
        window = _downtrend_window()
        rule = _channel_rule()
        support = _support(rule, window)
        st = {}
        # 래치 확정까지 2회 (보유 없음)
        evaluator.evaluate_stock(rule, [], _pf(support * 1.02), ohlc_window=window, regime_state=st)
        signals = evaluator.evaluate_stock(
            rule, [], _pf(support * 0.5), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].is_blocked is True
        assert signals[0].quantity == 0


class TestChannelUptrend:
    def test_uptrend_latch_locks_harvest_sell(self, evaluator):
        window = _uptrend_window()
        rule = _channel_rule()
        lots = [_lot(buy_price=100.0)]
        pf = _pf(130.0)  # +30%: 통상이면 익절 매도
        st = {"AAPL": {"regime": "uptrend", "adds": 0, "last_add_price": 100.0}}
        signals = evaluator.evaluate_stock(
            rule, lots, pf, ohlc_window=window, regime_state=st,
        )
        # 매도 잠금: 익절 신호가 나오면 안 됨 (눌림 매수 조건도 미충족 -> 빈 결과)
        assert all(s.action != OrderAction.SELL for s in signals)

    def test_uptrend_confirm_requires_two_bars(self, evaluator):
        window = _uptrend_window()
        rule = _channel_rule()
        lots = [_lot(buy_price=100.0)]
        pf = _pf(130.0)
        st = {}
        # 1봉째: 아직 SIDEWAYS 취급 -> 통상 익절 매도
        signals = evaluator.evaluate_stock(rule, lots, pf, ohlc_window=window, regime_state=st)
        assert any(s.action == OrderAction.SELL for s in signals)
        # 2봉째: 상승 확정 -> 매도 잠금
        signals = evaluator.evaluate_stock(rule, lots, pf, ohlc_window=window, regime_state=st)
        assert all(s.action != OrderAction.SELL for s in signals)
        assert st["AAPL"]["regime"] == "uptrend"

    def test_uptrend_breakdown_liquidates_despite_latch(self, evaluator):
        # 상승 래치 중에도 하단 채널선 이탈이면 청산 (채널 이탈이 이탈선 역할)
        window = _uptrend_window()
        rule = _channel_rule(trendbreak_partial_sell_pct=100.0)
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0)]
        st = {"AAPL": {"regime": "uptrend", "adds": 0, "last_add_price": 100.0}}
        signals = _eval_until_confirmed(
            evaluator, rule, lots, _pf(support * 0.95), window, st,
        )
        assert len(signals) == 1
        assert signals[0].regime_liquidation is True


class TestChannelTrailingLock:
    def test_lock_waits_between_lines(self, evaluator):
        # 지지선 아래 & 데드라인 위 -> 대기
        window = _sideways_window()
        rule = _channel_rule(trendbreak_trailing_drop_pct=3.0)
        support = _support(rule, window)
        lock_price = support * 0.95
        lots = [_lot(qty=5)]
        st = {"AAPL": {"trailing_lock": {
            "active": True, "lock_price": lock_price, "drop_pct": 3.0,
        }}}
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(lock_price * 0.99), ohlc_window=window, regime_state=st,
        )
        assert signals == []
        assert "trailing_lock" in st["AAPL"]  # 대기: 락 유지

    def test_lock_releases_on_recovery_above_support(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule(trendbreak_trailing_drop_pct=3.0)
        support = _support(rule, window)
        lots = [_lot(qty=5)]
        st = {"AAPL": {"trailing_lock": {
            "active": True, "lock_price": support * 0.95, "drop_pct": 3.0,
        }}}
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(support * 1.01), ohlc_window=window, regime_state=st,
        )
        assert signals == []
        assert "trailing_lock" not in st["AAPL"]  # 회복: 락 해제

    def test_lock_liquidates_remainder_on_further_drop(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule(trendbreak_trailing_drop_pct=3.0)
        support = _support(rule, window)
        lock_price = support * 0.95
        lots = [_lot(qty=5)]
        st = {"AAPL": {"trailing_lock": {
            "active": True, "lock_price": lock_price, "drop_pct": 3.0,
        }}}
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(lock_price * 0.96), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL
        assert signals[0].quantity == 5
        assert signals[0].regime_liquidation is True

    def test_lock_takes_precedence_over_downtrend_latch(self, evaluator):
        # 락 + 하락 래치 동시 활성: 락 평가가 우선 (반복 분할매도 방지)
        window = _downtrend_window()
        rule = _channel_rule(trendbreak_partial_sell_pct=50.0, trendbreak_trailing_drop_pct=3.0)
        support = _support(rule, window)
        lock_price = support * 0.95
        lots = [_lot(qty=5)]
        st = {"AAPL": {
            "downtrend": "active",
            "trailing_lock": {"active": True, "lock_price": lock_price, "drop_pct": 3.0},
        }}
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(lock_price * 0.99), ohlc_window=window, regime_state=st,
        )
        # 락 대기 (추가 partial sell이 나오면 안 됨)
        assert signals == []


class TestChannelUnknownFallback:
    def test_short_history_falls_through_to_normal(self, evaluator):
        # 히스토리 < lookback -> UNKNOWN -> 레짐 OFF와 동일 (통상 매매)
        window = _window(_geo(40, 100.0, 0.1))
        rule = _channel_rule()  # lookback=63
        lots = [_lot(buy_price=100.0)]
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(112.0), ohlc_window=window, regime_state={},
        )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL
        assert not signals[0].regime_liquidation

    def test_short_history_with_lock_holds(self, evaluator):
        # 락 추적 중 지표 결손 -> 안전 보류
        window = _window(_geo(40, 100.0, 0.1))
        rule = _channel_rule()
        lots = [_lot(qty=5)]
        st = {"AAPL": {"trailing_lock": {
            "active": True, "lock_price": 95.0, "drop_pct": 3.0,
        }}}
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(80.0), ohlc_window=window, regime_state=st,
        )
        assert signals == []
        assert "trailing_lock" in st["AAPL"]
