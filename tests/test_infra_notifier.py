# tests/test_infra_notifier.py
import pytest
from unittest.mock import patch, MagicMock
from src.infra.notifier import SlackNotifier, TelegramNotifier


@pytest.fixture
def mock_logger():
    return MagicMock()


class TestSlackNotifier:
    def test_send_message_no_url(self, mock_logger):
        """URL 및 토큰 없으면 로거로만 출력"""
        notifier = SlackNotifier("", mock_logger)
        notifier.send_message("test")
        mock_logger.info.assert_called_once()

    def test_send_alert_no_url(self, mock_logger):
        notifier = SlackNotifier("", mock_logger)
        notifier.send_alert("alert!")
        mock_logger.info.assert_called_once()

    @patch("src.infra.notifier.requests.post")
    def test_send_message_with_url(self, mock_post, mock_logger):
        """URL 있으면 HTTP POST"""
        mock_post.return_value = MagicMock(status_code=200)
        notifier = SlackNotifier("https://hooks.slack.com/test", mock_logger)
        notifier.send_message("test message")
        mock_post.assert_called_once()

    @patch("src.infra.notifier.requests.post")
    def test_send_error_logged(self, mock_post, mock_logger):
        """HTTP 에러 시 로그 기록"""
        mock_post.return_value = MagicMock(status_code=500, text="error")
        notifier = SlackNotifier("https://hooks.slack.com/test", mock_logger)
        notifier.send_message("test")
        mock_logger.error.assert_called_once()

    @patch("src.infra.notifier.requests.post")
    def test_send_with_detail_block_kit_webhook(self, mock_post, mock_logger):
        """웹후크 모드: 상세 정보가 있으면 Block Kit 형식 사용"""
        mock_post.return_value = MagicMock(status_code=200)
        notifier = SlackNotifier("https://hooks.slack.com/test", mock_logger)
        notifier.send_message("Summary", detail="Detail Log")
        
        args, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert "blocks" in payload
        assert len(payload["blocks"]) == 3
        assert payload["blocks"][0]["text"]["text"] == "*[MagicSplit]*\nSummary"
        assert "Detail Log" in payload["blocks"][2]["text"]["text"]

    @patch("src.infra.notifier.requests.post")
    def test_send_with_detail_threaded_api(self, mock_post, mock_logger):
        """API 모드: 상세 정보가 있으면 스레드(thread_ts) 사용"""
        # 첫 번째 호출(메인 메시지) 응답
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {"ok": True, "ts": "12345.6789"}), # 부모
            MagicMock(status_code=200, json=lambda: {"ok": True, "ts": "12345.6790"})  # 자식
        ]
        
        notifier = SlackNotifier("", mock_logger, bot_token="xoxb-test", channel_id="C123")
        notifier.send_message("Summary", detail="Detail Log")
        
        # 총 2번 호출되어야 함
        assert mock_post.call_count == 2
        
        # 첫 번째 호출 검증 (메인)
        first_call_args = mock_post.call_args_list[0]
        assert first_call_args.kwargs["json"]["text"] == "*[MagicSplit]*\nSummary"
        assert "thread_ts" not in first_call_args.kwargs["json"]
        
        # 두 번째 호출 검증 (스레드)
        second_call_args = mock_post.call_args_list[1]
        assert "Detail Log" in second_call_args.kwargs["json"]["text"]
        assert second_call_args.kwargs["json"]["thread_ts"] == "12345.6789"


class TestTelegramNotifier:
    def test_send_no_token(self, mock_logger):
        """토큰 없으면 로거로만 출력"""
        notifier = TelegramNotifier("", "", mock_logger)
        notifier.send_message("test")
        mock_logger.info.assert_called_once()

    @patch("src.infra.notifier.requests.post")
    def test_send_with_token(self, mock_post, mock_logger):
        mock_post.return_value = MagicMock(status_code=200)
        notifier = TelegramNotifier("token123", "chat456", mock_logger)
        notifier.send_message("hello")
        mock_post.assert_called_once()
