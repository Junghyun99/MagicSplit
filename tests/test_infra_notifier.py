# tests/test_infra_notifier.py
import pytest
from unittest.mock import patch, MagicMock
from src.infra.notifier import SlackNotifier, TelegramNotifier


@pytest.fixture
def mock_logger():
    return MagicMock()


class TestSlackNotifier:
    def test_send_message_no_url(self, mock_logger):
        """URL 없으면 로거로만 출력"""
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
