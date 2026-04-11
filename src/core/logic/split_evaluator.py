# src/core/logic/split_evaluator.py
import math
from typing import List, Optional, Tuple

from src.core.interfaces import ILogger
from src.core.models import (
    StockRule,
    PositionLot,
    Portfolio,
    SplitSignal,
    OrderAction,
)


class SplitEvaluator:
    """종목별 분할 매수/매도 신호를 평가한다.

    각 종목의 StockRule에 따라:
    - 기존 lot별로 매수가 대비 현재가 %를 계산
    - sell_threshold_pct 이상 상승 → 매도 신호
    - buy_threshold_pct 이하 하락 → 추가 매수 신호 (max_lots 미만일 때)
    - 보유 lot이 없으면 → 초기 매수 신호
    """

    def __init__(self, logger: Optional[ILogger] = None):
        self._logger = logger

    def evaluate(
        self,
        stock_rules: List[StockRule],
        positions: List[PositionLot],
        portfolio: Portfolio,
    ) -> List[SplitSignal]:
        """모든 종목에 대해 매수/매도 신호를 평가한다.

        Args:
            stock_rules: config.json에서 로드된 종목별 매매 규칙
            positions: 현재 보유 중인 분할 포지션 목록
            portfolio: 현재 포트폴리오 (현금, 보유 종목, 현재가)

        Returns:
            매수/매도 신호 리스트 (매도 신호가 먼저)
        """
        signals: List[SplitSignal] = []

        for rule in stock_rules:
            if not rule.enabled:
                continue

            ticker_lots = [p for p in positions if p.ticker == rule.ticker]
            current_price = portfolio.current_prices.get(rule.ticker, 0)

            if current_price <= 0:
                if self._logger:
                    self._logger.warning(
                        f"[{rule.ticker}] 현재가 조회 실패 (price={current_price}). 스킵."
                    )
                continue

            # 1. 기존 lot별 매도 판단
            sell_signals = self._evaluate_sells(rule, ticker_lots, current_price)
            signals.extend(sell_signals)

            # 2. 매수 판단 (초기 매수 또는 추가 매수)
            buy_signal = self._evaluate_buy(rule, ticker_lots, current_price)
            if buy_signal is not None:
                signals.append(buy_signal)

        # 매도 신호를 먼저, 매수 신호를 나중에 (자금 확보 우선)
        sell_first = [s for s in signals if s.action == OrderAction.SELL]
        buy_later = [s for s in signals if s.action == OrderAction.BUY]
        return sell_first + buy_later

    def _evaluate_sells(
        self,
        rule: StockRule,
        lots: List[PositionLot],
        current_price: float,
    ) -> List[SplitSignal]:
        """기존 lot별 매도 여부를 평가한다."""
        signals = []
        for lot in lots:
            pct_change = (current_price - lot.buy_price) / lot.buy_price * 100
            if pct_change >= rule.sell_threshold_pct:
                if self._logger:
                    self._logger.info(
                        f"[{rule.ticker}] {lot.lot_id}: 매수가 ${lot.buy_price:.2f} → "
                        f"현재가 ${current_price:.2f} ({pct_change:+.1f}%) → 익절 매도"
                    )
                signals.append(SplitSignal(
                    ticker=rule.ticker,
                    lot_id=lot.lot_id,
                    action=OrderAction.SELL,
                    quantity=lot.quantity,
                    price=current_price,
                    reason=f"{lot.lot_id} {pct_change:+.1f}% → 익절",
                    pct_change=pct_change,
                ))
        return signals

    def _evaluate_buy(
        self,
        rule: StockRule,
        lots: List[PositionLot],
        current_price: float,
    ) -> Optional[SplitSignal]:
        """추가 매수 또는 초기 매수 여부를 평가한다."""
        # max_lots 도달 시 추가 매수 불가
        if len(lots) >= rule.max_lots:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] max_lots({rule.max_lots}) 도달. 추가 매수 불가."
                )
            return None

        # 매수 수량 계산
        buy_qty = math.floor(rule.buy_amount / current_price)
        if buy_qty <= 0:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] 매수 금액(${rule.buy_amount:.2f})으로 "
                    f"1주도 매수 불가 (현재가 ${current_price:.2f}). 스킵."
                )
            return None

        # 보유 lot이 없으면 → 초기 매수
        if not lots:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] 보유 lot 없음 → 초기 매수 {buy_qty}주 @${current_price:.2f}"
                )
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=buy_qty,
                price=current_price,
                reason="초기 매수",
                pct_change=0.0,
            )

        # 기존 lot 중 가장 최근 lot의 매수가 대비 현재가 비교
        last_lot = max(lots, key=lambda l: l.buy_date)
        pct_from_last = (current_price - last_lot.buy_price) / last_lot.buy_price * 100

        if pct_from_last <= rule.buy_threshold_pct:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] 최근 매수가 ${last_lot.buy_price:.2f} 대비 "
                    f"{pct_from_last:+.1f}% → 추가 매수 {buy_qty}주 @${current_price:.2f}"
                )
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=buy_qty,
                price=current_price,
                reason=f"추가 매수 (최근 lot 대비 {pct_from_last:+.1f}%)",
                pct_change=pct_from_last,
            )

        return None
