# src/core/logic/regime_events.py
"""레짐 상태 변화를 차트용 이벤트로 환산하는 순수 함수 모듈.

split_evaluator는 regime_state를 in-place로 변이시키는데, 그 상태는 현재값
스냅샷일 뿐이라 "언제 하락 래치가 걸렸는지" 같은 구간 정보가 남지 않는다.
이 모듈은 사이클 전후 상태를 비교해 전이 시점만 이벤트로 뽑아낸다.

평가기/엔진 로직을 건드리지 않고 상태 diff만 보므로, 판정 로직이 바뀌어도
이벤트 기록이 따라 깨지지 않는다.

이벤트는 (진입, 해제) 쌍으로 설계되어 프런트가 구간 밴드를 복원할 수 있다.
"""
from typing import List, Optional

# 상태 키 -> (진입 이벤트명, 해제 이벤트명)
_LATCH_EVENTS = {
    "regime": ("uptrend_on", "uptrend_off"),
    "downtrend": ("downtrend_on", "downtrend_off"),
}


def _is_active(state: dict, key: str) -> bool:
    """래치 키가 활성 상태인지 판정한다.

    regime은 "uptrend", downtrend는 "active" 문자열을 쓰지만 둘 다
    "값이 있으면 활성"이라는 규칙이 동일하므로 truthy 검사로 충분하다.
    """
    return bool(state.get(key))


def _lock_stop(lock: dict) -> Optional[float]:
    """추종 데드라인의 청산선(lock_price * (1 - drop_pct/100))을 계산한다."""
    price = lock.get("lock_price")
    drop = lock.get("drop_pct")
    if price is None or drop is None:
        return None
    return round(float(price) * (1 - float(drop) / 100), 6)


def diff_regime_state(
    before: dict,
    after: dict,
    ticker: str,
    date: str,
    price: Optional[float] = None,
) -> List[dict]:
    """한 종목의 사이클 전후 regime_state를 비교해 이벤트 목록을 만든다.

    Args:
        before: 사이클 시작 시점 상태 (없으면 빈 dict)
        after: 사이클 종료 시점 상태 (없으면 빈 dict)
        ticker: 종목 코드
        date: 이벤트 시각 문자열
        price: 이벤트 시점 현재가 (참고용, None 허용)

    Returns:
        이벤트 dict 리스트. 변화가 없으면 빈 리스트.
    """
    before = before or {}
    after = after or {}
    events: List[dict] = []

    def emit(event: str, **extra) -> None:
        record = {"date": date, "ticker": ticker, "event": event}
        if price is not None:
            record["price"] = round(float(price), 6)
        record.update(extra)
        events.append(record)

    # 1. 단순 래치 (상승 레짐 / 하락 매수차단)
    for key, (on_event, off_event) in _LATCH_EVENTS.items():
        was, now = _is_active(before, key), _is_active(after, key)
        if now and not was:
            emit(on_event)
        elif was and not now:
            emit(off_event)

    # 2. 추종 데드라인 (Trailing Lock) - 청산선 수평선 렌더용
    lock_before = before.get("trailing_lock")
    lock_after = after.get("trailing_lock")
    if lock_after and not lock_before:
        emit(
            "trailing_lock_on",
            lock_price=lock_after.get("lock_price"),
            stop=_lock_stop(lock_after),
            gate=lock_after.get("reentry_gate"),
        )
    elif lock_before and not lock_after:
        emit("trailing_lock_off")

    # 3. 청산 후 재진입 게이트 구간
    gate_before = bool(before.get("post_liquidation"))
    gate_after = bool(after.get("post_liquidation"))
    if gate_after and not gate_before:
        emit(
            "reentry_gate_on",
            gate=after.get("post_liquidation_reentry_gate", "resistance"),
        )
    elif gate_before and not gate_after:
        emit("reentry_gate_off")

    # 4. 하단 이탈 확정 대기 (N/2 진행). 확정되면 카운트가 비워지므로
    #    증가하는 순간만 기록한다. 확정 자체는 위 lock/gate 이벤트로 드러난다.
    bd_before = len(before.get("breakdown_days") or [])
    bd_after = len(after.get("breakdown_days") or [])
    if bd_after > bd_before:
        emit("breakdown_pending", count=bd_after)

    return events


def seed_events_from_state(
    state: dict,
    ticker: str,
    date: str,
    price: Optional[float] = None,
) -> List[dict]:
    """현재 상태에서 "이미 진행 중인" 구간의 진입 이벤트를 생성한다.

    이벤트 적재를 처음 시작할 때 호출한다. 이력이 비어 있으면 이미 걸려 있는
    하락 래치나 추종 데드라인이 차트에 전혀 안 보이게 되므로, status.json에
    남아 있는 현재 상태를 시작점으로 한 번 심어 준다.
    """
    return diff_regime_state({}, state, ticker, date, price)
