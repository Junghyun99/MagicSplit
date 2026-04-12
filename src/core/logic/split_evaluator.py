# src/core/logic/split_evaluator.py
import math
from typing import List, Optional

from src.core.interfaces import ILogger
from src.core.models import (
    StockRule,
    PositionLot,
    Portfolio,
    SplitSignal,
    OrderAction,
)


class SplitEvaluator:
    """종목별 차수 기반 분할 매수/매도 신호를 평가한다.

    차수(Level) 시스템:
    - 마지막 차수(가장 높은 level)의 매수가만 기준으로 판단
    - 상승 시 → 마지막 차수 매도 (차수 감소)
    - 하락 시 → 다음 차수 매수 (차수 증가)
    - 한 종목당 한 사이클에 매도 OR 매수 중 하나만 실행
    - 보유 lot이 없으면 → 1차수 초기 매수
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
            매수/매도 신호 리스트 (매도 신호가 먼저, 자금 확보 우선)
        """
        signals: List[SplitSignal] = []
        for rule in stock_rules:
            signals.extend(self.evaluate_stock(rule, positions, portfolio))

        # 매도 신호를 먼저, 매수 신호를 나중에 (자금 확보 우선)
        sell_first = [s for s in signals if s.action == OrderAction.SELL]
        buy_later = [s for s in signals if s.action == OrderAction.BUY]
        return sell_first + buy_later

    def evaluate_stock(
        self,
        rule: StockRule,
        positions: List[PositionLot],
        portfolio: Portfolio,
    ) -> List[SplitSignal]:
        """단일 종목에 대해 매수/매도 신호를 평가한다.

        마지막 차수만 기준으로 판단하며, 매도 OR 매수 중 하나만 반환한다.

        Returns:
            최대 1개의 신호를 담은 리스트
        """
        if not rule.enabled:
            return []

        ticker_lots = [p for p in positions if p.ticker == rule.ticker]
        current_price = portfolio.current_prices.get(rule.ticker, 0)

        if current_price <= 0:
            if self._logger:
                self._logger.warning(
                    f"[{rule.ticker}] 현재가 조회 실패 (price={current_price}). 스킵."
                )
            return []

        # 보유 lot이 없으면 → 1차수 초기 매수
        if not ticker_lots:
            signal = self._evaluate_initial_buy(rule, current_price)
            return [signal] if signal else []

        # 마지막 차수(가장 높은 level) lot 찾기
        last_lot = max(ticker_lots, key=lambda l: l.level)

        # 매도 확인 (우선)
        sell_signal = self._evaluate_sell(rule, last_lot, current_price)
        if sell_signal is not None:
            return [sell_signal]

        # 매수 확인
        buy_signal = self._evaluate_buy(rule, ticker_lots, last_lot, current_price)
        if buy_signal is not None:
            return [buy_signal]

        return []

    def _evaluate_sell(
        self,
        rule: StockRule,
        last_lot: PositionLot,
        current_price: float,
    ) -> Optional[SplitSignal]:
        """마지막 차수 lot의 매도 여부를 평가한다."""
        pct_change = (current_price - last_lot.buy_price) / last_lot.buy_price * 100

        if pct_change >= rule.sell_threshold_pct:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] Lv{last_lot.level}: 매수가 ${last_lot.buy_price:.2f} → "
                    f"현재가 ${current_price:.2f} ({pct_change:+.1f}%) → 익절 매도"
                )
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=last_lot.lot_id,
                action=OrderAction.SELL,
                quantity=last_lot.quantity,
                price=current_price,
                reason=f"Lv{last_lot.level} {pct_change:+.1f}% → 익절",
                pct_change=pct_change,
                level=last_lot.level,
            )
        return None

    def _evaluate_initial_buy(
        self,
        rule: StockRule,
        current_price: float,
    ) -> Optional[SplitSignal]:
        """보유 lot이 없을 때 1차수 초기 매수를 평가한다."""
        buy_qty = math.floor(rule.buy_amount / current_price)
        if buy_qty <= 0:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] 매수 금액(${rule.buy_amount:.2f})으로 "
                    f"1주도 매수 불가 (현재가 ${current_price:.2f}). 스킵."
                )
            return None

        if self._logger:
            self._logger.info(
                f"[{rule.ticker}] 보유 lot 없음 → 초기 매수 Lv1 {buy_qty}주 @${current_price:.2f}"
            )
        return SplitSignal(
            ticker=rule.ticker,
            lot_id=None,
            action=OrderAction.BUY,
            quantity=buy_qty,
            price=current_price,
            reason="초기 매수 Lv1",
            pct_change=0.0,
            level=1,
        )

    def _evaluate_buy(
        self,
        rule: StockRule,
        lots: List[PositionLot],
        last_lot: PositionLot,
        current_price: float,
    ) -> Optional[SplitSignal]:
        """마지막 차수 대비 추가 매수 여부를 평가한다."""
        next_level = last_lot.level + 1

        # max_lots 도달 시 추가 매수 불가
        if next_level > rule.max_lots:
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

        # 마지막 차수 매수가 대비 현재가 비교
        pct_from_last = (current_price - last_lot.buy_price) / last_lot.buy_price * 100

        if pct_from_last <= rule.buy_threshold_pct:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] Lv{last_lot.level} 매수가 ${last_lot.buy_price:.2f} 대비 "
                    f"{pct_from_last:+.1f}% → 추가 매수 Lv{next_level} {buy_qty}주 @${current_price:.2f}"
                )
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=buy_qty,
                price=current_price,
                reason=f"추가 매수 Lv{next_level} (Lv{last_lot.level} 대비 {pct_from_last:+.1f}%)",
                pct_change=pct_from_last,
                level=next_level,
            )

        return None
