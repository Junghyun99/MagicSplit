# tests/test_migrate_manual_trade.py
"""브로커 앱 수동매매 사후 반영(scripts/migrate_manual_trade.py) 테스트."""
import json
import os

import pytest

from scripts.migrate_manual_trade import (
    accrue_realized_pnl, apply_trades, compose_reason, fix_snapshot_net_deposit,
    main, parse_trades, rebuild_status,
)
from src.core.models import ExecutionStatus, OrderAction, PositionLot, TradeExecution
from src.infra.repo import JsonRepository


def _lot(ticker, qty, level, buy_price=100.0, lot_id=None):
    return PositionLot(
        lot_id=lot_id or f"lot_{ticker}_{level:03d}",
        ticker=ticker,
        buy_price=buy_price,
        quantity=qty,
        buy_date="2026-07-17",
        level=level,
    )


class TestParseTrades:
    def test_normalizes_and_computes_fee(self):
        trades = parse_trades(
            '[{"ticker":"krw-eth","action":"SELL","quantity":2,"price":1000}]',
            default_date="2026-07-24", fee_rate=0.05,
        )
        assert len(trades) == 1
        t = trades[0]
        assert t["ticker"] == "KRW-ETH"
        assert t["action"] == "sell"
        assert t["date"] == "2026-07-24"
        assert t["fee"] == pytest.approx(2 * 1000 * 0.0005)

    def test_explicit_fee_wins_over_rate(self):
        trades = parse_trades(
            '[{"ticker":"A","action":"buy","quantity":1,"price":10,"fee":3}]',
            default_date="2026-07-24", fee_rate=0.05,
        )
        assert trades[0]["fee"] == 3

    def test_sell_is_ordered_before_buy(self):
        trades = parse_trades(
            '[{"ticker":"A","action":"buy","quantity":1,"price":10},'
            ' {"ticker":"B","action":"sell","quantity":1,"price":10}]',
            default_date="2026-07-24", fee_rate=0.0,
        )
        assert [t["action"] for t in trades] == ["sell", "buy"]

    @pytest.mark.parametrize("raw", [
        "not-json",
        "[]",
        "{}",
        '[{"ticker":"A","action":"hold","quantity":1,"price":10}]',
        '[{"ticker":"","action":"buy","quantity":1,"price":10}]',
        '[{"ticker":"A","action":"buy","quantity":0,"price":10}]',
        '[{"ticker":"A","action":"buy","quantity":1,"price":-5}]',
        '[{"ticker":"A","action":"buy","quantity":"x","price":10}]',
        '[{"ticker":"A","action":"buy","quantity":1,"price":10,"date":"07/24"}]',
    ])
    def test_rejects_invalid_input(self, raw):
        with pytest.raises(ValueError):
            parse_trades(raw, default_date=None, fee_rate=0.0)

    def test_rejects_mixed_dates(self):
        with pytest.raises(ValueError, match="같은 날짜"):
            parse_trades(
                '[{"ticker":"A","action":"buy","quantity":1,"price":10,"date":"2026-07-24"},'
                ' {"ticker":"B","action":"buy","quantity":1,"price":10,"date":"2026-07-25"}]',
                default_date=None, fee_rate=0.0,
            )


class TestApplyTrades:
    def test_partial_sell_shrinks_lot_and_records_pnl(self):
        positions = [_lot("KRW-BTC", 0.13703789, 1, buy_price=114690236.0)]
        last_sell_prices = {}
        trades = parse_trades(
            '[{"ticker":"KRW-BTC","action":"sell","quantity":0.06851894,'
            ' "price":95046508,"date":"2026-07-24"}]',
            default_date=None, fee_rate=0.05,
        )

        executions = apply_trades(positions, trades, last_sell_prices, "crypto")

        assert len(positions) == 1
        assert positions[0].quantity == pytest.approx(0.06851895)
        assert positions[0].level == 1
        assert positions[0].buy_price == 114690236.0
        assert last_sell_prices["KRW-BTC"] == 95046508
        exe = executions[0]
        assert exe.action == OrderAction.SELL
        assert exe.realized_pnl < 0
        assert exe.liquidation_lots[0]["level"] == 1

    def test_full_sell_removes_lot_highest_level_first(self):
        positions = [_lot("A", 5, 1), _lot("A", 5, 2, buy_price=90.0)]
        trades = parse_trades(
            '[{"ticker":"A","action":"sell","quantity":5,"price":120}]',
            default_date="2026-07-24", fee_rate=0.0,
        )

        apply_trades(positions, trades, {}, "domestic")

        assert len(positions) == 1
        assert positions[0].level == 1

    def test_buy_appends_lot_at_next_level(self):
        positions = [_lot("A", 5, 1)]
        trades = parse_trades(
            '[{"ticker":"A","action":"buy","quantity":3,"price":90,"date":"2026-07-24"}]',
            default_date=None, fee_rate=0.0,
        )

        executions = apply_trades(positions, trades, {}, "domestic")

        assert len(positions) == 2
        new_lot = positions[-1]
        assert new_lot.level == 2
        assert new_lot.quantity == 3
        assert new_lot.buy_date == "2026-07-24"
        assert new_lot.lot_id.endswith("_migrate")
        assert executions[0].lot_id == new_lot.lot_id

    def test_buy_honors_explicit_level(self):
        positions = []
        trades = parse_trades(
            '[{"ticker":"A","action":"buy","quantity":3,"price":90,"level":4}]',
            default_date="2026-07-24", fee_rate=0.0,
        )

        apply_trades(positions, trades, {}, "domestic")

        assert positions[0].level == 4

    def test_sell_without_lots_raises(self):
        trades = parse_trades(
            '[{"ticker":"A","action":"sell","quantity":1,"price":10}]',
            default_date="2026-07-24", fee_rate=0.0,
        )
        with pytest.raises(ValueError, match="차감할 lot"):
            apply_trades([], trades, {}, "domestic")

    def test_oversell_warns_and_drains_all(self):
        positions = [_lot("A", 5, 1)]
        trades = parse_trades(
            '[{"ticker":"A","action":"sell","quantity":8,"price":120}]',
            default_date="2026-07-24", fee_rate=0.0,
        )
        warnings = []

        apply_trades(positions, trades, {}, "domestic", warn=warnings.append)

        assert positions == []
        assert warnings and "미차감" in warnings[0]

    def test_crypto_residual_is_rounded_to_8_decimals(self):
        positions = [_lot("KRW-ETH", 1.54440048, 1)]
        trades = parse_trades(
            '[{"ticker":"KRW-ETH","action":"sell","quantity":0.77220024,"price":200}]',
            default_date="2026-07-24", fee_rate=0.0,
        )

        apply_trades(positions, trades, {}, "crypto")

        # 부동소수 잔차(0.7722002399999999 등)가 남지 않아야 한다
        assert positions[0].quantity == 0.77220024


class TestFixSnapshotNetDeposit:
    def test_subtracts_cash_impact_from_first_snapshot_after_trade(self):
        snapshots = [
            {"date": "2026-07-23", "net_deposit": 0.0},
            {"date": "2026-07-25", "net_deposit": 2127804.85},
            {"date": "2026-07-26", "net_deposit": 0.0},
        ]

        fixed = fix_snapshot_net_deposit(snapshots, "2026-07-24", 8625544.07)

        assert fixed["date"] == "2026-07-25"
        assert fixed["net_deposit"] == pytest.approx(-6497739.22)
        assert snapshots[0]["net_deposit"] == 0.0
        assert snapshots[2]["net_deposit"] == 0.0

    def test_returns_none_when_no_snapshot_after_trade(self):
        snapshots = [{"date": "2026-07-23", "net_deposit": 0.0}]
        assert fix_snapshot_net_deposit(snapshots, "2026-07-24", 100.0) is None

    def test_snapshot_on_trade_date_is_corrected(self):
        snapshots = [{"date": "2026-07-24", "net_deposit": 500.0}]
        fixed = fix_snapshot_net_deposit(snapshots, "2026-07-24", 200.0)
        assert fixed["net_deposit"] == 300.0


def _seed_data_root(tmp_path):
    root = tmp_path / "data" / "crypto"
    root.mkdir(parents=True)
    (root / "positions.json").write_text(json.dumps([{
        "lot_id": "lot_20260717_000000_KRW-ETH_001_migrate",
        "ticker": "KRW-ETH", "buy_price": 3847313.0, "quantity": 1.54440048,
        "buy_date": "2026-07-17", "level": 1, "trailing_highest_price": None,
    }]), encoding="utf-8")
    (root / "history.json").write_text(json.dumps([{
        "id": "tx_20260720", "date": "2026-07-20 19:31:11",
        "portfolio_value": 17500524.94, "cash_balance": 81737.75,
        "net_deposit": 0.0, "total_trade_amount": 0.0, "reason": "seed",
        "executions": [],
    }]), encoding="utf-8")
    (root / "status.json").write_text(json.dumps({
        "last_run_date": "2026-07-27",
        "reason": "모니터링 - 신호 없음",
        "enabled_tickers": ["KRW-ETH"],
        "realized_pnl_by_ticker": {},
        "portfolio": {
            "cash_balance": 2209542.6,
            "holdings": [
                {"ticker": "KRW-ETH", "qty": 0.77220024, "price": 2840000.0},
            ],
        },
    }), encoding="utf-8")
    (root / "snapshots.json").write_text(json.dumps([
        {"date": "2026-07-23", "portfolio_value": 1.0, "cash_balance": 81737.75,
         "stock_value": 0.0, "net_deposit": 0.0, "exchange_rate": None},
        {"date": "2026-07-25", "portfolio_value": 1.0, "cash_balance": 2199110.75,
         "stock_value": 0.0, "net_deposit": 2117373.0, "exchange_rate": None},
    ]), encoding="utf-8")
    return str(tmp_path / "data")


TRADES = ('[{"ticker":"KRW-ETH","action":"sell","quantity":0.77220024,'
          '"price":2742000,"date":"2026-07-24"}]')


class TestMainEndToEnd:
    def test_dry_run_leaves_files_untouched(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        before = (tmp_path / "data" / "crypto" / "positions.json").read_text(encoding="utf-8")

        rc = main([
            "--market", "crypto", "--data-root", data_root,
            "--fee-rate", "0.05", "--dry-run", "--trades-json", TRADES,
        ])

        assert rc == 0
        after = (tmp_path / "data" / "crypto" / "positions.json").read_text(encoding="utf-8")
        assert before == after

    def test_writes_positions_history_and_snapshot_fix(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        crypto = tmp_path / "data" / "crypto"

        rc = main([
            "--market", "crypto", "--data-root", data_root,
            "--fee-rate", "0.05", "--cash", "2209542.60",
            "--trades-json", TRADES,
        ])
        assert rc == 0

        positions = json.loads((crypto / "positions.json").read_text(encoding="utf-8"))
        assert len(positions) == 1
        assert positions[0]["quantity"] == 0.77220024

        history = json.loads((crypto / "history.json").read_text(encoding="utf-8"))
        assert len(history) == 2
        record = history[-1]
        assert record["date"] == "2026-07-24"
        assert record["cash_balance"] == 2209542.60
        exe = record["executions"][0]
        assert exe["action"] == "SELL"
        assert exe["level"] == 1
        assert exe["realized_pnl"] < 0

        last_sell = json.loads((crypto / "last_sell_prices.json").read_text(encoding="utf-8"))
        assert last_sell["KRW-ETH"] == 2742000

        snapshots = json.loads((crypto / "snapshots.json").read_text(encoding="utf-8"))
        # 2026-07-25 의 순입금에서 매도 대금(수수료 차감 후)이 빠져야 한다
        gross = 0.77220024 * 2742000
        cash_impact = gross - gross * 0.0005
        assert snapshots[1]["net_deposit"] == pytest.approx(2117373.0 - cash_impact, abs=0.01)

        decisions = json.loads((crypto / "decisions.json").read_text(encoding="utf-8"))
        assert decisions[-1]["date"] == "2026-07-24 00:00:00"
        assert decisions[-1]["reason"].startswith("KRW-ETH:SELL(")

        # status.json 은 보정된 포지션으로 재생성되어 불일치 경보가 사라져야 한다
        status = json.loads((crypto / "status.json").read_text(encoding="utf-8"))
        assert status["risk_summary"]["sync_error"] is False
        assert status["realized_pnl_by_ticker"]["KRW-ETH"] == exe["realized_pnl"]
        assert status["positions"]["KRW-ETH"]["total_qty"] == 0.77220024

    def test_appends_snapshot_when_none_after_trade_date(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        crypto = tmp_path / "data" / "crypto"
        snaps = json.loads((crypto / "snapshots.json").read_text(encoding="utf-8"))
        (crypto / "snapshots.json").write_text(json.dumps(snaps[:1]), encoding="utf-8")

        rc = main([
            "--market", "crypto", "--data-root", data_root,
            "--fee-rate", "0.05", "--trades-json", TRADES,
        ])
        assert rc == 0

        snapshots = json.loads((crypto / "snapshots.json").read_text(encoding="utf-8"))
        assert [s["date"] for s in snapshots] == ["2026-07-23", "2026-07-24"]

    def test_skip_snapshot_fix_leaves_snapshots_alone(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        crypto = tmp_path / "data" / "crypto"

        rc = main([
            "--market", "crypto", "--data-root", data_root,
            "--fee-rate", "0.05", "--skip-snapshot-fix", "--trades-json", TRADES,
        ])
        assert rc == 0

        snapshots = json.loads((crypto / "snapshots.json").read_text(encoding="utf-8"))
        assert snapshots[1]["net_deposit"] == 2117373.0

    def test_invalid_json_returns_error_code(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        assert main([
            "--market", "crypto", "--data-root", data_root,
            "--trades-json", "not-json",
        ]) == 1

    def test_missing_trades_file_returns_error_code(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        missing = os.path.join(str(tmp_path), "nope.json")
        assert main([
            "--market", "crypto", "--data-root", data_root,
            "--trades-file", missing,
        ]) == 1

    def test_trades_file_input(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        path = tmp_path / "trades.json"
        path.write_text(TRADES, encoding="utf-8")

        rc = main([
            "--market", "crypto", "--data-root", data_root,
            "--fee-rate", "0.05", "--dry-run", "--trades-file", str(path),
        ])
        assert rc == 0

    def test_sell_without_position_returns_error_code(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        assert main([
            "--market", "crypto", "--data-root", data_root,
            "--trades-json",
            '[{"ticker":"KRW-XRP","action":"sell","quantity":1,"price":10}]',
        ]) == 1


class TestAccrueRealizedPnl:
    def _sell(self, ticker, pnl):
        exe = TradeExecution(
            ticker=ticker, action=OrderAction.SELL, quantity=1, price=10, fee=0,
            date="2026-07-24 00:00:00", status=ExecutionStatus.FILLED,
        )
        exe.realized_pnl = pnl
        return exe

    def test_adds_to_existing_total(self):
        status = {"realized_pnl_by_ticker": {"KRW-ETH": -100.0}}

        out = accrue_realized_pnl(status, [self._sell("KRW-ETH", -50.0)])

        assert out == {"KRW-ETH": -150.0}
        # 원본은 건드리지 않는다
        assert status["realized_pnl_by_ticker"] == {"KRW-ETH": -100.0}

    def test_seeds_missing_ticker(self):
        out = accrue_realized_pnl({}, [self._sell("KRW-BTC", -1349223.66)])
        assert out == {"KRW-BTC": -1349223.66}

    def test_zero_pnl_execution_is_ignored(self):
        assert accrue_realized_pnl({}, [self._sell("A", 0.0)]) == {}


class TestComposeReason:
    def _sell(self, ticker, reason):
        return TradeExecution(
            ticker=ticker, action=OrderAction.SELL, quantity=1, price=10, fee=0,
            date="2026-07-24 00:00:00", status=ExecutionStatus.FILLED, reason=reason,
        )

    def test_engine_format_per_execution(self):
        out = compose_reason([self._sell("KRW-ETH", "MTS 수동 매도")])
        assert out == "KRW-ETH:SELL(MTS 수동 매도)"

    def test_multiple_executions_are_newline_joined(self):
        out = compose_reason([self._sell("KRW-ETH", "r1"), self._sell("KRW-BTC", "r2")])
        assert out == "KRW-ETH:SELL(r1),\nKRW-BTC:SELL(r2)"


class TestRebuildStatus:
    def _repo(self, tmp_path, status):
        root = tmp_path / "crypto"
        root.mkdir(parents=True)
        (root / "status.json").write_text(json.dumps(status), encoding="utf-8")
        return JsonRepository(str(root))

    def _status(self):
        return {
            "last_run_date": "2026-07-27",
            "reason": "모니터링 - 신호 없음",
            "enabled_tickers": ["KRW-ETH"],
            "portfolio": {
                "cash_balance": 2209542.6,
                "holdings": [
                    {"ticker": "KRW-ETH", "qty": 0.77220024, "price": 2840000.0},
                ],
            },
        }

    def test_uses_account_state_with_corrected_positions(self, tmp_path):
        repo = self._repo(tmp_path, self._status())
        positions = [_lot("KRW-ETH", 0.77220024, 1, buy_price=3847313.0)]

        out = rebuild_status(repo, positions, {"KRW-ETH": -854581.65}, "crypto")

        assert out["risk_summary"]["sync_error"] is False
        assert not [a for a in out["risk_summary"]["alerts"] if "잔고 불일치" in a]
        assert out["realized_pnl_by_ticker"] == {"KRW-ETH": -854581.65}
        assert out["positions"]["KRW-ETH"]["total_qty"] == 0.77220024

    def test_keeps_previous_run_date_instead_of_now(self, tmp_path):
        repo = self._repo(tmp_path, self._status())

        out = rebuild_status(repo, [_lot("KRW-ETH", 0.77220024, 1)], {}, "crypto")

        assert out["last_run_date"] == "2026-07-27"
        assert out["last_updated"] == "2026-07-27"

    def test_stale_positions_still_flagged_as_mismatch(self, tmp_path):
        """계좌와 어긋난 포지션을 넘기면 여전히 경보가 나와야 한다 (무조건 통과 방지)."""
        repo = self._repo(tmp_path, self._status())

        out = rebuild_status(repo, [_lot("KRW-ETH", 1.54440048, 1)], {}, "crypto")

        assert out["risk_summary"]["sync_error"] is True

    def test_returns_none_without_prior_account_state(self, tmp_path):
        repo = self._repo(tmp_path, {"last_run_date": "2026-07-27"})
        assert rebuild_status(repo, [], {}, "crypto") is None


class TestSkipStatusRebuild:
    def test_skip_flag_keeps_previous_status_body(self, tmp_path):
        data_root = _seed_data_root(tmp_path)
        crypto = tmp_path / "data" / "crypto"

        rc = main([
            "--market", "crypto", "--data-root", data_root,
            "--fee-rate", "0.05", "--skip-status-rebuild", "--trades-json", TRADES,
        ])
        assert rc == 0

        status = json.loads((crypto / "status.json").read_text(encoding="utf-8"))
        # 대시보드 블록은 그대로, 누적 실현손익만 갱신된다
        assert "risk_summary" not in status
        assert status["realized_pnl_by_ticker"]["KRW-ETH"] < 0
