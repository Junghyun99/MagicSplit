# tests/test_infra_repo.py
import json
import os
import pytest
from src.infra.repo import JsonRepository
from src.core.models import (
    PositionLot, Portfolio, TradeExecution, OrderAction, ExecutionStatus,
)


@pytest.fixture
def repo(tmp_path):
    return JsonRepository(str(tmp_path))


def test_deferred_writes_remain_readable_and_flush_to_disk(tmp_path):
    deferred = JsonRepository(str(tmp_path), defer_writes=True)
    lot = PositionLot("lot_001", "AAPL", 150.0, 5, "2026-04-01", level=1)

    deferred.save_positions([lot])

    assert deferred.load_positions() == [lot]
    assert not (tmp_path / "positions.json").exists()

    deferred.flush()

    assert json.loads((tmp_path / "positions.json").read_text(encoding="utf-8"))[0]["lot_id"] == "lot_001"


class TestDecisionLogs:
    def test_default_live_policy_keeps_latest_1000_records(self, tmp_path):
        repo = JsonRepository(str(tmp_path), defer_writes=True)

        for index in range(1002):
            repo.save_decision_log(f"day-{index}", f"reason-{index}")

        decisions = repo._load_json(repo.decisions_file, default=[])
        assert len(decisions) == 1000
        assert decisions[0]["date"] == "day-2"
        assert decisions[-1]["date"] == "day-1001"

    def test_unlimited_policy_preserves_full_backtest_period(self, tmp_path):
        repo = JsonRepository(
            str(tmp_path), defer_writes=True, max_decision_records=None,
        )

        for index in range(1002):
            repo.save_decision_log(f"day-{index}", f"reason-{index}")

        decisions = repo._load_json(repo.decisions_file, default=[])
        assert len(decisions) == 1002
        assert decisions[0]["date"] == "day-0"
        assert decisions[-1]["date"] == "day-1001"

    def test_decision_limit_must_be_positive_or_none(self, tmp_path):
        with pytest.raises(ValueError, match="max_decision_records"):
            JsonRepository(str(tmp_path), max_decision_records=0)

    def test_filter_episode_events_append_and_state_round_trip(self, tmp_path):
        repo = JsonRepository(str(tmp_path), defer_writes=True)
        state = {"AAPL": {"reason_code": "long_downtrend"}}

        repo.save_filter_episode_update(
            [{"date": "d1", "ticker": "AAPL", "event": "block_start"}],
            state,
        )

        assert repo.load_filter_events()[0]["event"] == "block_start"
        assert repo.load_filter_episode_state() == state

    def test_filter_event_limit_and_unlimited_mode(self, tmp_path):
        limited = JsonRepository(
            str(tmp_path / "limited"),
            defer_writes=True,
            max_filter_event_records=2,
        )
        limited.save_filter_episode_update(
            [{"event": "one"}, {"event": "two"}, {"event": "three"}], {},
        )
        assert [row["event"] for row in limited.load_filter_events()] == ["two", "three"]

        unlimited = JsonRepository(
            str(tmp_path / "unlimited"),
            defer_writes=True,
            max_filter_event_records=None,
        )
        unlimited.save_filter_episode_update(
            [{"event": str(index)} for index in range(5002)], {},
        )
        assert len(unlimited.load_filter_events()) == 5002

    def test_filter_event_limit_must_be_positive_or_none(self, tmp_path):
        with pytest.raises(ValueError, match="max_filter_event_records"):
            JsonRepository(str(tmp_path), max_filter_event_records=0)

    def test_shadow_records_are_limited_and_state_round_trips(self, tmp_path):
        repo = JsonRepository(
            str(tmp_path),
            defer_writes=True,
            max_shadow_score_records=2,
            max_shadow_event_records=2,
        )
        state = {"AAPL": {"effective_mode": "trend"}}

        repo.save_shadow_mode_update(
            [{"date": "d1"}, {"date": "d2"}, {"date": "d3"}],
            [{"event": "one"}, {"event": "two"}, {"event": "three"}],
            state,
        )

        assert [row["date"] for row in repo.load_shadow_mode_scores()] == ["d2", "d3"]
        assert [row["event"] for row in repo.load_shadow_mode_events()] == ["two", "three"]
        assert repo.load_shadow_mode_state() == state

    def test_shadow_record_limits_apply_per_version(self, tmp_path):
        repo = JsonRepository(
            str(tmp_path), defer_writes=True,
            max_shadow_score_records=2, max_shadow_event_records=2,
        )
        scores = [
            {"score_version": version, "value": value}
            for value in range(3)
            for version in (
                "price_action_v1", "price_action_v2", "price_action_v3",
                "price_action_v3_1", "price_action_v3_2",
            )
        ]
        events = [dict(row) for row in scores]
        repo.save_shadow_mode_update(scores, events, {})

        kept_scores = repo.load_shadow_mode_scores()
        kept_events = repo.load_shadow_mode_events()
        for version in (
            "price_action_v1", "price_action_v2", "price_action_v3",
            "price_action_v3_1", "price_action_v3_2",
        ):
            assert [row["value"] for row in kept_scores if row["score_version"] == version] == [1, 2]
            assert [row["value"] for row in kept_events if row["score_version"] == version] == [1, 2]

    @pytest.mark.parametrize(
        "setting",
        ["max_shadow_score_records", "max_shadow_event_records"],
    )
    def test_shadow_limits_must_be_positive_or_none(self, tmp_path, setting):
        with pytest.raises(ValueError, match=setting):
            JsonRepository(str(tmp_path), **{setting: 0})


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

    def test_position_entry_regimes_round_trip(self, repo):
        lot = PositionLot(
            "lot_001", "AAPL", 150.0, 5, "2026-04-01", level=1,
            entry_long_regime="uptrend", entry_short_regime="uptrend",
        )
        repo.save_positions([lot])

        loaded = repo.load_positions()[0]
        assert loaded.entry_long_regime == "uptrend"
        assert loaded.entry_short_regime == "uptrend"

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

    def test_trade_history_saves_exit_context(self, repo):
        execution = TradeExecution(
            "AAPL", OrderAction.SELL, 5, 140.0, 1.75,
            "2026-04-10", ExecutionStatus.FILLED,
            exit_trigger="channel_lower_break",
            exit_long_regime="uptrend",
            exit_short_regime="sideways",
        )
        portfolio = Portfolio(8000.0, {}, {"AAPL": 140.0})

        repo.save_trade_history([execution], portfolio, "상승 채널 하단 이탈")

        saved = json.loads(
            open(repo.history_file, encoding="utf-8").read()
        )[0]["executions"][0]
        assert saved["exit_trigger"] == "channel_lower_break"
        assert saved["exit_long_regime"] == "uptrend"
        assert saved["exit_short_regime"] == "sideways"

    def test_trade_history_saves_entry_trigger(self, repo):
        execution = TradeExecution(
            "AAPL", OrderAction.BUY, 5, 140.0, 1.75,
            "2026-04-10", ExecutionStatus.FILLED,
            entry_trigger="rebound_initial_entry",
        )
        repo.save_trade_history(
            [execution], Portfolio(8000.0, {"AAPL": 5}, {"AAPL": 140.0}),
            "반등 진입",
        )
        saved = json.loads(open(repo.history_file, encoding="utf-8").read())[0]["executions"][0]
        assert saved["entry_trigger"] == "rebound_initial_entry"

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

    def test_snapshot_records_exchange_rate(self, repo):
        """해외 포트폴리오의 그날 기준환율이 스냅샷에 저장된다 (원화 결산용)"""
        pf = Portfolio(1000.0, {"AAPL": 5}, {"AAPL": 200.0}, exchange_rate=1350.5)
        repo.save_snapshot(pf, [], sim_date="2026-04-01")
        assert repo.load_snapshots()[0]["exchange_rate"] == 1350.5

    def test_snapshot_exchange_rate_none_when_absent(self, repo):
        """환율 없는(domestic/조회실패) 포트폴리오는 exchange_rate가 None으로 저장"""
        repo.save_snapshot(Portfolio(10000.0, {}, {}), [], sim_date="2026-04-01")
        assert repo.load_snapshots()[0]["exchange_rate"] is None

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
        # 첫 실행 10000 + 둘째 실행 추가 입금 2000 = 당일 순입금 12000
        assert snaps[0]["net_deposit"] == 12000.0

    def test_snapshot_same_date_accumulates_trade_impact(self, repo):
        """같은 날짜 재실행이 각각 체결을 동반해도 순입금이 왜곡되지 않는다.

        두 실행 모두 매수(현금 유출)만 있고 실제 입출금은 0이면 당일 순입금도 0.
        덮어쓰기 시 앞선 실행의 매수 현금영향이 누락되면 안 된다.
        """
        # 전일: 현금 10000
        repo.save_snapshot(Portfolio(10000.0, {}, {}), [], sim_date="2026-04-01")
        # 당일 1차: 5주 @150 매수 -> 현금 9250
        exe1 = [TradeExecution("AAPL", OrderAction.BUY, 5, 150.0, 0.0,
                               "2026-04-02", ExecutionStatus.FILLED)]
        repo.save_snapshot(Portfolio(9250.0, {"AAPL": 5}, {"AAPL": 150.0}),
                           exe1, sim_date="2026-04-02")
        # 당일 2차: 3주 @100 매수 -> 현금 8950
        exe2 = [TradeExecution("MSFT", OrderAction.BUY, 3, 100.0, 0.0,
                               "2026-04-02", ExecutionStatus.FILLED)]
        repo.save_snapshot(Portfolio(8950.0, {"AAPL": 5, "MSFT": 3},
                                     {"AAPL": 150.0, "MSFT": 100.0}),
                           exe2, sim_date="2026-04-02")
        snaps = repo.load_snapshots()
        assert len(snaps) == 2
        # 순수 입출금 0 (매수만 발생) -> net_deposit 0, 앞선 매수 누락 시 -750이 됨
        assert snaps[1]["net_deposit"] == 0.0
        assert snaps[1]["cash_balance"] == 8950.0

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


class TestRegimeEvents:
    def test_empty_by_default(self, repo):
        assert repo.load_regime_events() == []

    def test_append_accumulates_across_calls(self, repo):
        repo.save_regime_events([{"date": "d1", "ticker": "AAPL", "event": "downtrend_on"}])
        repo.save_regime_events([{"date": "d2", "ticker": "TSLA", "event": "uptrend_on"}])

        events = repo.load_regime_events()
        assert [e["ticker"] for e in events] == ["AAPL", "TSLA"]

    def test_empty_list_is_a_noop(self, repo):
        repo.save_regime_events([{"date": "d1", "ticker": "AAPL", "event": "uptrend_on"}])
        repo.save_regime_events([])

        assert len(repo.load_regime_events()) == 1

    def test_rolling_trim_keeps_most_recent(self, repo):
        repo.save_regime_events([
            {"date": f"d{i}", "ticker": "AAPL", "event": "uptrend_on"}
            for i in range(5200)
        ])

        events = repo.load_regime_events()
        assert len(events) == 5000
        assert events[-1]["date"] == "d5199"


class TestChartSeries:
    def test_missing_chart_returns_none(self, repo):
        assert repo.load_chart_series("AAPL") is None

    def test_save_and_load_roundtrip(self, repo):
        chart = {"ticker": "AAPL", "cols": ["date", "close"],
                 "rows": [["2026-07-30", 331.2]]}
        repo.save_chart_series("AAPL", chart)

        assert repo.load_chart_series("AAPL") == chart

    def test_overwrites_instead_of_appending(self, repo):
        repo.save_chart_series("AAPL", {"rows": [1, 2, 3]})
        repo.save_chart_series("AAPL", {"rows": [9]})

        assert repo.load_chart_series("AAPL") == {"rows": [9]}

    def test_ticker_with_separator_is_filename_safe(self, repo):
        """업비트 티커(KRW-BTC)나 슬래시가 경로로 새어 나가면 안 된다."""
        repo.save_chart_series("KRW-BTC", {"rows": [1]})
        repo.save_chart_series("A/B", {"rows": [2]})

        assert repo.load_chart_series("KRW-BTC") == {"rows": [1]}
        assert repo.load_chart_series("A/B") == {"rows": [2]}
        names = sorted(os.listdir(repo.charts_dir))
        assert names == ["A_B.json", "KRW-BTC.json"]

    def test_written_without_indentation(self, repo):
        """차트는 기계 소비 파일이라 들여쓰기 없이 저장해 용량을 줄인다."""
        repo.save_chart_series("AAPL", {"cols": ["date", "close"], "rows": [["d", 1.0]]})

        path = os.path.join(repo.charts_dir, "AAPL.json")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        assert "\n" not in raw
        assert ": " not in raw

    def test_nan_is_sanitized_to_null(self, repo):
        repo.save_chart_series("AAPL", {"rows": [[float("nan")]]})

        path = os.path.join(repo.charts_dir, "AAPL.json")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        assert "NaN" not in raw
        assert json.loads(raw) == {"rows": [[None]]}


class TestRejectedExecutionsAreNotTrades:
    """거절은 체결이 아니다.

    사전 차단(스프레드 비정상 등)은 주문이 나가지 않았는데도 '요청 수량'을
    그대로 담아 REJECTED로 돌아온다. 수량만으로 걸러지지 않으므로 상태로 걸러야
    거래대금·순입금이 오염되지 않는다.
    """

    def _exe(self, action, qty, price, status, ticker="AAPL", date="2024-01-02 10:00:00"):
        return TradeExecution(
            ticker=ticker, action=action, quantity=qty, price=price, fee=0.0,
            date=date, status=status,
        )

    def test_cash_impact_excludes_rejected_with_quantity(self):
        from src.infra.repo import trade_cash_impact

        rejected = self._exe(OrderAction.SELL, 27, 45015.0, ExecutionStatus.REJECTED)
        assert trade_cash_impact([rejected]) == 0.0

    def test_cash_impact_counts_filled_only(self):
        from src.infra.repo import trade_cash_impact

        execs = [
            self._exe(OrderAction.SELL, 27, 45015.0, ExecutionStatus.REJECTED),
            self._exe(OrderAction.SELL, 2, 100.0, ExecutionStatus.FILLED),
        ]
        assert trade_cash_impact(execs) == 200.0

    def test_history_skips_rejected_execution(self, repo):
        pf = Portfolio(total_cash=1000.0, holdings={}, current_prices={"AAPL": 100.0})
        repo.save_trade_history(
            [
                self._exe(OrderAction.SELL, 27, 45015.0, ExecutionStatus.REJECTED),
                self._exe(OrderAction.BUY, 2, 100.0, ExecutionStatus.FILLED),
            ],
            pf, "혼합",
        )

        execs = repo.load_history()[-1]["executions"]
        assert len(execs) == 1
        assert execs[0]["status"] == "FILLED"

    def test_no_record_when_all_rejected(self, repo):
        """거절뿐이면 매매가 없던 사이클이므로 레코드 자체를 남기지 않는다."""
        pf = Portfolio(total_cash=1000.0, holdings={}, current_prices={})
        repo.save_trade_history(
            [self._exe(OrderAction.SELL, 27, 45015.0, ExecutionStatus.REJECTED)],
            pf, "전부 거절",
        )

        assert repo.load_history() == []

    def test_rejected_does_not_inflate_trade_amount(self, repo):
        pf = Portfolio(total_cash=1000.0, holdings={}, current_prices={})
        repo.save_trade_history(
            [
                self._exe(OrderAction.SELL, 27, 45015.0, ExecutionStatus.REJECTED),
                self._exe(OrderAction.BUY, 1, 50.0, ExecutionStatus.FILLED),
            ],
            pf, "혼합",
        )

        assert repo.load_history()[-1]["total_trade_amount"] == 50.0

    def test_rejected_does_not_skew_net_deposit(self, repo):
        """거절 매도가 현금 유입으로 잡히면 순입금이 그만큼 마이너스로 왜곡된다."""
        pf = Portfolio(total_cash=1000.0, holdings={}, current_prices={})
        repo.save_trade_history(
            [self._exe(OrderAction.BUY, 1, 100.0, ExecutionStatus.FILLED)], pf, "첫 거래",
        )
        repo.save_trade_history(
            [
                self._exe(OrderAction.SELL, 27, 45015.0, ExecutionStatus.REJECTED),
                self._exe(OrderAction.BUY, 1, 100.0, ExecutionStatus.FILLED),
            ],
            Portfolio(total_cash=900.0, holdings={}, current_prices={}), "거절 포함",
        )

        # 현금 1000 -> 900, 매수 100 -> 외부 입출금 0
        assert repo.load_history()[-1]["net_deposit"] == 0.0

    def test_last_trade_date_ignores_rejected(self, repo):
        """거절이 거래일로 잡히면 '장기 정체 종목'이 거짓으로 리셋된다."""
        pf = Portfolio(total_cash=1000.0, holdings={}, current_prices={})
        repo.save_trade_history(
            [self._exe(OrderAction.BUY, 1, 100.0, ExecutionStatus.FILLED,
                       date="2024-01-01 10:00:00")],
            pf, "체결",
        )
        # 이미 기록돼 있던 과거 거절 데이터를 직접 밀어 넣는다 (마이그레이션 전 상태)
        data = repo.load_history()
        data.append({
            "id": "tx_legacy", "date": "2024-06-01 10:00:00",
            "portfolio_value": 1000.0, "cash_balance": 1000.0,
            "net_deposit": 0.0, "total_trade_amount": 0.0, "reason": "-",
            "executions": [{
                "ticker": "AAPL", "action": "SELL", "quantity": 0, "price": 100.0,
                "fee": 0.0, "date": "2024-06-01 10:00:00", "status": "REJECTED",
            }],
        })
        repo._save_json(repo.history_file, data)

        assert repo.get_last_trade_dates()["AAPL"] == "2024-01-01"
