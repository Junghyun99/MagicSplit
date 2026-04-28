import pytest
from unittest.mock import patch, MagicMock

from src.infra.broker.kis_order_helpers import (
    poll_order_fill,
    resolve_timeout_outcome,
    TimeoutOutcome,
)


class TestPollOrderFill:
    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_immediate_success(self, mock_sleep, mock_time):
        """ODNO가 처음부터 미체결 목록에 없는 경우 즉시 성공 반환"""
        # mock_time: start time, while condition check
        mock_time.side_effect = [0, 0]

        # ODNO is "test_odno", but pending list is empty
        def get_pending_ids_fn():
            return set()

        logger = MagicMock()

        result = poll_order_fill(
            get_pending_ids_fn=get_pending_ids_fn,
            odno="test_odno",
            timeout=10,
            logger=logger
        )

        assert result is True
        mock_sleep.assert_not_called()
        logger.warning.assert_not_called()

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_delayed_success(self, mock_sleep, mock_time):
        """ODNO가 처음에 있다가 나중에 사라지는 경우 (지연 체결)"""
        # mock_time:
        # 1. start time (0)
        # 2. while loop check 1 (1) -> in pending
        # 3. while loop check 2 (3) -> not in pending
        mock_time.side_effect = [0, 1, 3]

        # Pending ids logic: first time contains odno, second time empty
        call_count = 0
        def get_pending_ids_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"test_odno", "other_odno"}
            return {"other_odno"}

        logger = MagicMock()

        result = poll_order_fill(
            get_pending_ids_fn=get_pending_ids_fn,
            odno="test_odno",
            timeout=10,
            logger=logger
        )

        assert result is True
        # Sleep should be called once after first failed check
        mock_sleep.assert_called_once_with(2)
        logger.warning.assert_not_called()

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_timeout_failure(self, mock_sleep, mock_time):
        """ODNO가 타임아웃 시간까지 사라지지 않는 경우 실패 반환"""
        # mock_time:
        # 1. start time (0)
        # 2. loop 1 check (1)
        # 3. loop 2 check (3)
        # 4. loop 3 check (11) -> breaks while loop because (11 - 0) > 10
        mock_time.side_effect = [0, 1, 3, 11]

        def get_pending_ids_fn():
            return {"test_odno"}

        logger = MagicMock()

        result = poll_order_fill(
            get_pending_ids_fn=get_pending_ids_fn,
            odno="test_odno",
            timeout=10,
            logger=logger
        )

        assert result is False
        assert mock_sleep.call_count == 2
        logger.warning.assert_not_called()

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_exception_handling(self, mock_sleep, mock_time):
        """get_pending_ids_fn에서 예외 발생 시 로거에 경고를 남기고 계속 진행"""
        # mock_time:
        # 1. start time (0)
        # 2. loop 1 check (1) -> exception
        # 3. loop 2 check (3) -> success (not in pending)
        mock_time.side_effect = [0, 1, 3]

        call_count = 0
        def get_pending_ids_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("API Network Error")
            return set()

        logger = MagicMock()

        result = poll_order_fill(
            get_pending_ids_fn=get_pending_ids_fn,
            odno="test_odno",
            timeout=10,
            logger=logger
        )

        assert result is True
        mock_sleep.assert_called_once_with(2)

        # Verify logger.warning was called with the expected message format
        logger.warning.assert_called_once()
        warning_msg = logger.warning.call_args[0][0]
        assert "[KisBroker] Fill poll error" in warning_msg
        assert "(ODNO=test_odno)" in warning_msg
        assert "API Network Error" in warning_msg


class TestResolveTimeoutOutcome:
    """resolve_timeout_outcome: 취소 → 재폴링 → 체결조회 분류 테스트."""

    def _patch_time(self, mock_time, count=8):
        # start, then increasing timestamps for loop checks
        mock_time.side_effect = [0.0] + [0.1 * i for i in range(1, count)]

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_rejected_cancel_ok_no_fill(self, mock_sleep, mock_time):
        """취소 OK + pending 사라짐 + fill 0 → REJECTED"""
        self._patch_time(mock_time)
        cancel_fn = MagicMock(return_value=True)
        pending_ids_fn = MagicMock(return_value=set())
        fill_query_fn = MagicMock(return_value=(0.0, 0, 0.0))
        logger = MagicMock()

        outcome = resolve_timeout_outcome(
            odno="X1", order_qty=5,
            cancel_fn=cancel_fn, pending_ids_fn=pending_ids_fn,
            fill_query_fn=fill_query_fn, logger=logger,
        )

        assert outcome.classification == "REJECTED"
        assert outcome.fill_qty == 0
        assert outcome.cancel_ok is True
        assert outcome.still_pending is False
        cancel_fn.assert_called_once()

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_partial_after_cancel(self, mock_sleep, mock_time):
        """취소 OK + pending 사라짐 + 부분 체결(3/5) → PARTIAL"""
        self._patch_time(mock_time)
        outcome = resolve_timeout_outcome(
            odno="X2", order_qty=5,
            cancel_fn=MagicMock(return_value=True),
            pending_ids_fn=MagicMock(return_value=set()),
            fill_query_fn=MagicMock(return_value=(100.0, 3, 0.5)),
            logger=MagicMock(),
        )
        assert outcome.classification == "PARTIAL"
        assert outcome.fill_qty == 3
        assert outcome.fill_price == 100.0
        assert outcome.fill_fee == 0.5
        assert outcome.detail == "partial_after_cancel"

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_filled_race(self, mock_sleep, mock_time):
        """race 전량 체결(5/5) → FILLED. fill_qty>=order_qty면 즉시 탈출"""
        self._patch_time(mock_time)
        outcome = resolve_timeout_outcome(
            odno="X3", order_qty=5,
            cancel_fn=MagicMock(return_value=True),
            pending_ids_fn=MagicMock(return_value={"X3"}),  # pending이지만 fill==qty면 FILLED
            fill_query_fn=MagicMock(return_value=(100.0, 5, 0.8)),
            logger=MagicMock(),
        )
        assert outcome.classification == "FILLED"
        assert outcome.fill_qty == 5

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_ordered_pending_with_partial_fill(self, mock_sleep, mock_time):
        """settle_timeout 끝까지 pending 잔존 + 일부 체결 → ORDERED, partial_fill_pending"""
        # start=0, loop checks at 0,5,11 (>10s timeout)
        mock_time.side_effect = [0.0, 0.0, 5.0, 11.0]
        outcome = resolve_timeout_outcome(
            odno="X4", order_qty=5,
            cancel_fn=MagicMock(return_value=True),
            pending_ids_fn=MagicMock(return_value={"X4"}),
            fill_query_fn=MagicMock(return_value=(100.0, 2, 0.3)),
            logger=MagicMock(),
            settle_wait_sec=0,
            settle_timeout_sec=10,
        )
        assert outcome.classification == "ORDERED"
        assert outcome.fill_qty == 2
        assert outcome.still_pending is True
        assert outcome.detail == "partial_fill_pending"

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_ordered_pending_no_fill(self, mock_sleep, mock_time):
        """pending 잔존 + 체결 0 → ORDERED, pending_after_cancel"""
        mock_time.side_effect = [0.0, 0.0, 5.0, 11.0]
        outcome = resolve_timeout_outcome(
            odno="X5", order_qty=5,
            cancel_fn=MagicMock(return_value=True),
            pending_ids_fn=MagicMock(return_value={"X5"}),
            fill_query_fn=MagicMock(return_value=(0.0, 0, 0.0)),
            logger=MagicMock(),
            settle_wait_sec=0,
            settle_timeout_sec=10,
        )
        assert outcome.classification == "ORDERED"
        assert outcome.fill_qty == 0
        assert outcome.detail == "pending_after_cancel"

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_cancel_fn_exception(self, mock_sleep, mock_time):
        """cancel_fn 예외 → cancel_ok=False, 흐름 유지하여 분류"""
        self._patch_time(mock_time)
        cancel_fn = MagicMock(side_effect=RuntimeError("cancel api 500"))
        logger = MagicMock()
        outcome = resolve_timeout_outcome(
            odno="X6", order_qty=5,
            cancel_fn=cancel_fn,
            pending_ids_fn=MagicMock(return_value=set()),
            fill_query_fn=MagicMock(return_value=(0.0, 0, 0.0)),
            logger=logger,
        )
        assert outcome.cancel_ok is False
        assert outcome.classification == "REJECTED"
        # warning 로그가 cancel 관련으로 한 번 이상 호출되어야 함
        warns = [c.args[0] for c in logger.warning.call_args_list]
        assert any("cancel_fn raised" in w for w in warns)

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_pending_ids_fn_exception(self, mock_sleep, mock_time):
        """pending_ids_fn 예외 → warning 로그 + still_pending 유지(이전 측정값)"""
        # 두 번째 라운드에 fill 발견되어 탈출
        mock_time.side_effect = [0.0, 0.0, 0.5, 1.5]
        pending_calls = [0]

        def pend():
            pending_calls[0] += 1
            if pending_calls[0] == 1:
                raise RuntimeError("pending api timeout")
            return set()

        logger = MagicMock()
        outcome = resolve_timeout_outcome(
            odno="X7", order_qty=5,
            cancel_fn=MagicMock(return_value=True),
            pending_ids_fn=pend,
            fill_query_fn=MagicMock(return_value=(100.0, 5, 0.9)),
            logger=logger,
            settle_wait_sec=0,
            settle_timeout_sec=10,
        )
        # 두 번째 라운드에서 fill_qty=5 발견 → FILLED 로 탈출
        assert outcome.classification == "FILLED"
        warns = [c.args[0] for c in logger.warning.call_args_list]
        assert any("pending re-poll error" in w for w in warns)

    @patch("src.infra.broker.kis_order_helpers.time.time")
    @patch("src.infra.broker.kis_order_helpers.time.sleep")
    def test_fill_query_exception(self, mock_sleep, mock_time):
        """fill_query_fn 예외 → fill 0 으로 간주"""
        self._patch_time(mock_time)
        logger = MagicMock()
        outcome = resolve_timeout_outcome(
            odno="X8", order_qty=5,
            cancel_fn=MagicMock(return_value=True),
            pending_ids_fn=MagicMock(return_value=set()),
            fill_query_fn=MagicMock(side_effect=RuntimeError("fill api 500")),
            logger=logger,
        )
        assert outcome.classification == "REJECTED"
        assert outcome.fill_qty == 0
        warns = [c.args[0] for c in logger.warning.call_args_list]
        assert any("fill re-query error" in w for w in warns)


class TestResolveTimeoutOutcomeDefensive:
    """방어 코드: 비정상 입력 처리."""

    def test_zero_order_qty_returns_rejected_without_calls(self):
        """order_qty=0 이면 API 호출 없이 즉시 REJECTED 반환."""
        cancel_fn = MagicMock()
        pending_ids_fn = MagicMock()
        fill_query_fn = MagicMock()
        logger = MagicMock()

        outcome = resolve_timeout_outcome(
            odno="ZQ", order_qty=0,
            cancel_fn=cancel_fn,
            pending_ids_fn=pending_ids_fn,
            fill_query_fn=fill_query_fn,
            logger=logger,
        )

        assert outcome.classification == "REJECTED"
        assert outcome.detail == "invalid_order_qty"
        assert outcome.cancel_ok is False
        # 어느 하나도 호출되어선 안 됨
        cancel_fn.assert_not_called()
        pending_ids_fn.assert_not_called()
        fill_query_fn.assert_not_called()
        # warning 로그 1회
        assert any("invalid" in str(c) or "order_qty=0" in str(c)
                   for c in logger.warning.call_args_list)

    def test_negative_order_qty_also_rejected(self):
        outcome = resolve_timeout_outcome(
            odno="NQ", order_qty=-1,
            cancel_fn=MagicMock(),
            pending_ids_fn=MagicMock(),
            fill_query_fn=MagicMock(),
            logger=MagicMock(),
        )
        assert outcome.classification == "REJECTED"
        assert outcome.detail == "invalid_order_qty"
