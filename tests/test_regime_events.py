# tests/test_regime_events.py
import pytest

from src.core.logic.regime_events import diff_regime_state, seed_events_from_state

DATE = "2026-07-31 00:01:32"


def _events(before, after, price=100.0):
    return diff_regime_state(before, after, "AAPL", DATE, price)


def _names(events):
    return [e["event"] for e in events]


class TestNoChange:
    def test_identical_state_yields_nothing(self):
        state = {"regime": "uptrend", "downtrend": "active", "adds": 2}
        assert _events(state, dict(state)) == []

    def test_empty_states_yield_nothing(self):
        assert _events({}, {}) == []

    def test_none_states_are_tolerated(self):
        assert diff_regime_state(None, None, "AAPL", DATE) == []

    def test_unrelated_key_changes_are_ignored(self):
        """adds/스윙고점 같은 카운터 변화는 이벤트가 아니다 (매수 마커로 드러남)."""
        before = {"adds": 1, "last_add_price": 100.0}
        after = {"adds": 2, "last_add_price": 120.0}
        assert _events(before, after) == []


class TestLatchTransitions:
    def test_uptrend_entry_and_exit(self):
        assert _names(_events({}, {"regime": "uptrend"})) == ["uptrend_on"]
        assert _names(_events({"regime": "uptrend"}, {})) == ["uptrend_off"]

    def test_downtrend_entry_and_exit(self):
        assert _names(_events({}, {"downtrend": "active"})) == ["downtrend_on"]
        assert _names(_events({"downtrend": "active"}, {})) == ["downtrend_off"]

    def test_downtrend_none_value_counts_as_inactive(self):
        """평가기는 해제 시 키를 지우지 않고 None을 넣는다."""
        assert _events({"downtrend": None}, {"downtrend": None}) == []
        assert _names(_events({"downtrend": "active"}, {"downtrend": None})) == ["downtrend_off"]

    def test_event_carries_ticker_date_and_price(self):
        event = _events({}, {"downtrend": "active"}, price=123.456789)[0]

        assert event["ticker"] == "AAPL"
        assert event["date"] == DATE
        assert event["price"] == pytest.approx(123.456789)

    def test_price_is_omitted_when_unknown(self):
        event = diff_regime_state({}, {"downtrend": "active"}, "AAPL", DATE, None)[0]
        assert "price" not in event


class TestTrailingLock:
    def test_activation_records_stop_line_and_gate(self):
        after = {"trailing_lock": {
            "lock_price": 308.72, "drop_pct": 3.0, "reentry_gate": "midline",
        }}

        event = _events({}, after)[0]

        assert event["event"] == "trailing_lock_on"
        assert event["lock_price"] == 308.72
        assert event["stop"] == pytest.approx(299.4584)
        assert event["gate"] == "midline"

    def test_release_records_off_event(self):
        before = {"trailing_lock": {"lock_price": 100.0, "drop_pct": 3.0}}
        assert _names(_events(before, {})) == ["trailing_lock_off"]

    def test_missing_drop_pct_yields_null_stop(self):
        after = {"trailing_lock": {"lock_price": 100.0}}
        assert _events({}, after)[0]["stop"] is None


class TestReentryGate:
    def test_gate_open_and_close(self):
        after = {"post_liquidation": True, "post_liquidation_reentry_gate": "midline"}

        event = _events({}, after)[0]
        assert event["event"] == "reentry_gate_on"
        assert event["gate"] == "midline"

        assert _names(_events(after, {})) == ["reentry_gate_off"]

    def test_gate_defaults_to_resistance(self):
        event = _events({}, {"post_liquidation": True})[0]
        assert event["gate"] == "resistance"


class TestBreakdownPending:
    def test_increase_is_recorded_with_count(self):
        before = {"breakdown_days": []}
        after = {"breakdown_days": ["2026-07-30"]}

        event = _events(before, after)[0]
        assert event["event"] == "breakdown_pending"
        assert event["count"] == 1

    def test_reset_is_not_recorded(self):
        """확정/취소로 카운트가 비워지는 건 별도 이벤트로 남기지 않는다."""
        before = {"breakdown_days": ["2026-07-29", "2026-07-30"]}
        assert _events(before, {"breakdown_days": []}) == []


class TestMultipleTransitions:
    def test_all_transitions_in_one_cycle_are_captured(self):
        """이탈 확정 사이클에서는 하락 래치와 청산이 함께 일어난다."""
        after = {
            "downtrend": "active",
            "trailing_lock": {"lock_price": 100.0, "drop_pct": 3.0},
            "breakdown_days": ["2026-07-30"],
        }

        assert _names(_events({}, after)) == [
            "downtrend_on", "trailing_lock_on", "breakdown_pending",
        ]


class TestSeeding:
    def test_seed_emits_currently_active_latches(self):
        state = {
            "downtrend": "active",
            "trailing_lock": {"lock_price": 308.72, "drop_pct": 3.0,
                              "reentry_gate": "midline"},
        }

        events = seed_events_from_state(state, "TSLA", DATE, 311.0)

        assert _names(events) == ["downtrend_on", "trailing_lock_on"]
        assert all(e["ticker"] == "TSLA" for e in events)

    def test_seed_of_clean_state_emits_nothing(self):
        assert seed_events_from_state({}, "AAPL", DATE) == []
