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
                           signals: Optional[List[SplitSignal]] = None,
                           sim_date: Optional[str] = None) -> None:
        """매매 내역을 저장한다."""
        ...

    @abstractmethod
    def update_status(self, portfolio: Portfolio,
                      positions: List[PositionLot],
                      reason: str,
                      sim_date: Optional[str] = None) -> None:
        """최신 상태를 저장한다."""
        ...

    @abstractmethod
    def get_last_run_date(self) -> Optional[str]:
        """마지막 실행 날짜를 반환한다."""
        ...
