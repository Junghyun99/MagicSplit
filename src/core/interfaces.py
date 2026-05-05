# src/core/interfaces.py
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from src.core.models import Portfolio, Order, TradeExecution, PositionLot, SplitSignal


class IBrokerAdapter(ABC):
    @abstractmethod
    def get_portfolio(self) -> Portfolio: ...
    @abstractmethod
    def execute_orders(self, orders: List[Order]) -> List[TradeExecution]: ...
    @abstractmethod
    def fetch_current_prices(self, tickers: List[str]) -> Dict[str, float]: ...


class ILogger(ABC):
    @abstractmethod
    def debug(self, msg: str) -> None: ...
    @abstractmethod
    def info(self, msg: str) -> None: ...
    @abstractmethod
    def warning(self, msg: str) -> None: ...
    @abstractmethod
    def error(self, msg: str) -> None: ...


class INotifier(ABC):
    @abstractmethod
    def send_message(self, message: str) -> None: ...
    @abstractmethod
    def send_alert(self, message: str) -> None: ...


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
