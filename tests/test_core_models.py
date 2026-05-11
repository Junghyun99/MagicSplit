# tests/test_core_models.py
import pytest
from src.core.models import (
    StockRule, PositionLot, Portfolio, Order, OrderAction,
    TradeExecution, ExecutionStatus, SplitSignal, TradeSignal, DayResult,
)


class TestStockRule:
    def test_creation(self):
        rule = StockRule(
            ticker="AAPL",
            buy_threshold_pct=-5.0,
            sell_threshold_pct=10.0,
            buy_amount=500,
            max_lots=10,
        )
        assert rule.ticker == "AAPL"
        assert rule.buy_threshold_pct == -5.0
        assert rule.sell_threshold_pct == 10.0
        assert rule.buy_amount == 500
        assert rule.max_lots == 10
        assert rule.enabled is True

    def test_disabled_rule(self):
        rule = StockRule("TSLA", -3.0, 8.0, 300, 5, enabled=False)
        assert rule.enabled is False

    def test_accessor_uses_scalar_when_no_array(self):
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        assert rule.buy_threshold_at(1) == -5.0
        assert rule.buy_threshold_at(5) == -5.0
        assert rule.sell_threshold_at(3) == 10.0
        assert rule.buy_amount_at(2) == 500

    def test_accessor_uses_array_when_present(self):
        rule = StockRule(
            "AAPL",
            buy_threshold_pct=-5.0,      # 배열이 있으면 무시되어야 함
            sell_threshold_pct=10.0,
            buy_amount=500,
            max_lots=10,
            buy_threshold_pcts=[-3.0, -5.0, -7.0, -10.0],
            sell_threshold_pcts=[5.0, 7.0, 10.0, 15.0],
            buy_amounts=[100.0, 200.0, 300.0, 400.0],
        )
        assert rule.buy_threshold_at(1) == -3.0
        assert rule.buy_threshold_at(3) == -7.0
        assert rule.sell_threshold_at(2) == 7.0
        assert rule.buy_amount_at(4) == 400.0

    def test_accessor_clamps_to_last_when_level_exceeds_array(self):
        rule = StockRule(
            "AAPL",
            buy_amount=500,
            max_lots=10,
            buy_threshold_pcts=[-5.0],
            sell_threshold_pcts=[10.0],
            buy_amounts=[100.0, 200.0],
        )
        assert rule.buy_threshold_at(4) == -5.0
        assert rule.sell_threshold_at(9) == 10.0
        assert rule.buy_amount_at(7) == 200.0

    def test_missing_buy_threshold_raises(self):
        with pytest.raises(ValueError, match="buy_threshold"):
            StockRule("AAPL", sell_threshold_pct=10.0, buy_amount=500)

    def test_missing_sell_threshold_raises(self):
        with pytest.raises(ValueError, match="sell_threshold"):
            StockRule("AAPL", buy_threshold_pct=-5.0, buy_amount=500)

    def test_missing_buy_amount_raises(self):
        with pytest.raises(ValueError, match="buy_amount"):
            StockRule("AAPL", buy_threshold_pct=-5.0, sell_threshold_pct=10.0)

    def test_empty_array_rejected(self):
        with pytest.raises(ValueError, match="buy_threshold_pcts"):
            StockRule(
                "AAPL",
                sell_threshold_pct=10.0,
                buy_amount=500,
                buy_threshold_pcts=[],
            )


class TestPositionLot:
    def test_creation(self):
        lot = PositionLot(
            lot_id="lot_001",
            ticker="AAPL",
            buy_price=150.0,
            quantity=3,
            buy_date="2026-04-01",
            level=1,
        )
        assert lot.lot_id == "lot_001"
        assert lot.ticker == "AAPL"
        assert lot.buy_price == 150.0
        assert lot.quantity == 3
        assert lot.buy_date == "2026-04-01"
        assert lot.level == 1

    def test_default_level(self):
        """level 미지정 시 기본값 0 (레거시 호환)"""
        lot = PositionLot("lot_001", "AAPL", 150.0, 3, "2026-04-01")
        assert lot.level == 0


class TestPortfolio:
    def test_total_value_cash_only(self):
        pf = Portfolio(total_cash=10000.0, holdings={}, current_prices={})
        assert pf.total_value == 10000.0

    def test_total_value_with_holdings(self):
        pf = Portfolio(
            total_cash=5000.0,
            holdings={"AAPL": 10, "MSFT": 5},
            current_prices={"AAPL": 150.0, "MSFT": 300.0},
        )
        # 5000 + (10 * 150) + (5 * 300) = 5000 + 1500 + 1500 = 8000
        assert pf.total_value == 8000.0

    def test_total_value_missing_price(self):
        """가격이 없는 종목은 0으로 계산"""
        pf = Portfolio(
            total_cash=1000.0,
            holdings={"UNKNOWN": 100},
            current_prices={},
        )
        assert pf.total_value == 1000.0


class TestOrder:
    def test_buy_order(self):
        order = Order("AAPL", OrderAction.BUY, 10, 150.0)
        assert order.ticker == "AAPL"
        assert order.action == OrderAction.BUY
        assert order.quantity == 10
        assert order.price == 150.0

    def test_sell_order(self):
        order = Order("MSFT", OrderAction.SELL, 5, 300.0)
        assert order.action == OrderAction.SELL


class TestTradeSignal:
    def test_has_orders_true(self):
        signal = TradeSignal(
            orders=[Order("AAPL", OrderAction.BUY, 1, 100.0)],
            reason="test",
        )
        assert signal.has_orders is True

    def test_has_orders_false(self):
        signal = TradeSignal(orders=[], reason="no signal")
        assert signal.has_orders is False


class TestSplitSignal:
    def test_buy_signal(self):
        sig = SplitSignal(
            ticker="AAPL",
            lot_id=None,
            action=OrderAction.BUY,
            quantity=5,
            price=95.0,
            reason="초기 매수 Lv1",
            pct_change=0.0,
            level=1,
        )
        assert sig.lot_id is None
        assert sig.action == OrderAction.BUY
        assert sig.level == 1

    def test_sell_signal(self):
        sig = SplitSignal(
            ticker="AAPL",
            lot_id="lot_001",
            action=OrderAction.SELL,
            quantity=5,
            price=110.0,
            reason="Lv1 +10.0% -> 익절",
            pct_change=10.0,
            level=1,
        )
        assert sig.lot_id == "lot_001"
        assert sig.action == OrderAction.SELL
        assert sig.level == 1

    def test_default_level(self):
        """level 미지정 시 기본값 0"""
        sig = SplitSignal("AAPL", None, OrderAction.BUY, 5, 100.0, "test", 0.0)
        assert sig.level == 0


class TestOrderAction:
    def test_str(self):
        assert str(OrderAction.BUY) == "BUY"
        assert str(OrderAction.SELL) == "SELL"


class TestExecutionStatus:
    def test_str(self):
        assert str(ExecutionStatus.FILLED) == "FILLED"
        assert str(ExecutionStatus.REJECTED) == "REJECTED"
