# tests/test_infra_market_data.py
"""라이브 레짐용 과거 일봉 제공자(YFinance/Upbit MarketDataProvider) 테스트."""
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from src.infra.data import UpbitMarketDataProvider, YFinanceMarketDataProvider


def _fake_yf_df(n=300, start="2025-01-01"):
    idx = pd.date_range(start, periods=n, freq="D")
    closes = np.linspace(100, 130, n)
    return pd.DataFrame({
        "Open": closes, "High": closes + 1, "Low": closes - 1,
        "Close": closes, "Adj Close": closes, "Volume": 1000,
    }, index=idx)


class TestYFinanceMarketDataProvider:
    def _provider(self, df):
        logger = MagicMock()
        p = YFinanceMarketDataProvider(logger, window_size=100)
        patcher = patch("yfinance.download", return_value=df)
        return p, patcher

    def test_window_excludes_asof_and_after(self):
        df = _fake_yf_df(300)
        p, patcher = self._provider(df)
        with patcher, patch("src.utils.ticker_reader.to_yfinance_ticker", return_value="AAPL"):
            w = p.get_ohlc_window("AAPL", df.index[-1])
        assert w is not None
        assert len(w) == 100
        assert w.index.max() < df.index[-1]  # 오늘 봉 제외
        assert list(w.columns) == ["High", "Low", "Close"]

    def test_download_cached_per_ticker(self):
        df = _fake_yf_df(300)
        logger = MagicMock()
        p = YFinanceMarketDataProvider(logger, window_size=50)
        with patch("yfinance.download", return_value=df) as dl, \
             patch("src.utils.ticker_reader.to_yfinance_ticker", return_value="AAPL"):
            p.get_ohlc_window("AAPL", "2025-06-01")
            p.get_ohlc_window("AAPL", "2025-06-02")
        assert dl.call_count == 1  # 사이클 내 1회만 다운로드

    def test_download_failure_returns_none(self):
        logger = MagicMock()
        p = YFinanceMarketDataProvider(logger, window_size=50)
        with patch("yfinance.download", side_effect=RuntimeError("network")), \
             patch("src.utils.ticker_reader.to_yfinance_ticker", return_value="AAPL"):
            assert p.get_ohlc_window("AAPL", "2025-06-01") is None
            # 실패도 캐시되어 재시도 없이 None
            assert p.get_ohlc_window("AAPL", "2025-06-01") is None

    def test_empty_data_returns_none(self):
        logger = MagicMock()
        p = YFinanceMarketDataProvider(logger, window_size=50)
        with patch("yfinance.download", return_value=pd.DataFrame()), \
             patch("src.utils.ticker_reader.to_yfinance_ticker", return_value="AAPL"):
            assert p.get_ohlc_window("AAPL", "2025-06-01") is None

    def test_batch_prefetch_single_download_for_all_tickers(self):
        # 티커 목록 제공 시 전 종목을 1회 배치 다운로드로 캐시
        n = 300
        idx = pd.date_range("2025-01-01", periods=n, freq="D")
        closes = np.linspace(100, 130, n)
        cols = pd.MultiIndex.from_product(
            [["High", "Low", "Close"], ["AAPL", "MSFT"]]
        )
        data = np.column_stack([closes + 1, closes + 2, closes - 1, closes - 2, closes, closes])
        batch_df = pd.DataFrame(data, index=idx, columns=cols)

        logger = MagicMock()
        p = YFinanceMarketDataProvider(logger, window_size=100, tickers=["AAPL", "MSFT"])
        with patch("yfinance.download", return_value=batch_df) as dl, \
             patch("src.utils.ticker_reader.to_yfinance_ticker", side_effect=lambda t: t):
            w1 = p.get_ohlc_window("AAPL", "2025-09-01")
            w2 = p.get_ohlc_window("MSFT", "2025-09-01")
        assert dl.call_count == 1  # 배치 1회로 전 종목 커버
        assert w1 is not None and w2 is not None
        assert len(w1) == 100 and len(w2) == 100


def _fake_upbit_batch(n, end="2026-07-18"):
    dates = pd.date_range(end=end, periods=n, freq="D")[::-1]  # 최신부터
    return [
        {
            "candle_date_time_kst": f"{d:%Y-%m-%d}T09:00:00",
            "candle_date_time_utc": f"{d:%Y-%m-%d}T00:00:00",
            "high_price": 101.0, "low_price": 99.0, "trade_price": 100.0,
        }
        for d in dates
    ]


class TestUpbitMarketDataProvider:
    def test_window_built_from_paginated_candles(self):
        logger = MagicMock()
        p = UpbitMarketDataProvider(logger, window_size=250)
        batches = [_fake_upbit_batch(200, end="2026-07-18"),
                   _fake_upbit_batch(200, end="2025-12-31")]
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(side_effect=batches)
        with patch("requests.get", return_value=resp) as rg, patch("time.sleep"):
            w = p.get_ohlc_window("KRW-BTC", "2026-07-19")
        assert rg.call_count == 2  # 200 + 60 -> 2회 페이지네이션
        assert w is not None
        assert len(w) == 250
        assert list(w.columns) == ["High", "Low", "Close"]
        assert w.index.max() < pd.Timestamp("2026-07-19")

    def test_failure_returns_none(self):
        logger = MagicMock()
        p = UpbitMarketDataProvider(logger, window_size=100)
        with patch("requests.get", side_effect=RuntimeError("network")):
            assert p.get_ohlc_window("KRW-BTC", "2026-07-19") is None
