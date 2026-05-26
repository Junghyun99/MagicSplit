# src/core/interfaces.py
from abc import ABC, abstractmethod
from typing import Any, List, Dict, Optional
from src.core.models import Portfolio, Order, TradeExecution, PositionLot, SplitSignal


class IBrokerAdapter(ABC):
    @abstractmethod
    def get_portfolio(self) -> Portfolio: ...
    @abstractmethod
    def execute_orders(self, orders: List[Order]) -> List[TradeExecution]: ...
    @abstractmethod
    def fetch_current_prices(self, tickers: List[str]) -> Dict[str, float]: ...


class IMarketDataProvider(ABC):
    """레짐 지표 계산용 과거 시세(OHLC)를 제공한다. 실행 브로커와 분리된 시장 데이터 출처.

    백테스트는 yfinance 캐시 기반 프레임을, 라이브는 yfinance/증권사 일봉을 구현으로 끼운다.
    """

    @abstractmethod
    def get_ohlc_window(self, ticker: str, asof: Any) -> Optional["Any"]:
        """asof(오늘) *직전*까지의 완성봉 OHLC 윈도우를 반환한다 (오늘 봉 제외).

        반환: index=날짜, columns=[High, Low, Close]인 DataFrame. 데이터가 없으면 None.
        """
        ...


class ILogger(ABC):
    @abstractmethod
    def debug(self, msg: str) -> None: ...
    @abstractmethod
    def info(self, msg: str) -> None: ...
    @abstractmethod
    def warning(self, msg: str) -> None: ...
    @abstractmethod
    def error(self, msg: str) -> None: ...
    
    @abstractmethod
    def set_ticker_context(self, ticker: Optional[str]) -> None:
        """현재 로그가 귀속될 종목명을 설정한다 (None은 공통 영역)."""
        ...
    
    @abstractmethod
    def get_captured_logs(self, ticker: Optional[str] = None) -> List[str]:
        """캡처된 로그를 추출한다. ticker 지정 시 해당 종목 로그만, None이면 전체 로그."""
        ...
    
    @abstractmethod
    def clear_captured_logs(self) -> None:
        """저장된 캡처 로그를 비운다."""
        ...


class INotifier(ABC):
    @abstractmethod
    def send_message(self, message: str, detail: Optional[str] = None) -> None: ...
    @abstractmethod
    def send_alert(self, message: str, detail: Optional[str] = None) -> None: ...


class IRepository(ABC):
    @abstractmethod
    def load_positions(self) -> List[PositionLot]:
        """저장된 분할 포지션 목록을 로드한다."""
        ...

    @abstractmethod
    def save_positions(self, lots: List[PositionLot]) -> None:
        """분할 포지션 목록을 저장한다."""
        ...

    @abstractmethod
    def save_trade_history(self, executions: List[TradeExecution],
                           portfolio: Portfolio, reason: str,
                           sim_date: Optional[str] = None) -> None:
        """매매 내역을 저장한다."""
        ...

    @abstractmethod
    def get_realized_pnl_by_ticker(self) -> Dict[str, float]:
        """과거 누적 실현 손익을 종목별로 반환한다."""
        ...

    @abstractmethod
    def save_status(self, status_data: dict) -> None:
        """최신 상태 딕셔너리를 저장한다."""
        ...

    @abstractmethod
    def load_status(self) -> dict:
        """최근 저장된 상태 딕셔너리를 로드한다."""
        ...

    @abstractmethod
    def get_last_run_date(self) -> Optional[str]:
        """마지막 실행 날짜를 반환한다."""
        ...

    @abstractmethod
    def load_last_sell_prices(self) -> Dict[str, float]:
        """티커별 직전 매도가를 로드한다 (동적 재매수 기준용)."""
        ...

    @abstractmethod
    def save_last_sell_prices(self, prices: Dict[str, float]) -> None:
        """티커별 직전 매도가를 저장한다."""
        ...

    @abstractmethod
    def save_decision_log(self, date: str, reason: str) -> None:
        """판단 내역(모니터링 사유)을 저장한다."""
        ...

    @abstractmethod
    def get_last_trade_dates(self) -> Dict[str, str]:
        """종목별 마지막 체결 날짜를 반환한다."""
        ...
