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
        """체결 내역이 없으면 저장하지 않음 (초기화된 빈 상태 유지)"""
        portfolio = Portfolio(10000.0, {}, {})
        repo.save_trade_history([], portfolio, "no trades")

        with open(repo.history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        assert data == []

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


class TestSnapshots:
    def test_save_and_load_snapshot(self, repo):
        """스냅샷 저장/로드 라운드트립 및 필드 구성"""
        pf = Portfolio(8000.0, {"AAPL": 5}, {"AAPL": 200.0})  # value=9000
        repo.save_snapshot(pf, [], sim_date="2026-04-01")

        snaps = repo.load_snapshots()
        assert len(snaps) == 1
        s = snaps[0]
        assert s["date"] == "2026-04-01"
        assert s["portfolio_value"] == 9000.0
        assert s["cash_balance"] == 8000.0
        assert s["stock_value"] == 1000.0

    def test_snapshot_saved_without_executions(self, repo):
        """거래가 없어도 스냅샷은 저장된다 (history와 달리 무조건 기록)"""
        pf = Portfolio(10000.0, {}, {})
        repo.save_snapshot(pf, [], sim_date="2026-04-01")
        assert len(repo.load_snapshots()) == 1

    def test_snapshot_net_deposit_first_record(self, repo):
        """첫 스냅샷의 순입금은 (현금 - 거래현금영향)"""
        pf = Portfolio(10000.0, {}, {})
        repo.save_snapshot(pf, [], sim_date="2026-04-01")
        assert repo.load_snapshots()[0]["net_deposit"] == 10000.0

    def test_snapshot_net_deposit_pure_deposit(self, repo):
        """거래 없이 현금만 증가하면 그대로 순입금으로 잡힌다"""
        repo.save_snapshot(Portfolio(10000.0, {}, {}), [], sim_date="2026-04-01")
        repo.save_snapshot(Portfolio(15000.0, {}, {}), [], sim_date="2026-04-02")
        snaps = repo.load_snapshots()
        assert snaps[1]["net_deposit"] == 5000.0

    def test_snapshot_net_deposit_excludes_trade(self, repo):
        """매수로 인한 현금 감소는 순입금에 반영되지 않는다"""
        repo.save_snapshot(Portfolio(10000.0, {}, {}), [], sim_date="2026-04-01")
        # 다음 날 5주 @150 매수 (수수료 0) -> 현금 10000 - 750 = 9250, 순입금 0
        exe = [TradeExecution("AAPL", OrderAction.BUY, 5, 150.0, 0.0,
                              "2026-04-02", ExecutionStatus.FILLED)]
        repo.save_snapshot(Portfolio(9250.0, {"AAPL": 5}, {"AAPL": 150.0}),
                           exe, sim_date="2026-04-02")
        assert repo.load_snapshots()[1]["net_deposit"] == 0.0

    def test_snapshot_same_date_overwrites(self, repo):
        """같은 날짜 재실행 시 덮어쓰기 — 하루 1개 대표값 유지"""
        repo.save_snapshot(Portfolio(10000.0, {}, {}), [], sim_date="2026-04-01")
        repo.save_snapshot(Portfolio(12000.0, {}, {}), [], sim_date="2026-04-01")
        snaps = repo.load_snapshots()
        assert len(snaps) == 1
        assert snaps[0]["portfolio_value"] == 12000.0

    def test_snapshot_datetime_normalized_to_date(self, repo):
        """sim_date=None (라이브) 시 날짜만(YYYY-MM-DD) 저장된다"""
        repo.save_snapshot(Portfolio(10000.0, {}, {}), [])
        s = repo.load_snapshots()[0]
        assert len(s["date"]) == 10 and s["date"].count("-") == 2

    def test_load_empty_snapshots(self, repo):
        """파일이 없으면 빈 리스트"""
        assert repo.load_snapshots() == []


class TestStatus:
    def test_save_and_get_status(self, repo):
        """상태 저장 및 마지막 실행일 조회"""
        status_data = {"last_run_date": "2026-04-10", "positions": {"AAPL": {}}}
        repo.save_status(status_data)

        last_run = repo.get_last_run_date()
        assert last_run == "2026-04-10"

    def test_get_last_run_date_no_file(self, repo):
        """status 파일에 데이터가 없으면 None"""
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
        repo.clear_cache()

        loaded = repo.load_positions()
        assert len(loaded) == 2
        assert loaded[0].level == 1
        assert loaded[1].level == 2


class TestRegimeStateRoundtrip:
    def test_status_preserves_nested_regime_state(self, repo):
        rs = {"AAPL": {"regime": "uptrend", "adds": 1, "last_add_swing_high": 150.5}}
        repo.save_status({"last_run_date": "2026-04-10", "regime_state_by_ticker": rs})
        loaded = repo.load_status()
        assert loaded["regime_state_by_ticker"] == rs


class TestBulkLiquidationHistory:
    def test_breakdown_expands_to_per_lot_records(self, repo):
        exe = TradeExecution(
            "AAPL", OrderAction.SELL, 10, 90.0, 0.0, "2024-01-02",
            ExecutionStatus.FILLED, reason="Bulk 청산",
            buy_price=55.0, realized_pnl=350.0,
            liquidation_lots=[
                {"lot_id": "lotB", "level": 2, "buy_price": 60.0, "quantity": 5, "realized_pnl": 150.0},
                {"lot_id": "lotA", "level": 1, "buy_price": 50.0, "quantity": 5, "realized_pnl": 200.0},
            ],
        )
        pf = Portfolio(total_cash=1000.0, holdings={}, current_prices={"AAPL": 90.0})
        repo.save_trade_history([exe], pf, "Bulk 청산", sim_date="2024-01-02")

        with open(repo.history_file, encoding="utf-8") as f:
            history = json.load(f)
        execs = history[-1]["executions"]
        # 1건의 통합 청산이 lot별 2개 레코드로 분리됨
        assert len(execs) == 2
        assert {e["level"] for e in execs} == {1, 2}
        assert {e["lot_id"] for e in execs} == {"lotA", "lotB"}
        # 분리된 레코드엔 raw breakdown 필드가 남지 않는다
        assert all("liquidation_lots" not in e for e in execs)
        # 차수별 손익 합 보존
        assert round(sum(e["realized_pnl"] for e in execs), 2) == 350.0
