# src/core/logic/chart_builder.py
"""종목별 판정 차트용 JSON을 조립하는 순수 함수 모듈.

레짐 판정선(회귀 채널/EMA)과 분할매매 그리드(차수 매수가/익절선/트레일링 스톱)를
한 장에 겹쳐 볼 수 있도록 프런트가 그대로 렌더할 수 있는 형태로 만든다.

지표선은 저장된 값을 읽는 게 아니라 regime.classify_series로 매번 재계산한다.
분류기를 라이브와 공유하므로 차트와 실제 판정이 어긋날 수 없다.

주가 시계열은 종가(Close) 라인을 쓴다. 시세 제공자가 High/Low/Close만 받아
Open이 없기도 하지만, 회귀 채널과 EMA가 실제로 종가만 소비하므로 판정 근거를
그대로 보여주는 표현이기도 하다.
"""
import math
from typing import List, Optional

import numpy as np

from src.core.logic.regime import classify_series, linreg_channel
from src.core.models import PositionLot, StockRule

# 채널 모드에서 내보낼 지표 컬럼 (RegimeReading 필드명 -> 출력 컬럼명)
_CHANNEL_COLS = [
    ("channel_mid", "mid"),
    ("channel_support", "support"),
    ("channel_resistance", "resistance"),
    ("ema20", "ema20"),
]
# ma_adx 모드에서 내보낼 지표 컬럼
_MA_ADX_COLS = [
    ("ema20", "ema20"),
    ("sma50", "sma50"),
    ("sma200", "sma200"),
    ("chandelier_stop", "chandelier"),
]


def _round(value) -> Optional[float]:
    """가격 자릿수를 적당히 줄인다 (코인처럼 큰 값은 소수 무의미)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    if abs(v) >= 1000:
        return round(v, 1)
    if abs(v) >= 1:
        return round(v, 2)
    return round(v, 6)  # 저가 코인은 유효숫자 확보 필요


def _price_line(label: str, value, kind: str) -> Optional[dict]:
    """수평 기준선 항목을 만든다. 값이 유효하지 않으면 None."""
    rounded = _round(value)
    if rounded is None:
        return None
    return {"label": label, "value": rounded, "kind": kind}


def build_price_lines(
    rule: StockRule,
    lots: List[PositionLot],
    last_sell_price: Optional[float] = None,
) -> List[dict]:
    """분할매매 그리드의 수평 기준선 목록을 만든다.

    차수별 매수가와 그 차수의 익절/트레일링 스톱, 그리고 다음 차수 추가매수선을
    함께 낸다. kind는 프런트가 색/선 스타일을 고르는 데 쓴다.
    """
    lines: List[dict] = []
    if not lots:
        return lines

    for lot in sorted(lots, key=lambda l: l.level):
        entry = _price_line(f"Lv{lot.level} 매수가", lot.buy_price, "buy")
        if entry:
            lines.append(entry)

        sell_thr = rule.sell_threshold_at(lot.level)
        trailing_drop = rule.trailing_drop_at(lot.level)
        target = lot.buy_price * (1 + sell_thr / 100)
        label = (
            f"Lv{lot.level} 트레일링 활성화선" if trailing_drop is not None
            else f"Lv{lot.level} 익절선"
        )
        item = _price_line(label, target, "sell")
        if item:
            lines.append(item)

        # 트레일링 활성화된 lot만 실제 스톱가를 그린다.
        if trailing_drop is not None and lot.trailing_highest_price:
            stop = lot.trailing_highest_price * (1 - trailing_drop / 100)
            item = _price_line(f"Lv{lot.level} 트레일링 스톱", stop, "stop")
            if item:
                lines.append(item)

    # 다음 차수 추가매수선. 동적 재매수 기준(직전 매도가가 더 높으면 그쪽)을 그대로 반영.
    last_lot = max(lots, key=lambda l: l.level)
    if last_lot.level < rule.max_lots:
        reference = last_lot.buy_price
        dynamic = bool(last_sell_price and last_sell_price > reference)
        if dynamic:
            reference = last_sell_price
        buy_thr = rule.buy_threshold_at(last_lot.level)
        label = (
            f"Lv{last_lot.level + 1} 추가매수선(동적)" if dynamic
            else f"Lv{last_lot.level + 1} 추가매수선"
        )
        item = _price_line(label, reference * (1 + buy_thr / 100), "add")
        if item:
            lines.append(item)

    return lines


def build_state_lines(rule: StockRule, regime_st: dict) -> List[dict]:
    """레짐 상태에서 파생되는 수평 기준선(추종 데드라인 등)을 만든다."""
    lines: List[dict] = []
    lock = (regime_st or {}).get("trailing_lock")
    if lock:
        item = _price_line("추종 데드라인 기준가", lock.get("lock_price"), "lock")
        if item:
            lines.append(item)
        drop = lock.get("drop_pct")
        if lock.get("lock_price") and drop is not None:
            stop = float(lock["lock_price"]) * (1 - float(drop) / 100)
            item = _price_line("추종 데드라인 청산선", stop, "stop")
            if item:
                lines.append(item)
    return lines


def build_regime_bands(readings: List[tuple]) -> List[dict]:
    """봉별 레짐 판정을 연속 구간(run)으로 압축한다.

    프런트가 배경 밴드를 그릴 때 필요한 건 구간 경계뿐이라, 날짜마다 레짐을
    싣는 것보다 구간으로 접는 편이 파일도 작고 렌더도 단순하다.

    Args:
        readings: [(date, RegimeReading), ...] 오름차순

    Returns:
        [{"from": 시작일, "to": 종료일, "regime": 레짐명}, ...]
    """
    bands: List[dict] = []
    for date, reading in readings:
        name = str(reading.regime)
        if bands and bands[-1]["regime"] == name:
            bands[-1]["to"] = date
        else:
            bands.append({"from": date, "to": date, "regime": name})
    return bands


def build_current_channel(
    rule: StockRule,
    ohlc_window,
    asof: Optional[str] = None,
) -> Optional[dict]:
    """오늘자 회귀 채널 1개를 창 전체에 펼친 '직선' 오버레이를 만든다.

    rows에 실린 채널선은 봉마다 창을 밀며 다시 회귀한 롤링 값이라 곡선이 된다
    (그 날 봇이 계산했을 값 = 과거 판정 검증용). 반면 이건 오늘 시점의 회귀
    하나를 lookback 구간에 그대로 펼친 것으로, 지금 채널이 어떤 기울기로 서
    있는지 보는 용도다. 로그 공간 직선이라 가격축에서는 완만한 지수곡선이다.

    마지막 점은 x=lookback(오늘)으로 외삽한 값이라 classify_channel이 오늘
    판정에 쓴 channel_mid/support/resistance와 정확히 일치한다.

    Returns:
        {"lookback", "stddev_k", "slope_pct", "cols", "rows"} 또는 None
        (채널 모드가 아니거나 히스토리 부족)
    """
    if not rule.regime_enabled or rule.regime_algo != "channel":
        return None
    if ohlc_window is None:
        return None

    lookback = rule.channel_lookback
    window = ohlc_window["Close"].tail(lookback)
    if len(window) < lookback:
        return None

    values = window.to_numpy(dtype=float)
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        return None

    m, c, sigma = linreg_channel(np.log(values))
    offset = rule.channel_stddev_k * sigma

    rows: List[list] = []
    for i in range(lookback):
        mid = float(np.exp(m * i + c))
        rows.append([
            _index_date(window, i),
            _round(mid),
            _round(mid * math.exp(-offset)),
            _round(mid * math.exp(offset)),
        ])

    # 오늘(x=lookback) 외삽 지점. rows의 마지막 봉은 어제이므로 한 칸 더 붙여야
    # 선의 끝이 오늘 판정에 실제로 쓰인 값과 맞는다.
    last_bar_date = rows[-1][0] if rows else None
    if asof and last_bar_date and asof > last_bar_date:
        mid = float(np.exp(m * lookback + c))
        rows.append([
            asof,
            _round(mid),
            _round(mid * math.exp(-offset)),
            _round(mid * math.exp(offset)),
        ])

    return {
        "lookback": lookback,
        "stddev_k": rule.channel_stddev_k,
        "slope_pct": round(float((np.exp(m * (lookback - 1)) - 1.0) * 100.0), 2),
        "cols": ["date", "mid", "support", "resistance"],
        "rows": rows,
    }


def build_chart_series(
    rule: StockRule,
    ohlc_window,
    classifier,
    lots: List[PositionLot],
    current_price: float,
    regime_st: Optional[dict] = None,
    last_sell_price: Optional[float] = None,
    markers: Optional[List[dict]] = None,
    asof: Optional[str] = None,
    max_points: int = 200,
) -> Optional[dict]:
    """한 종목의 차트 JSON을 조립한다.

    Args:
        rule: 종목 규칙
        ohlc_window: 시세 제공자가 준 OHLC DataFrame (오늘 봉 제외)
        classifier: RegimeReading을 반환하는 콜러블 (classify_for_rule 부분적용)
        lots: 해당 종목 보유 lot
        current_price: 브로커 실시간 현재가
        regime_st: 해당 종목 레짐 상태
        last_sell_price: 직전 전량청산 매도가
        markers: 매매 마커 [{date, action, reason}]
        asof: 기준일(YYYY-MM-DD)
        max_points: 차트에 낼 최대 봉 수

    Returns:
        차트 JSON dict. 시세가 없어 그릴 수 없으면 None.
    """
    if ohlc_window is None or len(ohlc_window) == 0:
        return None

    regime_st = regime_st or {}
    use_channel = rule.regime_algo == "channel"
    indicator_cols = _CHANNEL_COLS if use_channel else _MA_ADX_COLS

    rows: List[list] = []
    readings: List[tuple] = []
    if rule.regime_enabled:
        readings = classify_series(ohlc_window, classifier, max_points=max_points)
        for date, reading in readings:
            row = [date, _round(reading.close)]
            row.extend(_round(getattr(reading, field)) for field, _ in indicator_cols)
            rows.append(row)

    # 레짐 미사용(또는 히스토리 부족)이면 종가만이라도 그린다.
    if not rows:
        tail = ohlc_window.tail(max_points)
        for pos in range(len(tail)):
            rows.append([
                _index_date(tail, pos),
                _round(tail["Close"].iloc[pos]),
            ])
        cols = ["date", "close"]
    else:
        cols = ["date", "close"] + [name for _, name in indicator_cols]

    lines = build_price_lines(rule, lots, last_sell_price)
    lines.extend(build_state_lines(rule, regime_st))
    current = _price_line("현재가", current_price, "current")
    if current:
        lines.append(current)

    return {
        "ticker": rule.ticker,
        "market_type": rule.market_type,
        "algo": rule.regime_algo if rule.regime_enabled else "off",
        "regime_enabled": rule.regime_enabled,
        "asof": asof,
        "current_price": _round(current_price),
        "cols": cols,
        "rows": rows,
        "current_channel": build_current_channel(rule, ohlc_window, asof),
        "regime_bands": build_regime_bands(readings),
        "lines": lines,
        "markers": markers or [],
        "state": _public_state(regime_st),
        "params": _public_params(rule),
    }


def _index_date(df, pos: int) -> str:
    """DataFrame 인덱스의 pos번째 값을 YYYY-MM-DD 문자열로 반환한다."""
    import pandas as pd

    try:
        return pd.Timestamp(df.index[pos]).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(df.index[pos])[:10]


def _public_state(regime_st: dict) -> dict:
    """차트 렌더에 필요한 상태만 추린다 (내부 카운터 제외)."""
    lock = regime_st.get("trailing_lock")
    return {
        "regime": regime_st.get("regime") or "sideways",
        "downtrend": regime_st.get("downtrend"),
        "trailing_lock": bool(lock),
        "post_liquidation": bool(regime_st.get("post_liquidation")),
        "reentry_gate": regime_st.get("post_liquidation_reentry_gate"),
        "adds": regime_st.get("adds", 0),
        "breakdown_count": len(regime_st.get("breakdown_days") or []),
    }


def _public_params(rule: StockRule) -> dict:
    """차트 범례/툴팁에 표시할 규칙 파라미터."""
    return {
        "channel_lookback": rule.channel_lookback,
        "channel_stddev_k": rule.channel_stddev_k,
        "channel_slope_band_pct": rule.channel_slope_band_pct,
        "channel_breakdown_tolerance_pct": rule.channel_breakdown_tolerance_pct,
        "uptrend_pullback_band_pct": rule.uptrend_pullback_band_pct,
        "trendbreak_partial_sell_pct": rule.trendbreak_partial_sell_pct,
        "trendbreak_trailing_drop_pct": rule.trendbreak_trailing_drop_pct,
        "max_lots": rule.max_lots,
    }
