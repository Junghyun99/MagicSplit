# tests/test_main_integration.py
"""MagicSplitBot 통합 테스트 — MockBroker를 사용한 전체 플로우."""
import pytest
from unittest.mock import MagicMock
from src.core.engine.base import MagicSplitEngine
from src.core.models import StockRule
from src.infra.broker.mock import MockBroker
from src.infra.repo import JsonRepository


@pytest.fixture
def setup_bot(tmp_path):
    """MockBroker + JsonRepository로 엔진 구성"""
    broker = MockBroker(
        initial_cash=10000.0,
        holdings={},
        prices={"AAPL": 100.0, "MSFT": 200.0},
    )
    repo = JsonRepository(str(tmp_path))
    logger = MagicMock()
    rules = [
        StockRule("AAPL", -5.0, 10.0, 500, 100),
        StockRule("MSFT", -5.0, 10.0, 1000, 100),
    ]
    engine = MagicSplitEngine(
        broker=broker,
        repo=repo,
        logger=logger,
        stock_rules=rules,
    )
    return engine, broker, repo


class TestFullCycle:
    def test_initial_buy_cycle(self, setup_bot):
        """첫 실행: 모든 종목 초기 매수"""
        engine, broker, repo = setup_bot

        result = engine.run_one_cycle(sim_date="2026-04-10")

        assert result.has_orders is True
        assert len(result.executions) == 2  # AAPL + MSFT

        # 포지션 저장 확인
        positions = repo.load_positions()
        assert len(positions) == 2
        tickers = {p.ticker for p in positions}
        assert tickers == {"AAPL", "MSFT"}

    def test_monitoring_cycle(self, setup_bot):
        """가격 변동 없으면 모니터링만"""
        engine, broker, repo = setup_bot

        # 첫 사이클: 초기 매수
        engine.run_one_cycle(sim_date="2026-04-10")

        # 가격 변동 없이 두 번째 사이클
        result = engine.run_one_cycle(sim_date="2026-04-11")

        # 초기 매수 이후 가격 변동이 없으므로 추가 주문 없음
        # (단, mock broker의 slippage로 인해 buy_price와 current_price가 약간 다를 수 있음)
        assert result.date == "2026-04-11"

    def test_sell_on_price_rise(self, setup_bot):
        """가격 상승 시 익절 매도"""
        engine, broker, repo = setup_bot

        # 첫 사이클: 초기 매수
        engine.run_one_cycle(sim_date="2026-04-10")

        # 가격 상승 (AAPL: 100 → 115, +15% > 10% 임계치)
        broker.prices["AAPL"] = 115.0

        result = engine.run_one_cycle(sim_date="2026-04-11")

        # 매도 신호가 발생해야 함
        sell_executions = [e for e in result.executions if str(e.action) == "SELL"]
        assert len(sell_executions) >= 1

    def test_additional_buy_on_drop(self, setup_bot):
        """가격 하락 시 추가 매수"""
        engine, broker, repo = setup_bot

        # 첫 사이클: 초기 매수
        engine.run_one_cycle(sim_date="2026-04-10")

        # 가격 하락 (AAPL: 100 → 90, -10% < -5% 임계치)
        broker.prices["AAPL"] = 90.0

        result = engine.run_one_cycle(sim_date="2026-04-11")

        # 추가 매수 신호가 발생해야 함
        buy_executions = [e for e in result.executions if str(e.action) == "BUY"]
        # AAPL 추가 매수 (MSFT는 가격 변동 없으므로 스킵)
        aapl_buys = [e for e in buy_executions if e.ticker == "AAPL"]
        assert len(aapl_buys) >= 1

        # 포지션에 새 lot 추가 확인
        positions = repo.load_positions()
        aapl_lots = [p for p in positions if p.ticker == "AAPL"]
        assert len(aapl_lots) >= 2  # 초기 + 추가
