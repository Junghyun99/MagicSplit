# tests/test_split_evaluator_channel.py
"""채널 레짐 모드(regime_algo="channel")의 SplitEvaluator 통합 테스트.

이탈 판정 = (하락 래치 확정) OR (상승/횡보 중 하단 채널선 하향 돌파).
청산 방식은 trendbreak_partial_sell_pct(50=절반+추종 데드라인, 100=전량)를 따른다.
"""
import datetime
from unittest.mock import patch

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
    """이탈 확정 일수만큼 날짜를 변경하며 반복 평가해 마지막 신호를 반환한다."""
    base = datetime.date(2024, 6, 1)
    signals = []
    for i in range(BREAKDOWN_CONFIRM_BARS):
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = base + datetime.timedelta(days=i)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
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
        # 1일째 이탈은 확정 대기 -> 청산 신호 없음
        window = _sideways_window()
        rule = _channel_rule()
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0, qty=10)]
        st = {}
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 1)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            signals = evaluator.evaluate_stock(
                rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
            )
        assert all(
            not s.regime_liquidation and not s.regime_partial_liquidation
            for s in signals
        )
        assert st["AAPL"]["breakdown_days"] == ["2024-06-01"]

    def test_breakdown_streak_resets_on_recovery(self, evaluator):
        # Day1 이탈 -> Day2 회복 -> Day3 이탈: Day2 회복으로 연속 끊김 -> Day3만 1일
        window = _sideways_window()
        rule = _channel_rule()
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0, qty=10)]
        st = {}
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 1)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            evaluator.evaluate_stock(
                rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
            )
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 2)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            evaluator.evaluate_stock(
                rule, lots, _pf(support * 1.02, qty=10), ohlc_window=window, regime_state=st,
            )
        # Day2 회복: Day1 기록은 아직 남아있지만 Day3 시작 시 정리됨
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 3)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            signals = evaluator.evaluate_stock(
                rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
            )
        # Day3 이탈이지만 Day2가 회복이어서 스트릭 리셋, Day3만 1일 -> 확정 안 됨
        assert st["AAPL"]["breakdown_days"] == ["2024-06-03"]
        assert all(
            not s.regime_liquidation and not s.regime_partial_liquidation
            for s in signals
        )

    def test_same_day_cycles_do_not_stack(self, evaluator):
        # 같은 날 2사이클 이탈 -> 1일로만 카운트, 확정 안 됨
        window = _sideways_window()
        rule = _channel_rule()
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0, qty=10)]
        st = {}
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 1)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            evaluator.evaluate_stock(
                rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
            )
            signals = evaluator.evaluate_stock(
                rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
            )
        assert all(
            not s.regime_liquidation and not s.regime_partial_liquidation
            for s in signals
        )
        assert st["AAPL"]["breakdown_days"] == ["2024-06-01"]

    def test_intraday_recovery_cancels_today(self, evaluator):
        # Day1 장초 이탈 -> Day1 장말 회복 -> Day2+Day3 이탈해야 확정
        window = _sideways_window()
        rule = _channel_rule()
        support = _support(rule, window)
        lots = [_lot(buy_price=100.0, qty=10)]
        st = {}
        # Day1 10am: 이탈
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 1)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            evaluator.evaluate_stock(
                rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
            )
            assert st["AAPL"]["breakdown_days"] == ["2024-06-01"]
            # Day1 12pm: 회복 -> 오늘 카운트 취소
            evaluator.evaluate_stock(
                rule, lots, _pf(support * 1.02, qty=10), ohlc_window=window, regime_state=st,
            )
            assert st["AAPL"]["breakdown_days"] == []
        # Day2: 이탈 (Day1이 취소되어 스트릭 1일)
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 2)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            signals = evaluator.evaluate_stock(
                rule, lots, _pf(support * 0.95, qty=10), ohlc_window=window, regime_state=st,
            )
        assert st["AAPL"]["breakdown_days"] == ["2024-06-02"]
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
        assert signals[0].reentry_gate == "midline"

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


class TestChannelReentryGate:
    """채널 모드 재진입: 상승/횡보 이탈은 중심선, 하락 청산은 상단선 게이트."""

    def test_midline_gate_blocks_at_or_below_midline(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule()
        reading = classify_for_rule(rule, window)
        st = {"AAPL": {
            "post_liquidation": True,
            "post_liquidation_reentry_gate": "midline",
        }}
        signals = evaluator.evaluate_stock(
            rule, [], _pf(reading.channel_support * 1.01), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].is_blocked is True
        assert st["AAPL"]["post_liquidation"] is True  # 마커 유지

    def test_midline_gate_allows_entry_before_resistance(self, evaluator):
        window = _sideways_window()
        rule = _channel_rule()
        reading = classify_for_rule(rule, window)
        price = (reading.channel_mid + reading.channel_resistance) / 2
        st = {"AAPL": {
            "post_liquidation": True,
            "post_liquidation_reentry_gate": "midline",
        }}
        signals = evaluator.evaluate_stock(
            rule, [], _pf(price), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert not signals[0].is_blocked
        assert signals[0].quantity > 0
        assert "post_liquidation" not in st["AAPL"]
        assert "post_liquidation_reentry_gate" not in st["AAPL"]

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

    def test_resistance_gate_blocks_between_mid_and_resistance(self, evaluator):
        # 하락채널 청산은 중심선~상단 사이에서도 계속 차단한다.
        window = _sideways_window()
        rule = _channel_rule()
        reading = classify_for_rule(rule, window)
        price = (reading.channel_mid + reading.channel_resistance) / 2
        st = {"AAPL": {
            "post_liquidation": True,
            "post_liquidation_reentry_gate": "resistance",
        }}
        signals = evaluator.evaluate_stock(
            rule, [], _pf(price), ohlc_window=window, regime_state=st,
        )
        assert len(signals) == 1
        assert signals[0].is_blocked is True

    def test_legacy_post_liquidation_defaults_to_resistance(self, evaluator):
        # 기존 저장 상태에는 게이트 키가 없으므로 보수적으로 상단선 기준을 유지한다.
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
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 1)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            signals = evaluator.evaluate_stock(
                rule, lots, _pf(price), ohlc_window=window, regime_state=st,
            )
        assert all(not s.regime_liquidation for s in signals)

        # 2봉째: 래치 확정 -> 전량 청산
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 2)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            signals = evaluator.evaluate_stock(
                rule, lots, _pf(price), ohlc_window=window, regime_state=st,
            )
        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL
        assert signals[0].regime_liquidation is True
        assert signals[0].reentry_gate == "resistance"

    def test_downtrend_latch_blocks_initial_buy(self, evaluator):
        window = _downtrend_window()
        rule = _channel_rule()
        support = _support(rule, window)
        st = {}
        # 래치 확정까지 2회 (보유 없음)
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 1)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            evaluator.evaluate_stock(rule, [], _pf(support * 1.02), ohlc_window=window, regime_state=st)
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 2)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
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

    def test_uptrend_confirm_requires_two_days(self, evaluator):
        window = _uptrend_window()
        rule = _channel_rule()
        lots = [_lot(buy_price=100.0)]
        pf = _pf(130.0)
        st = {}
        # 1봉째: 아직 SIDEWAYS 취급 -> 통상 익절 매도
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 1)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            signals = evaluator.evaluate_stock(rule, lots, pf, ohlc_window=window, regime_state=st)
        assert any(s.action == OrderAction.SELL for s in signals)
        # 2봉째: 상승 확정 -> 매도 잠금
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 1)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            signals = evaluator.evaluate_stock(rule, lots, pf, ohlc_window=window, regime_state=st)
        assert st["AAPL"].get("regime") != "uptrend"
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 2)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
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


class TestBreakdownConfirmationSurvivesFailedOrder:
    """이탈 확정은 청산이 실제로 반영될 때까지 유지돼야 한다.

    확정 시점에 카운터를 비우면 주문이 거절됐을 때 확정을 잃고 2일을 다시
    센다. 리스크 관리 장치가 일시적 API 오류로 하루씩 밀리면 안 된다.
    """

    def _setup(self, evaluator, **rule_over):
        rule_over.setdefault("trendbreak_partial_sell_pct", 50.0)
        rule = _channel_rule(**rule_over)
        window = _sideways_window()
        support = _support(rule, window)
        price = support * 0.9  # 이탈선 아래
        lots = [_lot(level=1, qty=10)]
        st = {}
        signals = _eval_until_confirmed(
            evaluator, rule, lots, _pf(price, qty=10), window, st
        )
        return rule, window, support, price, lots, st, signals

    def test_confirmation_emits_liquidation_signal(self, evaluator):
        _, _, _, _, _, st, signals = self._setup(evaluator)

        assert len(signals) == 1
        assert signals[0].regime_partial_liquidation is True
        assert st["AAPL"]["breakdown_confirmed"] is True

    def test_counter_is_not_consumed_by_the_signal(self, evaluator):
        """신호를 냈다고 카운터가 비워지면 안 된다 (주문 결과를 아직 모른다)."""
        _, _, _, _, _, st, _ = self._setup(evaluator)

        assert len(st["AAPL"]["breakdown_days"]) >= BREAKDOWN_CONFIRM_BARS

    def test_retries_on_next_cycle_when_order_failed(self, evaluator):
        """주문 거절로 regime_state가 그대로일 때 다음 사이클에 즉시 재시도."""
        rule, window, _, price, lots, st, _ = self._setup(evaluator)

        # 거절이면 엔진이 포지션/상태를 건드리지 않는다 -> st 그대로 재평가
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 3)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            again = evaluator.evaluate_stock(
                rule, lots, _pf(price, qty=10), ohlc_window=window, regime_state=st,
            )

        assert len(again) == 1
        assert again[0].regime_partial_liquidation is True

    def test_recovery_cancels_the_confirmation(self, evaluator):
        """이탈선 위로 회복하면 청산 근거가 사라지므로 확정도 취소한다."""
        rule, window, support, _, lots, st, _ = self._setup(evaluator)

        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 3)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            signals = evaluator.evaluate_stock(
                rule, lots, _pf(support * 1.05, qty=10),
                ohlc_window=window, regime_state=st,
            )

        assert "breakdown_confirmed" not in st["AAPL"]
        assert all(not s.regime_partial_liquidation for s in signals)

    def test_zero_percent_liquidation_clears_confirmation(self, evaluator):
        """즉시 매도 0%는 주문 없이 상태만 바꾸므로 그 자리가 커밋 지점이다."""
        _, _, _, _, _, st, signals = self._setup(
            evaluator, trendbreak_partial_sell_pct=0.0
        )

        assert signals == []
        assert st["AAPL"]["trailing_lock"]["active"] is True
        assert "breakdown_confirmed" not in st["AAPL"]
        assert "breakdown_days" not in st["AAPL"]

    def test_downtrend_latch_takeover_clears_confirmation(self, evaluator):
        """하락 래치는 regime_state에 남아 스스로 재시도하므로 비워도 안전하다."""
        rule = _channel_rule(trendbreak_partial_sell_pct=50.0)
        window = _downtrend_window()
        st = {"AAPL": {"downtrend": "active", "breakdown_days": ["2024-06-01"],
                       "breakdown_confirmed": True}}
        lots = [_lot(level=1, qty=10)]

        signals = evaluator.evaluate_stock(
            rule, lots, _pf(50.0, qty=10), ohlc_window=window, regime_state=st,
        )

        assert len(signals) == 1
        assert signals[0].reentry_gate == "resistance"
        assert "breakdown_confirmed" not in st["AAPL"]
        assert "breakdown_days" not in st["AAPL"]


class TestDowntrendDuplicateLiquidationPrevention:
    """하락 래치 분할 청산 후 trailing_lock 해제 시 동일 래치 내 중복 이탈 청산 방지 검증."""

    def test_duplicate_liquidation_skipped_when_partially_liquidated(self, evaluator):
        window = _downtrend_window()
        rule = _channel_rule(trendbreak_partial_sell_pct=50.0)
        support = _support(rule, window)
        price = support * 1.02  # 하단 채널선 위
        lots = [_lot(qty=5)]
        st = {
            "AAPL": {
                "downtrend": "active",
                "downtrend_partially_liquidated": True,
            }
        }
        # trailing_lock이 해제된 후 실행 시 동일 하락 래치 중복 청산 스킵
        signals = evaluator.evaluate_stock(
            rule, lots, _pf(price, qty=5), ohlc_window=window, regime_state=st,
        )
        # 매도 청산 신호가 발생하지 않고 스킵되어야 함
        assert all(not s.regime_liquidation and not s.regime_partial_liquidation for s in signals)

    def test_flag_cleared_on_downtrend_release(self, evaluator):
        window = _uptrend_window()  # UPTREND 봉으로 하락 래치 탈출 조건 충족
        rule = _channel_rule()
        st = {
            "AAPL": {
                "downtrend": "active",
                "downtrend_partially_liquidated": True,
                "downtrend_exit_days": ["2024-06-01"],
            }
        }
        with patch("src.core.logic.split_evaluator.datetime") as mock_dt:
            mock_dt.date.today.return_value = datetime.date(2024, 6, 2)
            mock_dt.date.side_effect = lambda *a, **k: datetime.date(*a, **k)
            evaluator._resolve_downtrend_block(
                classify_for_rule(rule, window), st["AAPL"], "AAPL"
            )
        # 2일 확정 후 downtrend 해제 및 downtrend_partially_liquidated 삭제 확인
        assert st["AAPL"].get("downtrend") is None
        assert "downtrend_partially_liquidated" not in st["AAPL"]

