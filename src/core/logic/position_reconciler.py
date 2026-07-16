# src/core/logic/position_reconciler.py
from dataclasses import dataclass
from typing import List

from src.core.models import PositionLot, Portfolio, StockRule


# 부동소수 수량(코인) 비교 허용오차. 이보다 작은 차이는 동일로 간주한다.
QTY_MATCH_TOL = 1e-8


@dataclass
class QuantityMismatch:
    """브로커 보유수량과 positions.json 차수별 수량 합의 불일치 내역."""
    ticker: str
    broker_qty: float
    positions_qty: float
    lot_count: int
    levels: List[int]

    @property
    def diff(self) -> float:
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
        # 주식은 정수, 코인은 소수 -> int 캐스팅 없이 원 수량으로 비교(허용오차 적용).
        broker_qty = portfolio.holdings.get(ticker, 0)

        if abs(broker_qty - positions_qty) <= QTY_MATCH_TOL:
            continue

        mismatches.append(QuantityMismatch(
            ticker=ticker,
            broker_qty=broker_qty,
            positions_qty=positions_qty,
            lot_count=len(ticker_lots),
            levels=sorted(lot.level for lot in ticker_lots),
        ))

    return mismatches
