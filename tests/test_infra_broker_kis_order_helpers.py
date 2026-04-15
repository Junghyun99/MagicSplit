import pytest
from unittest.mock import patch, MagicMock

from src.infra.broker.kis_order_helpers import poll_order_fill


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
