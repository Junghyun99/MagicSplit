# src/backtest/fetcher.py
import pandas as pd
from src.backtest.cache import BacktestDataCache


def download_historical_data(
    tickers: list,
    start_date: str,
    end_date: str,
    cache: BacktestDataCache = None,
) -> pd.DataFrame:
    """
    백테스트용 종가 데이터 다운로드 (캐시 활용).

    Args:
        tickers: 종목 코드 리스트
        start_date: 시작일 'YYYY-MM-DD'
        end_date: 종료일 'YYYY-MM-DD'
        cache: BacktestDataCache 인스턴스. None이면 기본 인스턴스를 생성한다.

    Returns:
        Close 가격 DataFrame (index=DatetimeIndex, columns=tickers)
    """
    if cache is None:
        cache = BacktestDataCache()

    return cache.get_data(tickers, start_date, end_date)
