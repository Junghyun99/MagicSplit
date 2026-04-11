# src/infra/data.py
"""시세 데이터 로더 (선택적 — MagicSplit은 브로커 API로 현재가 조회 가능)."""


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
