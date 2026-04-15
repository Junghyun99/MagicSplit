# tests/test_core_engine.py
import pytest
from unittest.mock import MagicMock, patch
from src.core.engine.base import MagicSplitEngine
from src.core.models import (
    StockRule, PositionLot, Portfolio, Order, OrderAction,
    TradeExecution, ExecutionStatus, SplitSignal,
)


@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.get_portfolio.return_value = Portfolio(
        total_cash=10000.0,
        holdings={"AAPL": 5},
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

        def mock_evaluate_stock(rule, positions, portfolio):
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

        mock_logger.error.assert_any_call("[AAPL] 처리 실패: Mock evaluator error")

        assert result.has_orders is True
        assert len(result.executions) == 1
        assert result.executions[0].ticker == "MSFT"

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

        def mock_evaluate_stock(rule, positions, portfolio):
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

        def mock_update_positions(positions, signals, executions, today):
            # Check if this is the AAPL update
            if any(e.ticker == "AAPL" for e in executions):
                raise Exception("Mock position update error")
            return original_update_positions(positions, signals, executions, today)

        engine._update_positions = MagicMock(side_effect=mock_update_positions)

        result = engine.run_one_cycle(sim_date="2026-04-10")

        # AAPL 에러 로그 확인
        mock_logger.error.assert_any_call("[AAPL] 포지션 반영 실패 (체결은 완료됨): Mock position update error")

        # 두 종목 모두 체결은 완료되었는지 확인
        assert result.has_orders is True
        assert len(result.executions) == 2

        # MSFT 포지션 반영은 정상 수행되었는지 확인 (_update_positions가 여러 번 불렸는지)
        assert engine._update_positions.call_count == 2


    def test_full_cycle_no_signals(self, engine, mock_repo):
        """신호 없을 때 전체 사이클 정상 완료"""
        result = engine.run_one_cycle(sim_date="2026-04-10")

        assert result.date == "2026-04-10"
        mock_repo.save_positions.assert_called_once()
        mock_repo.update_status.assert_called_once()

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
        # 기존 lot: 매수가 90, 현재가 100 → +11.1% (> 10% 임계치)
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]

        execution = TradeExecution(
            "AAPL", OrderAction.SELL, 5, 99.9, 1.25,
            "2026-04-10", ExecutionStatus.FILLED,
        )
        mock_broker.execute_orders.return_value = [execution]

        result = engine.run_one_cycle(sim_date="2026-04-10")

        assert result.has_orders is True
        mock_repo.save_positions.assert_called_once()


class TestUpdatePositions:
    def test_buy_adds_new_lot_with_level(self, engine):
        """매수 체결 → level이 설정된 새 lot 추가"""
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
        """매도 체결 → signal의 lot_id로 특정 lot 제거"""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01", level=1),
        ]
        signals = [
            SplitSignal("AAPL", "lot_001", OrderAction.SELL, 5, 100.0,
                        "Lv1 +11.1% → 익절", 11.1, level=1),
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
                        "Lv2 +15.8% → 익절", 15.8, level=2),
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
