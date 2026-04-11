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
    def test_full_cycle_no_signals(self, engine, mock_repo):
        """신호 없을 때 전체 사이클 정상 완료"""
        # 현재가 100, 매수가 없음 (lot 없으면 초기 매수 발생)
        # 하지만 mock_broker가 execute_orders를 빈 리스트로 반환
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

    def test_full_cycle_with_sell(self, engine, mock_broker, mock_repo):
        """익절 매도 시 전체 사이클"""
        # 기존 lot: 매수가 90, 현재가 100 → +11.1% (> 10% 임계치)
        mock_repo.load_positions.return_value = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01"),
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
    def test_buy_adds_new_lot(self, engine):
        """매수 체결 → 새 lot 추가"""
        positions = []
        executions = [
            TradeExecution("AAPL", OrderAction.BUY, 5, 100.0, 1.0,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        updated = engine._update_positions(positions, executions, "2026-04-10")

        assert len(updated) == 1
        assert updated[0].ticker == "AAPL"
        assert updated[0].buy_price == 100.0
        assert updated[0].quantity == 5

    def test_sell_removes_lot(self, engine):
        """매도 체결 → lot 제거"""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 5, "2026-04-01"),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 5, 100.0, 1.0,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        updated = engine._update_positions(positions, executions, "2026-04-10")
        assert len(updated) == 0

    def test_partial_sell(self, engine):
        """부분 매도 → lot 수량 감소"""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 10, "2026-04-01"),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 3, 100.0, 1.0,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        updated = engine._update_positions(positions, executions, "2026-04-10")
        assert len(updated) == 1
        assert updated[0].quantity == 7

    def test_sell_fifo_order(self, engine):
        """FIFO: 가장 오래된 lot부터 매도"""
        positions = [
            PositionLot("lot_001", "AAPL", 90.0, 3, "2026-04-01"),
            PositionLot("lot_002", "AAPL", 95.0, 5, "2026-04-05"),
        ]
        executions = [
            TradeExecution("AAPL", OrderAction.SELL, 5, 100.0, 1.0,
                           "2026-04-10", ExecutionStatus.FILLED),
        ]

        updated = engine._update_positions(positions, executions, "2026-04-10")

        # lot_001(3주) 전량 + lot_002(2주) 부분 매도 = 5주
        assert len(updated) == 1
        assert updated[0].lot_id == "lot_002"
        assert updated[0].quantity == 3  # 5 - 2 = 3
