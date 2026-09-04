# src/core/logic/position_reconciler.py
from dataclasses import dataclass, replace
from typing import List, Tuple

from src.core.models import PositionLot, Portfolio, StockRule, TradeExecution


# 부동소수 수량(코인) 비교 허용오차. 이보다 작은 차이는 동일로 간주한다.
# 최소 주문 단위(1 satoshi = 1e-8)의 불일치는 잡되, 표현 오차(~1e-12)는 흡수하도록 1e-9.
QTY_MATCH_TOL = 1e-9


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


def drain_lots_by_qty(
    positions: List[PositionLot],
    ticker: str,
    qty: float,
    exe: TradeExecution,
) -> Tuple[List[PositionLot], float]:
    """고차수 lot부터 qty만큼 차감하고, exe에 매수단가/실현손익을 기록한다.

    엔진의 통합 청산과 외부 체결(수동매매) 사후 반영이 공유하는 순수 로직이다.
    positions는 in-place로 수정되며, 동일 리스트를 반환한다.

    Returns: (updated_positions, consumed_qty)
    exe.buy_price, exe.realized_pnl, exe.liquidation_lots 를 in-place 갱신.
    last_sell_prices 갱신 및 regime_state 처리는 호출부 책임.
    """
    qty_left = qty
    lots_desc = sorted(
        [l for l in positions if l.ticker == ticker],
        key=lambda l: l.level, reverse=True,
    )
    total_pnl = 0.0
    total_cost = 0.0
    consumed = 0.0
    breakdown = []
    for lot in lots_desc:
        if qty_left <= 0:
            break
        take = min(qty_left, lot.quantity)
        gross = (exe.price - lot.buy_price) * take
        total_pnl += gross
        total_cost += lot.buy_price * take
        consumed += take
        breakdown.append({
            "lot_id": lot.lot_id, "level": lot.level,
            "buy_price": lot.buy_price, "quantity": take, "_gross": gross,
            "entry_long_regime": lot.entry_long_regime,
            "entry_short_regime": lot.entry_short_regime,
            "entry_trigger": lot.entry_trigger,
        })
        if take >= lot.quantity:
            positions.remove(lot)
        else:
            positions[positions.index(lot)] = replace(
                lot, quantity=lot.quantity - take,
            )
        qty_left -= take

    if consumed > 0:
        exe.buy_price = round(total_cost / consumed, 4)
        exe.realized_pnl = round(total_pnl - exe.fee, 2)
        for item in breakdown:
            lot_fee = exe.fee * (item["quantity"] / consumed)
            item["realized_pnl"] = round(item.pop("_gross") - lot_fee, 2)
        exe.liquidation_lots = breakdown

    return positions, consumed
