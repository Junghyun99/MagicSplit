# tests/test_position_reconciler.py
from src.core.logic.position_reconciler import QuantityMismatch, detect_mismatches
from src.core.models import PositionLot, Portfolio, StockRule


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
