# tests/test_purge_rejected_history.py
import json

import pytest

from scripts.purge_rejected_history import cash_impact, check_invariant, process, purge


def _exe(action="SELL", qty=0, price=100.0, status="REJECTED", ticker="AAPL", fee=0.0):
    return {
        "ticker": ticker, "action": action, "quantity": qty, "price": price,
        "fee": fee, "date": "2026-07-30 10:00:00", "status": status,
    }


def _rec(rec_id, execs, cash=1000.0, net_deposit=0.0, trade_amount=0.0):
    return {
        "id": rec_id, "date": f"2026-07-{rec_id[-2:]} 10:00:00",
        "portfolio_value": cash, "cash_balance": cash,
        "net_deposit": net_deposit, "total_trade_amount": trade_amount,
        "reason": "-", "executions": execs,
    }


class TestCashImpact:
    def test_buy_is_negative_sell_is_positive(self):
        assert cash_impact([_exe("BUY", 2, 100.0, "FILLED")]) == -200.0
        assert cash_impact([_exe("SELL", 2, 100.0, "FILLED")]) == 200.0

    def test_fee_is_deducted_both_ways(self):
        assert cash_impact([_exe("SELL", 1, 100.0, "FILLED", fee=5.0)] ) == 95.0
        assert cash_impact([_exe("BUY", 1, 100.0, "FILLED", fee=5.0)]) == -105.0

    def test_zero_quantity_contributes_nothing(self):
        assert cash_impact([_exe("SELL", 0, 100.0, "REJECTED")]) == 0.0


class TestPurge:
    def test_removes_rejected_execution_but_keeps_record(self):
        records = [_rec("tx_01", [
            _exe("SELL", 0, 100.0, "REJECTED"),
            _exe("BUY", 1, 50.0, "FILLED"),
        ], trade_amount=50.0)]

        cleaned, n_exec, n_rec, _ = purge(records)

        assert n_exec == 1 and n_rec == 0
        assert len(cleaned) == 1
        assert [e["status"] for e in cleaned[0]["executions"]] == ["FILLED"]

    def test_drops_record_when_every_execution_rejected(self):
        records = [
            _rec("tx_01", [_exe("BUY", 1, 50.0, "FILLED")]),
            _rec("tx_02", [_exe("SELL", 27, 45015.0, "REJECTED")]),
        ]

        cleaned, n_exec, n_rec, _ = purge(records)

        assert n_exec == 1 and n_rec == 1
        assert [r["id"] for r in cleaned] == ["tx_01"]

    def test_recomputes_trade_amount(self):
        records = [_rec("tx_01", [
            _exe("SELL", 27, 45015.0, "REJECTED"),
            _exe("BUY", 1, 50.0, "FILLED"),
        ], trade_amount=27 * 45015.0 + 50.0)]

        cleaned, _, _, _ = purge(records)

        assert cleaned[0]["total_trade_amount"] == 50.0

    def test_corrects_net_deposit_by_removed_cash_impact(self):
        """거절 매도가 현금 유입으로 잡혀 순입금이 그만큼 깎여 있었다."""
        phantom = 27 * 45015.0
        records = [_rec("tx_01", [
            _exe("SELL", 27, 45015.0, "REJECTED"),
            _exe("BUY", 1, 50.0, "FILLED"),
        ], net_deposit=-phantom)]

        cleaned, _, _, removed = purge(records)

        assert removed == pytest.approx(phantom)
        assert cleaned[0]["net_deposit"] == pytest.approx(0.0)

    def test_zero_quantity_rejection_leaves_net_deposit_alone(self):
        records = [_rec("tx_01", [
            _exe("SELL", 0, 100.0, "REJECTED"),
            _exe("BUY", 1, 50.0, "FILLED"),
        ], net_deposit=123.45)]

        cleaned, _, _, removed = purge(records)

        assert removed == 0.0
        assert cleaned[0]["net_deposit"] == 123.45

    def test_dropped_record_carries_net_deposit_forward(self):
        """삭제되는 레코드의 순입금(실제 입출금)을 잃지 않아야 한다."""
        records = [
            _rec("tx_01", [_exe("BUY", 1, 50.0, "FILLED")], net_deposit=10.0),
            _rec("tx_02", [_exe("SELL", 0, 100.0, "REJECTED")], net_deposit=500.0),
            _rec("tx_03", [_exe("BUY", 1, 50.0, "FILLED")], net_deposit=20.0),
        ]

        cleaned, _, n_rec, _ = purge(records)

        assert n_rec == 1
        assert [r["id"] for r in cleaned] == ["tx_01", "tx_03"]
        assert cleaned[-1]["net_deposit"] == 520.0

    def test_carry_falls_back_to_previous_record_at_tail(self):
        records = [
            _rec("tx_01", [_exe("BUY", 1, 50.0, "FILLED")], net_deposit=10.0),
            _rec("tx_02", [_exe("SELL", 0, 100.0, "REJECTED")], net_deposit=500.0),
        ]

        cleaned, _, _, _ = purge(records)

        assert [r["id"] for r in cleaned] == ["tx_01"]
        assert cleaned[0]["net_deposit"] == 510.0

    def test_does_not_mutate_input(self):
        """dry-run이 원본을 건드리면 검증이 무의미해진다."""
        records = [_rec("tx_01", [_exe("SELL", 0, 100.0, "REJECTED"),
                                  _exe("BUY", 1, 50.0, "FILLED")])]
        snapshot = json.dumps(records, sort_keys=True)

        purge(records)

        assert json.dumps(records, sort_keys=True) == snapshot

    def test_untouched_records_are_left_alone(self):
        clean = _rec("tx_01", [_exe("BUY", 1, 50.0, "FILLED")], net_deposit=7.0,
                     trade_amount=50.0)
        records = [clean, _rec("tx_02", [_exe("SELL", 0, 100.0, "REJECTED"),
                                         _exe("BUY", 1, 50.0, "FILLED")])]

        cleaned, _, _, _ = purge(records)

        assert cleaned[0] == clean

    def test_no_rejections_is_a_noop(self):
        records = [_rec("tx_01", [_exe("BUY", 1, 50.0, "FILLED")], net_deposit=7.0)]

        cleaned, n_exec, n_rec, removed = purge(records)

        assert (n_exec, n_rec, removed) == (0, 0, 0.0)
        assert cleaned == records

    def test_records_without_cash_balance_survive(self):
        """수동매매 이관분은 cash_balance/net_deposit이 없다. 건드리면 안 된다."""
        legacy = {"id": "tx_legacy", "date": "2026-07-14 12:34:04",
                  "executions": [_exe("BUY", 1, 50.0, "FILLED")]}
        records = [legacy, _rec("tx_02", [_exe("SELL", 0, 100.0, "REJECTED"),
                                          _exe("BUY", 1, 50.0, "FILLED")])]

        cleaned, _, _, _ = purge(records)

        assert cleaned[0] == legacy


class TestInvariant:
    def test_total_net_deposit_rises_by_removed_impact(self):
        phantom = 27 * 45015.0
        before = [
            _rec("tx_01", [_exe("BUY", 1, 50.0, "FILLED")], net_deposit=10.0),
            _rec("tx_02", [_exe("SELL", 27, 45015.0, "REJECTED"),
                           _exe("BUY", 1, 50.0, "FILLED")], net_deposit=-phantom),
        ]

        after, _, _, removed = purge(before)
        got, expected, ok = check_invariant(before, after, removed)

        assert ok
        assert got == pytest.approx(expected)

    def test_detects_broken_correction(self):
        before = [_rec("tx_01", [_exe("SELL", 27, 45015.0, "REJECTED")], net_deposit=0.0)]
        tampered = [_rec("tx_01", [], net_deposit=999.0)]

        _, _, ok = check_invariant(before, tampered, 27 * 45015.0)

        assert not ok


class TestProcess:
    def test_dry_run_does_not_write(self, tmp_path, capsys):
        path = tmp_path / "history.json"
        records = [_rec("tx_01", [_exe("SELL", 0, 100.0, "REJECTED"),
                                  _exe("BUY", 1, 50.0, "FILLED")])]
        path.write_text(json.dumps(records), encoding="utf-8")

        assert process(str(tmp_path), dry_run=True) is False
        assert json.loads(path.read_text(encoding="utf-8")) == records

    def test_applies_and_writes(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text(json.dumps([
            _rec("tx_01", [_exe("SELL", 0, 100.0, "REJECTED"),
                           _exe("BUY", 1, 50.0, "FILLED")]),
        ]), encoding="utf-8")

        assert process(str(tmp_path), dry_run=False) is True
        execs = json.loads(path.read_text(encoding="utf-8"))[0]["executions"]
        assert [e["status"] for e in execs] == ["FILLED"]

    def test_missing_file_is_skipped(self, tmp_path):
        assert process(str(tmp_path / "nope"), dry_run=True) is False

    def test_clean_file_reports_no_change(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text(json.dumps([_rec("tx_01", [_exe("BUY", 1, 50.0, "FILLED")])]),
                        encoding="utf-8")

        assert process(str(tmp_path), dry_run=False) is False

    def test_is_idempotent(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text(json.dumps([
            _rec("tx_01", [_exe("SELL", 27, 45015.0, "REJECTED"),
                           _exe("BUY", 1, 50.0, "FILLED")], net_deposit=-27 * 45015.0),
        ]), encoding="utf-8")

        process(str(tmp_path), dry_run=False)
        first = path.read_text(encoding="utf-8")
        process(str(tmp_path), dry_run=False)

        assert path.read_text(encoding="utf-8") == first
