# src/infra/repo.py
import json
import copy
import math
import os
import re
from typing import List, Optional, Dict
from dataclasses import asdict
from datetime import datetime

from src.core.models import (
    PositionLot, Portfolio, TradeExecution, SplitSignal, OrderAction, ExecutionStatus,
)
from src.core.interfaces import IRepository
from src.utils.ticker_reader import get_alias


def trade_cash_impact(executions: List[TradeExecution]) -> float:
    """체결로 인한 순 현금 변동을 계산한다.

    BUY는 현금 감소(-), SELL은 현금 증가(+), 각각 수수료만큼 추가 차감.
    입출금(순입금) 역산 시 시세 변동/거래를 제외하기 위해 사용한다.

    거절(REJECTED)은 현금이 오간 적이 없으므로 제외한다. 수량 0인 거절뿐 아니라
    사전 차단(스프레드 비정상 등)으로 주문 자체가 나가지 않은 경우도 있는데,
    이때 브로커는 '요청 수량'을 그대로 담아 반환하므로 수량만으로는 걸러지지 않는다.
    """
    return sum(
        (-e.price * e.quantity - e.fee) if e.action == OrderAction.BUY
        else (e.price * e.quantity - e.fee)
        for e in executions
        if e.quantity > 0 and e.status != ExecutionStatus.REJECTED
    )


class JsonRepository(IRepository):
    """JSON 파일 기반 저장소.

    positions.json — 분할 포지션 목록
    history.json   — 매매 내역
    status.json    — 최신 상태 (대시보드용)
    """

    def __init__(self, root_path: str = "docs/data",
                 max_history_records: int = 100000):
        self._cache = {}  # ⚡ Bolt: Initialize cache first
        self.root = root_path
        self.max_history_records = max_history_records
        os.makedirs(self.root, exist_ok=True)

        self.positions_file = os.path.join(self.root, "positions.json")
        self.history_file = os.path.join(self.root, "history.json")
        self.status_file = os.path.join(self.root, "status.json")
        self.last_sell_prices_file = os.path.join(self.root, "last_sell_prices.json")
        self.decisions_file = os.path.join(self.root, "decisions.json")
        self.snapshots_file = os.path.join(self.root, "snapshots.json")
        self.regime_events_file = os.path.join(self.root, "regime_events.json")
        self.charts_dir = os.path.join(self.root, "charts")

        # 초기 파일 생성 (404 방지)
        if not os.path.exists(self.positions_file):
            self._save_json(self.positions_file, [])
        if not os.path.exists(self.history_file):
            self._save_json(self.history_file, [])
        if not os.path.exists(self.status_file):
            self._save_json(self.status_file, {})
        if not os.path.exists(self.last_sell_prices_file):
            self._save_json(self.last_sell_prices_file, {})
        if not os.path.exists(self.decisions_file):
            self._save_json(self.decisions_file, [])
        if not os.path.exists(self.snapshots_file):
            self._save_json(self.snapshots_file, [])
        if not os.path.exists(self.regime_events_file):
            self._save_json(self.regime_events_file, [])

        # ⚡ Bolt: Memory cache to avoid redundant disk I/O and JSON decoding
        # This dramatically speeds up the backtest loop which constantly reads/writes status.

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
        """분할 포지션 목록을 저장한다 (alias 필드 포함)."""
        # 동일 ticker의 lot이 여러 개일 수 있어 unique ticker로만 alias 조회
        alias_by_ticker = {l.ticker: get_alias(l.ticker) or l.ticker for l in lots}
        data = []
        for lot in lots:
            rec = asdict(lot)
            rec["alias"] = alias_by_ticker[lot.ticker]
            data.append(rec)
        self._save_json(self.positions_file, data)

    # === Trade History ===

    def save_trade_history(self, executions: List[TradeExecution],
                           portfolio: Portfolio, reason: str,
                           sim_date: Optional[str] = None) -> None:
        """매매 내역 저장 (Append 방식)

        거절된 주문은 기록하지 않는다. 체결이 없었으므로 매매 내역이 아니고,
        수량이 남아 있는 거절(사전 차단)은 거래대금·순입금까지 오염시킨다.
        거절 사실 자체는 로그와 Slack 알림이 남긴다.
        """
        if not executions:
            return

        executions = [
            e for e in executions if e.status != ExecutionStatus.REJECTED
        ]
        if not executions:
            return

        trade_amt = sum(e.price * e.quantity for e in executions)

        if sim_date:
            date_str = sim_date
            tx_id = f"tx_{sim_date.replace('-', '')}"
        else:
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tx_id = f"tx_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 동일 ticker의 체결이 여러 건일 수 있어 unique ticker로만 alias 조회
        alias_by_ticker = {e.ticker: get_alias(e.ticker) or e.ticker for e in executions}
        enriched_execs = []
        for e in executions:
            base = asdict(e)
            base["alias"] = alias_by_ticker[e.ticker]
            breakdown = base.pop("liquidation_lots", None)

            # 통합 청산(Bulk): 소진 lot별 N개 레코드로 분리 기록(차수별 손익 보존)
            if breakdown:
                for lot in breakdown:
                    rec = dict(base)
                    rec.update({
                        "quantity": lot["quantity"],
                        "lot_id": lot["lot_id"],
                        "level": lot["level"],
                        "buy_price": lot["buy_price"],
                        "realized_pnl": lot["realized_pnl"],
                    })
                    enriched_execs.append(rec)
                continue

            rec = base
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

        data = self._load_json(self.history_file, default=[])

        # Calculate net external cash flow (deposit/withdrawal) since previous record.
        trade_cash_impact = self._trade_cash_impact(executions)
        prev = data[-1] if data else None
        prev_cash = prev["cash_balance"] if prev and "cash_balance" in prev else None
        if prev_cash is not None:
            net_deposit = round(portfolio.total_cash - prev_cash - trade_cash_impact, 2)
        else:
            # First record: before-trade cash is the initial deposit
            net_deposit = round(portfolio.total_cash - trade_cash_impact, 2)

        record = {
            "id": tx_id,
            "date": date_str,
            "portfolio_value": portfolio.total_value,
            "cash_balance": portfolio.total_cash,
            "net_deposit": net_deposit,
            "total_trade_amount": trade_amt,
            "reason": reason,
            "executions": enriched_execs,
        }

        data.append(record)

        if self.max_history_records > 0:
            data = data[-self.max_history_records:]

        self._save_json(self.history_file, data)

    @staticmethod
    def _trade_cash_impact(executions: List[TradeExecution]) -> float:
        """체결로 인한 순 현금 변동 (모듈 함수 trade_cash_impact 에 위임)."""
        return trade_cash_impact(executions)

    def load_history(self) -> List[dict]:
        """매매 내역을 로드한다 (저장 순서 = 시간 오름차순)."""
        return self._load_json(self.history_file, default=[])

    # === Snapshots (일별 자산 스냅샷 — 결산용) ===

    def save_snapshot(self, portfolio: Portfolio,
                      executions: Optional[List[TradeExecution]] = None,
                      sim_date: Optional[str] = None) -> None:
        """일별 자산 스냅샷을 저장한다 (거래 유무와 무관하게 매 실행마다 기록).

        history.json(매매 로그)과 분리된 시계열로, 월간/기간 결산의 기초·기말 자산과
        순입금액 산출에 사용한다. 같은 날짜 레코드는 마지막 값으로 덮어써서
        하루 1개의 대표 스냅샷만 유지한다.

        net_deposit = 당일현금 - 직전스냅샷현금 - 당일체결현금영향
        (시세 재평가는 현금에 영향을 주지 않으므로 순수 입출금만 남는다.)
        """
        executions = executions or []
        date_str = sim_date if sim_date else datetime.now().strftime("%Y-%m-%d")
        # 스냅샷 날짜는 날짜 단위(YYYY-MM-DD)로 정규화
        date_key = date_str[:10]

        data = self._load_json(self.snapshots_file, default=[])

        trade_cash_impact = self._trade_cash_impact(executions)
        prev = data[-1] if data else None
        prev_cash = prev.get("cash_balance") if prev else None
        same_day = prev is not None and prev.get("date") == date_key

        if same_day:
            # 같은 날짜 재실행(수동매매/재실행 등): 직전(같은 날) 스냅샷의 net_deposit에
            # 이번 실행의 순입금 변동분만 누적 합산한다. prev_cash가 직전 실행 후
            # 현금이므로, 앞선 실행들의 체결 현금영향이 누락되지 않아 순입금이
            # 왜곡되지 않는다.
            run_net_deposit = portfolio.total_cash - (prev_cash or 0.0) - trade_cash_impact
            net_deposit = round(float(prev.get("net_deposit") or 0.0) + run_net_deposit, 2)
        elif prev_cash is not None:
            net_deposit = round(portfolio.total_cash - prev_cash - trade_cash_impact, 2)
        else:
            net_deposit = round(portfolio.total_cash - trade_cash_impact, 2)

        stock_value = round(portfolio.total_value - portfolio.total_cash, 2)
        record = {
            "date": date_key,
            "portfolio_value": round(portfolio.total_value, 2),
            "cash_balance": round(portfolio.total_cash, 2),
            "stock_value": stock_value,
            "net_deposit": net_deposit,
            # 그날 기준환율(KRW/USD). 해외 결산의 각 시점 원화 환산에 사용.
            # domestic은 None(원화 자체라 환산 불필요), 조회 실패 시에도 None.
            "exchange_rate": portfolio.exchange_rate,
        }

        # 같은 날짜면 덮어쓰기(하루 1개 대표값), 아니면 append
        if same_day:
            data[-1] = record
        else:
            data.append(record)

        if self.max_history_records > 0:
            data = data[-self.max_history_records:]

        self._save_json(self.snapshots_file, data)

    def load_snapshots(self) -> List[dict]:
        """일별 자산 스냅샷 목록을 로드한다 (날짜 오름차순 저장 순서)."""
        return self._load_json(self.snapshots_file, default=[])

    def save_snapshots(self, snapshots: List[dict]) -> None:
        """일별 자산 스냅샷 목록을 통째로 저장한다 (사후 보정용).

        정상 운영 경로는 save_snapshot()이며, 이 메서드는 봇이 보지 못한 외부
        체결을 사후 반영할 때 기존 레코드를 고쳐 쓰기 위한 통로다.
        """
        self._save_json(self.snapshots_file, snapshots)

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

    def load_status(self) -> dict:
        """최근 저장된 상태 딕셔너리를 로드한다."""
        return self._load_json(self.status_file, default={})

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

    # === Last Sell Prices (동적 재매수 기준) ===

    def load_last_sell_prices(self) -> Dict[str, float]:
        """티커별 직전 매도가를 로드한다."""
        return self._load_json(self.last_sell_prices_file, default={})

    def save_last_sell_prices(self, prices: Dict[str, float]) -> None:
        """티커별 직전 매도가를 저장한다."""
        self._save_json(self.last_sell_prices_file, prices)

    def save_decision_log(self, date: str, reason: str) -> None:
        """판단 내역(모니터링 사유)을 저장한다 (Rolling 방식)."""
        # 동일한 날짜/시간의 중복 기록 방지 (주로 백테스트 환경용)
        data = self._load_json(self.decisions_file, default=[])
        if data and data[-1].get("date") == date and data[-1].get("reason") == reason:
            return

        data.append({
            "date": date,
            "reason": reason
        })

        # 최근 1000건만 유지
        if len(data) > 1000:
            data = data[-1000:]

        self._save_json(self.decisions_file, data)

    def load_regime_events(self) -> List[dict]:
        """레짐 전이 이벤트 이력을 로드한다."""
        return self._load_json(self.regime_events_file, default=[])

    def save_regime_events(self, events: List[dict]) -> None:
        """레짐 전이 이벤트를 append 저장한다 (Rolling 방식).

        상태가 바뀔 때만 호출되므로 대부분의 사이클에서는 아무것도 쌓이지 않는다.
        """
        if not events:
            return
        data = self._load_json(self.regime_events_file, default=[])
        data.extend(events)

        # 최근 5000건만 유지 (decisions.json과 동일한 rolling 정책)
        if len(data) > 5000:
            data = data[-5000:]

        self._save_json(self.regime_events_file, data)

    def save_chart_series(self, ticker: str, chart: dict) -> None:
        """종목별 차트 시계열을 저장한다 (매 사이클 덮어쓰기).

        누적이 아니라 전량 재계산 후 교체다. 지표선은 OHLC에서 언제든 재현
        가능하므로 이력을 쌓을 이유가 없다.
        """
        os.makedirs(self.charts_dir, exist_ok=True)
        path = os.path.join(self.charts_dir, f"{self._chart_filename(ticker)}.json")
        self._save_json_compact(path, chart)

    def load_chart_series(self, ticker: str) -> Optional[dict]:
        """종목별 차트 시계열을 로드한다 (없으면 None)."""
        path = os.path.join(self.charts_dir, f"{self._chart_filename(ticker)}.json")
        return self._load_json(path, default=None)

    @staticmethod
    def _chart_filename(ticker: str) -> str:
        """티커를 파일명으로 안전하게 바꾼다 (KRW-BTC 등 구분자 포함 대응)."""
        return re.sub(r"[^A-Za-z0-9._-]", "_", ticker)

    def get_last_trade_dates(self) -> Dict[str, str]:
        """종목별 마지막 체결 날짜를 반환한다 (YYYY-MM-DD)."""
        history = self._load_json(self.history_file, default=[])
        result: Dict[str, str] = {}
        for record in history:
            rec_date = record.get("date", "").split(" ")[0]
            for exe in record.get("executions", []):
                # 거절은 거래가 아니다. 이미 기록된 과거 데이터도 여기서 걸러야
                # '장기 정체 종목'의 days_stale이 거짓으로 리셋되지 않는다.
                if exe.get("status") == ExecutionStatus.REJECTED.value:
                    continue
                ticker = exe.get("ticker")
                exe_date = exe.get("date", "").split(" ")[0] or rec_date
                if ticker and exe_date:
                    if ticker not in result or exe_date > result[ticker]:
                        result[ticker] = exe_date
        return result

    def clear_cache(self):
        """메모리 캐시를 비운다 (테스트 또는 외부 프로세스에 의한 파일 변경 대응용)."""
        self._cache = {}

    def _load_json(self, path: str, default=None):
        if path in self._cache:
            # ⚡ Bolt: Return a deep copy to prevent the caller from accidentally corrupting the cache
            return copy.deepcopy(self._cache[path])
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._cache[path] = data
                return data
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

        # ⚡ Bolt: Update cache ONLY after successful disk write
        self._cache[path] = sanitized

    def _save_json_compact(self, path: str, data):
        """들여쓰기 없이 최소 크기로 저장한다 (기계 생성/기계 소비 파일용).

        차트 시계열은 매 사이클 통째로 교체되므로 사람이 diff를 읽을 일이 없다.
        indent=4로 쓰면 파일 크기와 git 변경량이 수 배로 늘어난다.
        """
        sanitized = self._sanitize_for_json(data)
        content = json.dumps(sanitized, ensure_ascii=False, separators=(',', ':'))
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        self._cache[path] = sanitized
