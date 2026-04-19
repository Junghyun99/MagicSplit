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

    BacktestDataCache.get_data()는 (OHLCV, VIX, Dividends) 튜플을 반환한다.
    이 어댑터는 OHLCV의 MultiIndex에서 Close 레벨만 추출해 티커를 컬럼으로 하는
    flat DataFrame을 반환한다. 상장일 보정과 ffill은 캐시 내부에서 이미 적용된다.

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

    ohlcv, _, _ = cache.get_data(tickers, start_date, end_date)
    close_df = ohlcv["Close"]
    if isinstance(close_df, pd.Series):
        close_df = close_df.to_frame(name=tickers[0])
    return close_df
