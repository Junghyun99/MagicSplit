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
        """lot이 없으면 초기 매수 신호 생성"""
        rules = [create_rule(ticker="AAPL", buy_amount=500)]
        portfolio = create_portfolio(cash=10000.0, prices={"AAPL": 100.0})

        signals = evaluator.evaluate(rules, [], portfolio)

        assert len(signals) == 1
        assert signals[0].action == OrderAction.BUY
        assert signals[0].ticker == "AAPL"
        assert signals[0].quantity == 5  # 500 / 100 = 5
        assert signals[0].reason == "초기 매수"

    def test_initial_buy_insufficient_amount(self, evaluator, create_rule, create_portfolio):
        """매수 금액으로 1주도 살 수 없으면 스킵"""
        rules = [create_rule(ticker="AAPL", buy_amount=50)]
        portfolio = create_portfolio(prices={"AAPL": 100.0})

        signals = evaluator.evaluate(rules, [], portfolio)
        assert len(signals) == 0


class TestEvaluateSell:
    """익절 매도 테스트"""

    def test_sell_on_threshold(self, evaluator, create_rule, create_lot, create_portfolio):
        """매수가 대비 sell_threshold_pct 이상 상승 시 매도"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0)]
        lots = [create_lot(ticker="AAPL", buy_price=100.0, quantity=5)]
        # 현재가 111.0 → +11% (> 10% 임계치)
        portfolio = create_portfolio(prices={"AAPL": 111.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        sell_signals = [s for s in signals if s.action == OrderAction.SELL]
        assert len(sell_signals) == 1
        assert sell_signals[0].lot_id == lots[0].lot_id
        assert sell_signals[0].quantity == 5

    def test_no_sell_below_threshold(self, evaluator, create_rule, create_lot, create_portfolio):
        """임계치 미만이면 매도 신호 없음"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0)]
        lots = [create_lot(ticker="AAPL", buy_price=100.0, quantity=5)]
        # 현재가 105.0 → +5% (< 10%)
        portfolio = create_portfolio(prices={"AAPL": 105.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        sell_signals = [s for s in signals if s.action == OrderAction.SELL]
        assert len(sell_signals) == 0

    def test_sell_multiple_lots(self, evaluator, create_rule, create_lot, create_portfolio):
        """여러 lot이 모두 익절 조건 충족 시 각각 매도 신호"""
        rules = [create_rule(ticker="AAPL", sell_pct=10.0)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=90.0, quantity=3),
            create_lot(lot_id="lot_002", ticker="AAPL", buy_price=95.0, quantity=2),
        ]
        # 현재가 110.0 → lot_001: +22.2%, lot_002: +15.8% (둘 다 > 10%)
        portfolio = create_portfolio(prices={"AAPL": 110.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        sell_signals = [s for s in signals if s.action == OrderAction.SELL]
        assert len(sell_signals) == 2


class TestEvaluateAdditionalBuy:
    """추가 매수 (물타기) 테스트"""

    def test_buy_on_drop(self, evaluator, create_rule, create_lot, create_portfolio):
        """최근 lot 대비 buy_threshold_pct 이하 하락 시 추가 매수"""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, buy_amount=500)]
        lots = [create_lot(ticker="AAPL", buy_price=100.0, quantity=5, buy_date="2026-04-01")]
        # 현재가 94.0 → -6% (< -5% 임계치)
        portfolio = create_portfolio(cash=10000.0, prices={"AAPL": 94.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        buy_signals = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buy_signals) == 1
        assert buy_signals[0].quantity == 5  # 500 / 94 = 5

    def test_no_buy_above_threshold(self, evaluator, create_rule, create_lot, create_portfolio):
        """임계치 이상이면 추가 매수 안 함"""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0)]
        lots = [create_lot(ticker="AAPL", buy_price=100.0)]
        # 현재가 97.0 → -3% (> -5%)
        portfolio = create_portfolio(prices={"AAPL": 97.0}, holdings={"AAPL": 5})

        signals = evaluator.evaluate(rules, lots, portfolio)

        buy_signals = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buy_signals) == 0

    def test_no_buy_when_max_lots_reached(self, evaluator, create_rule, create_lot, create_portfolio):
        """max_lots에 도달하면 추가 매수 불가"""
        rules = [create_rule(ticker="AAPL", buy_pct=-5.0, max_lots=2)]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=100.0, buy_date="2026-04-01"),
            create_lot(lot_id="lot_002", ticker="AAPL", buy_price=95.0, buy_date="2026-04-05"),
        ]
        # 현재가 80.0 → 큰 하락이지만 max_lots=2 도달
        portfolio = create_portfolio(cash=10000.0, prices={"AAPL": 80.0}, holdings={"AAPL": 10})

        signals = evaluator.evaluate(rules, lots, portfolio)

        buy_signals = [s for s in signals if s.action == OrderAction.BUY]
        assert len(buy_signals) == 0


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
        """매도 신호가 매수 신호보다 앞에 배치"""
        rules = [
            create_rule(ticker="AAPL", sell_pct=10.0, buy_pct=-5.0, buy_amount=500, max_lots=10),
            create_rule(ticker="MSFT", buy_pct=-5.0, buy_amount=500, max_lots=10),
        ]
        lots = [
            create_lot(lot_id="lot_001", ticker="AAPL", buy_price=90.0, quantity=5, buy_date="2026-04-01"),
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
