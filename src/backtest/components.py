# src/backtest/components.py
from dataclasses import replace
from typing import List, Dict, Optional

from src.core.interfaces import ILogger
from src.core.models import Portfolio, Order, TradeExecution
from src.infra.broker.mock import MockBroker


class BacktestBroker(MockBroker):
    """
    MockBroker를 상속받되, '현재가'를 API가 아닌
    백테스터가 주입해준 가격(simulation_prices)으로 처리한다.
    """

    def __init__(self, initial_cash: float, logger: Optional[ILogger] = None):
        super().__init__(initial_cash=initial_cash, logger=logger)
        self.simulation_prices: Dict[str, float] = {}
        self.current_date = None  # 시뮬레이션 상의 '오늘'

    def set_date(self, date):
        """시뮬레이션 날짜를 설정한다. runner가 매 거래일마다 호출해야 한다."""
        self.current_date = date

    def set_prices(self, prices: Dict[str, float]):
        """시뮬레이션 종가를 설정한다."""
        self.simulation_prices = prices

    def fetch_current_prices(self, tickers: List[str]) -> Dict[str, float]:
        """백테스터가 설정해준 시뮬레이션 가격을 반환한다."""
        return {t: self.simulation_prices.get(t, 0.0) for t in tickers}

    def get_portfolio(self) -> Portfolio:
        """simulation_prices를 current_prices로 반영한 Portfolio를 반환한다."""
        return Portfolio(
            total_cash=self.cash,
            holdings=dict(self.holdings),
            current_prices=dict(self.simulation_prices),
        )

    def execute_orders(self, orders: List[Order]) -> List[TradeExecution]:
        """주문 가격을 simulation_prices로 교체한 뒤 부모 클래스에 위임한다."""
        updated_orders = [
            replace(order, price=self.simulation_prices.get(order.ticker, order.price))
            for order in orders
        ]
        return super().execute_orders(updated_orders)

    def _process_order(self, order: Order) -> TradeExecution:
        """체결 날짜를 실제 현재 시각이 아닌 시뮬레이션 날짜로 기록한다."""
        result = super()._process_order(order)
        if self.current_date is not None:
            if hasattr(self.current_date, 'strftime'):
                sim_date = self.current_date.strftime("%Y-%m-%d")
            else:
                sim_date = str(self.current_date)
            return replace(result, date=sim_date)
        return result
