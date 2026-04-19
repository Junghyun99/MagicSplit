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
    yfinance로 종가(Close) 데이터를 다운로드하고 Parquet으로 캐시한다.

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
        기존 캐시를 삭제하고 전체 티커의 종가를 새로 다운로드한다.

        1. 기존 캐시 삭제
        2. 전 티커 일괄 다운로드 (auto_adjust=False, Close만 추출)
        3. 가장 늦은 상장 티커 기준으로 시작일 조정 (로그 출력)
        4. 남은 NaN을 이전 값으로 채움 (ffill)
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

        # 3. 상장일 기준 시작일 조정
        close_df = self._trim_to_latest_ipo(close_df, tickers)

        # 4. 남은 NaN → 이전 값으로 채움
        nan_before = int(close_df.isna().sum().sum())
        if nan_before > 0:
            close_df = close_df.ffill()
            nan_after = int(close_df.isna().sum().sum())
            filled = nan_before - nan_after
            self._logger.info(f"NaN ffill 처리: {filled}개 채움, 잔여 {nan_after}개")

        # 5. 저장
        self._save_parquet(close_df, self.close_path)

        return close_df

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
                f"(요청 시작일 {original_start.date()} → 조정 후 시작일 {latest_ipo.date()})"
            )
            close_df = close_df.loc[latest_ipo:]
        else:
            self._logger.info(f"모든 티커 데이터 정상 (시작일: {original_start.date()})")

        return close_df

    # ── 다운로드 ───────────────────────────────────────────

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
