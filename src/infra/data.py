# src/infra/data.py
"""시세 데이터 로더.

- YFinanceLoader: 현재가 보조 조회 (선택적)
- YFinanceMarketDataProvider / UpbitMarketDataProvider:
  라이브 레짐 판정용 과거 일봉(OHLC) 제공자 (IMarketDataProvider 구현)
"""
from typing import Any, Optional

from src.core.interfaces import IMarketDataProvider


class YFinanceMarketDataProvider(IMarketDataProvider):
    """yfinance 일봉으로 레짐 지표용 OHLC 윈도우를 제공한다 (domestic/overseas).

    사이클당 종목별 1회 다운로드 후 메모리 캐시. 오늘(미완성) 봉은 제외한다.
    """

    def __init__(self, logger, window_size: int = 260):
        self.logger = logger
        self.window_size = window_size
        self._cache: dict = {}

    def get_ohlc_window(self, ticker: str, asof: Any) -> Optional[Any]:
        import pandas as pd

        cached = self._cache.get(ticker)
        if cached is None:
            cached = self._download(ticker)
            self._cache[ticker] = cached if cached is not None else "failed"
        if cached is None or isinstance(cached, str):
            return None

        asof_ts = pd.Timestamp(asof).normalize()
        window = cached.loc[cached.index < asof_ts].tail(self.window_size)
        return window if len(window) > 0 else None

    def _download(self, ticker: str):
        try:
            import yfinance as yf
            from src.utils.ticker_reader import to_yfinance_ticker

            yf_ticker = to_yfinance_ticker(ticker)
            # 캘린더일 여유 포함 (거래일 window_size 확보용 약 1.6배)
            period_days = int(self.window_size * 1.6) + 30
            df = yf.download(
                yf_ticker, period=f"{period_days}d",
                auto_adjust=False, progress=False,
            )
            if df is None or df.empty:
                self.logger.warning(f"[MarketData] {ticker}: yfinance 데이터 없음")
                return None
            import pandas as pd
            if isinstance(df.columns, pd.MultiIndex):
                df = df.xs(yf_ticker, axis=1, level=1)
            out = df[["High", "Low", "Close"]].dropna()
            out.index = pd.to_datetime(out.index).normalize()
            return out
        except Exception as e:
            self.logger.error(f"[MarketData] {ticker}: OHLC 조회 실패 - {e}")
            return None


class UpbitMarketDataProvider(IMarketDataProvider):
    """업비트 공개 일봉 캔들로 레짐 지표용 OHLC 윈도우를 제공한다 (crypto).

    인증 불필요한 공개 REST(/v1/candles/days) 사용. 요청당 최대 200봉이므로
    페이지네이션으로 window_size만큼 수집한다. 오늘(미완성) 봉은 제외한다.
    """

    API_URL = "https://api.upbit.com/v1/candles/days"

    def __init__(self, logger, window_size: int = 260):
        self.logger = logger
        self.window_size = window_size
        self._cache: dict = {}

    def get_ohlc_window(self, ticker: str, asof: Any) -> Optional[Any]:
        import pandas as pd

        cached = self._cache.get(ticker)
        if cached is None:
            cached = self._download(ticker)
            self._cache[ticker] = cached if cached is not None else "failed"
        if cached is None or isinstance(cached, str):
            return None

        asof_ts = pd.Timestamp(asof).normalize()
        window = cached.loc[cached.index < asof_ts].tail(self.window_size)
        return window if len(window) > 0 else None

    def _download(self, ticker: str):
        try:
            import time
            import requests
            import pandas as pd

            rows: list = []
            to: Optional[str] = None
            remaining = self.window_size + 10  # 오늘 봉 제외 여유분
            while remaining > 0:
                params = {"market": ticker, "count": min(200, remaining)}
                if to:
                    params["to"] = to
                resp = requests.get(self.API_URL, params=params, timeout=10)
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                rows.extend(batch)
                remaining -= len(batch)
                if len(batch) < params["count"]:
                    break  # 상장 초기 등 데이터 소진
                to = batch[-1]["candle_date_time_utc"]
                time.sleep(0.15)  # 공개 API 유량 예의

            if not rows:
                self.logger.warning(f"[MarketData] {ticker}: 업비트 캔들 없음")
                return None

            df = pd.DataFrame([
                {
                    "date": r["candle_date_time_kst"][:10],
                    "High": float(r["high_price"]),
                    "Low": float(r["low_price"]),
                    "Close": float(r["trade_price"]),
                }
                for r in rows
            ])
            df["date"] = pd.to_datetime(df["date"])
            df = df.drop_duplicates("date").set_index("date").sort_index()
            return df
        except Exception as e:
            self.logger.error(f"[MarketData] {ticker}: 업비트 캔들 조회 실패 - {e}")
            return None


class YFinanceLoader:
    """Yahoo Finance에서 시세 데이터를 조회한다.

    MagicSplit에서는 현재가 조회를 주로 브로커 API(fetch_current_prices)로 수행하므로,
    이 클래스는 보조적 용도로만 사용된다.
    """

    def __init__(self, logger):
        self.logger = logger

    def fetch_current_price(self, ticker: str) -> float:
        """단일 종목의 현재가를 조회한다."""
        try:
            import yfinance as yf
            data = yf.download(ticker, period="1d", auto_adjust=False, progress=False)
            if data is None or data.empty:
                raise ValueError(f"No data for {ticker}")
            import pandas as pd
            if isinstance(data.columns, pd.MultiIndex):
                return float(data.xs('Close', axis=1, level=0).iloc[-1, 0])
            return float(data['Close'].iloc[-1])
        except Exception as e:
            self.logger.error(f"[Data] Error fetching {ticker}: {e}")
            return 0.0
