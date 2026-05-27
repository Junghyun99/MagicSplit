# src/backtest/cache.py
import pandas as pd
import yfinance as yf
from pathlib import Path
from typing import List, Optional
from src.core.interfaces import ILogger
from src.utils.ticker_reader import to_yfinance_ticker

CACHE_DIR = Path(__file__).parent / "cache"


class _NullLogger:
    """로거가 없을 때 사용하는 아무것도 하지 않는 로거"""
    def info(self, msg: str) -> None: pass
    def warning(self, msg: str) -> None: pass
    def error(self, msg: str) -> None: pass


class BacktestDataCache:
    """
    yfinance로 OHLC(High/Low/Close) 데이터를 다운로드하고 Parquet으로 캐시한다.

    캐시 파일이 존재하고 요청 기간·티커를 모두 포함하면 다운로드를 건너뛴다.
    캐시가 없거나 범위가 부족하면 전체 재다운로드 후 저장한다.

    저장 위치: src/backtest/cache/ohlc.parquet
    """

    def __init__(self, cache_dir: Path = CACHE_DIR, logger: Optional[ILogger] = None):
        self.cache_dir = Path(cache_dir)
        self.ohlc_path = self.cache_dir / "ohlc.parquet"
        self._logger: ILogger = logger if logger is not None else _NullLogger()

    def get_ohlc(
        self, tickers: List[str], start_date: str, end_date: str
    ) -> pd.DataFrame:
        """OHLC(High/Low/Close) 데이터를 반환한다. 레짐 지표(MA/ADX/ATR) 계산용.

        반환 형태는 컬럼이 MultiIndex (field, ticker)인 DataFrame이며
        field 는 'High'/'Low'/'Close'. 저장 위치는 src/backtest/cache/ohlc.parquet.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        cached = self._try_load_ohlc_cache(tickers, start_date, end_date)
        if cached is not None:
            return cached

        if self.ohlc_path.exists():
            self.ohlc_path.unlink()
        self._logger.info(f"OHLC 다운로드 시작: {tickers} ({start_date} ~ {end_date})")
        ohlc_df = self._download_ohlc(tickers, start_date, end_date)

        if ohlc_df is None or ohlc_df.empty:
            raise ValueError(f"OHLC 다운로드 실패: {tickers} ({start_date} ~ {end_date})")

        # 상장일 기준 시작일 조정 (Close 서브프레임 기준으로 판정)
        close_sub = ohlc_df["Close"]
        trimmed_close = self._trim_to_latest_ipo(close_sub, tickers)
        ohlc_df = ohlc_df.loc[trimmed_close.index.min():]

        # 남은 NaN -> ffill
        nan_before = int(ohlc_df.isna().sum().sum())
        if nan_before > 0:
            ohlc_df = ohlc_df.ffill()
            nan_after = int(ohlc_df.isna().sum().sum())
            self._logger.info(
                f"OHLC NaN ffill: {nan_before - nan_after}개 채움, 잔여 {nan_after}개"
            )

        self._save_parquet(ohlc_df, self.ohlc_path)
        return ohlc_df

    def _try_load_ohlc_cache(
        self, tickers: List[str], start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """ohlc.parquet이 요청 기간·티커를 모두 포함하면 반환, 아니면 None."""
        if not self.ohlc_path.exists():
            return None
        try:
            cached_df = pd.read_parquet(self.ohlc_path)
        except Exception as e:
            self._logger.warning(f"OHLC 캐시 읽기 실패, 재다운로드합니다: {e}")
            return None

        if not isinstance(cached_df.columns, pd.MultiIndex):
            return None
        cached_tickers = set(cached_df.columns.get_level_values(1))
        missing = [t for t in tickers if t not in cached_tickers]
        if missing:
            self._logger.info(f"OHLC 캐시에 누락된 티커 {missing} -> 재다운로드")
            return None

        cache_start_ts = cached_df.index.min()
        cache_end_ts = cached_df.index.max()
        slack = pd.Timedelta(days=5)
        if (cache_start_ts > pd.Timestamp(start_date) + slack
                or cache_end_ts < pd.Timestamp(end_date) - slack):
            self._logger.info(
                f"OHLC 캐시 범위 부족 ({cache_start_ts.date()}~{cache_end_ts.date()}) "
                f"-> 재다운로드"
            )
            return None

        self._logger.info(
            f"✅ OHLC 캐시 히트: {self.ohlc_path.name} "
            f"({cache_start_ts.date()}~{cache_end_ts.date()})"
        )
        mask = cached_df.columns.get_level_values(1).isin(tickers)
        return cached_df.loc[start_date:end_date, mask]

    def _download_ohlc(
        self, tickers: List[str], start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """yfinance로 High/Low/Close를 다운로드한다. 컬럼은 (field, MagicSplit표준티커)."""
        fields = ["High", "Low", "Close"]
        yf_to_std = {to_yfinance_ticker(t): t for t in tickers}
        yf_tickers = list(yf_to_std.keys())
        try:
            df = yf.download(
                yf_tickers, start=start_date, end=end_date,
                auto_adjust=False, progress=False,
            )
            if df is None or df.empty:
                return None

            if not isinstance(df.columns, pd.MultiIndex):
                # 단일 티커: 컬럼이 필드명(SingleIndex)
                if any(f not in df.columns for f in fields):
                    return None
                sub = df[fields].copy()
                std = yf_to_std[yf_tickers[0]]
                sub.columns = pd.MultiIndex.from_product([fields, [std]])
                return sub

            level0 = df.columns.get_level_values(0)
            if any(f not in level0 for f in fields):
                return None
            sub = df[fields]
            sub = sub.rename(columns=yf_to_std, level=1)
            return sub
        except Exception as e:
            self._logger.warning(f"OHLC 다운로드 실패: {e}")
            return None



    # ── 상장일 조정 ────────────────────────────────────────

    def _trim_to_latest_ipo(self, close_df: pd.DataFrame, tickers: List[str]) -> pd.DataFrame:
        """
        가장 늦은 상장 티커의 첫 유효일을 기준으로 DataFrame 시작일을 조정한다.
        조정이 발생하면 원래 요청 시작일과 조정된 시작일을 로그에 출력한다.
        """
        latest_ipo: Optional[pd.Timestamp] = None
        latest_ticker: Optional[str] = None

        for ticker in tickers:
            if ticker not in close_df.columns:
                continue
            first_valid = close_df[ticker].first_valid_index()
            if first_valid is not None and (latest_ipo is None or first_valid > latest_ipo):
                latest_ipo = first_valid
                latest_ticker = ticker

        if latest_ipo is None:
            return close_df

        original_start = close_df.index.min()
        if latest_ipo > original_start:
            self._logger.warning(
                f"⚠️ 상장일 조정: {latest_ticker} 첫 거래일 {latest_ipo.date()} "
                f"(요청 시작일 {original_start.date()} -> 조정 후 시작일 {latest_ipo.date()})"
            )
            close_df = close_df.loc[latest_ipo:]
        else:
            self._logger.info(f"모든 티커 데이터 정상 (시작일: {original_start.date()})")

        return close_df



    def _save_parquet(self, df: pd.DataFrame, path: Path):
        if df is not None and not df.empty:
            try:
                df.to_parquet(path)
            except Exception as e:
                self._logger.warning(f"캐시 저장 실패 ({path.name}): {e}")

    def clear(self):
        """캐시 파일 삭제"""
        if self.ohlc_path.exists():
            self.ohlc_path.unlink()
            self._logger.info("OHLC 캐시 삭제 완료")
