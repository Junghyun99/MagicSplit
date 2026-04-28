# src/core/logic/split_evaluator.py
import math
from typing import Dict, List, Optional

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
        last_sell_prices: Optional[Dict[str, float]] = None,
    ) -> List[SplitSignal]:
        """모든 종목에 대해 매수/매도 신호를 평가한다.

        Args:
            stock_rules: config.json에서 로드된 종목별 매매 규칙
            positions: 현재 보유 중인 분할 포지션 목록
            portfolio: 현재 포트폴리오 (현금, 보유 종목, 현재가)
            last_sell_prices: 티커별 직전(전량 청산) 매도 단가.
                재진입 가드 평가에만 사용. 미상이면 생략.

        Returns:
            매수/매도 신호 리스트 (매도 신호가 먼저, 자금 확보 우선)
        """
        signals: List[SplitSignal] = []
        for rule in stock_rules:
            signals.extend(
                self.evaluate_stock(rule, positions, portfolio, last_sell_prices)
            )

        # 매도 신호를 먼저, 매수 신호를 나중에 (자금 확보 우선)
        sell_first = [s for s in signals if s.action == OrderAction.SELL]
        buy_later = [s for s in signals if s.action == OrderAction.BUY]
        return sell_first + buy_later

    def evaluate_stock(
        self,
        rule: StockRule,
        positions: List[PositionLot],
        portfolio: Portfolio,
        last_sell_prices: Optional[Dict[str, float]] = None,
    ) -> List[SplitSignal]:
        """단일 종목에 대해 매수/매도 신호를 평가한다.

        마지막 차수만 기준으로 판단하며, 매도 OR 매수 중 하나만 반환한다.

        Args:
            last_sell_prices: 티커별 직전 매도 단가 (재진입 가드용).
                상위 호출부(엔진)에서 history/repo로부터 조회해 전달한다.

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
            last_sell_price = (
                last_sell_prices.get(rule.ticker) if last_sell_prices else None
            )
            signal = self._evaluate_initial_buy(
                rule, current_price, last_sell_price=last_sell_price,
            )
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
        """마지막 차수 lot의 매도 여부를 평가한다.
        트레일링 스톱이 설정되어 있다면 활성화 후 하락을 추적하며 매도한다."""
        pct_change = (current_price - last_lot.buy_price) / last_lot.buy_price * 100
        sell_threshold = rule.sell_threshold_at(last_lot.level)
        trailing_drop = rule.trailing_drop_at(last_lot.level)

        if trailing_drop is not None:
            # 트레일링 스톱 로직
            # 1. 활성화 조건 충족 (매도 임계치 도달) 또는 이미 활성화된 상태
            if pct_change >= sell_threshold or last_lot.trailing_highest_price is not None:
                was_inactive = last_lot.trailing_highest_price is None

                # 2. 최고가 갱신
                if was_inactive or current_price > last_lot.trailing_highest_price:
                    old_highest = last_lot.trailing_highest_price
                    last_lot.trailing_highest_price = current_price
                    stop_price = current_price * (1 - trailing_drop / 100)
                    if self._logger:
                        if was_inactive:
                            self._logger.info(
                                f"[{rule.ticker}] Lv{last_lot.level}: "
                                f"🔔 트레일링 스톱 활성화 "
                                f"(매도조건 +{sell_threshold:.0f}% 도달, "
                                f"현재가 ${current_price:,.0f}, "
                                f"스톱가 ${stop_price:,.0f}, "
                                f"하락허용 {trailing_drop}%)"
                            )
                        else:
                            self._logger.info(
                                f"[{rule.ticker}] Lv{last_lot.level}: "
                                f"📈 트레일링 고점 갱신 "
                                f"${old_highest:,.0f} → ${current_price:,.0f} "
                                f"(매수가 대비 {pct_change:+.1f}%, "
                                f"스톱가 ${stop_price:,.0f})"
                            )
                else:
                    # 고점 미갱신: 보합 또는 소폭 하락 중 (추적 상태 로그)
                    drop_pct_now = (last_lot.trailing_highest_price - current_price) / last_lot.trailing_highest_price * 100
                    stop_price = last_lot.trailing_highest_price * (1 - trailing_drop / 100)
                    if self._logger:
                        self._logger.info(
                            f"[{rule.ticker}] Lv{last_lot.level}: "
                            f"⏳ 트레일링 추적 중 "
                            f"(현재가 ${current_price:,.0f}, "
                            f"고점 ${last_lot.trailing_highest_price:,.0f}, "
                            f"고점대비 -{drop_pct_now:.1f}%, "
                            f"스톱가 ${stop_price:,.0f})"
                        )

                # 3. 고점 대비 하락폭 계산
                drop_pct = (last_lot.trailing_highest_price - current_price) / last_lot.trailing_highest_price * 100

                # 4. 하락 허용치 도달 시 매도
                if drop_pct >= trailing_drop:
                    profit_pct = (current_price - last_lot.buy_price) / last_lot.buy_price * 100
                    if self._logger:
                        self._logger.info(
                            f"[{rule.ticker}] Lv{last_lot.level}: "
                            f"🔻 트레일링 스톱 매도 "
                            f"(매수가 ${last_lot.buy_price:,.0f} → "
                            f"고점 ${last_lot.trailing_highest_price:,.0f} → "
                            f"현재가 ${current_price:,.0f}, "
                            f"고점대비 -{drop_pct:.1f}%, "
                            f"수익률 {profit_pct:+.1f}%)"
                        )
                    return SplitSignal(
                        ticker=rule.ticker,
                        lot_id=last_lot.lot_id,
                        action=OrderAction.SELL,
                        quantity=last_lot.quantity,
                        price=current_price,
                        reason=f"Lv{last_lot.level} 트레일링 스톱 매도 (고점 대비 -{drop_pct:.2f}%)",
                        pct_change=pct_change,
                        level=last_lot.level,
                        buy_price=last_lot.buy_price,
                    )
            return None
        else:
            # 일반 고정 익절 로직 (기존)
            if pct_change >= sell_threshold:
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
                    buy_price=last_lot.buy_price,
                )
            return None

    def _evaluate_initial_buy(
        self,
        rule: StockRule,
        current_price: float,
        last_sell_price: Optional[float] = None,
    ) -> Optional[SplitSignal]:
        """보유 lot이 없을 때 1차수 초기 매수를 평가한다."""
        if not self._passes_reentry_guard(rule, current_price, last_sell_price):
            return None

        buy_amount = rule.buy_amount_at(1)
        buy_qty = math.floor(buy_amount / current_price)
        if buy_qty <= 0:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] 매수 금액(${buy_amount:.2f})으로 "
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

    def _passes_reentry_guard(
        self,
        rule: StockRule,
        current_price: float,
        last_sell_price: Optional[float],
    ) -> bool:
        """1차수 재진입 가드: 직전 매도가 대비 충분히 하락했는지 확인한다.

        예: rule.reentry_guard_pct = -0.1 이면
            current_price <= last_sell_price * (1 - 0.001) 일 때만 진입 허용.

        Args:
            rule: 종목 규칙 (reentry_guard_pct 포함)
            current_price: 현재가
            last_sell_price: 직전 (전량 청산) 매도 단가. None이면 가드 미적용.

        Returns:
            True: 진입 허용 (가드 통과 또는 가드 미설정).
            False: 진입 차단.
        """
        if rule.reentry_guard_pct is None:
            return True
        if last_sell_price is None or last_sell_price <= 0:
            return True

        pct_from_sell = (current_price - last_sell_price) / last_sell_price * 100
        if pct_from_sell <= rule.reentry_guard_pct:
            return True

        if self._logger:
            self._logger.info(
                f"[{rule.ticker}] 재진입 가드: 직전 매도가 {last_sell_price:.2f} 대비 "
                f"{pct_from_sell:+.2f}% > 임계 {rule.reentry_guard_pct:+.2f}% → 진입 보류"
            )
        return False

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

        # 매수 수량 계산 (다음 차수 기준 금액)
        buy_amount = rule.buy_amount_at(next_level)
        buy_qty = math.floor(buy_amount / current_price)
        if buy_qty <= 0:
            if self._logger:
                self._logger.info(
                    f"[{rule.ticker}] 매수 금액(${buy_amount:.2f})으로 "
                    f"1주도 매수 불가 (현재가 ${current_price:.2f}). 스킵."
                )
            return None

        # 마지막 차수 매수가 대비 현재가 비교 (임계치는 마지막 차수 기준)
        pct_from_last = (current_price - last_lot.buy_price) / last_lot.buy_price * 100
        buy_threshold = rule.buy_threshold_at(last_lot.level)

        if pct_from_last <= buy_threshold:
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
