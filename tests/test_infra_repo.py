# tests/test_infra_repo.py
import json
import pytest
from src.infra.repo import JsonRepository
from src.core.models import (
    PositionLot, Portfolio, TradeExecution, OrderAction, ExecutionStatus,
)


@pytest.fixture
def repo(tmp_path):
    return JsonRepository(str(tmp_path))


class TestPositions:
    def test_save_and_load_positions(self, repo):
        """포지션 저장/로드 라운드트립"""
        lots = [
            PositionLot("lot_001", "AAPL", 150.0, 5, "2026-04-01"),
            PositionLot("lot_002", "MSFT", 300.0, 3, "2026-04-02"),
        ]
        repo.save_positions(lots)
        loaded = repo.load_positions()

        assert len(loaded) == 2
        assert loaded[0].lot_id == "lot_001"
        assert loaded[0].ticker == "AAPL"
        assert loaded[0].buy_price == 150.0
        assert loaded[0].quantity == 5
        assert loaded[1].lot_id == "lot_002"

    def test_load_empty_positions(self, repo):
        """파일이 없으면 빈 리스트"""
        loaded = repo.load_positions()
        assert loaded == []

    def test_overwrite_positions(self, repo):
        """저장 시 기존 데이터 덮어쓰기"""
        repo.save_positions([PositionLot("lot_001", "AAPL", 100.0, 5, "2026-04-01")])
        repo.save_positions([PositionLot("lot_002", "MSFT", 200.0, 3, "2026-04-02")])

        loaded = repo.load_positions()
        assert len(loaded) == 1
        assert loaded[0].lot_id == "lot_002"


class TestTradeHistory:
    def test_save_trade_history(self, repo):
        """매매 내역 저장"""
        executions = [
            TradeExecution("AAPL", OrderAction.BUY, 5, 150.0, 1.88, "2026-04-10", ExecutionStatus.FILLED),
        ]
        portfolio = Portfolio(8000.0, {"AAPL": 5}, {"AAPL": 150.0})

        repo.save_trade_history(executions, portfolio, "초기 매수")

        # 파일 확인
        data = json.loads(open(repo.history_file, 'r').read())
        assert len(data) == 1
        assert data[0]["reason"] == "초기 매수"
        assert len(data[0]["executions"]) == 1

    def test_empty_executions_not_saved(self, repo):
        """체결 내역이 없으면 저장하지 않음"""
        portfolio = Portfolio(10000.0, {}, {})
        repo.save_trade_history([], portfolio, "no trades")

        import os
        assert not os.path.exists(repo.history_file)

    def test_append_history(self, repo):
        """매매 내역은 append 방식"""
        pf = Portfolio(10000.0, {"AAPL": 5}, {"AAPL": 150.0})
        exe1 = [TradeExecution("AAPL", OrderAction.BUY, 5, 150.0, 1.0, "2026-04-10", ExecutionStatus.FILLED)]
        exe2 = [TradeExecution("AAPL", OrderAction.SELL, 5, 160.0, 1.0, "2026-04-11", ExecutionStatus.FILLED)]

        repo.save_trade_history(exe1, pf, "매수")
        repo.save_trade_history(exe2, pf, "매도")

        data = json.loads(open(repo.history_file, 'r').read())
        assert len(data) == 2


class TestStatus:
    def test_update_and_get_status(self, repo):
        """상태 저장 및 마지막 실행일 조회"""
        portfolio = Portfolio(10000.0, {"AAPL": 5}, {"AAPL": 150.0})
        positions = [PositionLot("lot_001", "AAPL", 140.0, 5, "2026-04-01")]

        repo.update_status(portfolio, positions, "모니터링", sim_date="2026-04-10")

        last_run = repo.get_last_run_date()
        assert last_run == "2026-04-10"

    def test_get_last_run_date_no_file(self, repo):
        """status 파일이 없으면 None"""
        assert repo.get_last_run_date() is None

    def test_status_contains_position_details(self, repo):
        """상태에 포지션 상세 정보 포함"""
        portfolio = Portfolio(10000.0, {"AAPL": 5}, {"AAPL": 160.0})
        positions = [PositionLot("lot_001", "AAPL", 150.0, 5, "2026-04-01")]

        repo.update_status(portfolio, positions, "test")

        data = json.loads(open(repo.status_file, 'r').read())
        assert "positions" in data
        assert "AAPL" in data["positions"]
        assert data["positions"]["AAPL"]["lot_count"] == 1
        # pct_change: (160-150)/150*100 = 6.67%
        lot_detail = data["positions"]["AAPL"]["lots"][0]
        assert lot_detail["pct_change"] == pytest.approx(6.67, abs=0.01)
