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
from src.core.logic import SplitEvaluator, detect_mismatches
from src.core.engine.registry import register_engine


@register_engine(color="#1f77b4")
class MagicSplitEngine:
    """MagicSplit 매매 사이클 엔진.

    run_one_cycle()이 전체 사이클의 뼈대를 정의한다.
    종목별로 순차 실행: 평가 → 주문 → 포지션 반영 → 다음 종목.

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

        종목별 순차 실행: 각 종목을 평가 → 주문 → 포지션 반영 후 다음 종목으로.

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

            # 재진입 가드용 직전 매도가 조회.
            # TODO(reentry_guard): repo/history에서 티커별 직전 (전량 청산)
            # 매도 단가를 추출해 채운다. 현재는 비어 있어 가드 비활성.
            last_sell_prices: dict = {}

            # Step 2.5: 브로커 수량 ↔ positions 수량 합 불일치 검사
            # 불일치 종목은 이번 사이클에서 매매 중단 (자동 보정 미지원)
            halted_tickers = self._check_reconcile(positions, portfolio)

            # Step 3~5: 종목별 순차 실행
            for rule in self.stock_rules:
                if rule.ticker in halted_tickers:
                    self.logger.warning(
                        f"[{rule.ticker}] 수량 불일치로 매매 중단. "
                        f"scripts/reconcile_positions.py 로 보정 후 재실행."
                    )
                    failed_tickers.append(rule.ticker)
                    continue
                try:
                    self.logger.info(f">>> Processing {rule.ticker}")

                    # 3a-pre. 자금 설정 점검 (현재가 > buy_amount → 1주도 매수 불가)
                    self._warn_if_budget_insufficient(rule, portfolio, positions)

                    # 3a. 해당 종목 신호 평가
                    signals = self.evaluator.evaluate_stock(
                        rule, positions, portfolio, last_sell_prices,
                    )
                    all_signals.extend(signals)

                    if not signals:
                        self.logger.info(f"  [{rule.ticker}] 신호 없음. 스킵.")
                        continue

                    # 3b. 주문 실행
                    orders = self._signals_to_orders(signals)
                    executions = self._execute_stock_orders(orders)
                    all_executions.extend(executions)

                    # 3c. 포지션 즉시 반영 (다음 종목 판단에 영향)
                    if executions:
                        try:
                            positions = self._update_positions(
                                positions, signals, executions, today,
                            )
                            portfolio = self._refresh_portfolio(portfolio)
                        except Exception as e:
                            self.logger.error(
                                f"[{rule.ticker}] 포지션 반영 실패 "
                                f"(체결은 완료됨): {e}"
                            )
                            self._notify_alert(
                                f"[{rule.ticker}] 포지션 반영 실패 "
                                f"(체결 {len(executions)}건 완료됨): {e}"
                            )
                            failed_tickers.append(rule.ticker)
                except Exception as e:
                    self.logger.error(f"[{rule.ticker}] 처리 실패: {e}")
                    self._notify_alert(f"[{rule.ticker}] Error: {e}")
                    failed_tickers.append(rule.ticker)

        except Exception as e:
            self.logger.error(f"사이클 초기화 실패: {e}")
            self._notify_alert(f"Cycle init error: {e}")
        finally:
            # Step 6: 저장 — 포트폴리오와 포지션 모두 정상 로드된 경우에만 저장
            if portfolio is not None and positions is not None:
                self.logger.info(">>> Step 6: Persist")
                self._persist(portfolio, all_signals, all_executions, positions,
                              sim_date=sim_date)
            else:
                missing = []
                if portfolio is None:
                    missing.append("포트폴리오")
                if positions is None:
                    missing.append("포지션")
                self.logger.error(
                    f">>> Step 6: {', '.join(missing)} 조회 실패로 저장 생략"
                )

        # 알림
        fail_suffix = f" (실패: {', '.join(failed_tickers)})" if failed_tickers else ""
        if all_executions:
            self._notify_message(f"Orders Executed. Count: {len(all_executions)}{fail_suffix}")
        elif portfolio is not None:
            self._notify_message(
                f"모니터링 완료. 신호 없음 | ${portfolio.total_value:,.0f}{fail_suffix}"
            )
        else:
            self._notify_message(f"사이클 실패{fail_suffix}")

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
            f"[{m.ticker}] Qty Mismatch: broker={m.broker_qty}, "
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

    def _warn_if_budget_insufficient(
        self,
        rule: StockRule,
        portfolio: Portfolio,
        positions: List[PositionLot],
    ) -> None:
        """현재가가 buy_amount를 초과해 1주도 매수 불가한 경우 사용자 경고를 보낸다.

        이 상태로는 초기 진입도, 추가 매수(하락 시 분할)도 불가능하므로
        config의 buy_amount를 상향 조정해야 한다는 알림을 발송한다.
        단, 이미 max_lots에 도달한 종목은 어차피 추가 매수 대상이 아니므로 생략.
        """
        ticker_lot_count = sum(1 for p in positions if p.ticker == rule.ticker)
        if ticker_lot_count >= rule.max_lots:
            return

        current_price = portfolio.current_prices.get(rule.ticker, 0)
        if current_price <= 0 or rule.buy_amount <= 0:
            return
        if rule.buy_amount >= current_price:
            return

        msg = (
            f"[{rule.ticker}] buy_amount({rule.buy_amount:,.2f}) < "
            f"현재가({current_price:,.2f}) → 1주도 매수 불가. "
            f"config.buy_amount를 상향 조정하세요."
        )
        self.logger.warning(msg)
        self._notify_alert(msg)

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
    ) -> List[PositionLot]:
        """체결 결과를 반영하여 포지션을 업데이트한다.

        - 매수 체결 → 신호의 level로 새 lot 추가
        - 매도 체결 → 신호의 lot_id로 해당 차수 lot 제거
        """
        updated = list(positions)

        # 신호 매핑: (ticker, action) → signal
        # 한 종목당 한 사이클에 하나의 신호만 발생하므로 unambiguous
        signal_map = {}
        for sig in signals:
            signal_map[(sig.ticker, sig.action)] = sig

        for exe in executions:
            if exe.status == ExecutionStatus.REJECTED:
                self.logger.warning(
                    f"[Position] Skip: {exe.ticker} {exe.action} rejected"
                )
                continue
            if exe.status == ExecutionStatus.ORDERED:
                # 미체결 잔존 → 잔고 미확정. 포지션 미반영. 알림.
                self.logger.error(
                    f"[Position] ORDERED — 수동 확인 필요: "
                    f"{exe.ticker} {exe.action} reason={exe.reason}"
                )
                self._notify_alert(
                    f"[{exe.ticker}] {exe.action} 미체결 잔존 — "
                    f"KIS에서 직접 확인 후 scripts/reconcile_positions.py 실행 권장. "
                    f"{exe.reason}"
                )
                continue
            if exe.quantity <= 0:
                # PARTIAL/FILLED 인데 체결 수량이 0 — 비정상. 안전상 미반영.
                self.logger.warning(
                    f"[Position] Skip zero-qty execution: "
                    f"{exe.ticker} {exe.action} status={exe.status}"
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
                tag = " (PARTIAL)" if exe.status == ExecutionStatus.PARTIAL else ""
                self.logger.info(
                    f"[Position] New lot{tag}: {lot_id} Lv{level} "
                    f"{exe.ticker} {exe.quantity}주 @${exe.price:.2f}"
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
                            f"[Position] Over-fill detected: {exe.ticker} sold "
                            f"{exe.quantity}주 but lot {target_lot.lot_id} held "
                            f"{target_lot.quantity}주 — removing lot. "
                            f"scripts/reconcile_positions.py 로 정합성 확인 권장."
                        )
                    updated.remove(target_lot)
                    self.logger.info(
                        f"[Position] Remove lot: {target_lot.lot_id} "
                        f"Lv{target_lot.level} ({target_lot.quantity}주 전량 매도)"
                    )

        return updated

    def _persist(
        self,
        portfolio: Portfolio,
        signals: List[SplitSignal],
        executions: List[TradeExecution],
        positions: List[PositionLot],
        sim_date: Optional[str] = None,
    ) -> None:
        """Step 6: 저장 3종 호출."""
        reason = self._build_reason(signals)

        self.repo.save_positions(positions)
        self.repo.save_trade_history(executions, portfolio, reason,
                                        signals=signals, sim_date=sim_date)
        self.repo.update_status(portfolio, positions, reason, sim_date=sim_date)

    # ── Private helpers ──────────────────────────────────────────

    def _build_reason(self, signals: List[SplitSignal]) -> str:
        """신호 목록에서 사유 문자열을 생성한다."""
        if not signals:
            return "모니터링 - 신호 없음"
        reasons = [f"{s.ticker}:{s.action.value}({s.reason})" for s in signals]
        return ", ".join(reasons)

    def _notify_message(self, msg: str) -> None:
        if self.notifier:
            self.notifier.send_message(msg)

    def _notify_alert(self, msg: str) -> None:
        if self.notifier:
            self.notifier.send_alert(msg)
