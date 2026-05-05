# src/core/engine/base.py
import time
from dataclasses import replace
from datetime import datetime
from typing import List, Optional, Set

from src.core.interfaces import IBrokerAdapter, IRepository, ILogger, INotifier
from src.core.models import (
    StockRule,
    PositionLot,
    Portfolio,
    Order,
    OrderAction,
    TradeExecution,
    ExecutionStatus,
    SplitSignal,
    DayResult,
)
from src.core.logic import SplitEvaluator, detect_mismatches, build_dashboard_status
from src.core.engine.registry import register_engine
from src.utils.ticker_reader import display_ticker


@register_engine(color="#1f77b4")
class MagicSplitEngine:
    """MagicSplit 매매 사이클 엔진.

    run_one_cycle()이 전체 사이클의 뼈대를 정의한다.
    종목별로 순차 실행: 평가 -> 주문 -> 포지션 반영 -> 다음 종목.

    환경별 차이는 주입되는 구현체(broker, repo, notifier)가 담당하며,
    비즈니스 로직 자체는 단일 위치(이 클래스)에서만 관리된다.
    """

    def __init__(
        self,
        broker: IBrokerAdapter,
        repo: IRepository,
        logger: ILogger,
        stock_rules: List[StockRule],
        notifier: Optional[INotifier] = None,
        is_live_trading: bool = False,
    ):
        self.broker = broker
        self.repo = repo
        self.logger = logger
        self.evaluator = SplitEvaluator(logger=logger)
        self.stock_rules = [r for r in stock_rules if r.enabled]
        self.all_tickers = [r.ticker for r in self.stock_rules]
        self.notifier = notifier
        self.is_live_trading = is_live_trading

    def run_one_cycle(self, sim_date: Optional[str] = None) -> DayResult:
        """하루치 매매 사이클 전체를 실행한다.

        종목별 순차 실행: 각 종목을 평가 -> 주문 -> 포지션 반영 후 다음 종목으로.

        Args:
            sim_date: 시뮬레이션 날짜 ("YYYY-MM-DD").
                None이면 오늘 날짜 사용 (실시간 모드).

        Returns:
            DayResult: 사이클 실행 결과
        """
        today = sim_date or datetime.now().strftime("%Y-%m-%d")

        all_signals: List[SplitSignal] = []
        all_executions: List[TradeExecution] = []
        failed_tickers: List[str] = []
        portfolio: Optional[Portfolio] = None
        positions: Optional[List[PositionLot]] = None

        try:
            # Step 1: 포트폴리오 조회 + 실시간 가격 (전 종목 일괄)
            self.logger.info(">>> Step 1: Portfolio & Price Fetch")
            portfolio = self.get_portfolio()
            self.logger.info(
                f"Portfolio: Cash=${portfolio.total_cash:,.0f}, "
                f"Value=${portfolio.total_value:,.0f}"
            )

            # Step 2: 기존 분할 포지션 로드
            self.logger.info(">>> Step 2: Load Positions")
            positions = self.repo.load_positions()
            self.logger.info(f"Loaded {len(positions)} position lot(s)")

            # 재진입 가드 및 동적 재매수용 직전 매도가 조회.
            # 매도 체결 시 갱신, 매수 체결 시 초기화.
            last_sell_prices: dict = self.repo.load_last_sell_prices()

            # Step 2.1: 상태 전환 감지 및 자동 초기화 (OFF -> ON)
            self._handle_state_transitions(positions, last_sell_prices)

            # Step 2.5: 브로커 수량 ↔ positions 수량 합 불일치 검사
            # 불일치 종목은 이번 사이클에서 매매 중단 (자동 보정 미지원)
            halted_tickers = self._check_reconcile(positions, portfolio)

            # Step 3~5: 종목별 순차 실행
            for rule in self.stock_rules:
                if rule.ticker in halted_tickers:
                    self.logger.warning(
                        f"[{display_ticker(rule.ticker)}] 수량 불일치로 매매 중단. "
                        f"scripts/reconcile_positions.py 로 보정 후 재실행."
                    )
                    failed_tickers.append(rule.ticker)
                    continue
                try:
                    self.logger.info(f">>> Processing {display_ticker(rule.ticker)}")

                    # 3a. 해당 종목 신호 평가
                    signals = self.evaluator.evaluate_stock(
                        rule, positions, portfolio, last_sell_prices,
                    )

                    # 신호 3-way 분류: blocked(경고) / info(상태보고) / active(주문)
                    blocked_signals = [s for s in signals if s.is_blocked]
                    info_signals = [s for s in signals if s.is_info]
                    active_signals = [
                        s for s in signals
                        if not s.is_blocked and not s.is_info
                    ]

                    for s in blocked_signals:
                        self._notify_alert(f"[{display_ticker(s.ticker)}] {s.reason}")
                    for s in info_signals:
                        self._notify_message(f"[{display_ticker(s.ticker)}] {s.reason}")

                    all_signals.extend(active_signals)
                    all_signals.extend(blocked_signals)
                    all_signals.extend(info_signals)

                    if not active_signals:
                        # 활성 신호가 없고, 차단/정보 신호도 없었다면 '신호 없음' 상태 로깅
                        if not blocked_signals and not info_signals:
                            self._log_no_signal_status(
                                rule, positions, portfolio, last_sell_prices,
                            )
                        continue

                    # 3b. 주문 실행
                    orders = self._signals_to_orders(active_signals)
                    executions = self._execute_stock_orders(orders)
                    self._enrich_executions(executions, active_signals)
                    all_executions.extend(executions)

                    # 3c. 포지션 즉시 반영 (다음 종목 판단에 영향)
                    if executions:
                        try:
                            positions = self._update_positions(
                                positions, signals, executions, today,
                                last_sell_prices=last_sell_prices,
                            )
                            portfolio = self._refresh_portfolio(portfolio)
                        except Exception as e:
                            disp = display_ticker(rule.ticker)
                            self.logger.error(
                                f"[{disp}] 포지션 반영 실패 "
                                f"(체결은 완료됨): {e}"
                            )
                            self._notify_alert(
                                f"[{disp}] 포지션 반영 실패 "
                                f"(체결 {len(executions)}건 완료됨): {e}"
                            )
                            failed_tickers.append(rule.ticker)
                except Exception as e:
                    disp = display_ticker(rule.ticker)
                    self.logger.error(f"[{disp}] 처리 실패: {e}")
                    self._notify_alert(f"[{disp}] Error: {e}")
                    failed_tickers.append(rule.ticker)

        except Exception as e:
            self.logger.error(f"사이클 초기화 실패: {e}")
            self._notify_alert(f"Cycle init error: {e}")
        finally:
            # Step 6: 저장 — 포트폴리오와 포지션 모두 정상 로드된 경우에만 저장
            if portfolio is not None and positions is not None:
                self.logger.info(">>> Step 6: Persist")
                self._persist(portfolio, all_signals, all_executions, positions,
                              sim_date=sim_date,
                              last_sell_prices=last_sell_prices)
            else:
                missing = []
                if portfolio is None:
                    missing.append("포트폴리오")
                if positions is None:
                    missing.append("포지션")
                msg = (
                    f">>> Step 6: {', '.join(missing)} 조회 실패로 저장 생략. "
                    f"수동 데이터 복구 필요"
                )
                self.logger.error(msg)
                self._notify_alert(f"데이터 저장 생략: {', '.join(missing)} 조회 실패. 수동 데이터 복구 필요")

        # 알림
        fail_suffix = f" (실패: {', '.join(failed_tickers)})" if failed_tickers else ""
        filled_execs = [
            e for e in all_executions
            if e.status != ExecutionStatus.REJECTED
        ]
        rejected_count = len(all_executions) - len(filled_execs)
        reject_suffix = f" (거절: {rejected_count}건)" if rejected_count > 0 else ""
        if filled_execs:
            self._notify_message(
                f"Orders Executed. Count: {len(filled_execs)}"
                f"{reject_suffix}{fail_suffix}"
            )
        elif portfolio is not None:
            self._notify_message(
                f"모니터링 완료. 신호 없음 | ${portfolio.total_value:,.0f}"
                f"{reject_suffix}{fail_suffix}"
            )
        else:
            self._notify_message(f"사이클 실패{reject_suffix}{fail_suffix}")

        final_pf = portfolio or Portfolio(
            total_cash=0, holdings={}, current_prices={},
        )
        return DayResult(
            date=today,
            signals=all_signals,
            executions=all_executions,
            final_portfolio=final_pf,
            has_orders=len(all_executions) > 0,
        )

    # ── Overridable step methods ─────────────────────────────────

    def get_portfolio(self) -> Portfolio:
        """Step 1: 포트폴리오 조회 후 실시간 가격 업데이트."""
        portfolio = self.broker.get_portfolio()
        self.logger.info("Fetching real-time prices from Broker...")
        real_time_prices = self.broker.fetch_current_prices(self.all_tickers)
        for ticker, price in real_time_prices.items():
            if price > 0:
                portfolio.current_prices[ticker] = price
        return portfolio

    def _signals_to_orders(self, signals: List[SplitSignal]) -> List[Order]:
        """SplitSignal 리스트를 Order 리스트로 변환한다."""
        orders = []
        for sig in signals:
            orders.append(Order(
                ticker=sig.ticker,
                action=sig.action,
                quantity=sig.quantity,
                price=sig.price,
            ))
        return orders

    def _execute_stock_orders(
        self,
        orders: List[Order],
    ) -> List[TradeExecution]:
        """종목 단위 주문을 실행한다."""
        if not orders:
            return []
        self.logger.info(f"Executing {len(orders)} order(s)...")
        executions = self.broker.execute_orders(orders)
        if not executions:
            self._notify_alert("Orders sent but NO execution result returned.")
        return executions

    def _check_reconcile(
        self,
        positions: List[PositionLot],
        portfolio: Portfolio,
    ) -> Set[str]:
        """브로커 보유수량과 positions 수량 합의 불일치 티커 집합을 반환한다.

        불일치 감지 시 로그 + 알림을 발송한다. 자동 보정은 수행하지 않는다.
        """
        mismatches = detect_mismatches(positions, portfolio, self.stock_rules)
        if not mismatches:
            self.logger.info(">>> Step 2.5: Reconcile OK (수량 일치)")
            return set()

        # 불일치 N건을 한 통의 알림으로 묶어 전송 — 대규모 코퍼릿 액션 등으로
        # 다수 종목이 동시에 불일치할 때 Slack 스팸을 방지한다.
        detail_lines = [
            f"[{display_ticker(m.ticker)}] Qty Mismatch: broker={m.broker_qty}, "
            f"positions={m.positions_qty} (lots={m.lot_count}, levels={m.levels})"
            for m in mismatches
        ]
        summary = (
            f">>> Step 2.5: 수량 불일치 {len(mismatches)}건 감지 — "
            f"해당 종목 매매 중단\n"
            + "\n".join(detail_lines)
            + "\n실행 권장: scripts/reconcile_positions.py"
        )
        self.logger.error(summary)
        self._notify_alert(summary)
        return {m.ticker for m in mismatches}

    def _log_no_signal_status(
        self,
        rule: StockRule,
        positions: List[PositionLot],
        portfolio: Portfolio,
        last_sell_prices: Optional[dict] = None,
    ) -> None:
        """신호 없음일 때 마지막 차수 현황을 한 줄로 요약 로깅한다.

        매수가 대비 현재가/수익률과 매수·매도 임계치를 함께 보여주어
        신호까지 얼마나 남았는지 직관적으로 파악할 수 있도록 한다.
        """
        ticker = rule.ticker
        disp = display_ticker(ticker)
        current_price = portfolio.current_prices.get(ticker, 0)
        ticker_lots = [p for p in positions if p.ticker == ticker]

        if current_price <= 0:
            self.logger.info(f"  [{disp}] 신호 없음 | 현재가 조회 실패")
            return

        if not ticker_lots:
            msg = (
                f"  [{disp}] 신호 없음 | 보유 없음, "
                f"현재 ${current_price:,.2f} (1차 진입 대기)"
            )
            last_sell = last_sell_prices.get(ticker) if last_sell_prices else None
            if last_sell and last_sell > 0 and rule.reentry_guard_pct is not None:
                pct_from_sell = (current_price - last_sell) / last_sell * 100
                msg += (
                    f" | 직전 매도가 ${last_sell:,.2f} 대비 {pct_from_sell:+.2f}% "
                    f"(가드 {rule.reentry_guard_pct:+.2f}%)"
                )
            self.logger.info(msg)
            return

        last_lot = max(ticker_lots, key=lambda l: l.level)
        profit_pct = (current_price - last_lot.buy_price) / last_lot.buy_price * 100
        sell_threshold = rule.sell_threshold_at(last_lot.level)
        buy_threshold = rule.buy_threshold_at(last_lot.level)
        next_level = last_lot.level + 1

        msg = (
            f"  [{disp}] 신호 없음 | Lv{last_lot.level} "
            f"매수 ${last_lot.buy_price:,.2f} -> 현재 ${current_price:,.2f} "
            f"({profit_pct:+.2f}%) | 익절 +{sell_threshold:.1f}% / "
            f"추매 {buy_threshold:.1f}%"
        )
        if next_level > rule.max_lots:
            msg += f" (max_lots {rule.max_lots} 도달, 추매 불가)"
        self.logger.info(msg)

    def _refresh_portfolio(self, old_portfolio: Portfolio) -> Portfolio:
        """종목 처리 후 포트폴리오(현금 잔고) 갱신."""
        if self.is_live_trading:
            time.sleep(3)
        new_pf = self.broker.get_portfolio()
        # 이미 조회한 가격 유지, 추가 API 호출 최소화
        for ticker, price in old_portfolio.current_prices.items():
            if ticker not in new_pf.current_prices or new_pf.current_prices[ticker] <= 0:
                new_pf.current_prices[ticker] = price
        return new_pf

    def _update_positions(
        self,
        positions: List[PositionLot],
        signals: List[SplitSignal],
        executions: List[TradeExecution],
        today: str,
        last_sell_prices: Optional[dict] = None,
    ) -> List[PositionLot]:
        """체결 결과를 반영하여 포지션을 업데이트한다.

        - 매수 체결 -> 신호의 level로 새 lot 추가, last_sell_price 초기화
        - 매도 체결 -> 신호의 lot_id로 해당 차수 lot 제거, last_sell_price 갱신
        """
        updated = list(positions)

        # 신호 매핑: (ticker, action) -> signal
        # 한 종목당 한 사이클에 하나의 신호만 발생하므로 unambiguous
        signal_map = {}
        for sig in signals:
            signal_map[(sig.ticker, sig.action)] = sig

        for exe in executions:
            disp = display_ticker(exe.ticker)
            if exe.status == ExecutionStatus.REJECTED:
                self.logger.warning(
                    f"[Position] Skip: {disp} {exe.action} rejected"
                )
                self._notify_alert(
                    f"[{disp}] {exe.action} 주문 거절 (REJECTED): "
                    f"예수금 부족 등 브로커 사유 확인 필요. {exe.reason}"
                )
                continue
            if exe.status == ExecutionStatus.ORDERED:
                # 미체결 잔존 -> 잔고 미확정. 포지션 미반영. 알림.
                self.logger.error(
                    f"[Position] ORDERED — 수동 확인 필요: "
                    f"{disp} {exe.action} reason={exe.reason}"
                )
                self._notify_alert(
                    f"[{disp}] {exe.action} 미체결 잔존 — "
                    f"KIS에서 직접 확인 후 scripts/reconcile_positions.py 실행 권장. "
                    f"{exe.reason}"
                )
                continue
            if exe.quantity <= 0:
                # PARTIAL/FILLED 인데 체결 수량이 0 — 비정상. 안전상 미반영.
                self.logger.warning(
                    f"[Position] Skip zero-qty execution: "
                    f"{disp} {exe.action} status={exe.status}"
                )
                continue

            if exe.action == OrderAction.BUY:
                sig = signal_map.get((exe.ticker, OrderAction.BUY))
                level = sig.level if sig else 1
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                lot_id = f"lot_{ts}_{exe.ticker}_{level:03d}"
                new_lot = PositionLot(
                    lot_id=lot_id,
                    ticker=exe.ticker,
                    buy_price=exe.price,
                    quantity=exe.quantity,
                    buy_date=today,
                    level=level,
                )
                updated.append(new_lot)
                # 동적 재매수 소비: 매수 체결 시 직전 매도가 초기화
                if last_sell_prices is not None and exe.ticker in last_sell_prices:
                    self.logger.info(
                        f"[{disp}] 동적 재매수 기준 초기화 "
                        f"(매도가 ${last_sell_prices[exe.ticker]:.2f} -> 소비됨)"
                    )
                    del last_sell_prices[exe.ticker]
                tag = " (PARTIAL)" if exe.status == ExecutionStatus.PARTIAL else ""
                self.logger.info(
                    f"[Position] New lot{tag}: {lot_id} Lv{level} "
                    f"{disp} {exe.quantity}주 @${exe.price:.2f}"
                )

            elif exe.action == OrderAction.SELL:
                sig = signal_map.get((exe.ticker, OrderAction.SELL))
                target_lot = None
                if sig and sig.lot_id:
                    candidates = [l for l in updated if l.lot_id == sig.lot_id]
                    target_lot = candidates[0] if candidates else None
                else:
                    # 폴백: 가장 높은 level lot 선택
                    ticker_lots = [l for l in updated if l.ticker == exe.ticker]
                    target_lot = max(ticker_lots, key=lambda l: l.level) if ticker_lots else None

                if target_lot is None:
                    continue

                if (exe.status == ExecutionStatus.PARTIAL
                        and exe.quantity < target_lot.quantity):
                    new_qty = target_lot.quantity - exe.quantity
                    idx = updated.index(target_lot)
                    updated[idx] = replace(target_lot, quantity=new_qty)
                    self.logger.info(
                        f"[Position] Partial sell: {target_lot.lot_id} "
                        f"Lv{target_lot.level} ({exe.quantity}/{target_lot.quantity}주, "
                        f"잔량 {new_qty})"
                    )
                else:
                    if exe.quantity > target_lot.quantity:
                        # 수동 매도 등으로 lot 보유분보다 더 체결된 비정상 상태.
                        # lot 은 제거하되 reconcile 단계에서 잡히도록 경고.
                        self.logger.warning(
                            f"[Position] Over-fill detected: {disp} sold "
                            f"{exe.quantity}주 but lot {target_lot.lot_id} held "
                            f"{target_lot.quantity}주 — removing lot. "
                            f"scripts/reconcile_positions.py 로 정합성 확인 권장."
                        )
                    updated.remove(target_lot)
                    # 동적 재매수 기준 갱신: 매도 체결가를 기록
                    if last_sell_prices is not None:
                        last_sell_prices[exe.ticker] = exe.price
                    self.logger.info(
                        f"[Position] Remove lot: {target_lot.lot_id} "
                        f"Lv{target_lot.level} ({target_lot.quantity}주 전량 매도)"
                    )

        return updated

    def _enrich_executions(self, executions: List[TradeExecution], signals: List[SplitSignal]) -> None:
        """체결 내역에 신호의 비즈니스 컨텍스트(차수, 손익 등)를 주입한다."""
        signal_map = {(sig.ticker, sig.action): sig for sig in signals}
        for exe in executions:
            if exe.status == ExecutionStatus.REJECTED:
                continue
            sig = signal_map.get((exe.ticker, OrderAction(exe.action)))
            if sig:
                exe.lot_id = sig.lot_id
                exe.level = sig.level
                if exe.action == OrderAction.SELL and sig.buy_price > 0:
                    exe.buy_price = sig.buy_price
                    exe.realized_pnl = round(
                        (exe.price - sig.buy_price) * exe.quantity - exe.fee, 2
                    )

    def _persist(
        self,
        portfolio: Portfolio,
        signals: List[SplitSignal],
        executions: List[TradeExecution],
        positions: List[PositionLot],
        sim_date: Optional[str] = None,
        last_sell_prices: Optional[dict] = None,
    ) -> None:
        """Step 6: 저장 4종 호출."""
        reason = self._build_reason(signals)

        self.repo.save_positions(positions)
        if last_sell_prices is not None:
            self.repo.save_last_sell_prices(last_sell_prices)
        self.repo.save_trade_history(executions, portfolio, reason, sim_date=sim_date)
        
        # 상태 조립 및 저장 (코어 계층 비즈니스 로직)
        old_realized_pnl = self.repo.get_realized_pnl_by_ticker()
        status_data = build_dashboard_status(
            portfolio, positions, reason, old_realized_pnl, executions,
            self.all_tickers, sim_date
        )
        self.repo.save_status(status_data)

    # ── Private helpers ──────────────────────────────────────────

    def _handle_state_transitions(
        self,
        positions: List[PositionLot],
        last_sell_prices: Dict[str, float],
    ) -> None:
        """종목의 상태 전이(OFF -> ON)를 감지하여 낡은 상태값을 초기화한다."""
        try:
            # 1. 이전 실행 상태 로드
            prev_status = getattr(self.repo, "load_status", lambda: {})()
            if not isinstance(prev_status, dict):
                prev_status = {}
            
            prev_enabled = set(prev_status.get("enabled_tickers", []))
            current_enabled = set(self.all_tickers)

            # 2. 신규 활성화된 종목 식별 (OFF -> ON)
            newly_enabled = current_enabled - prev_enabled
            if not newly_enabled:
                return

            self.logger.info(f">>> Step 2.1: Detected {len(newly_enabled)} newly enabled ticker(s)")
            
            for ticker in newly_enabled:
                # A. 보유 수량이 0인 경우 -> 직전 매도가 및 실현 손익(새 시즌) 초기화
                ticker_lots = [l for l in positions if l.ticker == ticker]
                if not ticker_lots:
                    # 매도가 초기화
                    if ticker in last_sell_prices:
                        old_val = last_sell_prices.pop(ticker)
                        self.logger.info(
                            f"[{ticker}] OFF->ON 전환 감지: 0주 상태이므로 직전 매도가(${old_val:.2f}) 초기화"
                        )
                    
                    # 실현 손익 초기화 (새 시즌)
                    realized_pnls = prev_status.setdefault("realized_pnl_by_ticker", {})
                    if realized_pnls.get(ticker, 0.0) != 0.0:
                        old_pnl = realized_pnls[ticker]
                        realized_pnls[ticker] = 0.0
                        self.logger.info(
                            f"[{ticker}] OFF->ON 전환 감지: 0주 상태이므로 누적 실현 손익(${old_pnl:,.2f}) 초기화"
                        )
                
                # B. 보유 수량이 있는 경우 -> 트레일링 최고가 초기화 (현재가부터 다시 추적)
                else:
                    for lot in ticker_lots:
                        if lot.trailing_highest_price is not None:
                            lot.trailing_highest_price = None
                            self.logger.info(
                                f"[{ticker}] OFF->ON 전환 감지: Lv{lot.level} 트레일링 최고가 초기화"
                            )

            # 변경된 이전 상태 저장 (실현 손익 리셋 반영)
            self.repo.save_status(prev_status)

        except Exception as e:
            self.logger.error(f"상태 전이 처리 중 오류 발생 (무시하고 진행): {e}")

    def _build_reason(self, signals: List[SplitSignal]) -> str:
        """신호 목록에서 사유 문자열을 생성한다."""
        if not signals:
            return "모니터링 - 신호 없음"
        reasons = [
            f"{display_ticker(s.ticker)}:{s.action.value}({s.reason})"
            for s in signals
        ]
        return ", ".join(reasons)

    def _notify_message(self, msg: str) -> None:
        if self.notifier:
            self.notifier.send_message(msg)

    def _notify_alert(self, msg: str) -> None:
        if self.notifier:
            self.notifier.send_alert(msg)
