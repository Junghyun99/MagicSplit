from src.core.logic.filter_events import (
    classify_filter_reason,
    update_filter_episodes,
)
from src.core.models import OrderAction, SplitSignal


def _blocked(reason: str, ticker: str = "AAPL") -> SplitSignal:
    return SplitSignal(
        ticker=ticker,
        lot_id=None,
        action=OrderAction.BUY,
        quantity=0,
        price=100.0,
        reason=reason,
        pct_change=0.0,
        is_blocked=True,
    )


def test_classify_filter_reason_uses_stable_codes():
    assert classify_filter_reason("장기 하락 확정 - 신규 진입 차단") == "long_downtrend"
    assert classify_filter_reason("전량 청산 후 재진입 대기 - 중심선 회복 필요") == "reentry_gate"
    assert classify_filter_reason("비중 상한 초과") is None


def test_episode_records_structured_start_continuation_and_end():
    regimes = {"AAPL": {"long_trend": "uptrend", "short_trend": "downtrend"}}
    signals = [_blocked("단기 하락 - 신규 진입 중단")]

    events, state = update_filter_episodes(
        {}, signals, "2026-01-02", {"AAPL": 101.25}, regimes,
    )

    assert events == [{
        "date": "2026-01-02",
        "ticker": "AAPL",
        "event": "block_start",
        "reason_code": "short_downtrend",
        "reason": "단기 하락 - 신규 진입 중단",
        "long_regime": "uptrend",
        "short_regime": "downtrend",
        "price": 101.25,
    }]

    events, state = update_filter_episodes(
        state, signals, "2026-01-03", {"AAPL": 99.5}, regimes,
    )
    assert events == []
    assert state["AAPL"]["blocked_days"] == 2

    end_regimes = {"AAPL": {"long_trend": "uptrend", "short_trend": "uptrend"}}
    events, state = update_filter_episodes(
        state, [], "2026-01-04", {"AAPL": 104.0}, end_regimes,
    )
    assert state == {}
    assert events[0]["event"] == "block_end"
    assert events[0]["reason_code"] == "short_downtrend"
    assert events[0]["start_date"] == "2026-01-02"
    assert events[0]["start_price"] == 101.25
    assert events[0]["blocked_days"] == 2
    assert events[0]["price"] == 104.0
    assert events[0]["short_regime"] == "uptrend"
    assert events[0]["end_cause"] == "unblocked"


def test_reason_change_ends_old_episode_and_starts_new_one():
    _, state = update_filter_episodes(
        {}, [_blocked("단기 하락 - 신규 진입 중단")],
        "2026-01-02", {"AAPL": 100.0}, {},
    )

    events, state = update_filter_episodes(
        state, [_blocked("장기 하락 확정 - 신규·추가 매수 차단")],
        "2026-01-03", {"AAPL": 95.0}, {},
    )

    assert [event["event"] for event in events] == ["block_end", "block_start"]
    assert events[0]["end_cause"] == "reason_changed"
    assert events[1]["reason_code"] == "long_downtrend"
    assert state["AAPL"]["reason_code"] == "long_downtrend"


def test_unevaluated_ticker_does_not_close_active_episode():
    _, state = update_filter_episodes(
        {}, [_blocked("단기 하락 - 신규 진입 중단")],
        "2026-01-02", {"AAPL": 100.0}, {},
    )

    events, preserved = update_filter_episodes(
        state, [], "2026-01-03", {"AAPL": 99.0}, {},
        evaluated_tickers={"MSFT"},
    )

    assert events == []
    assert preserved == state


def test_non_filter_block_is_not_recorded():
    events, state = update_filter_episodes(
        {}, [_blocked("비중 상한 초과")], "2026-01-02", {"AAPL": 100.0}, {},
    )
    assert events == []
    assert state == {}
