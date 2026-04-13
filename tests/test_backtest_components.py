# tests/test_backtest_components.py
import pandas as pd
import pytest
from src.core.models import Order, OrderAction, ExecutionStatus
from src.backtest.components import BacktestBroker


@pytest.fixture
def broker():
    """초기 자금 10000의 BacktestBroker"""
    return BacktestBroker(initial_cash=10000.0)


class TestBacktestBrokerPriceInjection:
    def test_set_prices_and_fetch(self, broker):
        """set_prices 후 fetch_current_prices가 주입된 가격을 반환"""
        broker.set_prices({"AAPL": 150.0, "MSFT": 300.0})
        prices = broker.fetch_current_prices(["AAPL", "MSFT"])
        assert prices == {"AAPL": 150.0, "MSFT": 300.0}

    def test_fetch_missing_ticker_returns_zero(self, broker):
        """주입되지 않은 티커는 0.0 반환"""
        broker.set_prices({"AAPL": 150.0})
        prices = broker.fetch_current_prices(["AAPL", "GOOG"])
        assert prices["GOOG"] == 0.0

    def test_get_portfolio_uses_simulation_prices(self, broker):
        """get_portfolio가 simulation_prices를 current_prices로 반영"""
        broker.set_prices({"AAPL": 150.0})
        portfolio = broker.get_portfolio()
        assert portfolio.current_prices == {"AAPL": 150.0}
        assert portfolio.total_cash == 10000.0


class TestBacktestBrokerDateInjection:
    def test_execution_date_uses_simulation_date(self, broker):
        """체결 날짜가 시뮬레이션 날짜로 기록됨"""
        broker.set_date(pd.Timestamp("2024-03-15"))
        broker.set_prices({"AAPL": 150.0})

        orders = [Order(ticker="AAPL", action=OrderAction.BUY, quantity=2, price=150.0)]
        executions = broker.execute_orders(orders)

        assert len(executions) == 1
        assert executions[0].date == "2024-03-15"

    def test_execution_date_with_string_date(self, broker):
        """문자열 날짜도 정상 처리"""
        broker.set_date("2024-03-15")
        broker.set_prices({"AAPL": 150.0})

        orders = [Order(ticker="AAPL", action=OrderAction.BUY, quantity=1, price=150.0)]
        executions = broker.execute_orders(orders)

        assert executions[0].date == "2024-03-15"


class TestBacktestBrokerOrderExecution:
    def test_execute_buy_uses_simulation_price(self, broker):
        """매수 시 simulation_prices 가격이 사용됨 (order.price 무시)"""
        broker.set_prices({"AAPL": 200.0})
        broker.set_date(pd.Timestamp("2024-01-02"))

        # order.price=100 이지만 simulation_prices=200으로 체결되어야 함
        orders = [Order(ticker="AAPL", action=OrderAction.BUY, quantity=3, price=100.0)]
        executions = broker.execute_orders(orders)

        assert len(executions) == 1
        # 슬리피지 포함: 200 * 1.001 = 200.2
        assert abs(executions[0].price - 200.2) < 0.1

    def test_execute_sell(self, broker):
        """매도 정상 처리"""
        broker.set_prices({"AAPL": 150.0})
        broker.set_date(pd.Timestamp("2024-01-02"))

        # 먼저 매수
        buy_orders = [Order(ticker="AAPL", action=OrderAction.BUY, quantity=5, price=150.0)]
        broker.execute_orders(buy_orders)

        # 매도
        sell_orders = [Order(ticker="AAPL", action=OrderAction.SELL, quantity=3, price=150.0)]
        sell_execs = broker.execute_orders(sell_orders)

        assert len(sell_execs) == 1
        assert sell_execs[0].action == OrderAction.SELL
        assert sell_execs[0].quantity == 3

    def test_holdings_update_after_buy(self, broker):
        """매수 후 보유 수량이 증가"""
        broker.set_prices({"AAPL": 100.0})
        broker.set_date(pd.Timestamp("2024-01-02"))

        orders = [Order(ticker="AAPL", action=OrderAction.BUY, quantity=5, price=100.0)]
        broker.execute_orders(orders)

        assert broker.holdings["AAPL"] == 5

    def test_cash_decreases_after_buy(self, broker):
        """매수 후 현금이 감소"""
        broker.set_prices({"AAPL": 100.0})
        broker.set_date(pd.Timestamp("2024-01-02"))

        orders = [Order(ticker="AAPL", action=OrderAction.BUY, quantity=5, price=100.0)]
        broker.execute_orders(orders)

        # 100 * 1.001 * 5 + fee(0.25%) ≈ 501.75
        assert broker.cash < 10000.0

    def test_portfolio_value_reflects_holdings(self, broker):
        """포트폴리오 가치가 보유 종목을 반영"""
        broker.set_prices({"AAPL": 100.0})
        broker.set_date(pd.Timestamp("2024-01-02"))

        orders = [Order(ticker="AAPL", action=OrderAction.BUY, quantity=5, price=100.0)]
        broker.execute_orders(orders)

        broker.set_prices({"AAPL": 110.0})
        portfolio = broker.get_portfolio()

        # 보유 가치: 5 * 110 = 550
        assert portfolio.holdings["AAPL"] == 5
        assert portfolio.current_prices["AAPL"] == 110.0
