# src/core/logic/status_builder.py
from typing import List, Dict, Optional
from datetime import datetime
from src.core.models import Portfolio, PositionLot, TradeExecution
from src.utils.ticker_reader import get_alias

def build_dashboard_status(
    portfolio: Portfolio,
    positions: List[PositionLot],
    reason: str,
    old_realized_pnl_by_ticker: Dict[str, float],
    recent_executions: List[TradeExecution],
    sim_date: Optional[str] = None
) -> dict:
    """대시보드 렌더링에 필요한 상태 데이터 구조(JSON)를 조립한다."""
    last_updated = sim_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    last_run_date = sim_date or datetime.now().strftime("%Y-%m-%d")

    # 1. Update realized PnL incrementally
    realized_by_ticker = dict(old_realized_pnl_by_ticker)
    for exe in recent_executions:
        if getattr(exe, 'realized_pnl', 0.0) != 0.0:
            realized_by_ticker[exe.ticker] = realized_by_ticker.get(exe.ticker, 0.0) + exe.realized_pnl

    # 2. Build ticker summary from active positions
    ticker_summary = {}
    for lot in positions:
        if lot.ticker not in ticker_summary:
            ticker_summary[lot.ticker] = {
                "total_qty": 0,
                "lot_count": 0,
                "total_invested": 0.0,
                "current_value": 0.0,
                "lots": [],
            }
        ts = ticker_summary[lot.ticker]
        ts["total_qty"] += lot.quantity
        ts["lot_count"] += 1
        
        current_price = portfolio.current_prices.get(lot.ticker, 0.0)
        invested = lot.buy_price * lot.quantity
        
        ts["total_invested"] += invested
        ts["current_value"] += current_price * lot.quantity
        
        pct = ((current_price - lot.buy_price) / lot.buy_price * 100) if lot.buy_price > 0 else 0
        ts["lots"].append({
            "lot_id": lot.lot_id,
            "buy_price": lot.buy_price,
            "quantity": lot.quantity,
            "buy_date": lot.buy_date,
            "level": lot.level,
            "current_price": current_price,
            "pct_change": round(pct, 2),
        })

    # 3. Add aggregated PnL fields per ticker
    for ticker, ts in ticker_summary.items():
        total_invested = ts["total_invested"]
        current_value = ts["current_value"]
        unrealized_pnl = current_value - total_invested
        realized_pnl = realized_by_ticker.get(ticker, 0.0)

        ts["alias"] = get_alias(ticker) or ticker
        ts["avg_buy_price"] = round(total_invested / ts["total_qty"], 4) if ts["total_qty"] > 0 else 0.0
        ts["total_invested"] = round(total_invested, 2)
        ts["current_value"] = round(current_value, 2)
        ts["unrealized_pnl"] = round(unrealized_pnl, 2)
        ts["unrealized_pnl_pct"] = round(
            (unrealized_pnl / total_invested * 100) if total_invested > 0 else 0.0, 2
        )
        ts["realized_pnl"] = round(realized_pnl, 2)
        ts["total_pnl"] = round(realized_pnl + unrealized_pnl, 2)

    # 4. Construct final status dictionary
    status = {
        "last_updated": last_updated,
        "last_run_date": last_run_date,
        "reason": reason,
        "portfolio": {
            "total_value": portfolio.total_value,
            "cash_balance": portfolio.total_cash,
            "holdings": [
                {
                    "ticker": t,
                    "alias": get_alias(t) or t,
                    "qty": q,
                    "price": portfolio.current_prices.get(t, 0),
                    "value": q * portfolio.current_prices.get(t, 0),
                }
                for t, q in portfolio.holdings.items() if q > 0
            ],
        },
        "positions": ticker_summary,
        "realized_pnl_by_ticker": realized_by_ticker,
    }

    return status
