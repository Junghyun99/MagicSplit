# tests/test_position_reconciler.py
from src.core.logic.position_reconciler import (
    QuantityMismatch, detect_mismatches, drain_lots_by_qty,
)
from src.core.models import (
    ExecutionStatus, OrderAction, PositionLot, Portfolio, StockRule, TradeExecution,
)


def _rule(ticker: str, enabled: bool = True) -> StockRule:
    return StockRule(ticker, -5.0, 10.0, 500, 10, enabled=enabled)


def _lot(ticker: str, qty: int, level: int, lot_id: str = "lot_x") -> PositionLot:
    return PositionLot(lot_id, ticker, 100.0, qty, "2026-04-01", level=level)


def _portfolio(holdings: dict) -> Portfolio:
    return Portfolio(total_cash=0, holdings=holdings, current_prices={})


class TestDetectMismatches:
    def test_match_returns_empty(self):
        positions = [_lot("AAPL", 5, 1, "a1"), _lot("AAPL", 3, 2, "a2")]
        portfolio = _portfolio({"AAPL": 8})
        assert detect_mismatches(positions, portfolio, [_rule("AAPL")]) == []

    def test_broker_less_than_positions(self):
        positions = [_lot("AAPL", 5, 1, "a1"), _lot("AAPL", 5, 2, "a2")]
        portfolio = _portfolio({"AAPL": 7})

        out = detect_mismatches(positions, portfolio, [_rule("AAPL")])

        assert len(out) == 1
        m = out[0]
        assert m.ticker == "AAPL"
        assert m.broker_qty == 7
        assert m.positions_qty == 10
        assert m.lot_count == 2
        assert m.levels == [1, 2]
        assert m.diff == -3

    def test_broker_greater_than_positions(self):
        """브로커에는 있지만 positions 에는 없는 경우 — 이중 매수 위험."""
        portfolio = _portfolio({"AAPL": 5})
        out = detect_mismatches([], portfolio, [_rule("AAPL")])

        assert len(out) == 1
        assert out[0].broker_qty == 5
        assert out[0].positions_qty == 0
        assert out[0].lot_count == 0
        assert out[0].levels == []
        assert out[0].diff == 5

    def test_positions_exist_broker_zero(self):
        """브로커에서 전량 사라진 경우 (외부 전량 매도 등)."""
        positions = [_lot("AAPL", 5, 1)]
        portfolio = _portfolio({})

        out = detect_mismatches(positions, portfolio, [_rule("AAPL")])

        assert len(out) == 1
        assert out[0].broker_qty == 0
        assert out[0].positions_qty == 5

    def test_ignores_tickers_not_in_rules_and_not_in_positions(self):
        """봇 관리 대상이 아닌 티커(브로커에만 있음)는 무시."""
        portfolio = _portfolio({"TSLA": 10, "AAPL": 5})
        positions = [_lot("AAPL", 5, 1)]

        out = detect_mismatches(positions, portfolio, [_rule("AAPL")])
        assert out == []

    def test_disabled_rule_ticker_still_checked_when_lot_exists(self):
        """disabled rule 이라도 lot 이 남아있으면 검사 대상."""
        positions = [_lot("MSFT", 3, 1)]
        portfolio = _portfolio({"MSFT": 5})
        rules = [_rule("MSFT", enabled=False)]

        out = detect_mismatches(positions, portfolio, rules)
        assert len(out) == 1
        assert out[0].ticker == "MSFT"

    def test_multiple_tickers_sorted(self):
        positions = [_lot("AAPL", 5, 1), _lot("MSFT", 3, 1)]
        portfolio = _portfolio({"AAPL": 4, "MSFT": 10})

        out = detect_mismatches(positions, portfolio, [_rule("AAPL"), _rule("MSFT")])
        assert [m.ticker for m in out] == ["AAPL", "MSFT"]

    def test_quantity_mismatch_diff_property(self):
        m = QuantityMismatch("X", broker_qty=3, positions_qty=5, lot_count=1, levels=[1])
        assert m.diff == -2

    def test_fractional_match_within_tolerance(self):
        """코인 소수 수량이 허용오차 내로 일치하면 불일치 아님 (int 절단 없음)."""
        crypto_rule = StockRule("KRW-BTC", -5.0, 10.0, 100000, market_type="crypto")
        positions = [_lot("KRW-BTC", 0.00033333, 1, "b1"),
                     _lot("KRW-BTC", 0.00033333, 2, "b2")]
        portfolio = _portfolio({"KRW-BTC": 0.00066666})
        assert detect_mismatches(positions, portfolio, [crypto_rule]) == []

    def test_fractional_mismatch_detected(self):
        crypto_rule = StockRule("KRW-BTC", -5.0, 10.0, 100000, market_type="crypto")
        positions = [_lot("KRW-BTC", 0.00066666, 1, "b1")]
        portfolio = _portfolio({"KRW-BTC": 0.001})
        out = detect_mismatches(positions, portfolio, [crypto_rule])
        assert len(out) == 1
        assert out[0].broker_qty == 0.001
        assert out[0].positions_qty == 0.00066666


class TestDrainLotsByQty:
    """엔진 통합청산과 수동매매 사후 반영이 공유하는 lot 차감 로직."""

    def _exe(self, ticker: str, qty: float, price: float, fee: float = 0.0):
        return TradeExecution(
            ticker=ticker, action=OrderAction.SELL, quantity=qty, price=price,
            fee=fee, date="2026-07-24 00:00:00", status=ExecutionStatus.FILLED,
        )

    def test_drains_highest_level_first(self):
        positions = [_lot("AAPL", 5, 1, "a1"), _lot("AAPL", 5, 2, "a2")]
        exe = self._exe("AAPL", 5, 120.0)

        out, consumed = drain_lots_by_qty(positions, "AAPL", 5, exe)

        assert consumed == 5
        assert [l.lot_id for l in out] == ["a1"]
        assert exe.liquidation_lots[0]["level"] == 2

    def test_partial_drain_keeps_lot_with_remainder(self):
        positions = [_lot("AAPL", 5, 1, "a1")]
        exe = self._exe("AAPL", 2, 120.0)

        out, consumed = drain_lots_by_qty(positions, "AAPL", 2, exe)

        assert consumed == 2
        assert len(out) == 1
        assert out[0].quantity == 3
        assert out[0].buy_price == 100.0  # 단가는 그대로

    def test_realized_pnl_nets_out_fee(self):
        positions = [_lot("AAPL", 5, 1, "a1")]
        exe = self._exe("AAPL", 5, 120.0, fee=10.0)

        drain_lots_by_qty(positions, "AAPL", 5, exe)

        assert exe.buy_price == 100.0
        assert exe.realized_pnl == (120.0 - 100.0) * 5 - 10.0

    def test_fee_is_split_across_consumed_lots(self):
        positions = [_lot("AAPL", 5, 1, "a1"), _lot("AAPL", 5, 2, "a2")]
        exe = self._exe("AAPL", 10, 120.0, fee=10.0)

        drain_lots_by_qty(positions, "AAPL", 10, exe)

        per_lot = [b["realized_pnl"] for b in exe.liquidation_lots]
        assert per_lot == [95.0, 95.0]
        assert sum(per_lot) == exe.realized_pnl

    def test_shortfall_when_qty_exceeds_holdings(self):
        positions = [_lot("AAPL", 5, 1, "a1")]
        exe = self._exe("AAPL", 8, 120.0)

        out, consumed = drain_lots_by_qty(positions, "AAPL", 8, exe)

        assert consumed == 5
        assert out == []

    def test_no_lots_leaves_execution_untouched(self):
        exe = self._exe("AAPL", 5, 120.0)

        out, consumed = drain_lots_by_qty([], "AAPL", 5, exe)

        assert (out, consumed) == ([], 0)
        assert exe.realized_pnl == 0.0
        assert exe.liquidation_lots is None

    def test_other_tickers_are_untouched(self):
        positions = [_lot("AAPL", 5, 1, "a1"), _lot("MSFT", 5, 1, "m1")]
        exe = self._exe("AAPL", 5, 120.0)

        out, _ = drain_lots_by_qty(positions, "AAPL", 5, exe)

        assert [l.lot_id for l in out] == ["m1"]
