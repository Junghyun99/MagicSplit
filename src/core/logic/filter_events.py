"""전략적 매수 차단 신호를 구조화된 에피소드 이벤트로 변환한다."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

from src.core.models import SplitSignal


_FILTER_REASON_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("장기 하락 확정", "long_downtrend"),
    ("단기 하락", "short_downtrend"),
    ("DOWNTREND 확정", "confirmed_downtrend"),
    ("전량 청산 후 재진입 대기", "reentry_gate"),
    ("이탈 청산 후 재진입 대기", "legacy_reentry_gate"),
    ("단계형 반등 대기", "staged_rebound_wait"),
    ("반등형 추세 대기", "rebound_wait"),
    ("추세 전용 대기", "trend_alignment_wait"),
    ("추세 데이터 부족", "trend_data_insufficient"),
)


def classify_filter_reason(reason: str) -> Optional[str]:
    """사람용 사유 문자열을 안정적인 분석용 코드로 변환한다."""
    for prefix, code in _FILTER_REASON_PREFIXES:
        if reason.startswith(prefix):
            return code
    return None


def _regime_value(value) -> str:
    if value is None:
        return "unknown"
    raw = getattr(value, "value", value)
    return str(raw).lower()


def _context(ticker: str, prices: dict, regime_state: dict) -> dict:
    state = regime_state.get(ticker, {}) if regime_state else {}
    price = prices.get(ticker) if prices else None
    try:
        normalized_price = round(float(price), 6) if price is not None else None
    except (TypeError, ValueError):
        normalized_price = None
    return {
        "long_regime": _regime_value(state.get("long_trend")),
        "short_regime": _regime_value(state.get("short_trend")),
        "price": normalized_price,
    }


def update_filter_episodes(
    previous: Dict[str, dict],
    signals: Iterable[SplitSignal],
    date: str,
    prices: dict,
    regime_state: dict,
    evaluated_tickers: Optional[Set[str]] = None,
) -> Tuple[List[dict], Dict[str, dict]]:
    """현재 차단 신호와 직전 활성 상태를 비교해 START/END 이벤트를 만든다.

    같은 사유가 여러 날 이어져도 START는 한 번만 기록한다. 차단 코드가
    바뀌면 기존 에피소드를 END한 뒤 같은 날짜에 새 에피소드를 START한다.
    """
    day = date[:10]
    previous = previous or {}
    current: Dict[str, dict] = {}
    for signal in signals:
        if not signal.is_blocked:
            continue
        reason_code = classify_filter_reason(signal.reason)
        if reason_code is None:
            continue
        current.setdefault(signal.ticker, {
            "reason_code": reason_code,
            "reason": signal.reason,
        })

    events: List[dict] = []
    updated: Dict[str, dict] = {}
    for ticker in sorted(set(previous) | set(current)):
        before = previous.get(ticker)
        now = current.get(ticker)
        if evaluated_tickers is not None and ticker not in evaluated_tickers:
            if before:
                updated[ticker] = dict(before)
            continue
        context = _context(ticker, prices, regime_state)

        if before and (
            now is None or now["reason_code"] != before.get("reason_code")
        ):
            events.append({
                "date": day,
                "ticker": ticker,
                "event": "block_end",
                "reason_code": before.get("reason_code"),
                "reason": before.get("reason", ""),
                **context,
                "start_date": before.get("start_date"),
                "start_price": before.get("start_price"),
                "blocked_days": before.get("blocked_days", 1),
                "end_cause": "reason_changed" if now else "unblocked",
            })

        if now and (
            before is None or now["reason_code"] != before.get("reason_code")
        ):
            start = {
                **now,
                "start_date": day,
                "start_price": context["price"],
                "start_long_regime": context["long_regime"],
                "start_short_regime": context["short_regime"],
                "last_seen_date": day,
                "blocked_days": 1,
            }
            updated[ticker] = start
            events.append({
                "date": day,
                "ticker": ticker,
                "event": "block_start",
                **now,
                **context,
            })
        elif now and before:
            continued = dict(before)
            if continued.get("last_seen_date") != day:
                continued["blocked_days"] = continued.get("blocked_days", 1) + 1
            continued.update({
                "reason": now["reason"],
                "last_seen_date": day,
                "last_price": context["price"],
                "last_long_regime": context["long_regime"],
                "last_short_regime": context["short_regime"],
            })
            updated[ticker] = continued

    return events, updated
