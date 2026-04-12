# src/infra/repo.py
import json
import math
import os
from typing import List, Optional
from dataclasses import asdict
from datetime import datetime

from src.core.models import PositionLot, Portfolio, TradeExecution
from src.core.interfaces import IRepository


class JsonRepository(IRepository):
    """JSON 파일 기반 저장소.

    positions.json — 분할 포지션 목록
    history.json   — 매매 내역
    status.json    — 최신 상태 (대시보드용)
    """

    def __init__(self, root_path: str = "docs/data",
                 max_history_records: int = 100000):
        self.root = root_path
        self.max_history_records = max_history_records
        os.makedirs(self.root, exist_ok=True)

        self.positions_file = os.path.join(self.root, "positions.json")
        self.history_file = os.path.join(self.root, "history.json")
        self.status_file = os.path.join(self.root, "status.json")

    # === Positions ===

    def load_positions(self) -> List[PositionLot]:
        """저장된 분할 포지션 목록을 로드한다."""
        data = self._load_json(self.positions_file, default=[])
        lots = []
        for item in data:
            lots.append(PositionLot(
                lot_id=item["lot_id"],
                ticker=item["ticker"],
                buy_price=item["buy_price"],
                quantity=item["quantity"],
                buy_date=item["buy_date"],
                level=item.get("level", 0),
            ))

        # 레거시 마이그레이션: level=0인 lot에 순차 level 부여
        if any(lot.level == 0 for lot in lots):
            lots = self._migrate_legacy_levels(lots)

        return lots

    @staticmethod
    def _migrate_legacy_levels(lots: List[PositionLot]) -> List[PositionLot]:
        """level=0인 레거시 lot에 buy_date 순으로 순차 level을 부여한다."""
        by_ticker: dict = {}
        for lot in lots:
            by_ticker.setdefault(lot.ticker, []).append(lot)

        result = []
        for ticker, ticker_lots in by_ticker.items():
            has_legacy = any(l.level == 0 for l in ticker_lots)
            if has_legacy:
                sorted_lots = sorted(ticker_lots, key=lambda l: (l.buy_date, l.lot_id))
                for i, lot in enumerate(sorted_lots, start=1):
                    result.append(PositionLot(
                        lot_id=lot.lot_id,
                        ticker=lot.ticker,
                        buy_price=lot.buy_price,
                        quantity=lot.quantity,
                        buy_date=lot.buy_date,
                        level=i,
                    ))
            else:
                result.extend(ticker_lots)
        return result

    def save_positions(self, lots: List[PositionLot]) -> None:
        """분할 포지션 목록을 저장한다."""
        data = [asdict(lot) for lot in lots]
        self._save_json(self.positions_file, data)

    # === Trade History ===

    def save_trade_history(self, executions: List[TradeExecution],
                           portfolio: Portfolio, reason: str,
                           sim_date: Optional[str] = None) -> None:
        """매매 내역 저장 (Append 방식)"""
        if not executions:
            return

        trade_amt = sum(e.price * e.quantity for e in executions)

        if sim_date:
            date_str = sim_date
            tx_id = f"tx_{sim_date.replace('-', '')}"
        else:
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tx_id = f"tx_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        record = {
            "id": tx_id,
            "date": date_str,
            "portfolio_value": portfolio.total_value,
            "total_trade_amount": trade_amt,
            "reason": reason,
            "executions": [asdict(e) for e in executions],
        }

        data = self._load_json(self.history_file, default=[])
        data.append(record)

        if self.max_history_records > 0:
            data = data[-self.max_history_records:]

        self._save_json(self.history_file, data)

    # === Status ===

    def update_status(self, portfolio: Portfolio,
                      positions: List[PositionLot],
                      reason: str,
                      sim_date: Optional[str] = None) -> None:
        """최신 상태를 저장한다 (대시보드용)."""
        last_updated = sim_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 종목별 포지션 요약
        ticker_summary = {}
        for lot in positions:
            if lot.ticker not in ticker_summary:
                ticker_summary[lot.ticker] = {
                    "total_qty": 0,
                    "lot_count": 0,
                    "lots": [],
                }
            ts = ticker_summary[lot.ticker]
            ts["total_qty"] += lot.quantity
            ts["lot_count"] += 1
            current_price = portfolio.current_prices.get(lot.ticker, 0)
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

        status = {
            "last_updated": last_updated,
            "last_run_date": (sim_date or datetime.now().strftime("%Y-%m-%d")),
            "reason": reason,
            "portfolio": {
                "total_value": portfolio.total_value,
                "cash_balance": portfolio.total_cash,
                "holdings": [
                    {
                        "ticker": t,
                        "qty": q,
                        "price": portfolio.current_prices.get(t, 0),
                        "value": q * portfolio.current_prices.get(t, 0),
                    }
                    for t, q in portfolio.holdings.items() if q > 0
                ],
            },
            "positions": ticker_summary,
        }

        self._save_json(self.status_file, status)

    def get_last_run_date(self) -> Optional[str]:
        """마지막 실행 날짜를 반환한다."""
        data = self._load_json(self.status_file, default={})
        return data.get("last_run_date")

    # === Internal helpers ===

    def _load_json(self, path: str, default=None):
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            return default

    @staticmethod
    def _sanitize_for_json(obj):
        """NaN/Infinity 값을 None으로 변환하여 유효한 JSON을 보장한다."""
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: JsonRepository._sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [JsonRepository._sanitize_for_json(v) for v in obj]
        return obj

    def _save_json(self, path: str, data):
        sanitized = self._sanitize_for_json(data)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(sanitized, f, indent=4, ensure_ascii=False)
