# src/core/models.py
import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


# 시장별 기본 수량 정밀도 (허용 소수 자릿수).
# 주식(domestic/overseas)은 1주 단위(정수), 코인(crypto)은 소수 수량 허용.
DEFAULT_QTY_PRECISION: Dict[str, int] = {
    "domestic": 0,
    "overseas": 0,
    "crypto": 8,
}


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
    """종목별 매매 규칙 (설정 파일에서 로드).

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
    # 처리 우선순위 (1이 최우선). 동일 priority끼리는 랜덤 셔플.
    # None이면 우선순위 없는 마지막 그룹(전체 랜덤).
    priority: Optional[int] = None
    # 호가 스프레드 허용 한도 (%). None이면 브로커 기본값(SPREAD_THRESHOLD_PCT=0.5%) 사용.
    # 비인기/저유동성 종목은 2.0 이상 권장.
    spread_threshold_pct: Optional[float] = None
    # 주문 수량 정밀도 (허용 소수 자릿수). None이면 market_type 기본값
    # (domestic/overseas=0 정수, crypto=8 소수). KIS는 정수, 업비트는 소수 수량.
    qty_precision: Optional[int] = None

    # --- 레짐 필터 (전부 기본값 => OFF => 오늘과 완전히 동일 동작) ---
    regime_enabled: bool = False
    # 레짐 분류 알고리즘: "ma_adx"(이동평균 정렬+ADX) | "channel"(회귀 채널 기울기)
    regime_algo: str = "ma_adx"
    regime_adx_trend: float = 25.0      # ADX 이상이면 추세장으로 간주 (ma_adx 전용)
    regime_adx_range: float = 20.0      # ADX 미만이면 횡보장 (히스테리시스 하단, ma_adx 전용)
    regime_min_bars: int = 200          # 레짐 판정에 필요한 최소 봉 수 (ma_adx 전용. channel은 channel_lookback)
    # 회귀 채널 분류기 (regime_algo="channel" 전용)
    channel_lookback: int = 63                    # 회귀 윈도우 봉 수 (63 = 3개월)
    channel_stddev_k: float = 2.0                 # 채널 폭 = 중심선 +- k*잔차표준편차
    channel_slope_band_pct: float = 8.0           # |윈도우 전체 기울기%| 이내면 횡보 (백테스트 근거 5.0 -> 8.0)
    channel_breakdown_tolerance_pct: float = 0.0  # 하단선*(1-tol%) 미만이면 이탈
    # True면 이탈/하락 청산 후 재진입을 상단 저항선(2sigma) 상향 돌파 시에만 허용.
    # 경계 왕복 재진입 churn을 구조적으로 차단한다. (권장 조합의 핵심 옵션)
    channel_reentry_breakout: bool = False
    # True면 상승 래치 중 이탈 판정을 하단 채널선 대신 ma_adx식 이탈선
    # (trendbreak_use_sma50에 따라 50MA 또는 챈들리어 스톱)으로 전환.
    # 상승 추세의 정상 눌림(2sigma 하단 터치)이 청산되는 것을 방지하는 하이브리드.
    # 횡보/하락 감지와 재진입 게이트는 채널 방식 유지.
    channel_uptrend_exit_ma: bool = False
    # 상승 레짐: 차수 매도를 잠그고 추세 눌림에 누적 매수
    uptrend_pullback_band_pct: float = 1.5   # 눌림 매수 상한: 20EMA + band% 이하면 허용 (하단 제한 없음)
    uptrend_max_adds: int = 3                # 상승장 1사이클 최대 추가매수 횟수
    uptrend_add_amount: Optional[float] = None          # 회차 공통 금액 (scalar fallback)
    uptrend_add_amounts: Optional[List[float]] = None   # 회차별 금액 (점감 권장)
    uptrend_swing_lookback: int = 10         # 새 고점 게이트용 스윙 룩백
    uptrend_add_reset_pct: Optional[float] = None  # 마지막 매수가 대비 레벨업 판정 상승률(%)
    # 추세 이탈 (전량 청산). 눌림 밴드보다 깊어야 정상 눌림에 안 털린다.
    trendbreak_chandelier_k: float = 3.0     # 고점 - k*ATR
    trendbreak_chandelier_lookback: int = 22
    trendbreak_use_sma50: bool = True        # 이탈 = close<sma50 OR close<chandelier_stop
    # 추세 이탈 분할 매도 + 추종 데드라인(Trailing Lock)
    trendbreak_partial_sell_pct: float = 50.0  # 이탈 시 즉시 매도 비율(%). 100=전량, 50=절반
    trendbreak_trailing_drop_pct: float = 3.0   # 잔량 추종 데드라인 하락 허용치(%)

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
            ("uptrend_add_amounts", self.uptrend_add_amounts),
        ):
            if arr is not None and len(arr) == 0:
                raise ValueError(f"StockRule({self.ticker}): {name}는 비어 있으면 안 됩니다.")

        if self.spread_threshold_pct is not None and self.spread_threshold_pct < 0:
            raise ValueError(
                f"StockRule({self.ticker}): spread_threshold_pct는 0 이상이어야 합니다. "
                f"got {self.spread_threshold_pct}"
            )

        if self.regime_enabled:
            if self.regime_algo not in ("ma_adx", "channel"):
                raise ValueError(
                    f"StockRule({self.ticker}): regime_algo는 'ma_adx' 또는 'channel'이어야 합니다. "
                    f"got '{self.regime_algo}'"
                )
            if self.regime_algo == "channel":
                # 최소 1개월(21봉). 보조 지표(ema20/chandelier)는 전체 히스토리로
                # 계산하므로 윈도우가 짧아도 안전하다. 회귀 유의성 확보용 하한.
                if self.channel_lookback < 21:
                    raise ValueError(
                        f"StockRule({self.ticker}): channel_lookback은 21(1개월) 이상이어야 합니다. "
                        f"got {self.channel_lookback}"
                    )
                if self.channel_stddev_k <= 0:
                    raise ValueError(
                        f"StockRule({self.ticker}): channel_stddev_k는 양수여야 합니다. "
                        f"got {self.channel_stddev_k}"
                    )
                if self.channel_slope_band_pct < 0:
                    raise ValueError(
                        f"StockRule({self.ticker}): channel_slope_band_pct는 음수일 수 없습니다."
                    )
                if not (0 <= self.channel_breakdown_tolerance_pct < 100):
                    raise ValueError(
                        f"StockRule({self.ticker}): channel_breakdown_tolerance_pct는 "
                        f"0 이상 100 미만이어야 합니다."
                    )
            if self.regime_adx_range > self.regime_adx_trend:
                raise ValueError(
                    f"StockRule({self.ticker}): regime_adx_range({self.regime_adx_range})는 "
                    f"regime_adx_trend({self.regime_adx_trend}) 이하여야 합니다."
                )
            if self.uptrend_pullback_band_pct < 0:
                raise ValueError(
                    f"StockRule({self.ticker}): uptrend_pullback_band_pct는 음수일 수 없습니다."
                )
            if self.uptrend_max_adds < 0:
                raise ValueError(
                    f"StockRule({self.ticker}): uptrend_max_adds는 음수일 수 없습니다."
                )
            if self.uptrend_add_reset_pct is not None and self.uptrend_add_reset_pct < 0:
                raise ValueError(
                    f"StockRule({self.ticker}): uptrend_add_reset_pct는 음수일 수 없습니다."
                )
            if not (0 <= self.trendbreak_partial_sell_pct <= 100):
                raise ValueError(
                    f"StockRule({self.ticker}): trendbreak_partial_sell_pct는 0~100 범위여야 합니다."
                )
            if self.trendbreak_trailing_drop_pct < 0:
                raise ValueError(
                    f"StockRule({self.ticker}): trendbreak_trailing_drop_pct는 음수일 수 없습니다."
                )

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

    def uptrend_add_amount_at(self, add_index: int) -> float:
        """주어진 상승장 추가매수 회차(1-based)의 금액을 반환한다.

        uptrend_add_amounts(배열) > uptrend_add_amount(단일) > buy_amount(기본) 순으로 fallback.
        배열이 회차보다 짧으면 마지막 값으로 clamp.
        """
        if self.uptrend_add_amounts:
            idx = max(0, min(add_index - 1, len(self.uptrend_add_amounts) - 1))
            return float(self.uptrend_add_amounts[idx])
        if self.uptrend_add_amount is not None:
            return float(self.uptrend_add_amount)
        return self.buy_amount_at(1)

    def effective_qty_precision(self) -> int:
        """이 종목의 유효 수량 정밀도(소수 자릿수)를 반환한다.

        qty_precision이 명시되면 그 값을, 아니면 market_type 기본값을 쓴다.
        미등록 market_type은 안전하게 정수(0)로 처리한다.
        """
        if self.qty_precision is not None:
            return max(0, int(self.qty_precision))
        return DEFAULT_QTY_PRECISION.get(self.market_type, 0)

    def quantize_qty(self, raw_qty: float, round_up: bool = False) -> float:
        """주문 수량을 이 종목의 정밀도에 맞춰 정규화한다.

        - 정밀도 0(주식/KIS): 정수(int)로 반환 -> 기존 동작·직렬화 그대로.
        - 정밀도 p>0(코인/업비트): 소수 p자리로 절단하여 float로 반환.
        - round_up=False(기본, 매수): 내림 -> 예산 초과 방지.
        - round_up=True(부분매도): 올림 -> 잔량 dust 방지.
        """
        precision = self.effective_qty_precision()
        rounder = math.ceil if round_up else math.floor
        if precision <= 0:
            return int(rounder(raw_qty))
        factor = 10 ** precision
        # 부동소수 오차 방어: floor/ceil 직전에 12자리로 반올림해
        # 5.699999999999(=5.7)가 5로 잘리거나 57.0000001이 58로 올림되는 것을 막는다.
        return rounder(round(raw_qty * factor, 12)) / factor

    def min_order_qty(self) -> float:
        """이 종목의 최소 주문 단위 수량.

        정수 시장(주식/KIS)=1주, 소수 시장(코인/업비트)=10^-precision.
        """
        precision = self.effective_qty_precision()
        return 1 if precision <= 0 else 10 ** (-precision)


@dataclass
class PositionLot:
    """개별 분할 매수 건 (차수별 개별 관리)"""
    lot_id: str          # 고유 ID (예: "lot_20260410_001")
    ticker: str
    buy_price: float     # 매수 단가
    quantity: float      # 매수 수량 (주식=정수, 코인=소수)
    buy_date: str        # 매수 일자
    level: int = 0       # 차수 (1차, 2차, ..., 100차). 0 = 레거시 데이터
    trailing_highest_price: Optional[float] = None  # 트레일링 스톱 활성화 이후 최고가


@dataclass
class Portfolio:
    """현재 계좌 상태"""
    total_cash: float
    holdings: Dict[str, float]        # {ticker: quantity} (주식=정수, 코인=소수)
    current_prices: Dict[str, float]  # {ticker: price}
    exchange_rate: Optional[float] = None  # 조회 시점 기준환율 (KRW/USD). 해외주식 전용, 없으면 None

    @property
    def total_value(self) -> float:
        stock_val = sum(q * self.current_prices.get(t, 0) for t, q in self.holdings.items())
        return self.total_cash + stock_val


@dataclass
class Order:
    ticker: str
    action: OrderAction
    quantity: float
    price: float  # 예상가
    spread_threshold_pct: Optional[float] = None  # None이면 브로커 기본값 사용
    # 주문 수량 정밀도(소수 자릿수). None/0이면 정수(주식), p>0이면 소수(코인).
    # 브로커가 예산 조정 등으로 수량을 재계산할 때 정수/소수 여부를 판단하는 데 쓴다.
    qty_precision: Optional[int] = None


@dataclass
class TradeExecution:
    """실제 체결된 매매 결과 (영수증)"""
    ticker: str
    action: OrderAction
    quantity: float  # 실제 체결 수량 (주식=정수, 코인=소수)
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
    # 통합 청산(Bulk Sell) 시 소진한 lot별 분해 내역(차수별 손익 기록용).
    # 각 항목: {lot_id, level, buy_price, quantity, realized_pnl}.
    # 저장 시 이 내역을 차수별 N개 레코드로 펼친다.
    liquidation_lots: Optional[List[dict]] = None


@dataclass
class SplitSignal:
    """분할 매수/매도 판단 결과"""
    ticker: str
    lot_id: Optional[str]   # 매도 시 대상 lot (매수 시 None)
    action: OrderAction
    quantity: float
    price: float
    reason: str             # 판단 사유 (예: "Lv3 +12.3% -> 익절")
    pct_change: float       # 매수가 대비 변동률
    level: int = 0          # 대상 차수 (매도: 해당 lot 차수, 매수: 새로운 차수)
    buy_price: float = 0.0  # 매도 시 원래 매수 단가 (손익 계산용)
    is_blocked: bool = False  # 비중 제한 등으로 인해 실행이 차단된 신호 여부
    is_info: bool = False     # 정보성 알림 전용 신호 (주문 없이 상태 변화만 알림)
    # 상승장 누적매수(add) 신호 표식 + 체결 확정 시 기록할 스윙고점.
    # None이 아니면 "상승 add"이며, 매수 체결이 확정될 때 regime_state를 갱신한다.
    regime_add_swing_high: Optional[float] = None
    # 추세이탈 전량청산 매도 표식. 매도 체결이 확정될 때 regime_state를 리셋(flat 재시작)한다.
    regime_liquidation: bool = False
    # 추세이탈 분할청산(Trailing Lock 1단계) 매도 표식.
    # 체결 시 잔량은 유지하고 trailing_lock 상태를 활성화한다.
    regime_partial_liquidation: bool = False
    # 횡보장 trailing 벌크 매도 표식. 엔진이 _apply_trailing_bulk()로 라우팅.
    # regime_state 변경 없이 fired lot만 고차수부터 차감한다.
    trailing_bulk: bool = False


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

# --- Constants ---
REASON_NO_SIGNAL = "모니터링 - 신호 없음"
