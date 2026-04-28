# src/infra/repo.py
import json
import math
import os
import re
from typing import List, Optional, Dict
from dataclasses import asdict
from datetime import datetime

from src.core.models import PositionLot, Portfolio, TradeExecution, SplitSignal, OrderAction
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
                trailing_highest_price=item.get("trailing_highest_price"),
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
                        trailing_highest_price=lot.trailing_highest_price,
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

        enriched_execs = []
        for e in executions:
            rec = asdict(e)
            
            # JSON 깔끔함을 위해 불필요한 빈 필드 제거
            if rec.get("lot_id") is None:
                rec.pop("lot_id", None)
            if rec.get("level") == 0:
                rec.pop("level", None)
            if rec.get("buy_price") == 0.0:
                rec.pop("buy_price", None)
            if rec.get("realized_pnl") == 0.0:
                rec.pop("realized_pnl", None)

            enriched_execs.append(rec)

        record = {
            "id": tx_id,
            "date": date_str,
            "portfolio_value": portfolio.total_value,
            "total_trade_amount": trade_amt,
            "reason": reason,
            "executions": enriched_execs,
        }

        data = self._load_json(self.history_file, default=[])
        data.append(record)

        if self.max_history_records > 0:
            data = data[-self.max_history_records:]

        self._save_json(self.history_file, data)

    # === Status ===

    def get_realized_pnl_by_ticker(self) -> Dict[str, float]:
        """과거 누적 실현 손익을 종목별로 반환한다. (마이그레이션 대비)"""
        status = self._load_json(self.status_file, default={})
        if "realized_pnl_by_ticker" in status:
            return status["realized_pnl_by_ticker"]
        return self._calc_realized_pnl_by_ticker()

    def save_status(self, status_data: dict) -> None:
        """최신 상태 딕셔너리를 저장한다 (대시보드용)."""
        self._save_json(self.status_file, status_data)

    def _calc_realized_pnl_by_ticker(self) -> dict:
        """history.json에서 종목별 실현 손익 합계를 계산한다."""
        history = self._load_json(self.history_file, default=[])
        result: dict = {}
        for record in history:
            for exe in record.get("executions", []):
                pnl = exe.get("realized_pnl")
                if pnl is not None:
                    ticker = exe.get("ticker", "")
                    result[ticker] = result.get(ticker, 0.0) + pnl
        return result

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
        # 기본적으로 4칸 들여쓰기로 변환
        content = json.dumps(sanitized, indent=4, ensure_ascii=False)
        
        # 숫자나 문자열로만 구성된 단순 배열을 한 줄로 압축 (정규식 사용)
        # 1단계: 숫자 배열 압축 [ 1, 2, 3 ]
        content = re.sub(r'\[\s+((?:-?\d+(?:\.\d+)?(?:,\s+)?)+)\s+\]', 
                         lambda m: "[" + re.sub(r'\s+', ' ', m.group(1)) + "]", 
                         content)
        # 2단계: 문자열 배열 압축 [ "A", "B" ]
        content = re.sub(r'\[\s+((?:"[^"]*"(?:,\s+)?)+)\s+\]', 
                         lambda m: "[" + re.sub(r'\s+', ' ', m.group(1)) + "]", 
                         content)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
