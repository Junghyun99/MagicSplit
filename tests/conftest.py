# tests/conftest.py
import pytest
from src.core.models import (
    StockRule, PositionLot, Portfolio,
)


@pytest.fixture
def sample_rule():
    """기본 매매 규칙"""
    return StockRule(
        ticker="AAPL",
        buy_threshold_pct=-5.0,
        sell_threshold_pct=10.0,
        buy_amount=500,
        max_lots=10,
    )


@pytest.fixture
def sample_lot():
    """기본 포지션 lot"""
    return PositionLot(
        lot_id="lot_20260401_AAPL_000",
        ticker="AAPL",
        buy_price=100.0,
        quantity=5,
        buy_date="2026-04-01",
        level=1,
    )


@pytest.fixture
def sample_portfolio():
    """현금 + 보유 종목이 있는 포트폴리오"""
    return Portfolio(
        total_cash=10000.0,
        holdings={"AAPL": 5},
        current_prices={"AAPL": 100.0},
    )


@pytest.fixture
def empty_portfolio():
    """현금만 있는 초기 상태"""
    return Portfolio(
        total_cash=10000.0,
        holdings={},
        current_prices={"AAPL": 100.0, "MSFT": 200.0},
    )


@pytest.fixture
def create_rule():
    """원하는 값만 바꿔서 StockRule을 만드는 팩토리"""
    def _create(ticker="AAPL", buy_pct=-5.0, sell_pct=10.0,
                buy_amount=500, max_lots=10, market_type="overseas",
                enabled=True):
        return StockRule(
            ticker=ticker,
            buy_threshold_pct=buy_pct,
            sell_threshold_pct=sell_pct,
            buy_amount=buy_amount,
            max_lots=max_lots,
            market_type=market_type,
            enabled=enabled,
        )
    return _create


@pytest.fixture
def create_lot():
    """원하는 값만 바꿔서 PositionLot을 만드는 팩토리"""
    def _create(lot_id="lot_001", ticker="AAPL", buy_price=100.0,
                quantity=5, buy_date="2026-04-01", level=1):
        return PositionLot(
            lot_id=lot_id,
            ticker=ticker,
            buy_price=buy_price,
            quantity=quantity,
            buy_date=buy_date,
            level=level,
        )
    return _create


@pytest.fixture
def create_portfolio():
    """원하는 종목 구성으로 포트폴리오 생성"""
    def _create(cash=10000.0, holdings=None, prices=None):
        if holdings is None:
            holdings = {}
        if prices is None:
            prices = {"AAPL": 100.0}
        return Portfolio(
            total_cash=float(cash),
            holdings=holdings,
            current_prices=prices,
        )
    return _create
