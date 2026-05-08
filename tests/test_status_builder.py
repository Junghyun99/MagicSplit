# tests/test_status_builder.py
from src.core.logic.status_builder import build_dashboard_status
from src.core.models import Portfolio, PositionLot


def _empty_portfolio() -> Portfolio:
    return Portfolio(total_cash=0.0, holdings={}, current_prices={})


class TestStatusMarketType:
    def test_default_market_type_is_overseas(self):
        status = build_dashboard_status(
            portfolio=_empty_portfolio(),
            positions=[],
            reason="-",
            old_realized_pnl_by_ticker={},
            recent_executions=[],
            enabled_tickers=[],
            sim_date="2026-04-10",
        )
        assert status["market_type"] == "overseas"

    def test_market_type_propagates_when_domestic(self):
        status = build_dashboard_status(
            portfolio=_empty_portfolio(),
            positions=[],
            reason="-",
            old_realized_pnl_by_ticker={},
            recent_executions=[],
            enabled_tickers=[],
            sim_date="2026-04-10",
            market_type="domestic",
        )
        assert status["market_type"] == "domestic"

    def test_status_keeps_existing_keys(self):
        """기존 필드(portfolio/positions/risk_summary)는 유지된다."""
        status = build_dashboard_status(
            portfolio=Portfolio(
                total_cash=1000.0, holdings={"AAPL": 1},
                current_prices={"AAPL": 150.0},
            ),
            positions=[
                PositionLot("lot_1", "AAPL", 140.0, 1, "2026-04-01", level=1),
            ],
            reason="모니터링 - 신호 없음",
            old_realized_pnl_by_ticker={},
            recent_executions=[],
            enabled_tickers=["AAPL"],
            sim_date="2026-04-10",
            market_type="overseas",
        )
        assert "portfolio" in status
        assert "positions" in status
        assert "risk_summary" in status
        assert status["market_type"] == "overseas"
