# src/core/logic/split_evaluator.py
import datetime
import math
from typing import Dict, List, Optional

from src.core.interfaces import ILogger
from src.core.logic.regime import (
    Regime, channel_breakdown_line, classify, classify_channel,
)
from src.core.models import (
    StockRule,
    PositionLot,
    Portfolio,
    SplitSignal,
    OrderAction,
)
from src.utils.ticker_reader import display_ticker
from src.utils.currency import format_money, format_qty

# 상승 레짐 진입 확정에 필요한 연속 UPTREND 판정 횟수 (독립 조정 가능)
REGIME_CONFIRM_BARS = 2
# 하락 추세 래치 진입/탈출 확정에 필요한 연속 판정 횟수 (독립 조정 가능)
DOWNTREND_CONFIRM_BARS = 2
# 채널 하단 이탈 청산 확정에 필요한 연속 이탈 판정 횟수 (독립 조정 가능)
BREAKDOWN_CONFIRM_BARS = 2


def clear_breakdown_confirmation(st: dict) -> None:
    """이탈 확정 카운터/플래그를 비운다.

    청산이 실제로 반영된 뒤에만 호출해야 한다. 신호를 낸 시점에 비우면
    주문이 거절됐을 때 확정을 잃고 처음부터 다시 세게 된다.
    """
    for key in (
        "breakdown_confirmed", "breakdown_days",
        "breakdown_today_state", "breakdown_prev_date",
    ):
        st.pop(key, None)


def classify_for_rule(rule: StockRule, ohlc_window):
    """rule.regime_algo에 따라 레짐 분류기를 선택 호출한다 (엔진/평가기 공용)."""
    if rule.regime_algo == "channel":
        return classify_channel(
            ohlc_window,
            lookback=rule.channel_lookback,
            stddev_k=rule.channel_stddev_k,
            slope_band_pct=rule.channel_slope_band_pct,
            chandelier_k=rule.trendbreak_chandelier_k,
            chandelier_lookback=rule.trendbreak_chandelier_lookback,
            swing_lookback=rule.uptrend_swing_lookback,
        )
    return classify(
        ohlc_window,
        adx_trend_threshold=rule.regime_adx_trend,
        adx_range_threshold=rule.regime_adx_range,
        chandelier_k=rule.trendbreak_chandelier_k,
        chandelier_lookback=rule.trendbreak_chandelier_lookback,
        swing_lookback=rule.uptrend_swing_lookback,
        min_bars=rule.regime_min_bars,
    )


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
        self._active_exposure_limit: Optional[float] = None
        self._active_sell_multiplier: float = 1.0

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
        evaluation_date: Optional[str] = None,
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
        multi = None
        trend_only_uptrend_confirmed = False
        rebound_entry_confirmed = False
        staged_probe_trigger = None
        if rule.regime_enabled and ohlc_window is not None:
            reading = classify_for_rule(rule, ohlc_window)
            regime_st = regime_state.setdefault(rule.ticker, {}) if regime_state is not None else {}
            # 하락 래치 갱신: UPTREND 모드 중에도 항상 실행해 레짐 탈출 후 즉시 차단 가능하게 함
            downtrend_blocked = self._resolve_downtrend_block(
                reading, regime_st, rule.ticker, evaluation_date=evaluation_date
            )
            multi = self._resolve_multi_horizon(rule, ohlc_window, reading, regime_st)
            self._active_exposure_limit = multi["exposure_limit"] if multi else None
            self._active_sell_multiplier = multi["sell_multiplier"] if multi else 1.0

            # 추세 전용은 포지션이 없을 때도 기존 2거래일 상승 확인을
            # 누적한다. 진입·추가매수는 장기와 단기가 모두 상승일 때만 허용한다.
            if rule.trend_only_enabled and multi is not None:
                short_uptrend_confirmed = (
                    self._resolve_regime(
                        reading, regime_st, rule.ticker, current_price,
                        evaluation_date=evaluation_date,
                    ) == Regime.UPTREND
                )
                trend_only_uptrend_confirmed = (
                    short_uptrend_confirmed
                    and multi["long"] == Regime.UPTREND
                    and multi["short"] == Regime.UPTREND
                )
                if rule.trend_entry_mode in ("rebound", "staged_rebound") and not ticker_lots:
                    rebound_entry_confirmed = self._resolve_rebound_entry(
                        rule, reading, regime_st, multi, current_price,
                        evaluation_date=evaluation_date,
                    )
                    if rule.trend_entry_mode == "staged_rebound":
                        staged_probe_trigger = self._resolve_staged_rebound_probe(
                            rule, reading, multi, current_price,
                        )

            # 장·단기 하락 정렬은 단기 방어 뒤 남은 물량까지 정리하는 최종 청산 이벤트다.
            if (multi and ticker_lots and (
                    (multi["long"] == Regime.DOWNTREND and multi["short"] == Regime.DOWNTREND)
                    or regime_st.get("long_short_downtrend_liquidation_pending"))):
                regime_st["aligned_downtrend_reentry_lock"] = True
                regime_st["long_short_downtrend_liquidation_pending"] = True
                return self._aligned_downtrend_liquidation(
                    rule, ticker_lots, current_price
                )
            if ticker_lots:
                # 채널 모드 이탈 처리: 추종 데드라인 -> 하락 래치 청산 -> 하단 이탈 청산.
                # None이면 이탈 아님 -> 통상 흐름(상승/횡보) 계속.
                if rule.regime_algo == "channel":
                    exit_signals = self._evaluate_channel_exit(
                        rule, ticker_lots, current_price, reading, regime_st,
                        downtrend_blocked, portfolio, multi=multi,
                        evaluation_date=evaluation_date,
                    )
                    if exit_signals is not None:
                        return exit_signals
                transition_signals = self._evaluate_uptrend_sideways_transition(
                    rule, ticker_lots, current_price, regime_st, multi,
                    evaluation_date=evaluation_date,
                )
                if transition_signals is not None:
                    return transition_signals
                uptrend_resolved = (
                    trend_only_uptrend_confirmed
                    if rule.trend_only_enabled
                    else self._resolve_regime(
                        reading, regime_st, rule.ticker, current_price,
                        evaluation_date=evaluation_date,
                    ) == Regime.UPTREND
                )
                if uptrend_resolved:
                    if multi and multi["long"] == Regime.SIDEWAYS:
                        # 장기 횡보의 단기 상승은 초기 진입만 허용하고 추세 가산은 금지한다.
                        uptrend_resolved = False
                    elif multi and multi["buy_halted"]:
                        uptrend_resolved = False
                if uptrend_resolved:
                    if (rule.trend_entry_mode == "staged_rebound"
                            and regime_st.get("staged_rebound_probe_open")):
                        completion = self._evaluate_staged_rebound_completion(
                            rule, ticker_lots, current_price, reading, regime_st,
                            portfolio,
                        )
                        if completion is not None:
                            return [completion]
                    return self._evaluate_uptrend(
                        rule, ticker_lots, current_price, reading, regime_st, portfolio,
                        evaluation_date=evaluation_date,
                    )
                if rule.trend_only_enabled:
                    # 이탈 조건이 아닌 횡보/불명 국면에서는 기존 매직스플릿
                    # 익절·추가매수로 내려가지 않고 보유한다.
                    if rule.trend_entry_mode in ("rebound", "staged_rebound"):
                        for key in (
                            "pullback_rebound_armed", "pullback_rebound_start_date",
                            "pullback_rebound_low", "pullback_rebound_wait_days",
                            "pullback_rebound_confirm_days",
                        ):
                            regime_st.pop(key, None)
                    return []
            else:
                uptrend_resolved = False
        else:
            uptrend_resolved = False

        # 레짐 미사용/데이터 부족 호출 간 evaluator 인스턴스의 이전 값을 누수하지 않는다.
        if multi is None:
            self._active_exposure_limit = None
            self._active_sell_multiplier = 1.0

        # 추세 전용 보유 중 데이터가 부족하면 평균회귀 매매로 폴백하지 않는다.
        # 데이터가 복구될 때까지 보유하며, 계산 가능한 위험 청산은 위에서 우선 처리된다.
        if ticker_lots and rule.trend_only_enabled:
            return []

        # 보유 lot이 없으면 -> 1차수 초기 매수
        if not ticker_lots:
            if multi and multi["buy_locked"]:
                return [self._buy_blocked_signal(rule, current_price, multi["lock_reason"])]
            if multi and multi["buy_halted"]:
                return [self._buy_blocked_signal(rule, current_price, "단기 하락 - 신규 진입 중단")]
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

            # 3층 모드에서는 청산 원인과 무관하게 모든 전량 청산 뒤의 새 Lv1을
            # 같은 회복 조건으로 제한한다. 일반 익절 trailing도 포함한다.
            needs_full_liquidation_reentry = (
                regime_st.get("post_liquidation")
                or regime_st.get("aligned_downtrend_reentry_lock")
            )
            if rule.multi_horizon_regime_enabled and needs_full_liquidation_reentry:
                if multi is None:
                    return [self._buy_blocked_signal(
                        rule, current_price,
                        "전량 청산 후 재진입 대기 - 장기 추세 데이터 부족",
                    )]
                if not self._can_reenter_after_full_liquidation(reading, multi, current_price):
                    return [self._buy_blocked_signal(
                        rule, current_price,
                        "전량 청산 후 재진입 대기 - 장기 횡보 이상·단기 상승·채널 중심선 회복 필요",
                    )]
                regime_st.pop("post_liquidation", None)
                regime_st.pop("post_liquidation_reentry_gate", None)
                regime_st.pop("aligned_downtrend_reentry_lock", None)
                regime_st.pop("long_short_downtrend_liquidation_pending", None)
                if self._logger:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] 전량 청산 후 회복 확인 "
                        "(장기 횡보 이상·단기 상승·채널 중심선 상회) -> 재진입 허용"
                    )

            # 구 모드의 기존 청산 원인별 재진입 게이트는 그대로 보존한다.
            elif rule.regime_algo == "channel" and regime_st.get("post_liquidation"):
                gate = regime_st.get("post_liquidation_reentry_gate", "resistance")
                gate_line = (
                    reading.channel_mid if gate == "midline"
                    else reading.channel_resistance
                ) if reading is not None else float("nan")
                gate_name = "채널 중심선" if gate == "midline" else "상단 저항선"
                if math.isnan(gate_line) or current_price <= gate_line:
                    reason = (
                        f"이탈 청산 후 재진입 대기 - {gate_name} 미회복 "
                        f"(현재가 {format_money(current_price, rule.market_type)} <= "
                        f"기준선 {format_money(gate_line, rule.market_type)})"
                    )
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
                regime_st.pop("post_liquidation", None)
                regime_st.pop("post_liquidation_reentry_gate", None)
                if self._logger:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] {gate_name} 회복 확인 "
                        f"(현재가 {format_money(current_price, rule.market_type)} > "
                        f"기준선 {format_money(gate_line, rule.market_type)}) -> 재진입 허용"
                    )

            if rule.trend_only_enabled:
                if multi is None:
                    return [self._buy_blocked_signal(
                        rule, current_price, "추세 데이터 부족",
                    )]
                entry_confirmed = (
                    rebound_entry_confirmed or staged_probe_trigger is not None
                    if rule.trend_entry_mode == "staged_rebound"
                    else rebound_entry_confirmed
                    if rule.trend_entry_mode == "rebound"
                    else trend_only_uptrend_confirmed
                )
                if not entry_confirmed:
                    return [self._buy_blocked_signal(
                        rule, current_price,
                        ("단계형 반등 대기 - 회복 탐색 또는 장·단기 상승 정렬 필요"
                         if rule.trend_entry_mode == "staged_rebound"
                         else "반등형 추세 대기 - 장기 상승·단기 재상승·중심선 회복 필요"
                         if rule.trend_entry_mode == "rebound"
                         else "추세 전용 대기 - 장·단기 상승 정렬 필요"),
                    )]
            last_sell_price = (
                last_sell_prices.get(rule.ticker) if last_sell_prices else None
            )
            signal = self._evaluate_initial_buy(
                rule, current_price, last_sell_price=last_sell_price,
                portfolio=portfolio,
                bypass_reentry_guard=(rule.trend_entry_mode in ("rebound", "staged_rebound")),
                amount_multiplier=(rule.staged_rebound_probe_pct / 100
                                   if staged_probe_trigger is not None else 1.0),
                entry_trigger=(f"staged_rebound_probe_{staged_probe_trigger}"
                               if staged_probe_trigger is not None
                               else "rebound_initial_entry"
                               if rule.trend_entry_mode in ("rebound", "staged_rebound")
                               else "aligned_uptrend_entry"
                               if rule.trend_only_enabled else "legacy_magic_split"),
            )
            return [signal] if signal else []

        # 마지막 차수(가장 높은 level) lot 찾기
        last_lot = max(ticker_lots, key=lambda l: l.level)
        result: List[SplitSignal] = []

        if rule.trailing_drop_at(last_lot.level) is not None:
            # 새 경로: 멀티-lot trailing 동시 추적
            trailing_signals = self._evaluate_trailing_multi(rule, ticker_lots, current_price)
            if trailing_signals is not None:
                return trailing_signals
            # None -> last_lot 미활성화 -> buy eval로 진행
        else:
            # 기존 경로: 단건 고정 익절 (trailing OFF)
            self._trailing_info_signal = None
            sell_signal = self._evaluate_sell(rule, last_lot, current_price)
            if sell_signal is not None:
                return [sell_signal]
            if self._trailing_info_signal is not None:
                result.append(self._trailing_info_signal)
                self._trailing_info_signal = None

        # 하락 레짐 추가 매수 차단
        if (multi and (multi["buy_locked"] or multi["buy_halted"])) or downtrend_blocked:
            reason = (multi["lock_reason"] if multi and multi["buy_locked"]
                      else "단기 하락 - 추가 매수 중단" if multi
                      else "DOWNTREND 확정 - 추가 매수 차단")
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

        if multi and multi["long"] == Regime.SIDEWAYS and multi["short"] == Regime.UPTREND:
            # 장기 횡보·단기 상승은 새 1차수만 허용한다. 보유분의 일반/추세 가산은 모두 막는다.
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

    def _evaluate_trailing_multi(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
    ) -> Optional[List[SplitSignal]]:
        """모든 활성화된 lot을 동시 추적하여 drop 조건 충족 lot을 벌크 매도 신호로 반환한다.

        Returns:
            None  -> last_lot 미활성화, 호출부가 buy eval로 진행
            []    -> 활성화됐지만 미발동, buy eval skip
            [sig] -> 활성화 info 신호 또는 벌크 매도 신호
        """
        sorted_lots = sorted(ticker_lots, key=lambda l: l.level, reverse=True)
        last_lot = sorted_lots[0]

        # 1단계: last_lot 활성화 확인
        pct = (current_price - last_lot.buy_price) / last_lot.buy_price * 100
        if (last_lot.trailing_highest_price is None
                and pct < self._effective_sell_threshold(rule, last_lot)):
            return None

        # 2단계: 고차수->저차수 순차 탐색
        fired_lots: List[PositionLot] = []
        info_signals: List[SplitSignal] = []

        for lot in sorted_lots:
            t_drop = rule.trailing_drop_at(lot.level)
            if t_drop is None:
                break

            pct_lot = (current_price - lot.buy_price) / lot.buy_price * 100
            s_thr = self._effective_sell_threshold(rule, lot)
            lot_activated = lot.trailing_highest_price is not None or pct_lot >= s_thr

            if not lot_activated:
                break

            was_inactive = lot.trailing_highest_price is None
            updated_high = was_inactive or current_price > lot.trailing_highest_price

            if updated_high:
                lot.trailing_highest_price = current_price

            stop_price = lot.trailing_highest_price * (1 - t_drop / 100)

            if self._logger:
                if was_inactive:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] Lv{lot.level}: "
                        f"trailing 활성화 "
                        f"(매수가 대비 {pct_lot:+.1f}%, "
                        f"스톱가 {format_money(stop_price, rule.market_type)})"
                    )
                elif updated_high:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] Lv{lot.level}: "
                        f"trailing 고점 갱신 -> "
                        f"{format_money(current_price, rule.market_type)} "
                        f"(스톱가 {format_money(stop_price, rule.market_type)})"
                    )

            if was_inactive:
                info_signals.append(SplitSignal(
                    ticker=rule.ticker,
                    lot_id=lot.lot_id,
                    action=OrderAction.SELL,
                    quantity=0,
                    price=current_price,
                    reason=(
                        f"Lv{lot.level}: trailing 활성화 "
                        f"(매수가 대비 {pct_lot:+.1f}%, "
                        f"스톱가 {format_money(stop_price, rule.market_type)})"
                    ),
                    pct_change=pct_lot,
                    level=lot.level,
                    is_info=True,
                ))

            drop = (lot.trailing_highest_price - current_price) / lot.trailing_highest_price * 100
            if drop >= t_drop:
                fired_lots.append(lot)

        # 3단계: 결과 반환
        if not fired_lots:
            return info_signals

        fired_qty = sum(l.quantity for l in fired_lots)
        total_cost = sum(l.buy_price * l.quantity for l in fired_lots)
        avg_buy = total_cost / fired_qty
        pct_change = (current_price - avg_buy) / avg_buy * 100
        min_lv = min(l.level for l in fired_lots)
        max_lv = max(l.level for l in fired_lots)

        if self._logger:
            self._logger.info(
                f"[{display_ticker(rule.ticker)}] trailing 벌크 매도 발동: "
                f"Lv{min_lv}~Lv{max_lv} {format_qty(fired_qty, rule.market_type)} ({pct_change:+.1f}%)"
            )

        bulk_signal = SplitSignal(
            ticker=rule.ticker,
            lot_id=None,
            action=OrderAction.SELL,
            quantity=fired_qty,
            price=current_price,
            reason=f"trailing 벌크 매도 Lv{min_lv}~Lv{max_lv} ({format_qty(fired_qty, rule.market_type)} {pct_change:+.1f}%)",
            pct_change=pct_change,
            level=max_lv,
            buy_price=avg_buy,
            trailing_bulk=True,
        )
        return info_signals + [bulk_signal]

    def _resolve_multi_horizon(self, rule, ohlc_window, short_reading, st: dict) -> Optional[dict]:
        """장기 채널 정책 컨텍스트를 만들고 잠금 상태를 갱신한다.

        장기 데이터가 부족하면 None을 반환해 기존 channel 경로로 완전히 폴백한다.
        """
        if not rule.multi_horizon_regime_enabled or rule.regime_algo != "channel":
            return None
        long_reading = classify_channel(
            ohlc_window, lookback=rule.long_channel_lookback,
            stddev_k=rule.channel_stddev_k,
            slope_band_pct=rule.channel_slope_band_pct,
            chandelier_k=rule.trendbreak_chandelier_k,
            chandelier_lookback=rule.trendbreak_chandelier_lookback,
            swing_lookback=rule.uptrend_swing_lookback,
        )
        if long_reading.regime == Regime.UNKNOWN:
            st["long_trend"] = "unknown"
            return None

        long_regime = long_reading.regime
        short_regime = short_reading.regime
        previous_long = st.get("previous_long_regime")
        previous_short = st.get("previous_short_regime")
        st["long_trend"] = str(long_regime)
        st["short_trend"] = str(short_regime)
        st["previous_long_regime"] = str(long_regime)
        st["previous_short_regime"] = str(short_regime)

        if long_regime == Regime.DOWNTREND:
            st["long_downtrend_lock"] = True
        elif (st.get("long_downtrend_lock")
              and long_regime in (Regime.SIDEWAYS, Regime.UPTREND)
              and short_regime == Regime.UPTREND):
            st.pop("long_downtrend_lock", None)

        buy_locked = bool(st.get("long_downtrend_lock"))
        exposure_limit = None
        if rule.max_exposure_pct is not None:
            multiplier = rule.long_sideways_exposure_multiplier if long_regime == Regime.SIDEWAYS else 1.0
            exposure_limit = rule.max_exposure_pct * multiplier

        # 단기 하락은 장기 상승/횡보에서 일시 중단이고, 장기 하락은 영속 잠금이다.
        buy_halted = long_regime != Regime.DOWNTREND and short_regime == Regime.DOWNTREND
        sell_multiplier = (
            rule.long_uptrend_sideways_sell_multiplier
            if long_regime == Regime.UPTREND and short_regime == Regime.SIDEWAYS else 1.0
        )
        if previous_long != str(long_regime) or previous_short != str(short_regime):
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] 장·단기 레짐 전이: "
                    f"({previous_long or '-'}, {previous_short or '-'}) -> ({long_regime}, {short_regime})"
                )
        return {
            "long": long_regime, "short": short_regime,
            "long_reading": long_reading,
            "previous_long": previous_long, "previous_short": previous_short,
            "buy_locked": buy_locked, "buy_halted": buy_halted,
            "lock_reason": "장기 하락 확정 - 신규·추가 매수 차단",
            "exposure_limit": exposure_limit, "sell_multiplier": sell_multiplier,
        }

    def _resolve_rebound_entry(
        self, rule: StockRule, reading, st: dict, multi: dict,
        current_price: float, evaluation_date: Optional[str] = None,
    ) -> bool:
        """장기 상승 중 단기 비상승→상승 전환을 날짜 기준으로 확인한다."""
        prefix = "rebound_entry_"
        current_pair_ok = (
            multi["long"] == Regime.UPTREND
            and multi["short"] == Regime.UPTREND
        )
        if not current_pair_ok:
            for key in ("armed", "origin_regime", "days", "last_date", "confirmed"):
                st.pop(prefix + key, None)
            return False

        if not st.get(prefix + "armed"):
            previous_long = multi.get("previous_long")
            previous_short = multi.get("previous_short")
            previous_long_ok = (
                previous_long == str(Regime.UPTREND)
                or (
                    rule.trend_entry_mode == "staged_rebound"
                    and previous_long == str(Regime.SIDEWAYS)
                )
            )
            if not (
                previous_long_ok
                and previous_short in (str(Regime.SIDEWAYS), str(Regime.DOWNTREND))
            ):
                return False
            st[prefix + "armed"] = True
            st[prefix + "origin_regime"] = previous_short

        midline_ok = (
            not rule.rebound_entry_require_midline
            or (not math.isnan(reading.channel_mid) and current_price > reading.channel_mid)
        )
        if not midline_ok:
            st[prefix + "days"] = []
            st.pop(prefix + "confirmed", None)
            return False

        today = evaluation_date or datetime.date.today().isoformat()
        days = st.get(prefix + "days", [])
        if today not in days:
            days.append(today)
        st[prefix + "days"] = days
        st[prefix + "last_date"] = today
        confirmed = len(days) >= rule.rebound_entry_confirm_bars
        st[prefix + "confirmed"] = confirmed
        return confirmed

    def _resolve_staged_rebound_probe(
        self, rule: StockRule, reading, multi: dict, current_price: float,
    ) -> Optional[str]:
        """단계형 반등의 50% 탐색 진입 케이스를 판정한다."""
        if multi.get("buy_locked") or multi.get("buy_halted"):
            return None

        short_recovered = (
            multi["long"] == Regime.UPTREND
            and multi["short"] == Regime.SIDEWAYS
            and multi.get("previous_long") == str(Regime.UPTREND)
            and multi.get("previous_short") == str(Regime.DOWNTREND)
            and not math.isnan(reading.channel_mid)
            and not math.isnan(reading.ema20)
            and current_price > reading.channel_mid
            and current_price > reading.ema20
            and current_price > reading.close
        )
        if short_recovered:
            return "uptrend_short_recovery"

        if not rule.staged_rebound_allow_long_sideways:
            return None
        long_reading = multi.get("long_reading")
        if long_reading is None:
            return None
        short_midline_ok = (
            not rule.rebound_entry_require_midline
            or (not math.isnan(reading.channel_mid) and current_price > reading.channel_mid)
        )
        long_midline_ok = (
            not rule.staged_rebound_require_long_midline
            or (not math.isnan(long_reading.channel_mid)
                and current_price > long_reading.channel_mid)
        )
        long_slope_ok = (
            not rule.staged_rebound_require_nonnegative_long_slope
            or (not math.isnan(long_reading.channel_slope_pct)
                and long_reading.channel_slope_pct >= 0)
        )
        if (
            multi["long"] == Regime.SIDEWAYS
            and multi["short"] == Regime.UPTREND
            and short_midline_ok
            and long_midline_ok
            and long_slope_ok
        ):
            return "sideways_long_breakout"
        return None

    def _evaluate_staged_rebound_completion(
        self, rule: StockRule, lots: List[PositionLot], current_price: float,
        reading, st: dict, portfolio: Optional[Portfolio],
    ) -> Optional[SplitSignal]:
        """탐색 진입 뒤 상승 정렬에서 최초 기준금액의 잔여분을 한 번 매수한다."""
        if not st.get("staged_rebound_probe_open"):
            return None
        if (
            rule.rebound_entry_require_midline
            and (math.isnan(reading.channel_mid) or current_price <= reading.channel_mid)
        ):
            return None
        next_level = max(l.level for l in lots) + 1
        if next_level > rule.max_lots:
            return None
        amount = rule.buy_amount_at(1) * (1 - rule.staged_rebound_probe_pct / 100)
        buy_qty = rule.quantize_qty(amount / current_price)
        if buy_qty <= 0:
            return None
        passed, reason = self._passes_cash_guard(rule, current_price, buy_qty, portfolio)
        if not passed:
            return SplitSignal(
                ticker=rule.ticker, lot_id=None, action=OrderAction.BUY,
                quantity=buy_qty, price=current_price, reason=reason,
                pct_change=0.0, level=next_level, is_blocked=True,
                entry_trigger="staged_rebound_confirm_add",
            )
        passed, reason = self._passes_exposure_guard(
            rule, lots, current_price, buy_qty, portfolio,
        )
        if not passed:
            return SplitSignal(
                ticker=rule.ticker, lot_id=None, action=OrderAction.BUY,
                quantity=buy_qty, price=current_price, reason=reason,
                pct_change=0.0, level=next_level, is_blocked=True,
                entry_trigger="staged_rebound_confirm_add",
            )
        return SplitSignal(
            ticker=rule.ticker, lot_id=None, action=OrderAction.BUY,
            quantity=buy_qty, price=current_price,
            reason=f"단계형 반등 상승 정렬 완성 매수 Lv{next_level}",
            pct_change=0.0, level=next_level,
            entry_trigger="staged_rebound_confirm_add",
        )

    def _evaluate_uptrend_sideways_transition(
        self, rule: StockRule, lots: List[PositionLot], current_price: float,
        st: dict, multi: Optional[dict], evaluation_date: Optional[str] = None,
    ) -> Optional[List[SplitSignal]]:
        """상승 정렬에서 단기 횡보로 전환될 때 잔고를 한 번 선제 감축한다.

        하단 이탈 경로보다 뒤에서 호출되므로 기존 위험청산이 항상 우선한다.
        목표와 누적 체결량은 상태에 남겨 부분체결을 재시도하지만, 완료 후에는
        실제 신규 매수 체결 전까지 다시 무장하지 않는다.
        """
        pct = rule.uptrend_sideways_transition_partial_sell_pct
        if pct <= 0 or multi is None or not lots:
            return None

        prefix = "uptrend_sideways_transition_"
        current_pair = (multi["long"], multi["short"])
        target_pair = (Regime.UPTREND, Regime.SIDEWAYS)

        if current_pair != target_pair:
            sold_qty = float(st.get(prefix + "sold_qty", 0) or 0)
            if sold_qty > 0 and not st.get("transition_de_risked"):
                st["transition_de_risked"] = True
            for key in (
                prefix + "days", prefix + "last_date", prefix + "target_qty",
                prefix + "sold_qty",
            ):
                st.pop(key, None)
            return None

        if st.get("transition_de_risked"):
            return None

        days = int(st.get(prefix + "days", 0) or 0)
        if days == 0:
            if not (
                multi.get("previous_long") == str(Regime.UPTREND)
                and multi.get("previous_short") == str(Regime.UPTREND)
            ):
                return None

        date_key = evaluation_date or "__undated__"
        if st.get(prefix + "last_date") != date_key:
            days += 1
            st[prefix + "days"] = days
            st[prefix + "last_date"] = date_key

        if days < rule.uptrend_sideways_transition_confirm_bars:
            return []

        total_qty = sum(l.quantity for l in lots)
        min_qty = rule.min_order_qty()
        target_qty = st.get(prefix + "target_qty")
        if target_qty is None:
            target_qty = rule.quantize_qty(total_qty * pct / 100, round_up=True)
            max_sellable = rule.quantize_qty(max(0, total_qty - min_qty))
            target_qty = min(target_qty, max_sellable)
            st[prefix + "target_qty"] = target_qty

        sold_qty = float(st.get(prefix + "sold_qty", 0) or 0)
        remaining_target = max(0, float(target_qty) - sold_qty)
        max_sellable = rule.quantize_qty(max(0, total_qty - min_qty))
        sell_qty = min(rule.quantize_qty(remaining_target, round_up=True), max_sellable)
        if sell_qty < min_qty:
            return [SplitSignal(
                ticker=rule.ticker, lot_id=None, action=OrderAction.SELL,
                quantity=0, price=current_price,
                reason="상승→횡보 선제청산 대기 - 최소 1단위 잔량 보존",
                pct_change=0.0, is_blocked=True,
            )]

        avg_buy = sum(l.buy_price * l.quantity for l in lots) / total_qty
        change = (current_price - avg_buy) / avg_buy * 100 if avg_buy else 0.0
        bars = rule.uptrend_sideways_transition_confirm_bars
        return [SplitSignal(
            ticker=rule.ticker, lot_id=None, action=OrderAction.SELL,
            quantity=sell_qty, price=current_price,
            reason=f"장기 상승·단기 횡보 전환 {bars}일 확정 선제 {pct:g}% 청산",
            pct_change=change, level=max(l.level for l in lots),
            transition_partial_liquidation=True,
            exit_trigger="uptrend_sideways_transition",
            exit_long_regime=str(Regime.UPTREND),
            exit_short_regime=str(Regime.SIDEWAYS),
        )]

    @staticmethod
    def _can_reenter_after_full_liquidation(reading, multi: dict, current_price: float) -> bool:
        return (
            multi["long"] in (Regime.SIDEWAYS, Regime.UPTREND)
            and multi["short"] == Regime.UPTREND
            and not math.isnan(reading.channel_mid)
            and current_price > reading.channel_mid
        )

    @staticmethod
    def _buy_blocked_signal(rule, current_price: float, reason: str) -> SplitSignal:
        return SplitSignal(
            ticker=rule.ticker, lot_id=None, action=OrderAction.BUY, quantity=0,
            price=current_price, reason=reason, pct_change=0.0, is_blocked=True,
        )

    def _aligned_downtrend_liquidation(self, rule, lots, current_price: float) -> List[SplitSignal]:
        total_qty = sum(l.quantity for l in lots)
        total_cost = sum(l.buy_price * l.quantity for l in lots)
        avg_buy = total_cost / total_qty if total_qty else 0.0
        pct = (current_price - avg_buy) / avg_buy * 100 if avg_buy else 0.0
        return [SplitSignal(
            ticker=rule.ticker, lot_id=None, action=OrderAction.SELL,
            quantity=total_qty, price=current_price,
            reason=f"장·단기 하락 정렬 전량 청산 ({pct:+.1f}%)",
            pct_change=pct, level=max(l.level for l in lots),
            regime_liquidation=True, reentry_gate="resistance",
        )]

    def _effective_sell_threshold(self, rule: StockRule, lot: PositionLot) -> float:
        # 이미 시작된 trailing은 과거의 활성 기준·고점을 보존한다.
        multiplier = 1.0 if lot.trailing_highest_price is not None else self._active_sell_multiplier
        return rule.sell_threshold_at(lot.level) * multiplier

    def _evaluate_sell(
        self,
        rule: StockRule,
        last_lot: PositionLot,
        current_price: float,
    ) -> Optional[SplitSignal]:
        """마지막 차수 lot의 매도 여부를 평가한다.
        트레일링 스톱이 설정되어 있다면 활성화 후 하락을 추적하며 매도한다."""
        pct_change = (current_price - last_lot.buy_price) / last_lot.buy_price * 100
        sell_threshold = self._effective_sell_threshold(rule, last_lot)
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
        bypass_reentry_guard: bool = False,
        amount_multiplier: float = 1.0,
        entry_trigger: Optional[str] = None,
    ) -> Optional[SplitSignal]:
        """보유 lot이 없을 때 1차수 초기 매수를 평가한다."""
        passed, reason = (
            (True, "")
            if bypass_reentry_guard
            else self._passes_reentry_guard(rule, current_price, last_sell_price)
        )
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

        buy_amount = rule.buy_amount_at(1) * amount_multiplier
        buy_qty = rule.quantize_qty(buy_amount / current_price)
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
                f"초기 매수 Lv1 {format_qty(buy_qty, rule.market_type)} @{format_money(current_price, rule.market_type)}"
            )
        return SplitSignal(
            ticker=rule.ticker,
            lot_id=None,
            action=OrderAction.BUY,
            quantity=buy_qty,
            price=current_price,
            reason=("단계형 반등 탐색 매수 Lv1"
                    if entry_trigger and entry_trigger.startswith("staged_rebound_probe_")
                    else "초기 매수 Lv1"),
            pct_change=0.0,
            level=1,
            entry_trigger=entry_trigger,
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

        if pct_from_sell <= rule.reentry_guard_pct:
            return True, ""

        reason = (
            f"재진입 가드: 직전 매도가 {format_money(last_sell_price, rule.market_type)} 대비 "
            f"{pct_from_sell:+.2f}% > 임계 {rule.reentry_guard_pct:+.2f}% -> 진입 대기 중"
        )
        if self._logger:
            self._logger.info(f"[{display_ticker(rule.ticker)}] {reason}")
        return False, reason

    def price_anomaly_signal(
        self, rule: StockRule, current_price: float, ohlc_window,
    ) -> Optional[SplitSignal]:
        """현재가와 전일 종가의 단절을 감지해 종목 거래 차단 신호를 만든다."""
        if not math.isfinite(current_price) or current_price <= 0:
            return None
        if ohlc_window is None:
            return None
        try:
            closes = ohlc_window["Close"].dropna()
            if closes.empty:
                return None
            previous_close = float(closes.iloc[-1])
        except (KeyError, TypeError, ValueError, IndexError):
            return None
        if not math.isfinite(previous_close) or previous_close <= 0:
            return None

        pct_from_previous_close = (
            (current_price - previous_close) / previous_close * 100
        )
        if abs(pct_from_previous_close) < self.price_anomaly_threshold:
            return None
        reason = (
            f"가격 이격 과다({pct_from_previous_close:+.1f}%): 액면분할/병합 확인 필요 "
            f"(전일 종가 {format_money(previous_close, rule.market_type)} "
            f"vs 현재 {format_money(current_price, rule.market_type)})"
        )
        if self._logger:
            self._logger.warning(f"[{rule.ticker}] {reason}")
        return SplitSignal(
            ticker=rule.ticker,
            lot_id=None,
            action=OrderAction.BUY,
            quantity=0,
            price=current_price,
            reason=reason,
            pct_change=pct_from_previous_close,
            is_blocked=True,
        )

    def _passes_exposure_guard(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
        buy_qty: float,
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
        exposure_limit = self._active_exposure_limit
        if exposure_limit is None:
            exposure_limit = rule.max_exposure_pct
        if exposure_limit is None:
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

        if after_pct > exposure_limit:
            current_pct = current_holding_value / total_value * 100
            reason = (
                f"비중 상한 초과: 현재 {current_pct:.1f}% + 매수 예정 {after_pct - current_pct:.1f}% "
                f"= {after_pct:.1f}% > 상한 {exposure_limit:.1f}%"
            )
            if self._logger:
                self._logger.info(f"[{display_ticker(rule.ticker)}] {reason} -> 매수 보류")
            return False, reason

        return True, ""

    def _passes_cash_guard(
        self,
        rule: StockRule,
        current_price: float,
        buy_qty: float,
        portfolio: Optional[Portfolio],
    ) -> tuple[bool, str]:
        """잔고 부족 여부를 확인한다."""
        if portfolio is None:
            return True, ""

        # 1. 최소 주문 단위(주식=1주, 코인=10^-precision)조차 살 수 없는 경우
        min_order_cost = current_price * rule.min_order_qty()
        if portfolio.total_cash < min_order_cost:
            reason = (
                f"현금 부족: 보유 현금 {format_money(portfolio.total_cash, rule.market_type)} "
                f"< 최소 주문 금액 {format_money(min_order_cost, rule.market_type)} (최소 수량도 매수 불가)"
            )
            return False, reason

        # 2. 계획된 수량을 살 현금이 부족한 경우
        required_cash = buy_qty * current_price
        if portfolio.total_cash < required_cash:
            reason = (
                f"현금 부족: 보유 현금 {format_money(portfolio.total_cash, rule.market_type)} "
                f"< 매수 예정 금액 {format_money(required_cash, rule.market_type)} ({format_qty(buy_qty, rule.market_type)})"
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
        buy_qty = rule.quantize_qty(buy_amount / current_price)
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
                        f"{pct_from_ref:+.1f}% -> 추가 매수 Lv{next_level} {format_qty(buy_qty, rule.market_type)} "
                        f"@{format_money(current_price, rule.market_type)} "
                        f"(원래 기준 Lv{last_lot.level} {format_money(last_lot.buy_price, rule.market_type)})"
                    )
                else:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] Lv{last_lot.level} "
                        f"매수가 {format_money(last_lot.buy_price, rule.market_type)} 대비 "
                        f"{pct_from_ref:+.1f}% -> 추가 매수 Lv{next_level} {format_qty(buy_qty, rule.market_type)} "
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

    def _resolve_regime(
        self, reading, st: dict, ticker: str, current_price: float,
        evaluation_date: Optional[str] = None,
    ) -> Regime:
        """레짐 히스테리시스를 적용해 유효 레짐을 반환한다 (st를 in-place 변이).

        - 상승 진입은 REGIME_CONFIRM_BARS 연속 UPTREND 판정 후에만.
        - 일단 상승에 진입하면 탈출은 추세 이탈(전량 청산)로만 이뤄진다.
          (소프트 ADX 하락으로는 매도 churn을 일으키지 않음)
        """
        if st.get("regime") == "uptrend":
            return Regime.UPTREND

        # 동일 날짜의 반복 실행은 한 번만 세고, 장중 상승 조건이 해제되면
        # 그 날짜의 카운트를 취소한다. 하락 레짐/하단 이탈의 날짜 기준과 동일하다.
        today_str = evaluation_date or datetime.date.today().isoformat()
        days: list = st.get("uptrend_days", [])
        prev_date: str = st.get("uptrend_prev_date", "")
        if prev_date and prev_date != today_str:
            if st.get("uptrend_today_state", "") != "uptrend":
                days = []
            st["uptrend_today_state"] = ""
        st["uptrend_prev_date"] = today_str

        if reading.regime == Regime.UPTREND:
            st["uptrend_today_state"] = "uptrend"
            if today_str not in days:
                days.append(today_str)
            st["uptrend_days"] = days
            st["uptrend_streak"] = len(days)
        else:
            st["uptrend_today_state"] = ""
            if today_str in days:
                days.remove(today_str)
            st["uptrend_days"] = days
            st["uptrend_streak"] = 0

        if len(days) >= REGIME_CONFIRM_BARS:
            st["regime"] = "uptrend"
            st["adds"] = 0
            st["last_add_swing_high"] = reading.swing_high
            st["last_add_price"] = current_price
            st["uptrend_streak"] = 0
            st["uptrend_days"] = []
            st["uptrend_today_state"] = ""
            st["uptrend_prev_date"] = ""
            if self._logger:
                if math.isnan(reading.adx):
                    # 채널 분류기: ADX 미사용 -> 기울기 기준 메시지
                    self._logger.info(
                        f"[{display_ticker(ticker)}] 강한 상승 추세 진입 확정! "
                        f"(회귀 채널 기울기 {reading.channel_slope_pct:+.2f}% 밴드 상향 돌파, "
                        f"{REGIME_CONFIRM_BARS}일 연속)"
                    )
                else:
                    self._logger.info(
                        f"[{display_ticker(ticker)}] 강한 상승 추세 진입 확정! "
                        f"(ADX {reading.adx:.1f} 돌파, 20EMA/50MA/200MA 정배열 정렬 상승 국면)"
                    )
            return Regime.UPTREND
        return Regime.SIDEWAYS

    def _resolve_downtrend_block(
        self, reading, st: dict, ticker: str,
        evaluation_date: Optional[str] = None,
    ) -> bool:
        """DOWNTREND 매수 차단 래치를 관리한다 (st in-place 변이).

        진입: 서로 다른 거래일에 DOWNTREND_CONFIRM_BARS일 연속 DOWNTREND -> "active" 래치
        유지: SIDEWAYS 1봉으로 해제되지 않음 (UPTREND 래치와 대칭)
        탈출: 서로 다른 거래일에 비-DOWNTREND DOWNTREND_CONFIRM_BARS일 연속 -> 래치 해제

        같은 날짜에 봇이 여러 번 실행될 수 있으므로, 당일의 마지막 판정만 카운트한다.
        """
        today_str = evaluation_date or datetime.date.today().isoformat()

        if st.get("downtrend") == "active":
            exit_days: list = st.get("downtrend_exit_days", [])
            prev_date: str = st.get("downtrend_exit_prev_date", "")
            if prev_date and prev_date != today_str:
                if st.get("downtrend_exit_today_state", "") != "non_downtrend":
                    exit_days = []
                st["downtrend_exit_today_state"] = ""
            st["downtrend_exit_prev_date"] = today_str

            if reading.regime != Regime.DOWNTREND:
                st["downtrend_exit_today_state"] = "non_downtrend"
                if today_str not in exit_days:
                    exit_days.append(today_str)
                st["downtrend_exit_days"] = exit_days
                st["downtrend_exit_streak"] = len(exit_days)
                if len(exit_days) >= DOWNTREND_CONFIRM_BARS:
                    st["downtrend"] = None
                    st.pop("downtrend_partially_liquidated", None)
                    st["downtrend_streak"] = 0
                    st["downtrend_exit_streak"] = 0
                    st["downtrend_exit_days"] = []
                    st["downtrend_exit_today_state"] = ""
                    st["downtrend_exit_prev_date"] = ""
                    if self._logger:
                        self._logger.info(
                            f"[{display_ticker(ticker)}] 하락 추세 {DOWNTREND_CONFIRM_BARS}일 해제 - 매수 차단 해제"
                        )
                    return False
            else:
                st["downtrend_exit_today_state"] = ""
                if today_str in exit_days:
                    exit_days.remove(today_str)
                st["downtrend_exit_days"] = exit_days
                st["downtrend_exit_streak"] = 0
            return True

        days: list = st.get("downtrend_days", [])
        prev_date: str = st.get("downtrend_prev_date", "")
        if prev_date and prev_date != today_str:
            if st.get("downtrend_today_state", "") != "downtrend":
                days = []
            st["downtrend_today_state"] = ""
        st["downtrend_prev_date"] = today_str

        if reading.regime == Regime.DOWNTREND:
            st["downtrend_today_state"] = "downtrend"
            if today_str not in days:
                days.append(today_str)
            st["downtrend_days"] = days
            st["downtrend_streak"] = len(days)
        else:
            st["downtrend_today_state"] = ""
            if today_str in days:
                days.remove(today_str)
            st["downtrend_days"] = days
            st["downtrend_streak"] = 0
            st["downtrend_exit_streak"] = 0

        if len(days) >= DOWNTREND_CONFIRM_BARS:
            st["downtrend"] = "active"
            st["downtrend_streak"] = 0
            st["downtrend_days"] = []
            st["downtrend_today_state"] = ""
            st["downtrend_prev_date"] = ""
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(ticker)}] DOWNTREND {DOWNTREND_CONFIRM_BARS}일 확정 - 매수 차단"
                )
            return True

        return False

    def _evaluate_channel_exit(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
        reading,
        st: dict,
        downtrend_blocked: bool,
        portfolio: Optional[Portfolio],
        multi: Optional[dict] = None,
        evaluation_date: Optional[str] = None,
    ) -> Optional[List[SplitSignal]]:
        """채널 모드(regime_algo="channel")의 이탈 판정을 통상 흐름 앞에서 수행한다.

        이탈 = (하락 래치 확정) OR (현재가가 하단 채널선을 BREAKDOWN_CONFIRM_BARS봉
        연속 하향 돌파). 단봉 스파이크로 청산되지 않도록 상승/하락 확정과 동일한
        연속 확정 방식을 쓴다. 청산 방식은 trendbreak_partial_sell_pct
        (50=절반+추종 데드라인, 100=전량)를 따른다.

        반환:
            List -> 이탈 처리 신호 (빈 리스트 포함: 평가 보류/대기)
            None -> 이탈 아님. 호출부가 상승/횡보 통상 흐름을 계속한다.
        """
        lock_active = st.get("trailing_lock") is not None
        support = reading.channel_support

        if math.isnan(support):
            # 히스토리 부족(UNKNOWN)/데이터 결손 -> 이탈 판정 불가.
            # 추종 데드라인 추적 중엔 안전을 위해 평가를 보류하고,
            # 아니면 레짐 OFF와 동일하게 통상 흐름으로 폴백한다 (ma_adx UNKNOWN과 대칭).
            if lock_active:
                if self._logger:
                    self._logger.warning(
                        f"[{display_ticker(rule.ticker)}] ⚠️ 채널 지표 결손(NaN) - "
                        "추종 데드라인 추적 중이므로 매매 평가를 보류합니다."
                    )
                return []
            return None

        # 1. 추종 데드라인 활성 -> 전용 평가 (회복/추가 하락/대기)
        #    횡보장 분할 청산 후에도 잔량 추적이 유지되도록 레짐과 무관하게 최우선 처리.
        if lock_active:
            return self._evaluate_trailing_lock(
                rule, ticker_lots, current_price, reading, st, portfolio
            )

        # 2. 하락 래치 확정 -> 이탈 청산
        if downtrend_blocked:
            # 동일 하락 래치 회차 내에서 이미 분할 청산이 이뤄졌다면 2차 중복 이탈 청산 방지
            if st.get("downtrend_partially_liquidated"):
                if self._logger:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] 하락 래치 진행 중 - "
                        "이미 분할 청산을 거쳤으므로 동일 래치 내 2차 이탈 청산 스킵 (신규 매수만 차단 유지)"
                    )
                return None

            # 하락 래치가 청산 판단을 넘겨받았다. 래치는 regime_state에 남아
            # 주문이 실패해도 다음 사이클에 다시 청산을 시도하므로,
            # 하단 이탈 카운터를 여기서 비워도 확정을 잃지 않는다.
            clear_breakdown_confirmation(st)
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] 채널 기울기 하락 전환 확정 "
                    f"({reading.channel_slope_pct:+.2f}%/{rule.channel_lookback}봉) -> 이탈 청산 진행"
                )
            return self._handle_trendbreak(
                rule, ticker_lots, current_price, reading, st,
                reentry_gate="resistance",
                exit_reason="단기 채널 하락 전환",
                exit_trigger="channel_downtrend_transition",
                exit_short_regime=str(reading.regime),
                exit_long_regime=str(multi["long"]) if multi else None,
            )

        # 3. 상승/횡보 중 하단 채널선 하향 돌파 -> 연속 일(日) 확정 후 이탈 청산
        #    동일 날짜에 여러 사이클이 돌면 마지막 사이클 결과가 해당 날짜의 최종 상태.
        breakdown_line, breakdown_mode, used_atr_fallback = channel_breakdown_line(
            support, reading.atr, rule.channel_breakdown_atr_multiplier,
            rule.channel_breakdown_tolerance_pct,
        )
        if used_atr_fallback and self._logger:
            self._logger.warning(
                f"[{display_ticker(rule.ticker)}] ATR breakdown value invalid; "
                f"falling back to tolerance (-{rule.channel_breakdown_tolerance_pct}%)."
            )
        breakdown_rule_text = (
            f"ATR {rule.channel_breakdown_atr_multiplier}xATR"
            if breakdown_mode == "atr"
            else f"tolerance -{rule.channel_breakdown_tolerance_pct}%"
        )
        today_str = evaluation_date or datetime.date.today().isoformat()
        bd_days: list = st.get("breakdown_days", [])
        bd_prev_date: str = st.get("breakdown_prev_date", "")

        if bd_prev_date and bd_prev_date != today_str:
            prev_state = st.get("breakdown_today_state", "")
            if prev_state != "bd":
                bd_days = []
            st["breakdown_today_state"] = ""

        st["breakdown_prev_date"] = today_str

        if current_price < breakdown_line:
            st["breakdown_today_state"] = "bd"
            if today_str not in bd_days:
                bd_days.append(today_str)
            st["breakdown_days"] = bd_days

            if len(bd_days) < BREAKDOWN_CONFIRM_BARS:
                if self._logger:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] 하단 채널선 이탈 감지 "
                        f"({len(bd_days)}/{BREAKDOWN_CONFIRM_BARS}일, "
                        f"현재가 {format_money(current_price, rule.market_type)} < "
                        f"이탈선 {format_money(breakdown_line, rule.market_type)}, "
                        f"{breakdown_rule_text}) -> 확정 대기"
                    )
                return None

            # 확정 상태는 여기서 지우지 않는다. 청산이 실제로 반영될 때
            # (엔진의 체결 확정 경로) 비운다. 주문이 거절되면 확정을 잃고
            # 2일을 다시 세게 되는데, 리스크 관리 장치가 일시적 API 오류로
            # 지연되면 안 된다. 조건이 유지되는 한 다음 사이클에 재시도한다.
            retrying = bool(st.get("breakdown_confirmed"))
            st["breakdown_confirmed"] = True
            if self._logger:
                if retrying:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] 이탈 청산 재시도 "
                        f"(직전 사이클 미체결, 현재가 "
                        f"{format_money(current_price, rule.market_type)} < "
                        f"이탈선 {format_money(breakdown_line, rule.market_type)}, "
                        f"{breakdown_rule_text})"
                    )
                else:
                    self._logger.info(
                        f"[{display_ticker(rule.ticker)}] 하단 채널선 이탈 확정 "
                        f"({BREAKDOWN_CONFIRM_BARS}일 연속, "
                        f"현재가 {format_money(current_price, rule.market_type)} < "
                        f"이탈선 {format_money(breakdown_line, rule.market_type)}, "
                        f"채널하단 {format_money(support, rule.market_type)}, "
                        f"{breakdown_rule_text}) -> 이탈 청산 진행"
                    )
            short_label = {
                Regime.UPTREND: "상승",
                Regime.SIDEWAYS: "횡보",
            }.get(reading.regime, "단기")
            return self._handle_trendbreak(
                rule, ticker_lots, current_price, reading, st,
                reentry_gate="midline",
                exit_reason=f"{short_label} 채널 하단 이탈",
                exit_trigger="channel_lower_break",
                exit_short_regime=str(reading.regime),
                exit_long_regime=str(multi["long"]) if multi else None,
            )

        # 이탈선 위로 회복 -> 청산 근거가 사라졌으므로 확정도 취소한다.
        # (재시도는 조건이 유지되는 동안에만 계속돼야 한다)
        if st.pop("breakdown_confirmed", None) and self._logger:
            self._logger.info(
                f"[{display_ticker(rule.ticker)}] 이탈선 회복 -> 청산 확정 취소 "
                f"(현재가 {format_money(current_price, rule.market_type)} >= "
                f"이탈선 {format_money(breakdown_line, rule.market_type)}, "
                f"{breakdown_rule_text})"
            )
        st["breakdown_today_state"] = ""
        if today_str in bd_days:
            bd_days.remove(today_str)
        st["breakdown_days"] = bd_days
        return None

    def _evaluate_uptrend(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
        reading,
        st: dict,
        portfolio: Optional[Portfolio],
        evaluation_date: Optional[str] = None,
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
        # 채널 모드는 이탈(하단 채널선)을 evaluate_stock 상단 _evaluate_channel_exit에서
        # 이미 판정했으므로 여기서는 건너뛴다.

        if rule.regime_algo != "channel":
            # 지표 결손(NaN) 감지 시 안전 최우선 필터: 오작동 및 청산 누락 방지
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
            rule, ticker_lots, last_lot, current_price, reading, st, portfolio,
            evaluation_date=evaluation_date,
        )
        return [add_signal] if add_signal else []

    def _handle_trendbreak(
        self,
        rule: StockRule,
        ticker_lots: List[PositionLot],
        current_price: float,
        reading,
        st: dict,
        reentry_gate: str = "resistance",
        exit_reason: str = "추세 이탈",
        exit_trigger: str = "trend_break",
        exit_long_regime: Optional[str] = None,
        exit_short_regime: Optional[str] = None,
    ) -> List[SplitSignal]:
        """이탈 원인별 전량 청산 또는 분할 매도+추종 데드라인 활성화를 결정한다."""
        total_qty = sum(l.quantity for l in ticker_lots)
        total_cost = sum(l.buy_price * l.quantity for l in ticker_lots)
        avg_buy = total_cost / total_qty if total_qty else 0.0
        pct = (current_price - avg_buy) / avg_buy * 100 if avg_buy else 0.0
        max_level = max(l.level for l in ticker_lots)

        partial_pct = rule.trendbreak_partial_sell_pct

        # 이탈 기준선 로그 문구 (채널 모드는 하단 채널선)
        if rule.regime_algo == "channel":
            indicator_txt = f"채널하단 {format_money(reading.channel_support, rule.market_type)}"
        else:
            indicator_txt = (
                f"50MA {format_money(reading.sma50, rule.market_type)}, "
                f"Chandelier {format_money(reading.chandelier_stop, rule.market_type)}"
            )

        # 100%이면 기존 전량 청산 (하위 호환)
        if partial_pct >= 100.0:
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] {exit_reason} -> 통합 전량 청산(Bulk) "
                    f"{format_qty(total_qty, rule.market_type)} (평단 {format_money(avg_buy, rule.market_type)}, "
                    f"현재가 {format_money(current_price, rule.market_type)}, "
                    f"{indicator_txt})"
                )
            return [SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.SELL,
                quantity=total_qty,
                price=current_price,
                reason=f"{exit_reason} 통합 전량 청산(Bulk Sell, {format_qty(total_qty, rule.market_type)} {pct:+.1f}%)",
                pct_change=pct,
                level=max_level,
                regime_liquidation=True,
                reentry_gate=reentry_gate,
                exit_trigger=exit_trigger,
                exit_long_regime=exit_long_regime,
                exit_short_regime=exit_short_regime,
            )]

        # 분할 매도: partial_pct% 만큼 즉시 매도, 나머지는 추종 데드라인
        sell_qty = rule.quantize_qty(total_qty * partial_pct / 100, round_up=True) if partial_pct > 0 else 0

        # 올림으로 sell_qty가 total_qty 이상이 되면 전량 청산으로 처리 (상태 오염 방지)
        if sell_qty >= total_qty:
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] {exit_reason} -> 수량 부족으로 전량 청산(Bulk) "
                    f"{format_qty(total_qty, rule.market_type)} (평단 {format_money(avg_buy, rule.market_type)}, "
                    f"현재가 {format_money(current_price, rule.market_type)})"
                )
            return [SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.SELL,
                quantity=total_qty,
                price=current_price,
                reason=f"{exit_reason} 전량 청산(수량 부족, {format_qty(total_qty, rule.market_type)} {pct:+.1f}%)",
                pct_change=pct,
                level=max_level,
                regime_liquidation=True,
                reentry_gate=reentry_gate,
                exit_trigger=exit_trigger,
                exit_long_regime=exit_long_regime,
                exit_short_regime=exit_short_regime,
            )]

        if sell_qty <= 0:
            # 0%: 즉시 매도 없이 전량 추종 데드라인만 활성화
            # 상태 갱신은 여기서 직접 수행 (매도 체결이 없으므로 엔진 경유 불가)
            st["downtrend_partially_liquidated"] = True
            trailing_lock = {
                "active": True,
                "lock_price": current_price,
                "drop_pct": rule.trendbreak_trailing_drop_pct,
                "reentry_gate": reentry_gate,
            }
            if exit_trigger is not None:
                trailing_lock["exit_trigger"] = exit_trigger
            if exit_long_regime is not None:
                trailing_lock["exit_long_regime"] = exit_long_regime
            if exit_short_regime is not None:
                trailing_lock["exit_short_regime"] = exit_short_regime
            st["trailing_lock"] = trailing_lock
            # 주문 없이 상태 전환이 끝났으므로 여기가 곧 커밋 지점이다.
            clear_breakdown_confirmation(st)
            if self._logger:
                stop_price = current_price * (1 - rule.trendbreak_trailing_drop_pct / 100)
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] {exit_reason} -> 추종 데드라인 활성화 "
                    f"(즉시 매도 0%, 전량 {format_qty(total_qty, rule.market_type)} 추적, "
                    f"기준가 {format_money(current_price, rule.market_type)}, "
                    f"청산선 {format_money(stop_price, rule.market_type)})"
                )
            return []

        if self._logger:
            remain_qty = total_qty - sell_qty
            stop_price = current_price * (1 - rule.trendbreak_trailing_drop_pct / 100)
            self._logger.info(
                f"[{display_ticker(rule.ticker)}] {exit_reason} -> 분할 청산 "
                f"{format_qty(sell_qty, rule.market_type)}/{format_qty(total_qty, rule.market_type)} ({partial_pct:.0f}%) 즉시 매도, "
                f"잔량 {format_qty(remain_qty, rule.market_type)} 추종 데드라인 "
                f"(기준가 {format_money(current_price, rule.market_type)}, "
                f"청산선 {format_money(stop_price, rule.market_type)}, "
                f"평단 {format_money(avg_buy, rule.market_type)}, "
                f"{indicator_txt})"
            )
        return [SplitSignal(
            ticker=rule.ticker,
            lot_id=None,
            action=OrderAction.SELL,
            quantity=sell_qty,
            price=current_price,
            reason=f"{exit_reason} 분할 청산({format_qty(sell_qty, rule.market_type)}/{format_qty(total_qty, rule.market_type)}, {pct:+.1f}%)",
            pct_change=pct,
            level=max_level,
            regime_partial_liquidation=True,
            reentry_gate=reentry_gate,
            exit_trigger=exit_trigger,
            exit_long_regime=exit_long_regime,
            exit_short_regime=exit_short_regime,
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

        - 이탈 기준선 위로 회복 -> 데드라인 해제 (이탈선 회복, 잔량 보유 유지)
        - lock_price 대비 추가 하락 -> 잔량 전량 청산
        - 그 외 -> 대기 (매수/매도 없음)
        """
        lock = st["trailing_lock"]
        lock_price = lock["lock_price"]
        drop_pct = lock["drop_pct"]

        # 이탈 기준선: 채널 모드는 하단 채널선, ma_adx는 50MA 또는 Chandelier 스톱
        if rule.regime_algo == "channel":
            target_indicator = reading.channel_support
        elif rule.trendbreak_use_sma50:
            target_indicator = reading.sma50
        else:
            target_indicator = reading.chandelier_stop

        # 지표 결손 안전 필터
        if math.isnan(target_indicator):
            if self._logger:
                self._logger.warning(
                    f"[{display_ticker(rule.ticker)}] ⚠️ 추종 데드라인: 지표 결손(NaN) "
                    "-> 매매 평가 보류"
                )
            return []

        # 1. 회복 판정: 이탈 기준선 위로 복귀?
        recovered = current_price >= target_indicator

        if recovered:
            del st["trailing_lock"]
            if self._logger:
                self._logger.info(
                    f"[{display_ticker(rule.ticker)}] ✅ 추종 데드라인 해제! "
                    f"가격 {format_money(current_price, rule.market_type)}이 "
                    f"이탈 기준선 위로 회복 "
                    f"(잔량 {format_qty(sum(l.quantity for l in ticker_lots), rule.market_type)} 보유 유지)"
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
                    f"-> 잔량 {format_qty(total_qty, rule.market_type)} 전량 청산"
                )
            # trailing_lock 상태 리셋은 엔진에서 체결 확정 시 수행
            return [SplitSignal(
                ticker=rule.ticker,
                lot_id=None,
                action=OrderAction.SELL,
                quantity=total_qty,
                price=current_price,
                reason=f"추종 데드라인 발동 잔량 청산({format_qty(total_qty, rule.market_type)}, 기준가 대비 -{drop:.1f}%)",
                pct_change=pct,
                level=max_level,
                regime_liquidation=True,  # 전량 청산 -> 레짐 리셋
                reentry_gate=lock.get("reentry_gate", "resistance"),
                exit_trigger=lock.get("exit_trigger"),
                exit_long_regime=lock.get("exit_long_regime"),
                exit_short_regime=lock.get("exit_short_regime"),
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
        evaluation_date: Optional[str] = None,
    ) -> Optional[SplitSignal]:
        """상승 추세 눌림 매수(불타기) 평가. 새 고점 게이트 + 눌림/반등 확인."""
        # 한국시간 기준으로 체결일과 그 다음 날에는 같은 종목의 눌림 add를 막는다.
        # 날짜는 신호 생성이 아닌 체결 확정 시 엔진이 기록하므로, 거절/미체결 주문은 재시도된다.
        today_str = evaluation_date or datetime.date.today().isoformat()
        last_add_date = st.get("last_uptrend_add_date")
        if last_add_date:
            try:
                cooldown_end = datetime.date.fromisoformat(last_add_date) + datetime.timedelta(days=1)
                if datetime.date.fromisoformat(today_str) <= cooldown_end:
                    if self._logger:
                        self._logger.debug(
                            f"  [{display_ticker(rule.ticker)}] 불타기 대기 | "
                            f"최근 눌림 매수일 {last_add_date}, 쿨다운 종료 {cooldown_end.isoformat()}"
                        )
                    return None
            except (TypeError, ValueError):
                # 이전 상태 파일의 잘못된 날짜는 기존 동작을 보존한다.
                pass

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
        if rule.trend_entry_mode in ("rebound", "staged_rebound"):
            prefix = "pullback_rebound_"
            armed = bool(st.get(prefix + "armed"))
            if not armed:
                if in_band:
                    st[prefix + "armed"] = True
                    st[prefix + "start_date"] = today_str
                    st[prefix + "low"] = current_price
                    st[prefix + "wait_days"] = [today_str]
                return None

            wait_days = st.get(prefix + "wait_days", [])
            if today_str not in wait_days:
                wait_days.append(today_str)
            st[prefix + "wait_days"] = wait_days
            st[prefix + "low"] = min(float(st.get(prefix + "low", current_price)), current_price)
            if len(wait_days) > rule.pullback_rebound_max_wait_bars:
                for key in ("armed", "start_date", "low", "wait_days", "confirm_days"):
                    st.pop(prefix + key, None)
                return None
            # 눌림을 관측한 다음 거래일부터 EMA20 재돌파와 전일 대비 상승을 확인한다.
            rebound_ok = (
                today_str != st.get(prefix + "start_date")
                and in_band
                and current_price > ema20
                and current_price > reading.close
            )
            confirm_days = st.get(prefix + "confirm_days", [])
            if rebound_ok:
                if today_str not in confirm_days:
                    confirm_days.append(today_str)
            else:
                confirm_days = []
            st[prefix + "confirm_days"] = confirm_days
            if len(confirm_days) < rule.pullback_rebound_confirm_bars:
                return None
            bounced = True
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
        buy_qty = rule.quantize_qty(amount / current_price)
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
                f"{format_qty(buy_qty, rule.market_type)} @{format_money(current_price, rule.market_type)} "
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
            entry_trigger=("pullback_rebound_add"
                           if rule.trend_entry_mode in ("rebound", "staged_rebound") else None),
        )

