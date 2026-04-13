# src/backtest/cache.py
import pandas as pd
import yfinance as yf
from pathlib import Path
from typing import List, Optional
from src.core.interfaces import ILogger

CACHE_DIR = Path(__file__).parent / "cache"


class _NullLogger:
    """로거가 없을 때 사용하는 아무것도 하지 않는 로거"""
    def info(self, msg: str) -> None: pass
    def warning(self, msg: str) -> None: pass
    def error(self, msg: str) -> None: pass


class BacktestDataCache:
    """
    yfinance로 종가 데이터를 다운로드하고 Parquet으로 캐시한다.

    요청할 때마다 기존 캐시를 삭제하고 전체 재다운로드한다.
    증분 merge 없이 항상 깨끗한 데이터를 보장한다.

    저장 위치: src/backtest/cache/close.parquet
    """

    def __init__(self, cache_dir: Path = CACHE_DIR, logger: Optional[ILogger] = None):
        self.cache_dir = Path(cache_dir)
        self.close_path = self.cache_dir / "close.parquet"
        self._logger: ILogger = logger if logger is not None else _NullLogger()

    def get_data(
        self, tickers: List[str], start_date: str, end_date: str
    ) -> pd.DataFrame:
        """
        기존 캐시를 삭제하고 전체 티커를 새로 다운로드한다.

        1. 기존 캐시 삭제
        2. 전 티커 일괄 다운로드 (auto_adjust=False)
        3. Close 컬럼만 추출
        4. NaN을 이전 값으로 채움 (ffill)
        5. 저장 후 반환

        Returns:
            Close 가격 DataFrame (index=DatetimeIndex, columns=tickers)
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 1. 기존 캐시 삭제
        self.clear()

        # 2. 전체 다운로드
        self._logger.info(f"종가 다운로드 시작: {tickers} ({start_date} ~ {end_date})")
        close_df = self._download_close(tickers, start_date, end_date)

        if close_df is None or close_df.empty:
            raise ValueError(f"종가 다운로드 실패: {tickers} ({start_date} ~ {end_date})")

        # 3. NaN → 이전 값으로 채움
        nan_before = int(close_df.isna().sum().sum())
        if nan_before > 0:
            close_df = close_df.ffill()
            nan_after = int(close_df.isna().sum().sum())
            filled = nan_before - nan_after
            self._logger.info(f"NaN ffill 처리: {filled}개 채움, 잔여 {nan_after}개")

        # 4. 저장
        self._save_parquet(close_df, self.close_path)

        return close_df

    def _download_close(
        self, tickers: List[str], start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """yfinance로 종가를 다운로드한다."""
        try:
            df = yf.download(
                tickers, start=start_date, end=end_date,
                auto_adjust=False, progress=False,
            )
            if df is None or df.empty:
                return None

            # 단일 티커 + SingleIndex → 정규화
            if not isinstance(df.columns, pd.MultiIndex) and len(tickers) == 1:
                # SingleIndex DataFrame: 컬럼이 [Open, High, Low, Close, ...]
                if "Close" in df.columns:
                    close_df = df[["Close"]].copy()
                    close_df.columns = tickers
                    return close_df
                return None

            # MultiIndex DataFrame: ('Close', ticker) 형태
            if "Close" not in df.columns.get_level_values(0):
                return None
            close_df = df["Close"]

            # Series → DataFrame 변환 (단일 티커의 경우)
            if isinstance(close_df, pd.Series):
                close_df = close_df.to_frame(name=tickers[0])

            return close_df
        except Exception as e:
            self._logger.warning(f"종가 다운로드 실패: {e}")
            return None

    def _save_parquet(self, df: pd.DataFrame, path: Path):
        if df is not None and not df.empty:
            try:
                df.to_parquet(path)
            except Exception as e:
                self._logger.warning(f"캐시 저장 실패 ({path.name}): {e}")

    def clear(self):
        """캐시 파일 삭제"""
        if self.close_path.exists():
            self.close_path.unlink()
            self._logger.info("기존 캐시 삭제 완료")
