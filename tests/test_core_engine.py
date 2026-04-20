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

        mock_logger.error.assert_any_call("[AAPL] 처리 실패: Mock evaluator error")

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
    """현재가 > buy_amount 일 때 사용자 경고."""

    def test_warns_when_price_exceeds_buy_amount(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """현재가가 buy_amount보다 크면 logger.warning + notifier.send_alert 발송."""
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

        mock_logger.warning.assert_any_call(
            "[BRK-A] buy_amount(500.00) < 현재가(600,000.00) → 1주도 매수 불가. "
            "config.buy_amount를 상향 조정하세요."
        )
        notifier.send_alert.assert_any_call(
            "[BRK-A] buy_amount(500.00) < 현재가(600,000.00) → 1주도 매수 불가. "
            "config.buy_amount를 상향 조정하세요."
        )

    def test_no_warning_when_budget_sufficient(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """buy_amount >= 현재가이면 경고 미발송."""
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

        # 예산 경고 문구는 호출되지 않아야 함
        for call in mock_logger.warning.call_args_list:
            assert "buy_amount" not in call.args[0]
        for call in notifier.send_alert.call_args_list:
            assert "buy_amount" not in call.args[0]

    def test_no_warning_when_max_lots_reached(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """이미 max_lots에 도달한 종목은 어차피 추가 매수 불가 → 경고 생략."""
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

        for call in notifier.send_alert.call_args_list:
            assert "buy_amount" not in call.args[0]

    def test_no_warning_when_price_unavailable(
        self, mock_broker, mock_repo, mock_logger,
    ):
        """현재가 조회 실패(0 이하)이면 예산 경고 미발송 (별도 warning은 존재할 수 있음)."""
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

        for call in notifier.send_alert.call_args_list:
            assert "buy_amount" not in call.args[0]


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
        # AAPL: broker=7, positions sum=5 → 불일치
        # MSFT: broker=0, positions=[] → 일치
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
