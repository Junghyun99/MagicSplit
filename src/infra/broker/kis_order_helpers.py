# src/infra/broker/kis_order_helpers.py
"""KIS 해외/국내 공용 주문 체결 폴링 헬퍼."""
import time
from dataclasses import dataclass
from typing import Callable, Set, Tuple


def poll_order_fill(
    get_pending_ids_fn: Callable[[], Set[str]],
    odno: str,
    timeout: int,
    logger,
    log_prefix: str = "[KisBroker]",
) -> bool:
    """미체결 목록에서 해당 ODNO가 사라질 때까지 polling. 체결 여부 반환."""
    start = time.time()
    while (time.time() - start) < timeout:
        try:
            pending_ids = get_pending_ids_fn()
            if odno not in pending_ids:
                return True
        except Exception as e:
            logger.warning(f"{log_prefix} Fill poll error (ODNO={odno}): {e}")
        time.sleep(2)
    return False


@dataclass(frozen=True)
class TimeoutOutcome:
    """주문 타임아웃 후 취소·재폴링·체결조회 결과."""
    classification: str   # "FILLED" | "PARTIAL" | "REJECTED" | "ORDERED"
    fill_qty: int
    fill_price: float
    fill_fee: float
    cancel_ok: bool
    still_pending: bool
    detail: str


def resolve_timeout_outcome(
    odno: str,
    order_qty: int,
    cancel_fn: Callable[[], bool],
    pending_ids_fn: Callable[[], Set[str]],
    fill_query_fn: Callable[[], Tuple[float, int, float]],
    logger,
    *,
    settle_wait_sec: float = 1.5,
    settle_timeout_sec: float = 10.0,
    poll_interval_sec: float = 2.0,
    log_prefix: str = "[KisBroker]",
) -> TimeoutOutcome:
    """주문 타임아웃 시 취소 → 재폴링 → 체결조회를 묶어 결과를 분류한다.

    분류 규칙:
      - fill_qty == order_qty                   → FILLED
      - 0 < fill_qty < order_qty, !still_pending → PARTIAL
      - fill_qty == 0, !still_pending           → REJECTED
      - still_pending                           → ORDERED
    """
    # 0) order_qty 검증. 0 이하면 정상 주문이 아니므로 즉시 REJECTED 로 종료해
    #    이후 분류 로직(fill_qty>=order_qty 등)이 잘못 매칭되는 일을 방지.
    if order_qty <= 0:
        logger.warning(
            f"{log_prefix} resolve_timeout_outcome called with order_qty={order_qty} "
            f"(ODNO={odno}) — returning REJECTED without API calls"
        )
        return TimeoutOutcome(
            classification="REJECTED",
            fill_qty=0,
            fill_price=0.0,
            fill_fee=0.0,
            cancel_ok=False,
            still_pending=False,
            detail="invalid_order_qty",
        )

    # 1) 취소 시도. 예외/실패 시에도 체결/미체결 측정으로 분류는 가능하므로 진행
    cancel_ok = False
    try:
        cancel_ok = bool(cancel_fn())
    except Exception as e:
        logger.warning(f"{log_prefix} cancel_fn raised (ODNO={odno}): {e}")

    # 2) KIS 서버 반영 지연 흡수용 즉시 대기
    if settle_wait_sec > 0:
        time.sleep(settle_wait_sec)

    # 3) 재폴링 루프 — pending 사라짐 OR 전량 체결 OR 타임아웃까지
    fill_qty = 0
    fill_price = 0.0
    fill_fee = 0.0
    still_pending = True

    start = time.time()
    while True:
        try:
            pending_ids = pending_ids_fn()
            still_pending = odno in pending_ids
        except Exception as e:
            logger.warning(f"{log_prefix} pending re-poll error (ODNO={odno}): {e}")

        try:
            fp, fq, ff = fill_query_fn()
            fill_price, fill_qty, fill_fee = fp, fq, ff
        except Exception as e:
            logger.warning(f"{log_prefix} fill re-query error (ODNO={odno}): {e}")

        if not still_pending:
            break
        if fill_qty >= order_qty:
            break
        if (time.time() - start) >= settle_timeout_sec:
            break
        time.sleep(poll_interval_sec)

    # 4) 분류
    if fill_qty >= order_qty and order_qty > 0:
        classification = "FILLED"
        detail = "race_full_fill"
    elif still_pending:
        classification = "ORDERED"
        detail = "pending_after_cancel" if fill_qty == 0 else "partial_fill_pending"
    elif fill_qty > 0:
        classification = "PARTIAL"
        detail = "partial_after_cancel"
    else:
        classification = "REJECTED"
        detail = "cancelled_no_fill"

    return TimeoutOutcome(
        classification=classification,
        fill_qty=fill_qty,
        fill_price=fill_price,
        fill_fee=fill_fee,
        cancel_ok=cancel_ok,
        still_pending=still_pending,
        detail=detail,
    )
