# src/core/logic/position_reconciler.py
from dataclasses import dataclass
from typing import List

from src.core.models import PositionLot, Portfolio, StockRule


@dataclass
class QuantityMismatch:
    """브로커 보유수량과 positions.json 차수별 수량 합의 불일치 내역."""
    ticker: str
    broker_qty: int
    positions_qty: int
    lot_count: int
    levels: List[int]

    @property
    def diff(self) -> int:
        """broker_qty - positions_qty (양수면 브로커가 많음)."""
        return self.broker_qty - self.positions_qty


def detect_mismatches(
    positions: List[PositionLot],
    portfolio: Portfolio,
    rules: List[StockRule],
) -> List[QuantityMismatch]:
    """브로커 보유수량과 positions.json 수량 합의 불일치 종목을 찾아 반환한다.

    검사 대상: rules 에 정의된 모든 ticker ∪ positions 에 등장하는 ticker.
    rules 에 정의되지 않고 positions 에도 없는, 단순히 브로커에만 있는 ticker 는
    봇의 관리 대상이 아니므로 무시한다 (수동으로 별도 매수한 종목 등).
    """
    rule_tickers = {r.ticker for r in rules}
    position_tickers = {lot.ticker for lot in positions}
    target_tickers = rule_tickers | position_tickers

    mismatches: List[QuantityMismatch] = []
    for ticker in sorted(target_tickers):
        ticker_lots = [lot for lot in positions if lot.ticker == ticker]
        positions_qty = sum(lot.quantity for lot in ticker_lots)
        broker_qty = int(portfolio.holdings.get(ticker, 0))

        if broker_qty == positions_qty:
            continue

        mismatches.append(QuantityMismatch(
            ticker=ticker,
            broker_qty=broker_qty,
            positions_qty=positions_qty,
            lot_count=len(ticker_lots),
            levels=sorted(lot.level for lot in ticker_lots),
        ))

    return mismatches
