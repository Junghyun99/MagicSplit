# src/core/logic/regime.py
"""시장 레짐(횡보/상승/하락) 판정용 순수 함수 모듈.

OHLC 시계열(pandas DataFrame)에 대해 이동평균/ADX/ATR 등을 계산하고
종목의 현재 국면을 분류한다. 외부 의존성(TA-Lib 등) 없이 pandas/numpy로 계산한다.

이 모듈은 상태를 갖지 않는 스냅샷 리더다. 말단 bar 기준의 RegimeReading을 반환하며,
레짐 전이의 히스테리시스 같은 시간축 상태 관리는 호출부(평가기)가 담당한다.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class Regime(str, Enum):
    SIDEWAYS = "sideways"
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    UNKNOWN = "unknown"  # 히스토리 부족 등으로 판정 불가

    def __str__(self):
        return self.value


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """TR = max(H-L, |H-Cprev|, |L-Cprev|)."""
    prev_close = df["Close"].shift(1)
    ranges = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder smoothing(ewm alpha=1/period)을 적용한 ATR."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX. +DM/-DM -> Wilder 평활 -> +DI/-DI -> DX -> ADX 순으로 계산."""
    high = df["High"]
    low = df["Low"]
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr = true_range(df)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    # 변동이 전혀 없는 횡보/거래정지 구간은 atr_=0 -> 0으로 나누지 않도록 NaN 처리 후 0으로 복구
    atr_safe = atr_.replace(0, np.nan)
    plus_di = (100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_safe).fillna(0.0)
    minus_di = (100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_safe).fillna(0.0)

    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    # 방향성 움직임이 전혀 없으면(0/0) DX=0 으로 본다.
    dx = dx.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def swing_high(df: pd.DataFrame, lookback: int = 10) -> float:
    """최근 lookback 봉의 고가 최대값."""
    return float(df["High"].tail(lookback).max())


@dataclass(frozen=True)
class RegimeReading:
    regime: Regime
    close: float
    prev_close: float
    ema20: float
    sma50: float
    sma200: float
    adx: float
    atr: float
    aligned_up: bool
    swing_high: float
    chandelier_stop: float
    n_bars: int


def _is_nan(*values: float) -> bool:
    return any(v is None or (isinstance(v, float) and np.isnan(v)) for v in values)


def classify(
    df: pd.DataFrame,
    *,
    adx_trend_threshold: float = 25.0,
    adx_range_threshold: float = 20.0,
    chandelier_k: float = 3.0,
    chandelier_lookback: int = 22,
    swing_lookback: int = 10,
    adx_period: int = 14,
    atr_period: int = 14,
    min_bars: int = 200,
) -> RegimeReading:
    """말단 bar 기준으로 레짐을 분류한다.

    - 히스토리가 부족(min_bars 미만)하거나 말단 이동평균이 NaN이면 UNKNOWN.
    - UPTREND  : close>sma200 and ema20>sma50>sma200 (정렬 상승) and adx>=adx_trend.
    - DOWNTREND: 역정렬 and adx>=adx_trend (감지만, 호출부에서 no-op 가능).
    - 그 외    : SIDEWAYS (히스테리시스 밴드 range..trend 포함).
    """
    n_bars = int(len(df))
    close = float(df["Close"].iloc[-1]) if n_bars >= 1 else float("nan")
    prev_close = float(df["Close"].iloc[-2]) if n_bars >= 2 else close

    if n_bars < min_bars:
        return RegimeReading(
            regime=Regime.UNKNOWN, close=close, prev_close=prev_close,
            ema20=float("nan"), sma50=float("nan"), sma200=float("nan"),
            adx=float("nan"), atr=float("nan"), aligned_up=False,
            swing_high=float("nan"), chandelier_stop=float("nan"), n_bars=n_bars,
        )

    ema20 = float(ema(df["Close"], 20).iloc[-1])
    sma50 = float(sma(df["Close"], 50).iloc[-1])
    sma200 = float(sma(df["Close"], 200).iloc[-1])
    adx_val = float(adx(df, adx_period).iloc[-1])
    atr_val = float(atr(df, atr_period).iloc[-1])
    sh = swing_high(df, swing_lookback)
    highest_high = float(df["High"].tail(chandelier_lookback).max())
    chandelier_stop = highest_high - chandelier_k * atr_val

    if _is_nan(ema20, sma50, sma200, adx_val, atr_val):
        return RegimeReading(
            regime=Regime.UNKNOWN, close=close, prev_close=prev_close,
            ema20=ema20, sma50=sma50, sma200=sma200, adx=adx_val, atr=atr_val,
            aligned_up=False, swing_high=sh, chandelier_stop=chandelier_stop,
            n_bars=n_bars,
        )

    aligned_up = close > sma200 and ema20 > sma50 > sma200
    aligned_down = close < sma200 and ema20 < sma50 < sma200
    trending = adx_val >= adx_trend_threshold

    if aligned_up and trending:
        regime = Regime.UPTREND
    elif aligned_down and trending:
        regime = Regime.DOWNTREND
    else:
        regime = Regime.SIDEWAYS

    return RegimeReading(
        regime=regime, close=close, prev_close=prev_close,
        ema20=ema20, sma50=sma50, sma200=sma200, adx=adx_val, atr=atr_val,
        aligned_up=aligned_up, swing_high=sh, chandelier_stop=chandelier_stop,
        n_bars=n_bars,
    )
