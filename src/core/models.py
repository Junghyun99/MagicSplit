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
    """종목별 매매 규칙 (config.json에서 로드).

    차수별 차등을 위해 배열 필드(`*_pcts`, `buy_amounts`)를 제공한다.
    배열이 있으면 배열이 우선, 없으면 단일값(`*_pct`, `buy_amount`)을 사용.
    배열 길이가 실제 차수보다 짧으면 마지막 값으로 clamp.
    """
    ticker: str
    buy_threshold_pct: Optional[float] = None    # 매수가 대비 -N% 이하 시 추가매수 (음수, 예: -5.0)
    sell_threshold_pct: Optional[float] = None   # 매수가 대비 +N% 이상 시 매도 (양수, 예: 10.0)
    buy_amount: Optional[float] = None           # 1회 매수 금액 (원 or USD)
    max_lots: int = 10                           # 최대 분할 횟수
    market_type: str = "overseas"  # "overseas" | "domestic"
    enabled: bool = True
    exchange: str = ""  # 거래소 단축 코드 (NAS, NYS, AMS 등; 해외주식 전용)
    # 재진입 가드: 직전 매도가 대비 X% 하락해야 1차수 재매수 허용 (음수, 예: -0.1)
    # None이면 가드 비활성 (전량 청산 직후에도 다음 사이클 즉시 재진입)
    reentry_guard_pct: Optional[float] = None
    # 차수별 배열 (있으면 단일값보다 우선). level(1-based)을 배열 인덱스로 매핑.
    buy_threshold_pcts: Optional[List[float]] = None
    sell_threshold_pcts: Optional[List[float]] = None
    buy_amounts: Optional[List[float]] = None
    # 트레일링 스톱용 하락 허용치 (예: 2.0 = 고점 대비 2% 하락 시 매도)
    trailing_drop_pct: Optional[float] = None
    trailing_drop_pcts: Optional[List[float]] = None
    # 종목별 최대 투입 비중 (예: 20.0 = 계좌 총 자산의 20%까지만 투입)
    # None이면 비중 제한 없음. 글로벌 설정을 strategy_config에서 상속받을 수 있음.
    max_exposure_pct: Optional[float] = None

    def __post_init__(self):
        if self.buy_threshold_pct is None and not self.buy_threshold_pcts:
            raise ValueError(
                f"StockRule({self.ticker}): buy_threshold_pct 또는 buy_threshold_pcts 중 하나는 필요합니다."
            )
        if self.sell_threshold_pct is None and not self.sell_threshold_pcts:
            raise ValueError(
                f"StockRule({self.ticker}): sell_threshold_pct 또는 sell_threshold_pcts 중 하나는 필요합니다."
            )
        if self.buy_amount is None and not self.buy_amounts:
            raise ValueError(
                f"StockRule({self.ticker}): buy_amount 또는 buy_amounts 중 하나는 필요합니다."
            )
        for name, arr in (
            ("buy_threshold_pcts", self.buy_threshold_pcts),
            ("sell_threshold_pcts", self.sell_threshold_pcts),
            ("buy_amounts", self.buy_amounts),
            ("trailing_drop_pcts", self.trailing_drop_pcts),
        ):
            if arr is not None and len(arr) == 0:
                raise ValueError(f"StockRule({self.ticker}): {name}는 비어 있으면 안 됩니다.")

    @staticmethod
    def _at(arr: Optional[List[float]], scalar: Optional[float], level: int) -> float:
        if arr:
            idx = max(0, min(level - 1, len(arr) - 1))
            return float(arr[idx])
        if scalar is not None:
            return float(scalar)
        raise ValueError("array and scalar are both missing")

    def buy_threshold_at(self, level: int) -> float:
        """주어진 차수(1-based)의 매수 임계치(%)를 반환한다."""
        return self._at(self.buy_threshold_pcts, self.buy_threshold_pct, level)

    def sell_threshold_at(self, level: int) -> float:
        """주어진 차수(1-based)의 매도 임계치(%)를 반환한다."""
        return self._at(self.sell_threshold_pcts, self.sell_threshold_pct, level)

    def buy_amount_at(self, level: int) -> float:
        """주어진 차수(1-based)의 1회 매수 금액을 반환한다."""
        return self._at(self.buy_amounts, self.buy_amount, level)

    def trailing_drop_at(self, level: int) -> Optional[float]:
        """주어진 차수(1-based)의 트레일링 스톱 하락 허용치(%)를 반환한다."""
        if self.trailing_drop_pcts:
            idx = max(0, min(level - 1, len(self.trailing_drop_pcts) - 1))
            return float(self.trailing_drop_pcts[idx])
        if self.trailing_drop_pct is not None:
            return float(self.trailing_drop_pct)
        return None


@dataclass
class PositionLot:
    """개별 분할 매수 건 (차수별 개별 관리)"""
    lot_id: str          # 고유 ID (예: "lot_20260410_001")
    ticker: str
    buy_price: float     # 매수 단가
    quantity: int        # 매수 수량
    buy_date: str        # 매수 일자
    level: int = 0       # 차수 (1차, 2차, ..., 100차). 0 = 레거시 데이터
    trailing_highest_price: Optional[float] = None  # 트레일링 스톱 활성화 이후 최고가


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
    # --- 코어 엔진에서 신호와 매칭하여 추가하는 비즈니스 컨텍스트 ---
    lot_id: Optional[str] = None
    level: int = 0
    buy_price: float = 0.0
    realized_pnl: float = 0.0


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
    is_blocked: bool = False  # 비중 제한 등으로 인해 실행이 차단된 신호 여부


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
