# tests/test_split_evaluator.py
import pytest
from src.core.logic.split_evaluator import SplitEvaluator
from src.core.models import (
    StockRule, PositionLot, Portfolio, OrderAction, SplitSignal,
)


@pytest.fixture
def evaluator():
    return SplitEvaluator()


class TestEvaluateInitialBuy:
    """보유 lot이 없을 때 초기 매수 테스트"""

    def test_initial_buy_when_no_lots(self, evaluator, create_rule, create_portfolio):
        """lot이 없으면 Lv1 초기 매수 신호 생성"""
        rules = [create_rule(ticker="AAPL", buy_amount=500)]
        portfolio = create_portfolio(cash=10000.0, prices={"AAPL": 100.0})

        signals = evaluator.evaluate(rules, [], portfolio)

        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert signals[0].ticker == "AAPL"
        assert signals[0].quantity == 5  # 500 / 100 = 5
        assert signals[0].level == 1
        assert "Lv1" in signals[0].reason

    def test_initial_buy_insufficient_amount(self, evaluator, create_rule, create_portfolio):
        """매수 금액으로 1주도 살 수 없으면 스킵"""
        rules = [create_rule(ticker="AAPL", buy_amount=50)]
        portfolio = create_portfolio(prices={"AAPL": 100.0})

        signals = evaluator.evaluate(rules, [], portfolio)
        assert len(signals) == 0


class TestEvaluateSell:
    """익절 매도 테스트"""

    def test_sell_on_threshold(self, evaluator, create_rule, create_lot, create_portfolio):
        """마지막 차수 매수가 대비 sell_threshold_pct 이상 상승 시 매도"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0)]
        lots = [create_lot(ticker="AAPL", buy_price=100.0, quantity=5, level=1)]
        # 현재가 111.0 → +11% (> 10% 임계치)
        portfolio = create_portfolio(prices={"AAPL": 111.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        sell_signals = [s for s in signals if s.action == OrderAction.SELL]
        assert len(sell_signals) == 1
        assert sell_signals[0].lot_id == lots[0].lot_id
        assert sell_signals[0].quantity == 5
        assert sell_signals[0].level == 1

    def test_no_sell_below_threshold(self, evaluator, create_rule, create_lot, create_portfolio):
        """임계치 미만이면 매도 신호 없음"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0)]
        lots = [create_lot(ticker="AAPL", buy_price=100.0, quantity=5, level=1)]
        # 현재가 105.0 → +5% (< 10%)
        portfolio = create_portfolio(prices={"AAPL": 105.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        sell_signals = [s for s in signals if s.action == OrderAction.SELL]
        assert len(sell_signals) == 0

    def test_sell_only_last_level(self, evaluator, create_rule, create_lot, create_portfolio):
        """여러 차수가 있어도 마지막 차수(가장 높은 level)만 매도 평가"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=90.0, quantity=3, level=1),
            create_lot(lot_id="lot_002", ticker="AAPL", buy_price=95.0, quantity=2, level=2),
        ]
        # 현재가 110.0 → lot_001: +22.2%, lot_002: +15.8% (둘 다 > 10%)
        # 하지만 마지막 차수(lot_002)만 매도
        portfolio = create_portfolio(prices={"AAPL": 110.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        sell_signals = [s for s in signals if s.action == OrderAction.SELL]
        assert len(sell_signals) == 1
        assert sell_signals[0].lot_id == "lot_002"
        assert sell_signals[0].level == 2

    def test_sell_last_level_below_threshold(self, evaluator, create_rule, create_lot, create_portfolio):
        """이전 차수가 조건 충족해도 마지막 차수가 미달이면 매도 안 함"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=80.0, quantity=3, level=1),  # +25%
            create_lot(lot_id="lot_002", ticker="AAPL", buy_price=95.0, quantity=2, level=2),  # +5.3%
        ]
        portfolio = create_portfolio(prices={"AAPL": 100.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        sell_signals = [s for s in signals if s.action == OrderAction.SELL]
        assert len(sell_signals) == 0


class TestEvaluateAdditionalBuy:
    """추가 매수 (물타기) 테스트"""

    def test_buy_on_drop(self, evaluator, create_rule, create_lot, create_portfolio):
        """마지막 차수 대비 buy_threshold_pct 이하 하락 시 추가 매수"""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, buy_amount=500)]
        lots = [create_lot(ticker="AAPL", buy_price=100.0, quantity=5, buy_date="2026-04-01", level=1)]
        # 현재가 94.0 → -6% (< -5% 임계치)
        portfolio = create_portfolio(cash=10000.0, prices={"AAPL": 94.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        buy_signals = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buy_signals) == 1
        assert buy_signals[0].quantity == 5  # 500 / 94 = 5
        assert buy_signals[0].level == 2  # 다음 차수

    def test_no_buy_above_threshold(self, evaluator, create_rule, create_lot, create_portfolio):
        """임계치 이상이면 추가 매수 안 함"""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0)]
        lots = [create_lot(ticker="AAPL", buy_price=100.0, level=1)]
        # 현재가 97.0 → -3% (> -5%)
        portfolio = create_portfolio(prices={"AAPL": 97.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        buy_signals = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buy_signals) == 0

    def test_no_buy_when_max_lots_reached(self, evaluator, create_rule, create_lot, create_portfolio):
        """max_lots에 도달하면 추가 매수 불가"""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, max_lots=2)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=100.0, buy_date="2026-04-01", level=1),
            create_lot(lot_id="lot_002", ticker="AAPL", buy_price=95.0, buy_date="2026-04-05", level=2),
        ]
        # 현재가 80.0 → 큰 하락이지만 max_lots=2 도달 (next_level=3 > max_lots=2)
        portfolio = create_portfolio(cash=10000.0, prices={"AAPL": 80.0}, holdings={"AAPL": 10})

        signals = evaluator.evaluate(rules, lots, portfolio)

        buy_signals = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buy_signals) == 0


class TestMutualExclusivity:
    """한 종목에서 매도+매수 동시 불가 테스트"""

    def test_no_simultaneous_buy_and_sell(self, evaluator, create_rule, create_lot, create_portfolio):
        """한 종목에서 매도와 매수가 동시에 발생하지 않음"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0, buy_pct=-5.0, buy_amount=500, max_lots=100)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=90.0, quantity=3, level=1),
        ]
        # 현재가 100 → +11.1% → 매도 조건 충족
        portfolio = create_portfolio(prices={"AAPL": 100.0}, holdings={"AAPL": 3})

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio)

        actions = {s.action for s in signals}
        assert len(actions) <= 1, "한 종목에서 매도와 매수가 동시에 발생하면 안 됨"

    def test_sell_takes_priority(self, evaluator, create_rule, create_lot, create_portfolio):
        """매도 조건 충족 시 매수 확인하지 않고 매도만 반환"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0, buy_pct=-5.0)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=90.0, quantity=3, level=1),
        ]
        # +11.1% → 매도 조건 충족
        portfolio = create_portfolio(prices={"AAPL": 100.0}, holdings={"AAPL": 3})

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio)

        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL


class TestLevelStaircase:
    """차수 계단식 동작 테스트"""

    def test_level_staircase_pattern(self, evaluator, create_rule, create_lot, create_portfolio):
        """매도 후 이전 차수로 돌아가서 다음 평가에 활용"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0, buy_pct=-5.0, buy_amount=500)]
        # 1차, 2차가 있는 상태에서 하락 → 3차 매수
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=100.0, quantity=5, level=1),
            create_lot(lot_id="lot_002", ticker="AAPL", buy_price=95.0, quantity=5, level=2),
        ]
        # 2차 대비 -6.3% → 추가 매수 조건 충족
        portfolio = create_portfolio(prices={"AAPL": 89.0}, holdings={"AAPL": 10})

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio)

        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert signals[0].level == 3  # 다음 차수

    def test_buy_level_based_on_highest_level(self, evaluator, create_rule, create_lot, create_portfolio):
        """매수 시 가장 높은 level + 1로 차수 결정"""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, buy_amount=500, max_lots=100)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=100.0, level=1),
            create_lot(lot_id="lot_003", ticker="AAPL", buy_price=85.0, level=3),
        ]
        # Lv3 대비 -6% → 추가 매수
        portfolio = create_portfolio(prices={"AAPL": 79.9}, holdings={"AAPL": 10})

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio)

        assert len(signals) == 1
        assert signals[0].level == 4


class TestEvaluateEdgeCases:
    """엣지 케이스 테스트"""

    def test_disabled_rule_skipped(self, evaluator, create_rule, create_portfolio):
        """disabled 종목은 스킵"""
        rules = [create_rule(ticker="AAPL", enabled=False)]
        portfolio = create_portfolio(prices={"AAPL": 100.0})

        signals = evaluator.evaluate(rules, [], portfolio)
        assert len(signals) == 0

    def test_zero_price_skipped(self, evaluator, create_rule, create_portfolio):
        """현재가가 0이면 스킵"""
        rules = [create_rule(ticker="AAPL")]
        portfolio = create_portfolio(prices={"AAPL": 0.0})

        signals = evaluator.evaluate(rules, [], portfolio)
        assert len(signals) == 0

    def test_missing_price_skipped(self, evaluator, create_rule, create_portfolio):
        """현재가가 없으면 스킵"""
        rules = [create_rule(ticker="UNKNOWN")]
        portfolio = create_portfolio(prices={"AAPL": 100.0})

        signals = evaluator.evaluate(rules, [], portfolio)
        assert len(signals) == 0

    def test_sell_before_buy_ordering(self, evaluator, create_rule, create_lot, create_portfolio):
        """여러 종목 간 매도 신호가 매수 신호보다 앞에 배치"""
        rules = [
            create_rule(ticker="AAPL", sell_pct=10.0, buy_pct=-5.0, buy_amount=500, max_lots=100),
            create_rule(ticker="MSFT", buy_pct=-5.0, buy_amount=500, max_lots=100),
        ]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=90.0, quantity=5, buy_date="2026-04-01", level=1),
        ]
        portfolio = create_portfolio(
            cash=10000.0,
            prices={"AAPL": 110.0, "MSFT": 200.0},
            holdings={"AAPL": 5},
        )

        signals = evaluator.evaluate(rules, lots, portfolio)

        # AAPL 익절(매도) + MSFT 초기 매수
        sell_idx = [i for i, s in enumerate(signals) if s.action == OrderAction.SELL]
        buy_idx = [i for i, s in enumerate(signals) if s.action == OrderAction.BUY]

        if sell_idx and buy_idx:
            assert max(sell_idx) < min(buy_idx), "매도가 매수보다 먼저 와야 함"

    def test_multiple_stocks(self, evaluator, create_rule, create_portfolio):
        """여러 종목을 동시에 평가"""
        rules = [
            create_rule(ticker="AAPL", buy_amount=500),
            create_rule(ticker="MSFT", buy_amount=1000),
        ]
        portfolio = create_portfolio(
            cash=10000.0,
            prices={"AAPL": 100.0, "MSFT": 200.0},
        )

        signals = evaluator.evaluate(rules, [], portfolio)

        assert len(signals) == 2
        tickers = {s.ticker for s in signals}
        assert tickers == {"AAPL", "MSFT"}


class TestPerLevelThresholds:
    """차수별 배열 threshold/amount 테스트"""

    def _rule_with_arrays(
        self,
        ticker="AAPL",
        buy_pcts=None,
        sell_pcts=None,
        buy_amounts=None,
        max_lots=10,
    ):
        from src.core.models import StockRule
        return StockRule(
            ticker=ticker,
            buy_threshold_pcts=buy_pcts,
            sell_threshold_pcts=sell_pcts,
            buy_amounts=buy_amounts,
            buy_threshold_pct=(None if buy_pcts else -5.0),
            sell_threshold_pct=(None if sell_pcts else 10.0),
            buy_amount=(None if buy_amounts else 500.0),
            max_lots=max_lots,
        )

    def test_buy_uses_level_specific_threshold(
        self, evaluator, create_lot, create_portfolio
    ):
        """Lv1 → Lv2 매수 판단은 buy_threshold_pcts[0] 기준, Lv2→Lv3는 [1]."""
        rule = self._rule_with_arrays(
            buy_pcts=[-3.0, -5.0, -7.0],
            buy_amounts=[100.0, 200.0, 300.0],
            max_lots=5,
        )
        # Lv1 매수가 100, 현재가 96 → -4% ≤ -3% → Lv2 매수 트리거
        lots = [create_lot(ticker="AAPL", buy_price=100.0, quantity=1, level=1)]
        portfolio = create_portfolio(prices={"AAPL": 96.0}, holdings={"AAPL": 1})

        signals = evaluator.evaluate_stock(rule, lots, portfolio)
        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert signals[0].level == 2

    def test_lv2_to_lv3_requires_stricter_drop(
        self, evaluator, create_lot, create_portfolio
    ):
        """Lv2→Lv3 평가는 buy_threshold_pcts[1]=-5% 기준: -4%로는 부족."""
        rule = self._rule_with_arrays(
            buy_pcts=[-3.0, -5.0, -7.0],
            buy_amounts=[100.0, 200.0, 300.0],
            max_lots=5,
        )
        lots = [
            create_lot(lot_id="l1", ticker="AAPL", buy_price=100.0, level=1),
            create_lot(lot_id="l2", ticker="AAPL", buy_price=95.0, level=2),
        ]
        # Lv2 매수가 95, 현재가 91.2 → -4% (> -5%) → 매수 안 됨
        portfolio = create_portfolio(prices={"AAPL": 91.2}, holdings={"AAPL": 2})

        buy_signals = [
            s for s in evaluator.evaluate_stock(rule, lots, portfolio)
            if s.action == OrderAction.BUY
        ]
        assert buy_signals == []

    def test_buy_amount_for_next_level(
        self, evaluator, create_lot, create_portfolio
    ):
        """추가 매수 금액은 다음 차수(new lot) 기준."""
        rule = self._rule_with_arrays(
            buy_pcts=[-3.0, -5.0, -7.0],
            buy_amounts=[100.0, 200.0, 300.0],
            max_lots=5,
        )
        lots = [create_lot(ticker="AAPL", buy_price=100.0, level=1)]
        portfolio = create_portfolio(prices={"AAPL": 96.0}, holdings={"AAPL": 1})

        signals = evaluator.evaluate_stock(rule, lots, portfolio)
        # Lv2 신규 lot → buy_amounts[1] = 200, 200/96 = 2주
        assert signals[0].level == 2
        assert signals[0].quantity == 2

    def test_sell_uses_level_specific_threshold(
        self, evaluator, create_lot, create_portfolio
    ):
        """Lv2 매도 판단은 sell_threshold_pcts[1] 기준."""
        rule = self._rule_with_arrays(
            sell_pcts=[5.0, 8.0, 12.0],
            buy_amounts=[100.0, 200.0, 300.0],
            max_lots=5,
        )
        lots = [
            create_lot(lot_id="l1", ticker="AAPL", buy_price=100.0, level=1),
            create_lot(lot_id="l2", ticker="AAPL", buy_price=100.0, level=2),
        ]
        # Lv2 매수가 100, 현재가 109 → +9% ≥ 8% → 매도
        portfolio = create_portfolio(prices={"AAPL": 109.0}, holdings={"AAPL": 2})

        signals = evaluator.evaluate_stock(rule, lots, portfolio)
        assert signals and signals[0].action == OrderAction.SELL
        assert signals[0].level == 2

    def test_sell_threshold_clamps_for_deep_level(
        self, evaluator, create_lot, create_portfolio
    ):
        """배열보다 깊은 level은 마지막 값으로 clamp."""
        rule = self._rule_with_arrays(
            sell_pcts=[5.0],
            buy_amounts=[100.0],
            max_lots=10,
        )
        lots = [create_lot(ticker="AAPL", buy_price=100.0, level=4)]
        # Lv4에서도 sell threshold는 배열의 마지막 값 5% 사용 → +6% → 매도
        portfolio = create_portfolio(prices={"AAPL": 106.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate_stock(rule, lots, portfolio)
        assert signals and signals[0].action == OrderAction.SELL

    def test_initial_buy_uses_level1_amount(
        self, evaluator, create_portfolio
    ):
        """보유 lot 없을 때 초기 매수는 buy_amounts[0] 사용."""
        rule = self._rule_with_arrays(
            buy_amounts=[300.0, 500.0, 800.0],
            max_lots=5,
        )
        portfolio = create_portfolio(cash=10000.0, prices={"AAPL": 100.0})

        signals = evaluator.evaluate([rule], [], portfolio)
        assert len(signals) == 1
        assert signals[0].level == 1
        assert signals[0].quantity == 3  # 300 / 100


class TestDynamicReentry:
    """동적 재매수 기준(매도가 기반) 테스트"""

    def test_dynamic_reentry_uses_sell_price_when_higher(
        self, evaluator, create_rule, create_lot, create_portfolio
    ):
        """직전 매도가가 마지막 차수 매수가보다 높으면, 매도가를 기준으로 매수 판단."""
        # Lv1(100), Lv2(95) 보유. Lv3가 매도된 상태.
        # 직전 매도가 = 110 (트레일링 스톱으로 높게 매도)
        # 기존 기준: 95 * 0.95 = 90.25 → 현재가 104 > 90.25 → 매수 안 됨
        # 동적 기준: 110 * 0.95 = 104.5 → 현재가 104 ≤ 104.5 → 매수!
        # Lv2 매수가 95에서 현재가 104 → +9.47% < 10% → 매도 조건 미달
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, buy_amount=500, max_lots=10)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=100.0, level=1),
            create_lot(lot_id="lot_002", ticker="AAPL", buy_price=95.0, level=2),
        ]
        portfolio = create_portfolio(
            cash=10000.0, prices={"AAPL": 104.0}, holdings={"AAPL": 10}
        )
        last_sell_prices = {"AAPL": 110.0}

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio, last_sell_prices)

        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert signals[0].level == 3
        assert "동적 재매수" in signals[0].reason

    def test_dynamic_reentry_not_triggered_when_price_above(
        self, evaluator, create_rule, create_lot, create_portfolio
    ):
        """현재가가 동적 기준가보다 높으면 매수 안 됨."""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, sell_pct=15.0, buy_amount=500, max_lots=10)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=100.0, level=1),
            create_lot(lot_id="lot_002", ticker="AAPL", buy_price=95.0, level=2),
        ]
        # 매도가 110 * 0.95 = 104.5 → 현재가 105 > 104.5 → 매수 안 됨
        # Lv2 매수가 95에서 현재가 105 → +10.5% < 15% → 매도 조건 미달
        portfolio = create_portfolio(
            cash=10000.0, prices={"AAPL": 105.0}, holdings={"AAPL": 10}
        )
        last_sell_prices = {"AAPL": 110.0}

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio, last_sell_prices)
        buy_signals = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buy_signals) == 0

    def test_dynamic_reentry_falls_back_to_grid_when_sell_price_lower(
        self, evaluator, create_rule, create_lot, create_portfolio
    ):
        """매도가가 마지막 차수 매수가보다 낮으면 기존 그리드 기준 사용."""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, buy_amount=500, max_lots=10)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=100.0, level=1),
        ]
        # 매도가 90 < Lv1 매수가 100 → 기존 기준 사용: 100 * 0.95 = 95
        # 현재가 94 < 95 → 매수 (기존 그리드)
        portfolio = create_portfolio(
            cash=10000.0, prices={"AAPL": 94.0}, holdings={"AAPL": 5}
        )
        last_sell_prices = {"AAPL": 90.0}

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio, last_sell_prices)

        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert signals[0].level == 2
        assert "추가 매수" in signals[0].reason  # 일반 매수 reason
        assert "동적" not in signals[0].reason

    def test_dynamic_reentry_no_sell_price_uses_grid(
        self, evaluator, create_rule, create_lot, create_portfolio
    ):
        """last_sell_prices가 None이면 기존 그리드 기준 사용."""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, buy_amount=500, max_lots=10)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=100.0, level=1),
        ]
        # 기존 기준: 100 * 0.95 = 95 → 현재가 94 < 95 → 매수
        portfolio = create_portfolio(
            cash=10000.0, prices={"AAPL": 94.0}, holdings={"AAPL": 5}
        )

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio, None)

        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert signals[0].level == 2

    def test_dynamic_reentry_does_not_affect_sell(
        self, evaluator, create_rule, create_lot, create_portfolio
    ):
        """동적 재매수 기준은 매도 로직에 영향 없음."""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0, buy_pct=-5.0)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=90.0, level=1),
        ]
        # 현재가 100 → +11.1% > 10% → 매도
        portfolio = create_portfolio(prices={"AAPL": 100.0}, holdings={"AAPL": 5})
        last_sell_prices = {"AAPL": 200.0}  # 매도가가 높아도 매도 로직에 영향 없음

        signals = evaluator.evaluate_stock(rules[0], lots, portfolio, last_sell_prices)

        assert len(signals) == 1
        assert signals[0].action == OrderAction.SELL
