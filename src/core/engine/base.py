# src/core/engine/base.py
import time
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

                    # 3a. 해당 종목 신호 평가
                    signals = self.evaluator.evaluate_stock(rule, positions, portfolio)
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

        self.logger.error(
            f">>> Step 2.5: 수량 불일치 {len(mismatches)}건 감지 — 해당 종목 매매 중단"
        )
        for m in mismatches:
            msg = (
                f"[{m.ticker}] Qty Mismatch: broker={m.broker_qty}, "
                f"positions={m.positions_qty} (lots={m.lot_count}, levels={m.levels}) "
                f"— 매매 중단. scripts/reconcile_positions.py 실행 권장."
            )
            self.logger.error(msg)
            self._notify_alert(msg)
        return {m.ticker for m in mismatches}

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
                self.logger.info(
                    f"[Position] New lot: {lot_id} Lv{level} "
                    f"{exe.ticker} {exe.quantity}주 @${exe.price:.2f}"
                )

            elif exe.action == OrderAction.SELL:
                sig = signal_map.get((exe.ticker, OrderAction.SELL))
                if sig and sig.lot_id:
                    # 신호에 지정된 lot_id로 제거
                    target = [l for l in updated if l.lot_id == sig.lot_id]
                    if target:
                        updated.remove(target[0])
                        self.logger.info(
                            f"[Position] Remove lot: {sig.lot_id} Lv{sig.level} "
                            f"({target[0].quantity}주 전량 매도)"
                        )
                else:
                    # 폴백: 가장 높은 level lot 제거
                    ticker_lots = [l for l in updated if l.ticker == exe.ticker]
                    if ticker_lots:
                        highest = max(ticker_lots, key=lambda l: l.level)
                        updated.remove(highest)
                        self.logger.info(
                            f"[Position] Remove lot: {highest.lot_id} Lv{highest.level} "
                            f"({highest.quantity}주 전량 매도)"
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
