# tests/test_infra_broker.py
from src.infra.broker.mock import MockBroker
from src.core.models import Order, OrderAction, ExecutionStatus


class TestMockBroker:
    def test_initial_portfolio(self):
        broker = MockBroker(initial_cash=10000.0)
        pf = broker.get_portfolio()
        assert pf.total_cash == 10000.0
        assert pf.holdings == {}

    def test_fetch_current_prices(self):
        broker = MockBroker(prices={"AAPL": 150.0, "MSFT": 300.0})
        prices = broker.fetch_current_prices(["AAPL", "MSFT"])
        assert prices["AAPL"] == 150.0
        assert prices["MSFT"] == 300.0

    def test_fetch_unknown_ticker_price(self):
        broker = MockBroker()
        prices = broker.fetch_current_prices(["UNKNOWN"])
        assert prices["UNKNOWN"] == 100.0  # 기본값

    def test_buy_order(self):
        broker = MockBroker(initial_cash=10000.0, prices={"AAPL": 100.0})
        orders = [Order("AAPL", OrderAction.BUY, 5, 100.0)]
        executions = broker.execute_orders(orders)

        assert len(executions) == 1
        assert executions[0].action == OrderAction.BUY
        assert executions[0].status == ExecutionStatus.FILLED
        assert executions[0].quantity == 5

        pf = broker.get_portfolio()
        assert pf.holdings["AAPL"] == 5
        assert pf.total_cash < 10000.0

    def test_sell_order(self):
        broker = MockBroker(
            initial_cash=5000.0,
            holdings={"AAPL": 10},
            prices={"AAPL": 100.0},
        )
        orders = [Order("AAPL", OrderAction.SELL, 5, 100.0)]
        executions = broker.execute_orders(orders)

        assert len(executions) == 1
        assert executions[0].action == OrderAction.SELL
        assert executions[0].quantity == 5

        pf = broker.get_portfolio()
        assert pf.holdings["AAPL"] == 5
        assert pf.total_cash > 5000.0

    def test_sell_before_buy(self):
        """매도가 매수보다 먼저 실행됨"""
        broker = MockBroker(
            initial_cash=1000.0,
            holdings={"AAPL": 10},
            prices={"AAPL": 100.0},
        )
        orders = [
            Order("AAPL", OrderAction.BUY, 2, 100.0),
            Order("AAPL", OrderAction.SELL, 5, 100.0),
        ]
        executions = broker.execute_orders(orders)

        # 매도가 먼저 실행되어야 함
        assert executions[0].action == OrderAction.SELL
        assert executions[1].action == OrderAction.BUY

    def test_sell_more_than_holdings(self):
        """보유량보다 많이 매도 시도 → 보유량만큼만 체결"""
        broker = MockBroker(holdings={"AAPL": 3}, prices={"AAPL": 100.0})
        orders = [Order("AAPL", OrderAction.SELL, 10, 100.0)]
        executions = broker.execute_orders(orders)

        assert executions[0].quantity == 3  # 보유량만큼만

    def test_buy_insufficient_cash(self):
        """자금 부족 시 가능한 만큼만 매수"""
        broker = MockBroker(initial_cash=200.0, prices={"AAPL": 100.0})
        orders = [Order("AAPL", OrderAction.BUY, 10, 100.0)]
        executions = broker.execute_orders(orders)

        # 200 * 0.98 / (100 * 1.01) = 1.94 → 1주만 매수 가능
        assert executions[0].quantity <= 2

    def test_multiple_orders(self):
        """여러 주문 동시 처리"""
        broker = MockBroker(
            initial_cash=20000.0,
            prices={"AAPL": 100.0, "MSFT": 200.0},
        )
        orders = [
            Order("AAPL", OrderAction.BUY, 5, 100.0),
            Order("MSFT", OrderAction.BUY, 3, 200.0),
        ]
        executions = broker.execute_orders(orders)

        assert len(executions) == 2
        pf = broker.get_portfolio()
        assert pf.holdings["AAPL"] == 5
        assert pf.holdings["MSFT"] == 3
