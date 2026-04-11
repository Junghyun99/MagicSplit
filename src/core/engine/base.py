# src/core/engine/base.py
import time
from datetime import datetime
from typing import List, Optional, Tuple

from src.core.interfaces import IBrokerAdapter, IRepository, ILogger, INotifier
from src.core.models import (
    StockRule,
    PositionLot,
    Portfolio,
    Order,
    OrderAction,
    TradeExecution,
    TradeSignal,
    SplitSignal,
    DayResult,
)
from src.core.logic import SplitEvaluator
from src.core.engine.registry import register_engine


@register_engine(color="#1f77b4")
class MagicSplitEngine:
    """MagicSplit 매매 사이클 엔진 (Template Method 패턴).

    run_one_cycle()이 전체 사이클의 뼈대(template)를 정의하며,
    각 단계(Step 1~6)는 개별 메서드로 분리되어 서브클래스에서 오버라이드 가능하다.

    main.py (실시간)에서 이 엔진을 사용한다.
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
        """하루치 매매 사이클 전체를 실행한다 (Template Method).

        Args:
            sim_date: 시뮬레이션 날짜 ("YYYY-MM-DD").
                None이면 오늘 날짜 사용 (실시간 모드).

        Returns:
            DayResult: 사이클 실행 결과
        """
        today = sim_date or datetime.now().strftime("%Y-%m-%d")

        # Step 1: 포트폴리오 조회 + 실시간 가격
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

        # Step 3: 종목별 매수/매도 신호 평가
        self.logger.info(">>> Step 3: Evaluate Split Signals")
        signals = self.evaluator.evaluate(
            stock_rules=self.stock_rules,
            positions=positions,
            portfolio=portfolio,
        )
        self.logger.info(f"Generated {len(signals)} signal(s)")

        # Step 4: 주문 실행 (매도 우선)
        self.logger.info(">>> Step 4: Execute Orders")
        orders = self._signals_to_orders(signals)
        executions, final_pf = self._execute_orders(orders, portfolio)

        # Step 5: 포지션 업데이트
        self.logger.info(">>> Step 5: Update Positions")
        updated_positions = self._update_positions(positions, executions, today)

        # Step 6: 저장
        self.logger.info(">>> Step 6: Persist")
        self._persist(final_pf, signals, executions, updated_positions,
                      sim_date=sim_date)

        has_orders = len(executions) > 0
        return DayResult(
            date=today,
            signals=signals,
            executions=executions,
            final_portfolio=final_pf,
            has_orders=has_orders,
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

    def _execute_orders(
        self,
        orders: List[Order],
        portfolio: Portfolio,
    ) -> Tuple[List[TradeExecution], Portfolio]:
        """Step 4: 주문을 실행한다 (매도 → 매수 순서)."""
        executions: List[TradeExecution] = []
        final_pf = portfolio

        if not orders:
            self.logger.info("No orders to execute.")
            self._notify_message(
                f"모니터링 완료. 신호 없음 | ${portfolio.total_value:,.0f}"
            )
            return executions, final_pf

        self.logger.info(f"Executing {len(orders)} order(s)...")
        executions = self.broker.execute_orders(orders)

        if executions:
            self._notify_message(f"Orders Executed. Count: {len(executions)}")
            if self.is_live_trading:
                time.sleep(3)
            final_pf = self.broker.get_portfolio()
            # 실시간 가격 다시 반영
            real_time_prices = self.broker.fetch_current_prices(self.all_tickers)
            for ticker, price in real_time_prices.items():
                if price > 0:
                    final_pf.current_prices[ticker] = price
            self.logger.info(
                f"Updated Portfolio: Cash=${final_pf.total_cash:,.0f}, "
                f"Value=${final_pf.total_value:,.0f}"
            )
        else:
            self._notify_alert("Orders sent but NO execution result returned.")

        return executions, final_pf

    def _update_positions(
        self,
        positions: List[PositionLot],
        executions: List[TradeExecution],
        today: str,
    ) -> List[PositionLot]:
        """Step 5: 체결 결과를 반영하여 포지션을 업데이트한다.

        - 매수 체결 → 새 lot 추가
        - 매도 체결 → 해당 lot 제거 (lot_id가 SplitSignal에서 지정됨)
        """
        updated = list(positions)

        for exe in executions:
            if exe.action == OrderAction.BUY:
                # 새 lot 생성
                lot_id = f"lot_{today.replace('-', '')}_{exe.ticker}_{len(updated):03d}"
                new_lot = PositionLot(
                    lot_id=lot_id,
                    ticker=exe.ticker,
                    buy_price=exe.price,
                    quantity=exe.quantity,
                    buy_date=today,
                )
                updated.append(new_lot)
                self.logger.info(
                    f"[Position] New lot: {lot_id} "
                    f"{exe.ticker} {exe.quantity}주 @${exe.price:.2f}"
                )

            elif exe.action == OrderAction.SELL:
                # 해당 종목의 가장 오래된 lot부터 제거 (FIFO)
                remaining_qty = exe.quantity
                to_remove = []
                for lot in updated:
                    if lot.ticker == exe.ticker and remaining_qty > 0:
                        if lot.quantity <= remaining_qty:
                            remaining_qty -= lot.quantity
                            to_remove.append(lot)
                            self.logger.info(
                                f"[Position] Remove lot: {lot.lot_id} "
                                f"({lot.quantity}주 전량 매도)"
                            )
                        else:
                            lot_quantity_before = lot.quantity
                            # 직접 수정하지 않고 새 객체로 교체
                            idx = updated.index(lot)
                            updated[idx] = PositionLot(
                                lot_id=lot.lot_id,
                                ticker=lot.ticker,
                                buy_price=lot.buy_price,
                                quantity=lot.quantity - remaining_qty,
                                buy_date=lot.buy_date,
                            )
                            self.logger.info(
                                f"[Position] Partial sell: {lot.lot_id} "
                                f"{lot_quantity_before}주 → {updated[idx].quantity}주"
                            )
                            remaining_qty = 0

                for lot in to_remove:
                    updated.remove(lot)

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
        self.repo.save_trade_history(executions, portfolio, reason, sim_date=sim_date)
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
