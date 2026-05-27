# src/backtest/fetcher.py
import pandas as pd
from src.backtest.cache import BacktestDataCache


def download_ohlc_data(
    tickers: list,
    start_date: str,
    end_date: str,
    cache: BacktestDataCache = None,
) -> pd.DataFrame:
    """백테스트용 OHLC(High/Low/Close) 데이터 다운로드 (캐시 활용).

    Returns:
        컬럼이 MultiIndex (field, ticker)인 DataFrame. field는 High/Low/Close.
    """
    if cache is None:
        cache = BacktestDataCache()

    return cache.get_ohlc(tickers, start_date, end_date)
