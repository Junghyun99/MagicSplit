# src/core/logic/status_builder.py
from typing import List, Dict, Optional
from datetime import datetime
from src.core.models import Portfolio, PositionLot, TradeExecution, StockRule
from src.utils.ticker_reader import get_alias

def build_dashboard_status(
    portfolio: Portfolio,
    positions: List[PositionLot],
    reason: str,
    old_realized_pnl_by_ticker: Dict[str, float],
    recent_executions: List[TradeExecution],
    enabled_tickers: List[str],
    sim_date: Optional[str] = None,
    stock_rules: Optional[List[StockRule]] = None,
    last_trade_dates: Optional[Dict[str, str]] = None,
    market_type: str = "overseas",
) -> dict:
    """대시보드 렌더링에 필요한 상태 데이터 구조(JSON)를 조립한다.

    market_type은 status.json에 기록되어 프런트가 통화(KRW/USD)를 결정할 때 사용된다.
    """
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

    # 3.5 Calculate Risk Summary (Liquidity & Stale info)
    next_level_needs = 0
    max_potential_exposure = 0
    stale_info = []
    
    ticker_max_levels = {}
    for lot in positions:
        ticker_max_levels[lot.ticker] = max(ticker_max_levels.get(lot.ticker, 0), lot.level)
    
    if stock_rules:
        rule_map = {r.ticker: r for r in stock_rules}
        for ticker, max_lv in ticker_max_levels.items():
            rule = rule_map.get(ticker)
            if rule and max_lv < rule.max_lots:
                next_level_needs += rule.buy_amount_at(max_lv + 1)
        
        for rule in stock_rules:
            for lv in range(1, rule.max_lots + 1):
                max_potential_exposure += rule.buy_amount_at(lv)

    # Calculate stale days
    today_dt = datetime.strptime(last_run_date, "%Y-%m-%d")
    for ticker in ticker_max_levels.keys():
        last_trade = (last_trade_dates or {}).get(ticker)
        
        # Fallback to the latest buy_date if no history
        if not last_trade:
            ticker_lots = [l for l in positions if l.ticker == ticker]
            if ticker_lots:
                last_trade = max(l.buy_date for l in ticker_lots).split(" ")[0]
        
        if last_trade:
            try:
                lt_dt = datetime.strptime(last_trade.split(" ")[0], "%Y-%m-%d")
                days_stale = (today_dt - lt_dt).days
                stale_info.append({
                    "ticker": ticker,
                    "alias": get_alias(ticker) or ticker,
                    "last_trade_date": last_trade.split(" ")[0],
                    "days_stale": max(0, days_stale)
                })
            except (ValueError, TypeError):
                pass
    
    stale_info.sort(key=lambda x: x["days_stale"], reverse=True)

    # 3.6 Advanced Risk Metrics & Scoring (Phase 3)
    alerts = []
    sync_error = False
    
    # 1. Sync Check (Local vs Portfolio)
    local_sums = {t: ts["total_qty"] for t, ts in ticker_summary.items()}
    all_tickers = set(local_sums.keys()) | {t for t, q in portfolio.holdings.items() if q > 0}
    for t in all_tickers:
        l_qty = local_sums.get(t, 0)
        p_qty = portfolio.holdings.get(t, 0)
        if l_qty != p_qty:
            sync_error = True
            alias = get_alias(t) or t
            alerts.append(f"⚠️ [{alias}] 잔고 불일치 (봇: {l_qty}, 계좌: {p_qty})")

    # 2. Metrics for Scoring
    total_val = portfolio.total_value
    cash_ratio = (portfolio.total_cash / total_val * 100) if total_val > 0 else 0
    
    max_concentration = 0
    for t, ts in ticker_summary.items():
        weight = (ts["current_value"] / total_val * 100) if total_val > 0 else 0
        if weight > max_concentration:
            max_concentration = weight
            
    high_level_count = sum(1 for lot in positions if lot.level >= 8)
    high_level_ratio = (high_level_count / len(positions)) if positions else 0
    stale_count = sum(1 for s in stale_info if s["days_stale"] >= 30)
    
    # 3. Calculate Risk Score (Base 100)
    score = 100
    if cash_ratio < 25:
        score -= min(30, (25 - cash_ratio) * 1.5)
        if cash_ratio < 10:
            alerts.append(f"⚠️ 현금 비중 부족 ({cash_ratio:.1f}%)")
        
    if max_concentration > 15:
        score -= min(30, (max_concentration - 15) * 2.0)
        if max_concentration > 20:
            alerts.append(f"⚠️ 단일 종목 집중도 과다 ({max_concentration:.1f}%)")
        
    if high_level_ratio > 0:
        score -= min(20, high_level_ratio * 50)
        if high_level_ratio > 0.3:
            alerts.append(f"⚠️ 고차수 포지션 비중 높음 ({high_level_ratio*100:.1f}%)")
            
    if stale_count > 0:
        score -= min(20, stale_count * 4)
        if stale_count >= 3:
            alerts.append(f"⚠️ 장기 정체 종목 존재 ({stale_count}개)")

    # 4. Construct final status dictionary
    status = {
        "last_updated": last_updated,
        "last_run_date": last_run_date,
        "market_type": market_type,
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
        "enabled_tickers": enabled_tickers,
        "risk_summary": {
            "next_level_needs": round(next_level_needs, 2),
            "max_potential_exposure": round(max_potential_exposure, 2),
            "stale_info": stale_info,
            "risk_score": max(0, round(score)),
            "sync_error": sync_error,
            "alerts": alerts,
            "max_ticker_concentration": round(max_concentration, 2),
            "high_level_ratio": round(high_level_ratio, 4)
        }
    }

    return status

