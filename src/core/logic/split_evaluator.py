# src/core/logic/split_evaluator.py
import math
from typing import Dict, List, Optional

from src.core.interfaces import ILogger
from src.core.logic.regime import Regime, classify
from src.core.models import (
    StockRule,
    PositionLot,
    Portfolio,
    SplitSignal,
    OrderAction,
)
from src.utils.ticker_reader import display_ticker
from src.utils.currency import format_money

# 상승 레짐 진입 확정에 필요한 연속 UPTREND 판정 횟수 (독립 조정 가능)
REGIME_CONFIRM_BARS = 2
# 하락 추세 래치 진입/탈출 확정에 필요한 연속 판정 횟수 (독립 조정 가능)
DOWNTREND_CONFIRM_BARS = 2


class SplitEvaluator:
    """종목별 차수 기반 분할 매수/매도 신호를 평가한다.

    차수(Level) 시스템:
    - 마지막 차수(가장 높은 level)의 매수가만 기준으로 판단
    - 상승 시 -> 마지막 차수 매도 (차수 감소)
    - 하락 시 -> 다음 차수 매수 (차수 증가)
    - 한 종목당 한 사이클에 매도 OR 매수 중 하나만 실행
    - 보유 lot이 없으면 -> 1차수 초기 매수
    """

    def __init__(self, logger: Optional[ILogger] = None):
        self._logger = logger
        self.price_anomaly_threshold = 30.0  # % 이격 발생 시 차단

    def evaluate(
        self,
        stock_rules: List[StockRule],
        positions: List[PositionLot],
        portfolio: Portfolio,
        last_sell_prices: Optional[Dict[str, float]] = None,
    ) -> List[SplitSignal]:
        """모든 종목에 대해 매수/매도 신호를 평가한다.

        Args:
            stock_rules: 설정 파일에서 로드된 종목별 매매 규칙
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
        ohlc_window=None,
        regime_state: Optional[dict] = None,
    ) -> List[SplitSignal]:
        """단일 종목에 대해 매수/매도 신호를 평가한다.

        마지막 차수만 기준으로 판단하며, 매도 OR 매수 중 하나만 반환한다.

        Args:
            last_sell_prices: 티커별 직전 매도 단가.
                재진입 가드 및 동적 재매수 기준에 사용.
                상위 호출부(엔진)에서 history/repo로부터 조회해 전달한다.

        Returns:
            최대 1개의 신호를 담은 리스트
        """
        if not rule.enabled:
            return []

        ticker_lots = [p for p in positions if p.ticker == rule.ticker]
        current_price = portfolio.current_prices.get(rule.ticker, 0)

        if current_price <= 0:
            reason = f"현재가 조회 실패 (price={current_price}). 종목 코드/API 상태 확인 필요"
            if self._logger:
                self._logger.warning(f"[{display_ticker(rule.ticker)}] {reason}")
            return [SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=0,
                price=0.0,
                reason=reason,
                pct_change=0.0,
                is_blocked=True,
            )]

        # 레짐 분류: regime_enabled이면 ticker_lots 유무와 무관하게 reading/regime_st 확보.
        reading = None
        regime_st: dict = {}
        downtrend_blocked = False
        if rule.regime_enabled and ohlc_window is not None:
            reading = classify(
                ohlc_window,
                adx_trend_threshold=rule.regime_adx_trend,
                adx_range_threshold=rule.regime_adx_range,
                chandelier_k=rule.trendbreak_chandelier_k,
                chandelier_lookback=rule.trendbreak_chandelier_lookback,
                swing_lookback=rule.uptrend_swing_lookback,
                min_bars=rule.regime_min_bars,
            )
            regime_st = regime_state.setdefault(rule.ticker, {}) if regime_state is not None else {}
            # 하락 래치 갱신: UPTREND 모드 중에도 항상 실행해 레짐 탈출 후 즉시 차단 가능하게 함
            downtrend_blocked = self._resolve_downtrend_block(reading, regime_st, rule.ticker)
            # 상승 레짐: 보유 lot이 있을 때만 누적 매수/이탈 청산 경로
            if ticker_lots and self._resolve_regime(reading, regime_st, rule.ticker, current_price) == Regime.UPTREND:
                return self._evaluate_uptrend(
                    rule, ticker_lots, current_price, reading, regime_st, portfolio
                )

        # 보유 lot이 없으면 -> 1차수 초기 매수
        if not ticker_lots:
            if downtrend_blocked:
                reason = "DOWNTREND 확정 - 신규 진입 차단"
                if self._logger:
                    self._logger.info(f"[{display_ticker(rule.ticker)}] {reason}")
                return [SplitSignal(
                    ticker=rule.ticker,
                    lot_id=None,
                    action=OrderAction.BUY,
                    quantity=0,
                    price=current_price,
                    reason=reason,
                    pct_change=0.0,
                    is_blocked=True,
                )]
            last_sell_price = (
                last_sell_prices.get(rule.ticker) if last_sell_prices else None
            )
            signal = self._evaluate_initial_buy(
                rule, current_price, last_sell_price=last_sell_price,
                portfolio=portfolio,
            )
            return [signal] if signal else []

        # 마지막 차수(가장 높은 level) lot 찾기
        last_lot = max(ticker_lots, key=lambda l: l.level)

        # 매도 확인 (우선 - 하락 중에도 이익실현/트레일링 매도는 정상 작동)
        self._trailing_info_signal = None
        sell_signal = self._evaluate_sell(rule, last_lot, current_price)
        if sell_signal is not None:
            return [sell_signal]

        # 트레일링 스톱 활성화 시 info 신호 수집
        result: List[SplitSignal] = []
        if self._trailing_info_signal is not None:
            result.append(self._trailing_info_signal)
            self._trailing_info_signal = None

        # 하락 레짐 추가 매수 차단
        if downtrend_blocked:
            reason = "DOWNTREND 확정 - 추가 매수 차단"
            if self._logger:
                self._logger.info(f"[{display_ticker(rule.ticker)}] {reason}")
            result.append(SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=0,
                price=current_price,
                reason=reason,
                pct_change=0.0,
                is_blocked=True,
            ))
            return result

        # 매수 확인 (동적 재매수 기준 적용)
        last_sell_price = (
            last_sell_prices.get(rule.ticker) if last_sell_prices else None
        )
        buy_signal = self._evaluate_buy(
            rule, ticker_lots, last_lot, current_price,
            last_sell_price=last_sell_price,
            portfolio=portfolio,
        )
        if buy_signal is not None:
            result.append(buy_signal)

        return result

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
                                f"[{display_ticker(rule.ticker)}] Lv{last_lot.level}: "
                                f"트레일링 스톱 활성화 "
                                f"(매도조건 +{sell_threshold:.0f}% 도달, "
                                f"현재가 {format_money(current_price, rule.market_type)}, "
                                f"스톱가 {format_money(stop_price, rule.market_type)}, "
                                f"하락허용 {trailing_drop}%)"
                            )
                        else:
                            self._logger.info(
                                f"[{display_ticker(rule.ticker)}] Lv{last_lot.level}: "
                                f"트레일링 고점 갱신 "
                                f"{format_money(old_highest, rule.market_type)} -> "
                                f"{format_money(current_price, rule.market_type)} "
                                f"(매수가 대비 {pct_change:+.1f}%, "
                                f"스톱가 {format_money(stop_price, rule.market_type)})"
                            )
                    # 최초 활성화 시 정보성 알림 신호 생성
                    if was_inactive:
                        info_reason = (
                            f"Lv{last_lot.level}: 트레일링 스톱 활성화 - "
                            f"현재가 {format_money(current_price, rule.market_type)} "
                            f"(매수가 대비 {pct_change:+.1f}%), "
                            f"스톱가 {format_money(stop_price, rule.market_type)}"
                        )
                        self._trailing_info_signal = SplitSignal(
                            ticker=rule.ticker,
                            lot_id=last_lot.lot_id,
                            action=OrderAction.SELL,
                            quantity=0,
                            price=current_price,
                            reason=info_reason,
                            pct_change=pct_change,
                            level=last_lot.level,
                            buy_price=last_lot.buy_price,
                            is_info=True,
                        )
                else:
                    # 고점 미갱신: 보합 또는 소폭 하락 중 (추적 상태 로그)
                    drop_pct_now = (last_lot.trailing_highest_price - current_price) / last_lot.trailing_highest_price * 100
                    stop_price = last_lot.trailing_highest_price * (1 - trailing_drop / 100)
                    if self._logger:
                        self._logger.info(
                            f"[{display_ticker(rule.ticker)}] Lv{last_lot.level}: "
                            f"트레일링 추적 중 "
                            f"(현재가 {format_money(current_price, rule.market_type)}, "
                            f"고점 {format_money(last_lot.trailing_highest_price, rule.market_type)}, "
                            f"고점대비 -{drop_pct_now:.1f}%, "
                            f"스톱가 {format_money(stop_price, rule.market_type)})"
                        )

                # 3. 고점 대비 하락폭 계산
                drop_pct = (last_lot.trailing_highest_price - current_price) / last_lot.trailing_highest_price * 100

                # 4. 하락 허용치 도달 시 매도
                if drop_pct >= trailing_drop:
                    profit_pct = (current_price - last_lot.buy_price) / last_lot.buy_price * 100
                    if self._logger:
                        self._logger.info(
                            f"[{display_ticker(rule.ticker)}] Lv{last_lot.level}: "
                            f"트레일링 스톱 매도 "
                            f"(매수가 {format_money(last_lot.buy_price, rule.market_type)} -> "
                            f"고점 {format_money(last_lot.trailing_highest_price, rule.market_type)} -> "
                            f"현재가 {format_money(current_price, rule.market_type)}, "
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
                        f"[{display_ticker(rule.ticker)}] Lv{last_lot.level}: "
                        f"매수가 {format_money(last_lot.buy_price, rule.market_type)} -> "
                        f"현재가 {format_money(current_price, rule.market_type)} "
                        f"({pct_change:+.1f}%) -> 익절 매도"
                    )
                return SplitSignal(
                    ticker=rule.ticker,
                    lot_id=last_lot.lot_id,
                    action=OrderAction.SELL,
                    quantity=last_lot.quantity,
                    price=current_price,
                    reason=f"Lv{last_lot.level} {pct_change:+.1f}% -> 익절",
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
        portfolio: Optional[Portfolio] = None,
    ) -> Optional[SplitSignal]:
        """보유 lot이 없을 때 1차수 초기 매수를 평가한다."""
        passed, reason = self._passes_reentry_guard(rule, current_price, last_sell_price)
        if not passed:
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=0,
                price=current_price,
                reason=reason,
                pct_change=0.0,
                level=1,
                is_info=True,
            )

        buy_amount = rule.buy_amount_at(1)
        buy_qty = math.floor(buy_amount / current_price)
        if buy_qty <= 0:
            reason = (
                f"buy_amount({format_money(buy_amount, rule.market_type)}) < "
                f"현재가({format_money(current_price, rule.market_type)}) -> 1주도 매수 불가. "
                f"buy_amount 상향 조정 필요"
            )
            if self._logger:
                self._logger.info(f"[{display_ticker(rule.ticker)}] {reason}")
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=0,
                price=current_price,
                reason=reason,
                pct_change=0.0,
                level=1,
                is_blocked=True,
            )

        # 잔고 부족 체크
        passed, reason = self._passes_cash_guard(rule, current_price, buy_qty, portfolio)
        if not passed:
            if self._logger:
                self._logger.info(f"[{display_ticker(rule.ticker)}] {reason} -> 매수 보류")
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=buy_qty,
                price=current_price,
                reason=reason,
                pct_change=0.0,
                level=1,
                is_blocked=True,
            )

        # 비중 상한 체크
        passed, reason = self._passes_exposure_guard(
            rule, [], current_price, buy_qty, portfolio
        )
        if not passed:
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=buy_qty,
                price=current_price,
                reason=reason,
                pct_change=0.0,
                level=1,
                is_blocked=True,
            )

        if self._logger:
            self._logger.info(
                f"[{display_ticker(rule.ticker)}] 보유 lot 없음 -> "
                f"초기 매수 Lv1 {buy_qty}주 @{format_money(current_price, rule.market_type)}"
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
            return True, ""
        if last_sell_price is None or last_sell_price <= 0:
            return True, ""

        pct_from_sell = (current_price - last_sell_price) / last_sell_price * 100

        # [방어선 2] 가격 이격 과다 체크 (액면분할/병합 의심)
        if abs(pct_from_sell) >= self.price_anomaly_threshold:
            reason = (
                f"가격 이격 과다({pct_from_sell:+.1f}%): 액면분할/병합 확인 필요 "
                f"(직전매도 {format_money(last_sell_price, rule.market_type)} "
                f"vs 현재 {format_money(current_price, rule.market_type)})"
            )
            if self._logger:
                self._logger.warning(f"[{rule.ticker}] {reason}")
            return False, reason

        if pct_from_sell <= rule.reentry_guard_pct:
            return True, ""

        reason = (
            f"재진입 가드: 직전 매도가 {format_money(last_sell_price, rule.market_type)} 대비 "
            f"{pct_from_sell:+.2f}% > 임계 {rule.reentry_guard_pct:+.2f}% -> 진입 대기 중"
        )
        if self._logger:
            self._logger.info(f"[{display_ticker(rule.ticker)}] {reason}")
        return False, reason

    def _passes_exposure_guard(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
        buy_qty: int,
        portfolio: Optional[Portfolio],
    ) -> bool:
        """종목별 투입 비중 상한 가드: 매수 후 비중이 상한을 넘는지 확인한다.

        (현재 보유 평가액 + 매수 예정 금액) / 계좌 총 자산 > max_exposure_pct
        이면 매수를 차단한다.

        Args:
            rule: 종목 규칙 (max_exposure_pct 포함)
            ticker_lots: 해당 종목의 현재 보유 lot 목록
            current_price: 현재가
            buy_qty: 매수 예정 수량
            portfolio: 현재 포트폴리오 (비중 계산용)

        Returns:
            (True, ""): 매수 허용 (가드 통과 또는 미설정).
            (False, reason): 매수 차단 및 사유.
        """
        if rule.max_exposure_pct is None:
            return True, ""
        if portfolio is None:
            return True, ""

        total_value = portfolio.total_value
        if total_value <= 0:
            return True, ""

        # 현재 보유 평가액
        current_holding_value = sum(
            lot.quantity * current_price for lot in ticker_lots
        )
        # 매수 후 예상 평가액
        buy_value = buy_qty * current_price
        after_exposure = current_holding_value + buy_value
        after_pct = after_exposure / total_value * 100

        if after_pct > rule.max_exposure_pct:
            current_pct = current_holding_value / total_value * 100
            reason = (
                f"비중 상한 초과: 현재 {current_pct:.1f}% + 매수 예정 {after_pct - current_pct:.1f}% "
                f"= {after_pct:.1f}% > 상한 {rule.max_exposure_pct:.1f}%"
            )
            if self._logger:
                self._logger.info(f"[{display_ticker(rule.ticker)}] {reason} -> 매수 보류")
            return False, reason

        return True, ""

    def _passes_cash_guard(
        self,
        rule: StockRule,
        current_price: float,
        buy_qty: int,
        portfolio: Optional[Portfolio],
    ) -> tuple[bool, str]:
        """잔고 부족 여부를 확인한다."""
        if portfolio is None:
            return True, ""

        # 1. 1주도 살 수 없는 경우
        if portfolio.total_cash < current_price:
            reason = (
                f"현금 부족: 보유 현금 {format_money(portfolio.total_cash, rule.market_type)} "
                f"< 현재가 {format_money(current_price, rule.market_type)} (1주도 매수 불가)"
            )
            return False, reason

        # 2. 계획된 수량을 살 현금이 부족한 경우
        required_cash = buy_qty * current_price
        if portfolio.total_cash < required_cash:
            reason = (
                f"현금 부족: 보유 현금 {format_money(portfolio.total_cash, rule.market_type)} "
                f"< 매수 예정 금액 {format_money(required_cash, rule.market_type)} ({buy_qty}주)"
            )
            return False, reason

        return True, ""

    def _evaluate_buy(
        self,
        rule: StockRule,
        lots: List[PositionLot],
        last_lot: PositionLot,
        current_price: float,
        last_sell_price: Optional[float] = None,
        portfolio: Optional[Portfolio] = None,
    ) -> Optional[SplitSignal]:
        """마지막 차수 대비 추가 매수 여부를 평가한다.

        동적 재매수 기준(Dynamic Re-entry):
        직전 매도가(last_sell_price)가 마지막 차수 매수가보다 높으면
        매도가를 기준으로 사용한다. 트레일링 스톱으로 높게 매도한 뒤
        원래 그리드까지 기다리지 않고, 매도가 대비 하락 시 재매수.
        """
        next_level = last_lot.level + 1

        # max_lots 도달 시 추가 매수 불가
        if next_level > rule.max_lots:
            pct_from_buy = (current_price - last_lot.buy_price) / last_lot.buy_price * 100
            reason = (
                f"max_lots({rule.max_lots}) 도달: "
                f"현재가 {format_money(current_price, rule.market_type)} "
                f"(Lv{last_lot.level} 대비 {pct_from_buy:+.1f}%) "
                f"-> 추가 하락 대응 불가"
            )
            if self._logger:
                self._logger.info(f"[{display_ticker(rule.ticker)}] {reason}")
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=0,
                price=current_price,
                reason=reason,
                pct_change=pct_from_buy,
                level=last_lot.level,
                is_blocked=True,
            )

        # 매수 수량 계산 (다음 차수 기준 금액)
        buy_amount = rule.buy_amount_at(next_level)
        buy_qty = math.floor(buy_amount / current_price)
        if buy_qty <= 0:
            reason = (
                f"buy_amount({format_money(buy_amount, rule.market_type)}) < "
                f"현재가({format_money(current_price, rule.market_type)}) -> 1주도 매수 불가. "
                f"buy_amount 상향 조정 필요"
            )
            if self._logger:
                self._logger.info(f"[{display_ticker(rule.ticker)}] {reason}")
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=0,
                price=current_price,
                reason=reason,
                pct_change=0.0,
                level=next_level,
                is_blocked=True,
            )

        # 동적 재매수 기준: max(마지막 차수 매수가, 직전 매도가)
        reference_price = last_lot.buy_price
        is_dynamic = False
        if last_sell_price and last_sell_price > reference_price:
            reference_price = last_sell_price
            is_dynamic = True

        pct_from_ref = (current_price - reference_price) / reference_price * 100
        buy_threshold = rule.buy_threshold_at(last_lot.level)

        if pct_from_ref <= buy_threshold:
            # 잔고 부족 체크
            passed, reason = self._passes_cash_guard(
                rule, current_price, buy_qty, portfolio
            )
            if not passed:
                if self._logger:
                    self._logger.info(f"[{display_ticker(rule.ticker)}] {reason} -> 매수 보류")
                return SplitSignal(
                    ticker=rule.ticker,
                    lot_id=None,
                    action=OrderAction.BUY,
                    quantity=buy_qty,
                    price=current_price,
                    reason=reason,
                    pct_change=pct_from_ref,
                    level=next_level,
                    is_blocked=True,
                )

            # 비중 상한 체크
            passed, reason = self._passes_exposure_guard(
                rule, lots, current_price, buy_qty, portfolio
            )
            if not passed:
                return SplitSignal(
                    ticker=rule.ticker,
                    lot_id=None,
                    action=OrderAction.BUY,
                    quantity=buy_qty,
                    price=current_price,
                    reason=reason,
                    pct_change=pct_from_ref,
                    level=next_level,
                    is_blocked=True,
                )

            if self._logger:
                if is_dynamic:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] 동적 재매수: "
                        f"매도가 {format_money(last_sell_price, rule.market_type)} 대비 "
                        f"{pct_from_ref:+.1f}% -> 추가 매수 Lv{next_level} {buy_qty}주 "
                        f"@{format_money(current_price, rule.market_type)} "
                        f"(원래 기준 Lv{last_lot.level} {format_money(last_lot.buy_price, rule.market_type)})"
                    )
                else:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] Lv{last_lot.level} "
                        f"매수가 {format_money(last_lot.buy_price, rule.market_type)} 대비 "
                        f"{pct_from_ref:+.1f}% -> 추가 매수 Lv{next_level} {buy_qty}주 "
                        f"@{format_money(current_price, rule.market_type)}"
                    )
            reason_detail = (
                f"동적 재매수 Lv{next_level} "
                f"(매도가 {format_money(last_sell_price, rule.market_type)} 대비 {pct_from_ref:+.1f}%)"
                if is_dynamic
                else f"추가 매수 Lv{next_level} (Lv{last_lot.level} 대비 {pct_from_ref:+.1f}%)"
            )
            return SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.BUY,
                quantity=buy_qty,
                price=current_price,
                reason=reason_detail,
                pct_change=pct_from_ref,
                level=next_level,
            )

        return None

    # ── 레짐(상승장) 분기 ──────────────────────────────────────

    def _resolve_regime(self, reading, st: dict, ticker: str, current_price: float) -> Regime:
        """레짐 히스테리시스를 적용해 유효 레짐을 반환한다 (st를 in-place 변이).

        - 상승 진입은 REGIME_CONFIRM_BARS 연속 UPTREND 판정 후에만.
        - 일단 상승에 진입하면 탈출은 추세 이탈(전량 청산)로만 이뤄진다.
          (소프트 ADX 하락으로는 매도 churn을 일으키지 않음)
        """
        if st.get("regime") == "uptrend":
            return Regime.UPTREND

        if reading.regime == Regime.UPTREND:
            st["uptrend_streak"] = st.get("uptrend_streak", 0) + 1
        else:
            st["uptrend_streak"] = 0

        if st.get("uptrend_streak", 0) >= REGIME_CONFIRM_BARS:
            st["regime"] = "uptrend"
            st["adds"] = 0
            st["last_add_swing_high"] = reading.swing_high
            st["last_add_price"] = current_price
            st["uptrend_streak"] = 0
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(ticker)}] 강한 상승 추세 진입 확정! "
                    f"(ADX {reading.adx:.1f} 돌파, 20EMA/50MA/200MA 정배열 정렬 상승 국면)"
                )
            return Regime.UPTREND
        return Regime.SIDEWAYS

    def _resolve_downtrend_block(self, reading, st: dict, ticker: str) -> bool:
        """DOWNTREND 매수 차단 래치를 관리한다 (st in-place 변이).

        진입: DOWNTREND_CONFIRM_BARS 연속 DOWNTREND -> "active" 래치
        유지: SIDEWAYS 1봉으로 해제되지 않음 (UPTREND 래치와 대칭)
        탈출: 비-DOWNTREND DOWNTREND_CONFIRM_BARS 연속 -> 래치 해제
        """
        if st.get("downtrend") == "active":
            if reading.regime != Regime.DOWNTREND:
                st["downtrend_exit_streak"] = st.get("downtrend_exit_streak", 0) + 1
                if st["downtrend_exit_streak"] >= DOWNTREND_CONFIRM_BARS:
                    st["downtrend"] = None
                    st["downtrend_streak"] = 0
                    st["downtrend_exit_streak"] = 0
                    if self._logger:
                        self._logger.info(
                            f"[{display_ticker(ticker)}] 하락 추세 해제 - 매수 차단 해제"
                        )
                    return False
            else:
                st["downtrend_exit_streak"] = 0
            return True

        if reading.regime == Regime.DOWNTREND:
            st["downtrend_streak"] = st.get("downtrend_streak", 0) + 1
        else:
            st["downtrend_streak"] = 0
            st["downtrend_exit_streak"] = 0

        if st.get("downtrend_streak", 0) >= DOWNTREND_CONFIRM_BARS:
            st["downtrend"] = "active"
            st["downtrend_streak"] = 0
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(ticker)}] DOWNTREND {DOWNTREND_CONFIRM_BARS}봉 확정 - 매수 차단"
                )
            return True

        return False

    def _evaluate_uptrend(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
        reading,
        st: dict,
        portfolio: Optional[Portfolio],
    ) -> List[SplitSignal]:
        """상승 레짐: 차수별 매도를 잠그고 추세 눌림에 누적 매수하며,
        추세 이탈 시 분할 청산 또는 전량 청산한다."""
        # 0. 가격 레벨업 기반 카운트 리셋 판정
        reset_pct = rule.uptrend_add_reset_pct
        last_add_price = st.get("last_add_price")
        
        # 하위 호환 폴백: last_add_price가 없는데 기존 포지션이 존재할 경우 최고 차수 매수가로 복구
        if last_add_price is None and ticker_lots:
            last_lot = max(ticker_lots, key=lambda l: l.level)
            last_add_price = last_lot.buy_price
            st["last_add_price"] = last_add_price

        if reset_pct is not None and reset_pct > 0 and last_add_price is not None:
            if current_price >= last_add_price * (1 + reset_pct / 100):
                old_adds = st.get("adds", 0)
                st["adds"] = 0
                st["last_add_price"] = current_price
                st["last_add_swing_high"] = None  # None으로 리셋하여 새 고점 게이트 오픈!
                if self._logger:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] 📈 주가 레벨업 감지! "
                        f"마지막 매수가 {format_money(last_add_price, rule.market_type)} 대비 +{reset_pct}% 돌파 "
                        f"(현재 {format_money(current_price, rule.market_type)}) "
                        f"-> adds 횟수({old_adds}회) 및 매수금액 초기화 (게이트 오픈)"
                    )

        # 0-1. 추종 데드라인(Trailing Lock) 활성 상태이면 전용 평가로 분기
        trailing_lock = st.get("trailing_lock")
        if trailing_lock is not None:
            return self._evaluate_trailing_lock(
                rule, ticker_lots, current_price, reading, st, portfolio
            )

        # 1. 추세 이탈 판정
        # 기본(use_sma50)은 50MA 하향 이탈을 쓴다. 50MA는 상승 정렬에서 항상 20EMA보다
        # 아래이므로, 20EMA로의 정상 눌림이 이탈로 오인되지 않는 버퍼가 보장된다.
        # use_sma50=False면 변동성 기반 Chandelier 스톱을 쓴다(버퍼는 사용자 책임).
        
        # 지표 결손(NaN) 감지 시 안전 최우선 필터: 오작동 및 청산 누락 방지
        import math
        target_indicator = reading.sma50 if rule.trendbreak_use_sma50 else reading.chandelier_stop
        if math.isnan(target_indicator):
            if self._logger:
                self._logger.warning(
                    f"[{display_ticker(rule.ticker)}] ⚠️ 레짐 기술적 지표 결손(NaN) 감지! "
                    "추세 이탈 여부를 판단할 수 없으므로 안전을 위해 매매 평가를 보류합니다."
                )
            return []

        if rule.trendbreak_use_sma50:
            broke = current_price < reading.sma50
        else:
            broke = current_price < reading.chandelier_stop
        if broke:
            return self._handle_trendbreak(
                rule, ticker_lots, current_price, reading, st
            )

        # 2. 매도 잠금 -> 추세 눌림 누적 매수만 평가
        last_lot = max(ticker_lots, key=lambda l: l.level)
        add_signal = self._evaluate_uptrend_add(
            rule, ticker_lots, last_lot, current_price, reading, st, portfolio
        )
        return [add_signal] if add_signal else []

    def _handle_trendbreak(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
        reading,
        st: dict,
    ) -> List[SplitSignal]:
        """추세 이탈 감지 시 전량 청산 또는 분할 매도+추종 데드라인 활성화를 결정한다."""
        total_qty = sum(l.quantity for l in ticker_lots)
        total_cost = sum(l.buy_price * l.quantity for l in ticker_lots)
        avg_buy = total_cost / total_qty if total_qty else 0.0
        pct = (current_price - avg_buy) / avg_buy * 100 if avg_buy else 0.0
        max_level = max(l.level for l in ticker_lots)

        partial_pct = rule.trendbreak_partial_sell_pct

        # 100%이면 기존 전량 청산 (하위 호환)
        if partial_pct >= 100.0:
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] 추세 이탈 -> 통합 전량 청산(Bulk) "
                    f"{total_qty}주 (평단 {format_money(avg_buy, rule.market_type)}, "
                    f"현재가 {format_money(current_price, rule.market_type)}, "
                    f"50MA {format_money(reading.sma50, rule.market_type)}, "
                    f"Chandelier {format_money(reading.chandelier_stop, rule.market_type)})"
                )
            return [SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.SELL,
                quantity=total_qty,
                price=current_price,
                reason=f"추세 이탈 통합 전량 청산(Bulk Sell, {total_qty}주 {pct:+.1f}%)",
                pct_change=pct,
                level=max_level,
                regime_liquidation=True,
            )]

        # 분할 매도: partial_pct% 만큼 즉시 매도, 나머지는 추종 데드라인
        sell_qty = math.ceil(total_qty * partial_pct / 100) if partial_pct > 0 else 0

        # ceil 올림으로 sell_qty가 total_qty 이상이 되면 전량 청산으로 처리 (상태 오염 방지)
        if sell_qty >= total_qty:
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] 추세 이탈 -> 수량 부족으로 전량 청산(Bulk) "
                    f"{total_qty}주 (평단 {format_money(avg_buy, rule.market_type)}, "
                    f"현재가 {format_money(current_price, rule.market_type)})"
                )
            return [SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.SELL,
                quantity=total_qty,
                price=current_price,
                reason=f"추세 이탈 전량 청산(수량 부족, {total_qty}주 {pct:+.1f}%)",
                pct_change=pct,
                level=max_level,
                regime_liquidation=True,
            )]

        if sell_qty <= 0:
            # 0%: 즉시 매도 없이 전량 추종 데드라인만 활성화
            # 상태 갱신은 여기서 직접 수행 (매도 체결이 없으므로 엔진 경유 불가)
            st["trailing_lock"] = {
                "active": True,
                "lock_price": current_price,
                "drop_pct": rule.trendbreak_trailing_drop_pct,
            }
            if self._logger:
                stop_price = current_price * (1 - rule.trendbreak_trailing_drop_pct / 100)
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] 추세 이탈 -> 추종 데드라인 활성화 "
                    f"(즉시 매도 0%, 전량 {total_qty}주 추적, "
                    f"기준가 {format_money(current_price, rule.market_type)}, "
                    f"청산선 {format_money(stop_price, rule.market_type)})"
                )
            return []

        if self._logger:
            remain_qty = total_qty - sell_qty
            stop_price = current_price * (1 - rule.trendbreak_trailing_drop_pct / 100)
            self._logger.info(
                f"[{display_ticker(rule.ticker)}] 추세 이탈 -> 분할 청산 "
                f"{sell_qty}주/{total_qty}주 ({partial_pct:.0f}%) 즉시 매도, "
                f"잔량 {remain_qty}주 추종 데드라인 "
                f"(기준가 {format_money(current_price, rule.market_type)}, "
                f"청산선 {format_money(stop_price, rule.market_type)}, "
                f"평단 {format_money(avg_buy, rule.market_type)}, "
                f"50MA {format_money(reading.sma50, rule.market_type)})"
            )
        return [SplitSignal(
            ticker=rule.ticker,
            lot_id=None,
            action=OrderAction.SELL,
            quantity=sell_qty,
            price=current_price,
            reason=f"추세 이탈 분할 청산({sell_qty}/{total_qty}주, {pct:+.1f}%)",
            pct_change=pct,
            level=max_level,
            regime_partial_liquidation=True,
        )]

    def _evaluate_trailing_lock(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
        reading,
        st: dict,
        portfolio: Optional[Portfolio],
    ) -> List[SplitSignal]:
        """추종 데드라인(Trailing Lock) 활성 상태에서 잔량을 평가한다.

        - 이탈 기준선 위로 회복 -> 데드라인 해제, 정상 상승 레짐 복귀
        - lock_price 대비 추가 하락 -> 잔량 전량 청산
        - 그 외 -> 대기 (매수/매도 없음)
        """
        lock = st["trailing_lock"]
        lock_price = lock["lock_price"]
        drop_pct = lock["drop_pct"]

        # 지표 결손 안전 필터
        target_indicator = reading.sma50 if rule.trendbreak_use_sma50 else reading.chandelier_stop
        if math.isnan(target_indicator):
            if self._logger:
                self._logger.warning(
                    f"[{display_ticker(rule.ticker)}] ⚠️ 추종 데드라인: 지표 결손(NaN) "
                    "-> 매매 평가 보류"
                )
            return []

        # 1. 회복 판정: 이탈 기준선 위로 복귀?
        if rule.trendbreak_use_sma50:
            recovered = current_price >= reading.sma50
        else:
            recovered = current_price >= reading.chandelier_stop

        if recovered:
            del st["trailing_lock"]
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] ✅ 추종 데드라인 해제! "
                    f"가격 {format_money(current_price, rule.market_type)}이 "
                    f"이탈 기준선 위로 회복 -> 상승 레짐 복귀 "
                    f"(잔량 {sum(l.quantity for l in ticker_lots)}주 보유 유지)"
                )
            return []

        # 2. 추가 하락 판정: lock_price 대비 X% 이상 하락?
        if lock_price is None or lock_price <= 0:
            if self._logger:
                self._logger.error(
                    f"[{display_ticker(rule.ticker)}] 추종 데드라인: 기준가 오류"
                    f"(lock_price={lock_price}) -> 매매 평가 보류"
                )
            return []
        drop = (lock_price - current_price) / lock_price * 100
        if drop >= drop_pct:
            total_qty = sum(l.quantity for l in ticker_lots)
            total_cost = sum(l.buy_price * l.quantity for l in ticker_lots)
            avg_buy = total_cost / total_qty if total_qty else 0.0
            pct = (current_price - avg_buy) / avg_buy * 100 if avg_buy else 0.0
            max_level = max(l.level for l in ticker_lots)
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] 🔻 추종 데드라인 발동! "
                    f"기준가 {format_money(lock_price, rule.market_type)} 대비 "
                    f"-{drop:.1f}% (허용 -{drop_pct}%) "
                    f"-> 잔량 {total_qty}주 전량 청산"
                )
            # trailing_lock 상태 리셋은 엔진에서 체결 확정 시 수행
            return [SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.SELL,
                quantity=total_qty,
                price=current_price,
                reason=f"추종 데드라인 발동 잔량 청산({total_qty}주, 기준가 대비 -{drop:.1f}%)",
                pct_change=pct,
                level=max_level,
                regime_liquidation=True,  # 전량 청산 -> 레짐 리셋
            )]

        # 3. 대기 (매수/매도 없음)
        if self._logger:
            stop_price = lock_price * (1 - drop_pct / 100)
            self._logger.info(
                f"[{display_ticker(rule.ticker)}] 추종 데드라인 추적 중 "
                f"(현재가 {format_money(current_price, rule.market_type)}, "
                f"기준가 {format_money(lock_price, rule.market_type)}, "
                f"대비 -{drop:.1f}%, "
                f"청산선 {format_money(stop_price, rule.market_type)})"
            )
        return []

    def _evaluate_uptrend_add(
        self,
        rule: StockRule,
        lots: List[PositionLot],
        last_lot: PositionLot,
        current_price: float,
        reading,
        st: dict,
        portfolio: Optional[Portfolio],
    ) -> Optional[SplitSignal]:
        """상승 추세 눌림 매수(불타기) 평가. 새 고점 게이트 + 눌림/반등 확인."""
        adds = st.get("adds", 0)
        if adds >= rule.uptrend_max_adds:
            if self._logger:
                self._logger.debug(
                    f"  [{display_ticker(rule.ticker)}] 불타기 대기 | "
                    f"최대 추가 매수 횟수 도달 ({adds}/{rule.uptrend_max_adds})"
                )
            return None
        next_level = last_lot.level + 1
        if next_level > rule.max_lots:
            if self._logger:
                self._logger.debug(
                    f"  [{display_ticker(rule.ticker)}] 불타기 대기 | "
                    f"최대 보유 차수 도달 ({next_level - 1}/{rule.max_lots})"
                )
            return None

        # 새 고점 게이트: 직전 add(또는 진입) 이후 새 스윙 고점이 나와야 추가
        # (테스트: 게이트 우회 - 횟수(uptrend_max_adds)로만 제어)
        # last_high = st.get("last_add_swing_high")
        # if last_high is not None and not (reading.swing_high > last_high):
        #     if self._logger:
        #         self._logger.debug(
        #             f"  [{display_ticker(rule.ticker)}] 불타기 대기 | "
        #             f"고점 게이트 미갱신 (현재 swing_high {format_money(reading.swing_high, rule.market_type)} "
        #             f"<= 직전 고점 {format_money(last_high, rule.market_type)})"
        #         )
        #     return None

        # 눌림 + 반등 확인.
        # 상한: 20EMA + band% 초과 시 추격 매수 차단. 하단 제한 없음(EMA30 수준 깊은 눌림도 허용).
        # 윈도우는 "어제까지"이므로 reading.close = 직전 완성봉(어제) 종가.
        # 반등 = 현재가가 어제 종가 위 또는 20EMA 위.
        ema20 = reading.ema20
        band_pct = rule.uptrend_pullback_band_pct
        upper = ema20 * (1 + band_pct / 100)
        in_band = current_price <= upper
        bounced = current_price > reading.close or current_price > ema20
        if not (in_band and bounced):
            if self._logger:
                self._logger.debug(
                    f"  [{display_ticker(rule.ticker)}] 불타기 대기 | "
                    f"눌림목 조건 미충족 (현재 {format_money(current_price, rule.market_type)} vs "
                    f"20EMA {format_money(ema20, rule.market_type)}, "
                    f"상한 {format_money(upper, rule.market_type)} (+{band_pct}%), "
                    f"초과={current_price > upper}, bounced={bounced})"
                )
            return None

        amount = rule.uptrend_add_amount_at(adds + 1)
        buy_qty = math.floor(amount / current_price)
        if buy_qty <= 0:
            if self._logger:
                self._logger.warning(
                    f"  [{display_ticker(rule.ticker)}] 불타기 취소 | "
                    f"주문 수량이 0주입니다 (금액 {format_money(amount, rule.market_type)} "
                    f"< 현재가 {format_money(current_price, rule.market_type)})"
                )
            return None

        passed, reason = self._passes_cash_guard(rule, current_price, buy_qty, portfolio)
        if not passed:
            return SplitSignal(
                ticker=rule.ticker, lot_id=None, action=OrderAction.BUY,
                quantity=buy_qty, price=current_price, reason=reason,
                pct_change=0.0, level=next_level, is_blocked=True,
            )
        passed, reason = self._passes_exposure_guard(
            rule, lots, current_price, buy_qty, portfolio
        )
        if not passed:
            return SplitSignal(
                ticker=rule.ticker, lot_id=None, action=OrderAction.BUY,
                quantity=buy_qty, price=current_price, reason=reason,
                pct_change=0.0, level=next_level, is_blocked=True,
            )

        # 상태 갱신은 여기서 하지 않는다. 매수 체결이 확정될 때(엔진 _update_positions)
        # regime_state["adds"]/["last_add_swing_high"]를 갱신해야 백테스트/라이브가 동일해진다.
        # 신호에 스윙고점을 실어 보내 체결 시 엔진이 커밋하도록 한다.
        if self._logger:
            self._logger.info(
                f"[{display_ticker(rule.ticker)}] 상승장 누적 매수 Lv{next_level} "
                f"{buy_qty}주 @{format_money(current_price, rule.market_type)} "
                f"(20EMA {format_money(ema20, rule.market_type)} 눌림, add {adds + 1}/{rule.uptrend_max_adds})"
            )
        return SplitSignal(
            ticker=rule.ticker,
            lot_id=None,
            action=OrderAction.BUY,
            quantity=buy_qty,
            price=current_price,
            reason=f"상승장 누적 매수 Lv{next_level} (20EMA 눌림, add {adds + 1})",
            pct_change=0.0,
            level=next_level,
            regime_add_swing_high=reading.swing_high,
        )

