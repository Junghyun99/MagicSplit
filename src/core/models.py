# src/core/models.py
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class OrderAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    def __str__(self):
        return self.value


class ExecutionStatus(str, Enum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    ORDERED = "ORDERED"

    def __str__(self):
        return self.value


@dataclass
class StockRule:
    """종목별 매매 규칙 (config.json에서 로드)"""
    ticker: str
    buy_threshold_pct: float    # 매수가 대비 -N% 이하 시 추가매수 (음수, 예: -5.0)
    sell_threshold_pct: float   # 매수가 대비 +N% 이상 시 매도 (양수, 예: 10.0)
    buy_amount: float           # 1회 매수 금액 (원 or USD)
    max_lots: int               # 최대 분할 횟수
    market_type: str = "overseas"  # "overseas" | "domestic"
    enabled: bool = True
    exchange: str = ""  # 거래소 단축 코드 (NAS, NYS, AMS 등; 해외주식 전용)
    # 재진입 가드: 직전 매도가 대비 X% 하락해야 1차수 재매수 허용 (음수, 예: -0.1)
    # None이면 가드 비활성 (전량 청산 직후에도 다음 사이클 즉시 재진입)
    reentry_guard_pct: Optional[float] = None


@dataclass
class PositionLot:
    """개별 분할 매수 건 (차수별 개별 관리)"""
    lot_id: str          # 고유 ID (예: "lot_20260410_001")
    ticker: str
    buy_price: float     # 매수 단가
    quantity: int        # 매수 수량
    buy_date: str        # 매수 일자
    level: int = 0       # 차수 (1차, 2차, ..., 100차). 0 = 레거시 데이터


@dataclass
class Portfolio:
    """현재 계좌 상태"""
    total_cash: float
    holdings: Dict[str, int]          # {ticker: quantity}
    current_prices: Dict[str, float]  # {ticker: price}

    @property
    def total_value(self) -> float:
        stock_val = sum(q * self.current_prices.get(t, 0) for t, q in self.holdings.items())
        return self.total_cash + stock_val


@dataclass
class Order:
    ticker: str
    action: OrderAction
    quantity: int
    price: float  # 예상가


@dataclass
class TradeExecution:
    """실제 체결된 매매 결과 (영수증)"""
    ticker: str
    action: OrderAction
    quantity: int    # 실제 체결 수량
    price: float     # 실제 체결 단가 (평균단가)
    fee: float       # 수수료
    date: str        # 체결 시간
    status: ExecutionStatus
    reason: str = ""  # 거부 사유 등


@dataclass
class SplitSignal:
    """분할 매수/매도 판단 결과"""
    ticker: str
    lot_id: Optional[str]   # 매도 시 대상 lot (매수 시 None)
    action: OrderAction
    quantity: int
    price: float
    reason: str             # 판단 사유 (예: "Lv3 +12.3% → 익절")
    pct_change: float       # 매수가 대비 변동률
    level: int = 0          # 대상 차수 (매도: 해당 lot 차수, 매수: 새로운 차수)
    buy_price: float = 0.0  # 매도 시 원래 매수 단가 (손익 계산용)


@dataclass
class TradeSignal:
    """전략 판단 결과 (주문 목록)"""
    orders: List[Order]
    reason: str

    @property
    def has_orders(self) -> bool:
        return len(self.orders) > 0


@dataclass
class DayResult:
    """하루치 매매 사이클 결과"""
    date: str
    signals: List[SplitSignal]
    executions: List[TradeExecution]
    final_portfolio: Portfolio
    has_orders: bool
