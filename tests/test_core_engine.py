# tests/test_core_engine.py
import pytest
from unittest.mock import MagicMock, patch
from src.core.engine.base import MagicSplitEngine
from src.utils.ticker_reader import display_ticker
from src.core.models import (
    StockRule, PositionLot, Portfolio, Order, OrderAction,
    TradeExecution, ExecutionStatus, SplitSignal,
)


@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.get_portfolio.return_value = Portfolio(
        total_cash=10000.0,
        holdings={},
        current_prices={"AAPL": 100.0},
    )
    broker.fetch_current_prices.return_value = {"AAPL": 100.0}
    broker.execute_orders.return_value = []
    return broker


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.load_positions.return_value = []
    return repo


@pytest.fixture
def mock_logger():
    logger = MagicMock()
    logger.get_captured_logs.return_value = []
    return logger


@pytest.fixture
def default_rules():
    return [StockRule("AAPL", -5.0, 10.0, 500, 10)]


@pytest.fixture
def engine(mock_broker, mock_repo, mock_logger, default_rules):
    return MagicSplitEngine(
        broker=mock_broker,
        repo=mock_repo,
        logger=mock_logger,
        stock_rules=default_rules,
    )


class TestEngineInit:
    def test_filters_disabled_rules(self, mock_broker, mock_repo, mock_logger):
        """disabled 종목은 필터링"""
        rules = [
            StockRule("AAPL", -5.0, 10.0, 500, 10, enabled=True),
            StockRule("MSFT", -5.0, 10.0, 500, 10, enabled=False),
        ]
        eng = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo,
            logger=mock_logger, stock_rules=rules,
        )
        assert len(eng.stock_rules) == 1
        assert eng.all_tickers == ["AAPL"]

    def test_all_tickers(self, engine):
        assert engine.all_tickers == ["AAPL"]


class TestRunOneCycle:
    def test_engine_continues_on_rule_error(self, mock_broker, mock_repo, mock_logger):
        """특정 종목 평가 중 에러 발생 시, 해당 종목을 건너뛰고 다음 종목은 정상 처리"""
        rules = [
            StockRule("AAPL", -5.0, 10.0, 500, 10),
            StockRule("MSFT", -5.0, 10.0, 500, 10),
        ]
        engine = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo,
            logger=mock_logger, stock_rules=rules,
        )

        def mock_evaluate_stock(rule, positions, portfolio, last_sell_prices=None):
            if rule.ticker == "AAPL":
                raise Exception("Mock evaluator error")
            elif rule.ticker == "MSFT":
                return [SplitSignal("MSFT", None, OrderAction.BUY, 5, 200.0, "MSFT Buy", 0.0, 1)]
            return []

        engine.evaluator.evaluate_stock = MagicMock(side_effect=mock_evaluate_stock)

        execution = TradeExecution(
            "MSFT", OrderAction.BUY, 5, 200.0, 1.0,
            "2026-04-10", ExecutionStatus.FILLED,
        )
        mock_broker.execute_orders.return_value = [execution]

        result = engine.run_one_cycle(sim_date="2026-04-10")

        mock_logger.error.assert_any_call(f"[{display_ticker('AAPL')}] 처리 실패: Mock evaluator error")

        assert result.has_orders is True
        assert len(result.executions) == 1
        assert result.executions[0].ticker == "MSFT"

        # MSFT 포지션만 저장되었는지 확인
        mock_repo.save_positions.assert_called_once()
        saved_positions = mock_repo.save_positions.call_args[0][0]
        assert len(saved_positions) == 1
        assert saved_positions[0].ticker == "MSFT"

    def test_engine_continues_on_position_update_error(self, mock_broker, mock_repo, mock_logger):
        """포지션 업데이트 중 에러 발생 시, 해당 종목을 건너뛰고 다음 종목은 정상 처리"""
        rules = [
            StockRule("AAPL", -5.0, 10.0, 500, 10),
            StockRule("MSFT", -5.0, 10.0, 500, 10),
        ]
        engine = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo,
            logger=mock_logger, stock_rules=rules,
        )

        def mock_evaluate_stock(rule, positions, portfolio, last_sell_prices=None):
            if rule.ticker == "AAPL":
                return [SplitSignal("AAPL", None, OrderAction.BUY, 5, 100.0, "AAPL Buy", 0.0, 1)]
            elif rule.ticker == "MSFT":
                return [SplitSignal("MSFT", None, OrderAction.BUY, 5, 200.0, "MSFT Buy", 0.0, 1)]
            return []

        engine.evaluator.evaluate_stock = MagicMock(side_effect=mock_evaluate_stock)

        def mock_execute_orders(orders):
            return [
                TradeExecution(
                    o.ticker, o.action, o.quantity, o.price, 1.0,
                    "2026-04-10", ExecutionStatus.FILLED,
                ) for o in orders
            ]

        mock_broker.execute_orders = MagicMock(side_effect=mock_execute_orders)

        # Original _update_positions method
        original_update_positions = engine._update_positions

        def mock_update_positions(positions, signals, executions, today, last_sell_prices=None):
            # Check if this is the AAPL update
            if any(e.ticker == "AAPL" for e in executions):
                raise Exception("Mock position update error")
            return original_update_positions(positions, signals, executions, today, last_sell_prices=last_sell_prices)

        engine._update_positions = MagicMock(side_effect=mock_update_positions)

        result = engine.run_one_cycle(sim_date="2026-04-10")

        # AAPL 에러 로그 확인
        mock_logger.error.assert_any_call(f"[{display_ticker('AAPL')}] 포지션 반영 실패 (체결은 완료됨): Mock position update error")

        # 두 종목 모두 체결은 완료되었는지 확인
        assert result.has_orders is True
        assert len(result.executions) == 2

        # MSFT 포지션 반영은 정상 수행되었는지 확인 (_update_positions가 여러 번 불렸는지)
        assert engine._update_positions.call_count == 2


    def test_full_cycle_no_signals(self, engine, mock_repo):
        """신호 없을 때 전체 사이클 정상 완료"""
        # transition logic 회피를 위해 기존 상태 설정
        mock_repo.load_status.return_value = {"enabled_tickers": ["AAPL"]}

        result = engine.run_one_cycle(sim_date="2026-04-10")

        assert result.date == "2026-04-10"
        mock_repo.save_positions.assert_called_once()
        mock_repo.save_status.assert_called_once()

    def test_full_cycle_with_buy(self, engine, mock_broker, mock_repo):
        """초기 매수 시 전체 사이클"""
        execution = TradeExecution(
            "AAPL", OrderAction.BUY, 5, 100.1, 1.25,
            "2026-04-10", ExecutionStatus.FILLED,
        )
        mock_broker.execute_orders.return_value = [execution]

        result = engine.run_one_cycle(sim_date="2026-04-10")

        assert result.has_orders is True
        assert len(result.executions) == 1
        # 포지션 저장 확인
        mock_repo.save_positions.assert_called_once()
        saved_positions = mock_repo.save_positions.call_args[0][0]
        assert len(saved_positions) == 1  # 새 lot 1개
        assert saved_positions[0].level == 1  # 초기 매수 = Lv1

    def test_full_cycle_with_sell(self, engine, mock_broker, mock_repo):
        """익절 매도 시 전체 사이클"""
        # 기존 lot: 매수가 90, 현재가 100 -> +11.1% (> 10% 임계치)
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]
        # 브로커 수량과 positions 합 일치 (불일치 감지 회피)
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0,
            holdings={"AAPL": 5},
            current_prices={"AAPL": 100.0},
        )

        execution = TradeExecution(
            "AAPL", OrderAction.SELL, 5, 99.9, 1.25,
            "2026-04-10", ExecutionStatus.FILLED,
        )
        mock_broker.execute_orders.return_value = [execution]

        result = engine.run_one_cycle(sim_date="2026-04-10")

        assert result.has_orders is True
        mock_repo.save_positions.assert_called_once()


class TestBudgetWarning:
    """현재가 > buy_amount 일 때 evaluator가 is_blocked 신호를 반환하고
    엔진이 _notify_alert로 전달하는지 검증."""

    def test_warns_when_price_exceeds_buy_amount(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """현재가가 buy_amount보다 크면 notifier.send_alert 발송."""
        rules = [StockRule("BRK-A", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={},
            current_prices={"BRK-A": 600000.0},
        )
        mock_broker.fetch_current_prices.return_value = {"BRK-A": 600000.0}
        notifier = MagicMock()
        eng = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo, logger=mock_logger,
            stock_rules=rules, notifier=notifier,
        )
        eng.run_one_cycle(sim_date="2026-04-10")

        alert_msgs = [c.args[0] for c in notifier.send_alert.call_args_list]
        assert any("매수 불가" in m and "BRK-A" in m for m in alert_msgs)

    def test_no_warning_when_budget_sufficient(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """buy_amount >= 현재가이면 예산 경고 미발송."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={},
            current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        notifier = MagicMock()
        eng = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo, logger=mock_logger,
            stock_rules=rules, notifier=notifier,
        )
        eng.run_one_cycle(sim_date="2026-04-10")

        for call in notifier.send_alert.call_args_list:
            assert "매수 불가" not in call.args[0]

    def test_no_warning_when_max_lots_reached(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """이미 max_lots에 도달한 종목은 max_lots 차단 알림이 가되 예산 알림은 미발송."""
        rules = [StockRule("BRK-A", -5.0, 10.0, 500, max_lots=2)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={"BRK-A": 2},
            current_prices={"BRK-A": 600000.0},
        )
        mock_broker.fetch_current_prices.return_value = {"BRK-A": 600000.0}
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "BRK-A", 100.0, 1, "2026-04-01", level=1),
            PositionLot("lot_002", "BRK-A", 90.0, 1, "2026-04-05", level=2),
        ]
        notifier = MagicMock()
        eng = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo, logger=mock_logger,
            stock_rules=rules, notifier=notifier,
        )
        eng.run_one_cycle(sim_date="2026-04-10")

        alert_msgs = [c.args[0] for c in notifier.send_alert.call_args_list]
        assert not any("buy_amount" in m for m in alert_msgs)

    def test_no_warning_when_price_unavailable(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """현재가 조회 실패(0 이하)이면 가격 조회 실패 알림이 발생하되 예산 경고는 미발송."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={},
            current_prices={"AAPL": 0.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 0.0}
        notifier = MagicMock()
        eng = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo, logger=mock_logger,
            stock_rules=rules, notifier=notifier,
        )
        eng.run_one_cycle(sim_date="2026-04-10")

        alert_msgs = [c.args[0] for c in notifier.send_alert.call_args_list]
        assert not any("buy_amount" in m for m in alert_msgs)



class TestReconcileHalt:
    """브로커 수량 ≠ positions 수량 합 불일치 감지 후 매매 중단 동작."""

    def test_halts_mismatched_ticker_and_trades_others(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """불일치 종목은 스킵, 일치 종목은 정상 매매."""
        rules = [
            StockRule("AAPL", -5.0, 10.0, 500, 10),
            StockRule("MSFT", -5.0, 10.0, 500, 10),
        ]
        # AAPL: broker=7, positions sum=5 -> 불일치
        # MSFT: broker=0, positions=[] -> 일치
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0,
            holdings={"AAPL": 7},
            current_prices={"AAPL": 100.0, "MSFT": 200.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0, "MSFT": 200.0}
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]

        notifier = MagicMock()
        engine = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo,
            logger=mock_logger, stock_rules=rules, notifier=notifier,
        )

        msft_exe = TradeExecution(
            "MSFT", OrderAction.BUY, 2, 200.0, 1.0,
            "2026-04-10", ExecutionStatus.FILLED,
        )
        mock_broker.execute_orders.return_value = [msft_exe]

        result = engine.run_one_cycle(sim_date="2026-04-10")

        # AAPL 은 매매 신호/체결 없어야 한다
        assert all(s.ticker != "AAPL" for s in result.signals)
        assert all(e.ticker != "AAPL" for e in result.executions)

        # MSFT 는 정상 처리
        assert any(e.ticker == "MSFT" for e in result.executions)

        # 알림이 AAPL 불일치로 한 번 이상 호출되어야 한다
        alert_msgs = [c.args[0] for c in notifier.send_alert.call_args_list]
        assert any("AAPL" in m and "Mismatch" in m for m in alert_msgs)

    def test_no_halt_when_all_match(self, mock_broker, mock_repo, mock_logger):
        """모든 종목 수량 일치 시 halt 티커 없음."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0,
            holdings={"AAPL": 5},
            current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]

        engine = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo,
            logger=mock_logger, stock_rules=rules,
        )
        halted = engine._check_reconcile(
            mock_repo.load_positions.return_value,
            mock_broker.get_portfolio.return_value,
        )
        assert halted == set()


class TestUpdatePositions:
    def test_buy_adds_new_lot_with_level(self, engine):
        """매수 체결 -> level이 설정된 새 lot 추가"""
        positions = []
        signals = [
            SplitSignal("AAPL", None, OrderAction.BUY, 5, 100.0,
                        "초기 매수 Lv1", 0.0, level=1),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.BUY, 5, 100.0, 1.0,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        updated = engine._update_positions(positions, signals, executions, "2026-04-10")

        assert len(updated) == 1
        assert updated[0].ticker == "AAPL"
        assert updated[0].buy_price == 100.0
        assert updated[0].quantity == 5
        assert updated[0].level == 1

    def test_sell_removes_specific_lot(self, engine):
        """매도 체결 -> signal의 lot_id로 특정 lot 제거"""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]
        signals = [
            SplitSignal("AAPL", "lot_001", OrderAction.SELL, 5, 100.0,
                        "Lv1 +11.1% -> 익절", 11.1, level=1),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 5, 100.0, 1.0,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        updated = engine._update_positions(positions, signals, executions, "2026-04-10")
        assert len(updated) == 0

    def test_sell_removes_by_level_not_fifo(self, engine):
        """매도는 FIFO가 아닌 마지막 차수(가장 높은 level) lot을 제거"""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 3, "2026-04-01", level=1),
            PositionLot("lot_002", "AAPL", 95.0, 5, "2026-04-05", level=2),
        ]
        signals = [
            SplitSignal("AAPL", "lot_002", OrderAction.SELL, 5, 110.0,
                        "Lv2 +15.8% -> 익절", 15.8, level=2),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 5, 110.0, 1.0,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        updated = engine._update_positions(positions, signals, executions, "2026-04-10")

        assert len(updated) == 1
        assert updated[0].lot_id == "lot_001"
        assert updated[0].level == 1

    def test_buy_adds_correct_level(self, engine):
        """추가 매수 시 signal의 level이 새 lot에 반영"""
        positions = [
            PositionLot("lot_001", "AAPL", 100.0, 5, "2026-04-01", level=1),
        ]
        signals = [
            SplitSignal("AAPL", None, OrderAction.BUY, 5, 94.0,
                        "추가 매수 Lv2", -6.0, level=2),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.BUY, 5, 94.0, 1.0,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        updated = engine._update_positions(positions, signals, executions, "2026-04-10")

        assert len(updated) == 2
        new_lot = [l for l in updated if l.level == 2][0]
        assert new_lot.buy_price == 94.0
        assert new_lot.level == 2


class TestUpdatePositionsPartialAndOrdered:
    """엔진의 PARTIAL/ORDERED 분기 처리."""

    def test_buy_partial_uses_executed_quantity(self, engine):
        """BUY PARTIAL -> 새 lot 의 quantity 가 체결분(exe.quantity) 으로 추가."""
        positions = []
        signals = [
            SplitSignal("AAPL", None, OrderAction.BUY, 5, 100.0,
                        "Lv1 매수", 0.0, level=1),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.BUY, 3, 100.0, 0.5,
                           "2026-04-10", ExecutionStatus.PARTIAL,
                           reason="ODNO=X partial_after_cancel(3/5)"),
        ]

        updated = engine._update_positions(positions, signals, executions, "2026-04-10")
        assert len(updated) == 1
        assert updated[0].quantity == 3
        assert updated[0].level == 1

    def test_sell_partial_decrements_lot_quantity(self, engine):
        """SELL PARTIAL -> 대상 lot quantity 차감, lot_id 유지."""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=2),
        ]
        signals = [
            SplitSignal("AAPL", "lot_001", OrderAction.SELL, 5, 100.0,
                        "Lv2 익절", 11.1, level=2),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 2, 100.0, 0.3,
                           "2026-04-10", ExecutionStatus.PARTIAL),
        ]

        updated = engine._update_positions(positions, signals, executions, "2026-04-10")
        assert len(updated) == 1
        assert updated[0].lot_id == "lot_001"
        assert updated[0].quantity == 3
        assert updated[0].level == 2
        assert updated[0].buy_price == 90.0  # 평균가 보존

    def test_sell_partial_full_quantity_removes_lot(self, engine):
        """SELL PARTIAL 인데 fill_qty == lot.quantity 면 전량 제거."""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]
        signals = [
            SplitSignal("AAPL", "lot_001", OrderAction.SELL, 5, 100.0,
                        "Lv1 익절", 11.1, level=1),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 5, 100.0, 0.5,
                           "2026-04-10", ExecutionStatus.PARTIAL),
        ]
        updated = engine._update_positions(positions, signals, executions, "2026-04-10")
        assert len(updated) == 0

    def test_ordered_skips_position_update_and_alerts(self, mock_broker, mock_repo, mock_logger):
        """ORDERED -> 포지션 미반영 + notifier.send_alert 호출."""
        notifier = MagicMock()
        engine = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo, logger=mock_logger,
            stock_rules=[StockRule("AAPL", -5.0, 10.0, 500, 10)],
            notifier=notifier,
        )
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]
        signals = [
            SplitSignal("AAPL", "lot_001", OrderAction.SELL, 5, 100.0,
                        "Lv1 익절", 11.1, level=1),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 0, 100.0, 0.0,
                           "2026-04-10", ExecutionStatus.ORDERED,
                           reason="ODNO=Z PARTIAL_FILL=2 manual_check_required"),
        ]

        updated = engine._update_positions(positions, signals, executions, "2026-04-10")
        # 포지션 그대로 유지
        assert len(updated) == 1
        assert updated[0].quantity == 5
        # 알림 호출
        notifier.send_alert.assert_called_once()
        msg = notifier.send_alert.call_args[0][0]
        assert "AAPL" in msg
        assert "미체결 잔존" in msg

    def test_zero_qty_partial_skipped(self, engine):
        """status가 PARTIAL/FILLED 인데 quantity=0 인 비정상 경우 -> 미반영."""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]
        signals = [
            SplitSignal("AAPL", "lot_001", OrderAction.SELL, 5, 100.0,
                        "Lv1 익절", 11.1, level=1),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 0, 100.0, 0.0,
                           "2026-04-10", ExecutionStatus.PARTIAL),
        ]
        updated = engine._update_positions(positions, signals, executions, "2026-04-10")
        assert len(updated) == 1
        assert updated[0].quantity == 5


class TestUpdatePositionsOverFill:
    def test_over_fill_warns_and_removes_lot(self, engine, mock_logger):
        """exe.quantity > target_lot.quantity 시 warning 로그 후 lot 제거."""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 3, "2026-04-01", level=1),
        ]
        signals = [
            SplitSignal("AAPL", "lot_001", OrderAction.SELL, 3, 100.0,
                        "Lv1 익절", 11.1, level=1),
        ]
        # 보유 3주인데 5주 체결된 비정상 시나리오 (수동 매도 등)
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 5, 100.0, 0.5,
                           "2026-04-10", ExecutionStatus.PARTIAL),
        ]

        updated = engine._update_positions(positions, signals, executions, "2026-04-10")
        assert len(updated) == 0
        # over-fill warning 호출 확인
        warns = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert any("Over-fill" in w for w in warns)

    def test_normal_full_sell_no_over_fill_warning(self, engine, mock_logger):
        """exe.quantity == lot.quantity 인 정상 전량 매도는 warning 없음."""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]
        signals = [
            SplitSignal("AAPL", "lot_001", OrderAction.SELL, 5, 100.0,
                        "Lv1 익절", 11.1, level=1),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 5, 100.0, 0.5,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]
        updated = engine._update_positions(positions, signals, executions, "2026-04-10")
        assert len(updated) == 0
        warns = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert not any("Over-fill" in w for w in warns)


class TestNoSignalLog:
    """신호 없음 로그가 마지막 차수/매수가/현재가/수익률/임계치를 보여주는지 확인."""

    def test_logs_last_level_status_with_thresholds(self, engine, mock_logger):
        """보유 lot이 있을 때: Lv, 매수가, 현재가, 수익률, 익절·추매 임계치 표시."""
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        positions = [
            PositionLot("lot_001", "AAPL", 100.0, 5, "2026-04-01", level=1),
            PositionLot("lot_002", "AAPL", 95.0, 5, "2026-04-05", level=2),
        ]
        portfolio = Portfolio(
            total_cash=1000.0, holdings={"AAPL": 10},
            current_prices={"AAPL": 97.0},
        )
        engine._log_no_signal_status(rule, positions, portfolio)
        msgs = [c.args[0] for c in mock_logger.info.call_args_list]
        assert len(msgs) == 1
        msg = msgs[0]
        assert f"[{display_ticker('AAPL')}]" in msg
        assert "신호 없음" in msg
        assert "Lv2" in msg
        assert "USD 95.00" in msg
        assert "USD 97.00" in msg
        assert "+2.11%" in msg
        assert "익절 +10.0%" in msg
        assert "추매 -5.0%" in msg

    def test_logs_no_position_waiting_initial(self, engine, mock_logger):
        """보유 lot이 없을 때: 보유 없음 + 현재가 + 1차 진입 대기 표시."""
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        positions = []
        portfolio = Portfolio(
            total_cash=1000.0, holdings={},
            current_prices={"AAPL": 100.0},
        )
        engine._log_no_signal_status(rule, positions, portfolio)
        msgs = [c.args[0] for c in mock_logger.info.call_args_list]
        assert len(msgs) == 1
        assert "보유 없음" in msgs[0]
        assert "USD 100.00" in msgs[0]
        assert "1차 진입 대기" in msgs[0]

    def test_logs_reentry_guard_distance_when_active(self, engine, mock_logger):
        """재진입 가드가 설정된 경우 직전 매도가 대비 거리도 함께 표시."""
        rule = StockRule(
            "AAPL", -5.0, 10.0, 500, 10, reentry_guard_pct=-1.0,
        )
        portfolio = Portfolio(
            total_cash=1000.0, holdings={},
            current_prices={"AAPL": 99.5},
        )
        last_sell_prices = {"AAPL": 100.0}
        engine._log_no_signal_status(rule, [], portfolio, last_sell_prices)
        msg = mock_logger.info.call_args_list[0].args[0]
        assert "직전 매도가 USD 100.00" in msg
        assert "-0.50%" in msg
        assert "가드 -1.00%" in msg

    def test_logs_max_lots_reached_hint(self, engine, mock_logger):
        """max_lots 도달 시 추매 불가 힌트 추가."""
        rule = StockRule("AAPL", -5.0, 10.0, 500, max_lots=2)
        positions = [
            PositionLot("lot_001", "AAPL", 100.0, 5, "2026-04-01", level=1),
            PositionLot("lot_002", "AAPL", 95.0, 5, "2026-04-05", level=2),
        ]
        portfolio = Portfolio(
            total_cash=1000.0, holdings={"AAPL": 10},
            current_prices={"AAPL": 96.0},
        )
        engine._log_no_signal_status(rule, positions, portfolio)
        msg = mock_logger.info.call_args_list[0].args[0]
        assert "max_lots 2 도달" in msg

    def test_logs_price_unavailable(self, engine, mock_logger):
        """현재가 조회 실패(0) 시 짧은 메시지."""
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        portfolio = Portfolio(
            total_cash=1000.0, holdings={}, current_prices={"AAPL": 0.0},
        )
        engine._log_no_signal_status(rule, [], portfolio)
        msg = mock_logger.info.call_args_list[0].args[0]
        assert "현재가 조회 실패" in msg

    def test_run_one_cycle_emits_enriched_no_signal_log(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """run_one_cycle 통합: 신호 없음일 때 enriched 로그가 호출된다."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={"AAPL": 5},
            current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "AAPL", 99.0, 5, "2026-04-01", level=1),
        ]
        mock_repo.load_last_sell_prices.return_value = {}
        eng = MagicSplitEngine(
            broker=mock_broker, repo=mock_repo,
            logger=mock_logger, stock_rules=rules,
        )
        eng.run_one_cycle(sim_date="2026-04-10")
        msgs = [c.args[0] for c in mock_logger.info.call_args_list]
        assert any(
            f"[{display_ticker('AAPL')}]" in m and "신호 없음" in m and "Lv1" in m
            and "USD 99.00" in m and "USD 100.00" in m
            for m in msgs
        )


class TestRunManualTrade:
    """수동매매(run_manual_trade) — evaluate_stock 우회 후 동일 파이프라인 사용."""

    def _make_engine(self, mock_broker, mock_repo, mock_logger, rules, notifier=None):
        return MagicSplitEngine(
            broker=mock_broker, repo=mock_repo, logger=mock_logger,
            stock_rules=rules, notifier=notifier,
        )

    def test_buy_creates_new_lot_at_next_level(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """매수: rule.buy_amount(500)/현재가(100)=5주를 자동 도출, Lv2로 신규 lot 생성."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={"AAPL": 5},
            current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "AAPL", 110.0, 5, "2026-04-01", level=1),
        ]
        mock_repo.load_last_sell_prices.return_value = {}
        mock_broker.execute_orders.return_value = [
            TradeExecution("AAPL", OrderAction.BUY, 5, 100.0, 0.5,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        result = eng.run_manual_trade(
            ticker="AAPL", action=OrderAction.BUY, sim_date="2026-04-10",
        )

        assert result.has_orders is True
        assert len(result.signals) == 1
        assert result.signals[0].reason == "수동 매매(Manual Trade)"
        assert result.signals[0].level == 2
        assert result.signals[0].quantity == 5  # 500 / 100 = 5

        mock_repo.save_positions.assert_called_once()
        saved = mock_repo.save_positions.call_args[0][0]
        assert len(saved) == 2
        new_lot = max(saved, key=lambda l: l.level)
        assert new_lot.level == 2
        assert new_lot.quantity == 5
        assert new_lot.buy_price == 100.0

    def test_sell_auto_derives_qty_from_highest_lot_and_updates_last_sell_prices(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """매도: qty 미지정. 엔진이 최고 차수 lot 전량을 자동 도출."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={"AAPL": 10},
            current_prices={"AAPL": 110.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 110.0}
        mock_repo.load_positions.return_value = [
            PositionLot("lot_lv1", "AAPL", 100.0, 5, "2026-04-01", level=1),
            PositionLot("lot_lv2", "AAPL", 95.0, 5, "2026-04-05", level=2),
        ]
        mock_repo.load_last_sell_prices.return_value = {}
        mock_broker.execute_orders.return_value = [
            TradeExecution("AAPL", OrderAction.SELL, 5, 110.0, 0.5,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        result = eng.run_manual_trade(
            ticker="AAPL", action=OrderAction.SELL, sim_date="2026-04-10",
        )

        # 신호: 최고 차수(Lv2) lot 5주 매도
        assert result.signals[0].lot_id == "lot_lv2"
        assert result.signals[0].level == 2
        assert result.signals[0].buy_price == 95.0
        assert result.signals[0].quantity == 5

        # 브로커에 전달된 주문 수량도 자동 도출된 값
        sent_orders = mock_broker.execute_orders.call_args[0][0]
        assert sent_orders[0].quantity == 5

        saved = mock_repo.save_positions.call_args[0][0]
        assert len(saved) == 1
        assert saved[0].lot_id == "lot_lv1"  # Lv2 lot이 제거됨

        # 완전 청산이므로 last_sell_prices 갱신
        mock_repo.save_last_sell_prices.assert_called_once()
        saved_lsp = mock_repo.save_last_sell_prices.call_args[0][0]
        assert saved_lsp["AAPL"] == 110.0

    def test_sell_with_no_position_raises(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """보유 수량 0인 종목 매도 시도 -> RuntimeError."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={}, current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = []
        mock_repo.load_last_sell_prices.return_value = {}

        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        with pytest.raises(RuntimeError, match="매도할 포지션"):
            eng.run_manual_trade(
                ticker="AAPL", action=OrderAction.SELL,
                sim_date="2026-04-10",
            )

    def test_buy_uses_buy_amounts_array_for_target_level(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """buy_amounts 배열이 있으면 다음 차수 인덱스 값을 사용 (단일값 buy_amount보다 우선)."""
        rules = [StockRule(
            "AAPL", -5.0, 10.0, 500, 10,
            buy_amounts=[300.0, 600.0, 900.0],
        )]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={"AAPL": 3},
            current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "AAPL", 110.0, 3, "2026-04-01", level=1),
        ]
        mock_repo.load_last_sell_prices.return_value = {}
        mock_broker.execute_orders.return_value = [
            TradeExecution("AAPL", OrderAction.BUY, 6, 100.0, 0.5,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        result = eng.run_manual_trade(
            ticker="AAPL", action=OrderAction.BUY, sim_date="2026-04-10",
        )

        # 다음 레벨 = Lv2 -> buy_amounts[1] = 600 / 100 = 6주
        assert result.signals[0].level == 2
        assert result.signals[0].quantity == 6

    def test_buy_amount_too_small_raises(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """rule.buy_amount < 현재가 -> 도출 수량이 0이면 RuntimeError."""
        rules = [StockRule("AAPL", -5.0, 10.0, 50, 10)]  # buy_amount=50
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={}, current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = []
        mock_repo.load_last_sell_prices.return_value = {}

        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        with pytest.raises(RuntimeError, match="매수 수량 0주"):
            eng.run_manual_trade(
                ticker="AAPL", action=OrderAction.BUY, sim_date="2026-04-10",
            )

    def test_buy_at_max_lots_raises(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """이미 max_lots 도달 상태에서 추가 BUY 요청 -> RuntimeError."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 2)]  # max_lots=2
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={"AAPL": 10},
            current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = [
            PositionLot("lot_lv1", "AAPL", 110.0, 5, "2026-04-01", level=1),
            PositionLot("lot_lv2", "AAPL", 100.0, 5, "2026-04-05", level=2),
        ]
        mock_repo.load_last_sell_prices.return_value = {}

        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        with pytest.raises(RuntimeError, match="max_lots"):
            eng.run_manual_trade(
                ticker="AAPL", action=OrderAction.BUY, sim_date="2026-04-10",
            )

    def test_unknown_ticker_raises(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """설정에 없는 ticker -> ValueError."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        with pytest.raises(ValueError, match="등록되지 않은 종목"):
            eng.run_manual_trade(
                ticker="UNKNOWN", action=OrderAction.BUY,
                sim_date="2026-04-10",
            )

    def test_disabled_ticker_buy_raises(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """비활성 ticker BUY -> ValueError, 매수 차단."""
        rules = [
            StockRule("AAPL", -5.0, 10.0, 500, 10, enabled=True),
            StockRule("MSFT", -5.0, 10.0, 500, 10, enabled=False),
        ]
        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        with pytest.raises(ValueError, match="비활성화된 종목 매수 불가"):
            eng.run_manual_trade(
                ticker="MSFT", action=OrderAction.BUY,
                sim_date="2026-04-10",
            )

    def test_disabled_ticker_sell_allowed_for_liquidation(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """비활성 ticker SELL -> 청산 목적으로 허용. 수량은 자동 도출."""
        rules = [
            StockRule("AAPL", -5.0, 10.0, 500, 10, enabled=True),
            StockRule("MSFT", -5.0, 10.0, 500, 10, enabled=False),
        ]
        # MSFT는 비활성이므로 self.all_tickers/get_portfolio prices에 안 잡힘
        # → fetch_current_prices 폴백으로 가격 채움
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={"MSFT": 5},
            current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.side_effect = (
            lambda tickers: {t: (200.0 if t == "MSFT" else 100.0) for t in tickers}
        )
        mock_repo.load_positions.return_value = [
            PositionLot("lot_msft", "MSFT", 180.0, 5, "2026-04-01", level=1),
        ]
        mock_repo.load_last_sell_prices.return_value = {}
        mock_broker.execute_orders.return_value = [
            TradeExecution("MSFT", OrderAction.SELL, 5, 200.0, 0.5,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        result = eng.run_manual_trade(
            ticker="MSFT", action=OrderAction.SELL, sim_date="2026-04-10",
        )
        assert result.has_orders is True
        saved = mock_repo.save_positions.call_args[0][0]
        assert all(l.ticker != "MSFT" for l in saved)

    def test_persist_called_with_manual_signal(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """체결 성공 시 _persist를 통해 save_positions/save_trade_history/save_status 모두 호출."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={}, current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = []
        mock_repo.load_last_sell_prices.return_value = {}
        mock_repo.get_realized_pnl_by_ticker.return_value = {}
        mock_repo.get_last_trade_dates.return_value = {}
        mock_broker.execute_orders.return_value = [
            TradeExecution("AAPL", OrderAction.BUY, 5, 100.0, 0.5,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        eng = self._make_engine(mock_broker, mock_repo, mock_logger, rules)
        eng.run_manual_trade(
            ticker="AAPL", action=OrderAction.BUY,
            sim_date="2026-04-10",
        )

        mock_repo.save_positions.assert_called_once()
        mock_repo.save_trade_history.assert_called_once()
        mock_repo.save_status.assert_called_once()
        # 사유 문자열에 "수동 매매(Manual Trade)"가 포함되어야 한다
        history_args = mock_repo.save_trade_history.call_args
        reason = history_args[0][2]
        assert "수동 매매(Manual Trade)" in reason

    def test_rejected_execution_does_not_persist(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """REJECTED 체결만 있으면 포지션 변경이 없으므로 _persist는 호출되지만
        positions에 새 lot은 추가되지 않고 alert가 발송된다."""
        rules = [StockRule("AAPL", -5.0, 10.0, 500, 10)]
        mock_broker.get_portfolio.return_value = Portfolio(
            total_cash=10000.0, holdings={}, current_prices={"AAPL": 100.0},
        )
        mock_broker.fetch_current_prices.return_value = {"AAPL": 100.0}
        mock_repo.load_positions.return_value = []
        mock_repo.load_last_sell_prices.return_value = {}
        mock_repo.get_realized_pnl_by_ticker.return_value = {}
        mock_repo.get_last_trade_dates.return_value = {}
        mock_broker.execute_orders.return_value = [
            TradeExecution("AAPL", OrderAction.BUY, 0, 0.0, 0.0,
                           "2026-04-10", ExecutionStatus.REJECTED, reason="잔고 부족"),
        ]
        notifier = MagicMock()
        eng = self._make_engine(
            mock_broker, mock_repo, mock_logger, rules, notifier=notifier,
        )
        eng.run_manual_trade(
            ticker="AAPL", action=OrderAction.BUY,
            sim_date="2026-04-10",
        )

        saved = mock_repo.save_positions.call_args[0][0]
        assert saved == []  # 새 lot 미추가
        alert_msgs = [c.args[0] for c in notifier.send_alert.call_args_list]
        assert any("수동매매" in m and "체결 실패 또는 거절" in m for m in alert_msgs)
