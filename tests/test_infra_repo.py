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
            PositionLot("lot_001", "AAPL", 150.0, 5, "2026-04-01", level=1),
            PositionLot("lot_002", "MSFT", 300.0, 3, "2026-04-02", level=1),
        ]
        repo.save_positions(lots)
        loaded = repo.load_positions()

        assert len(loaded) == 2
        assert loaded[0].lot_id == "lot_001"
        assert loaded[0].ticker == "AAPL"
        assert loaded[0].buy_price == 150.0
        assert loaded[0].quantity == 5
        assert loaded[0].level == 1
        assert loaded[1].lot_id == "lot_002"
        assert loaded[1].level == 1

    def test_load_empty_positions(self, repo):
        """파일이 없으면 빈 리스트"""
        loaded = repo.load_positions()
        assert loaded == []

    def test_overwrite_positions(self, repo):
        """저장 시 기존 데이터 덮어쓰기"""
        repo.save_positions([PositionLot("lot_001", "AAPL", 100.0, 5, "2026-04-01", level=1)])
        repo.save_positions([PositionLot("lot_002", "MSFT", 200.0, 3, "2026-04-02", level=1)])

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
        with open(repo.history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
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

        with open(repo.history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert len(data) == 2


class TestStatus:
    def test_save_and_get_status(self, repo):
        """상태 저장 및 마지막 실행일 조회"""
        status_data = {"last_run_date": "2026-04-10", "positions": {"AAPL": {}}}
        repo.save_status(status_data)

        last_run = repo.get_last_run_date()
        assert last_run == "2026-04-10"

    def test_get_last_run_date_no_file(self, repo):
        """status 파일이 없으면 None"""
        assert repo.get_last_run_date() is None

    def test_get_realized_pnl_by_ticker(self, repo):
        """누적 손익 조회"""
        status_data = {"realized_pnl_by_ticker": {"AAPL": 100.0}}
        repo.save_status(status_data)
        
        pnl = repo.get_realized_pnl_by_ticker()
        assert pnl == {"AAPL": 100.0}

    def test_load_legacy_positions_without_level(self, repo):
        """레거시 positions.json (level 필드 없음) 정상 로드 및 마이그레이션"""
        legacy_data = [
            {"lot_id": "lot_001", "ticker": "AAPL", "buy_price": 100.0,
             "quantity": 5, "buy_date": "2026-04-01"},
            {"lot_id": "lot_002", "ticker": "AAPL", "buy_price": 95.0,
             "quantity": 5, "buy_date": "2026-04-05"},
        ]
        with open(repo.positions_file, 'w', encoding='utf-8') as f:
            json.dump(legacy_data, f, ensure_ascii=False)

        loaded = repo.load_positions()
        assert len(loaded) == 2
        assert loaded[0].level == 1
        assert loaded[1].level == 2
