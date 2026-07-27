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


class TestRegimeStatePersistence:
    def test_regime_state_included_in_status(self):
        rs = {"AAPL": {"regime": "uptrend", "adds": 2, "last_add_swing_high": 198.4}}
        status = build_dashboard_status(
            portfolio=_empty_portfolio(),
            positions=[],
            reason="-",
            old_realized_pnl_by_ticker={},
            recent_executions=[],
            enabled_tickers=["AAPL"],
            sim_date="2026-04-10",
            regime_state_by_ticker=rs,
        )
        assert status["regime_state_by_ticker"] == rs

    def test_regime_state_defaults_empty(self):
        status = build_dashboard_status(
            portfolio=_empty_portfolio(),
            positions=[],
            reason="-",
            old_realized_pnl_by_ticker={},
            recent_executions=[],
            enabled_tickers=[],
            sim_date="2026-04-10",
        )
        assert status["regime_state_by_ticker"] == {}


class TestSyncAlertTolerance:
    """봇 lot 합계와 계좌 수량 비교는 부동소수 허용오차를 써야 한다."""

    def _status(self, lot_qtys, broker_qty, market_type="crypto"):
        positions = [
            PositionLot(f"lot_{i}", "KRW-USDT", 1476.0, q, "2026-07-18", level=i + 1)
            for i, q in enumerate(lot_qtys)
        ]
        return build_dashboard_status(
            portfolio=Portfolio(
                total_cash=1000.0, holdings={"KRW-USDT": broker_qty},
                current_prices={"KRW-USDT": 1453.0},
            ),
            positions=positions,
            reason="-",
            old_realized_pnl_by_ticker={},
            recent_executions=[],
            enabled_tickers=["KRW-USDT"],
            sim_date="2026-07-27",
            market_type=market_type,
        )

    def test_float_representation_error_is_not_a_mismatch(self):
        # 6.77506775 + 6.84462696 == 13.619694710000001 (부동소수 합산 오차)
        status = self._status([6.77506775, 6.84462696], 13.61969471)

        risk = status["risk_summary"]
        assert risk["sync_error"] is False
        assert not [a for a in risk["alerts"] if "잔고 불일치" in a]

    def test_real_mismatch_is_still_reported(self):
        status = self._status([6.77506775], 13.61969471)

        risk = status["risk_summary"]
        assert risk["sync_error"] is True
        assert [a for a in risk["alerts"] if "잔고 불일치" in a]

    def test_mismatch_alert_shows_full_precision_quantity(self):
        """지수표기(1.2e-05)나 절단 없이 코인 수량이 그대로 보여야 한다."""
        status = self._status([0.00001234], 0.00002468)

        alert = [a for a in status["risk_summary"]["alerts"] if "잔고 불일치" in a][0]
        assert "0.00001234" in alert
        assert "0.00002468" in alert
        assert "e-" not in alert
