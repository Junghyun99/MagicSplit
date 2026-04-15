import json
import os
from datetime import datetime
from unittest.mock import patch, mock_open, MagicMock

import pytest

from src.infra.broker.kis_token_cache import save_token_to_cache, KIS_TOKEN_CACHE_PATH


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_datetime():
    return datetime(2023, 1, 1, 12, 0, 0)


def test_save_token_to_cache_file_not_exists(mock_logger, mock_datetime):
    """Test saving a token when the cache file does not exist."""
    app_key = "test_app_key"
    token = "test_token"

    m_open = mock_open()

    with patch("os.path.exists", return_value=False), \
         patch("builtins.open", m_open), \
         patch("json.dump") as mock_json_dump:

        save_token_to_cache(app_key, token, mock_datetime, mock_logger)

        # Check if open was called correctly for writing
        m_open.assert_called_once_with(KIS_TOKEN_CACHE_PATH, "w", encoding="utf-8")

        # Check if json.dump was called with the correct data
        expected_cache = {
            app_key: {
                "access_token": token,
                "expires_at": mock_datetime.isoformat()
            }
        }
        mock_json_dump.assert_called_once()
        args, kwargs = mock_json_dump.call_args
        assert args[0] == expected_cache
        assert args[1] == m_open()
        assert kwargs.get("ensure_ascii") is False
        assert kwargs.get("indent") == 2

        # Verify logger.info was called
        mock_logger.info.assert_called_once()


def test_save_token_to_cache_file_exists(mock_logger, mock_datetime):
    """Test saving a token when the cache file already exists."""
    app_key = "new_app_key"
    token = "new_token"
    existing_cache = {
        "existing_key": {
            "access_token": "old_token",
            "expires_at": "2022-01-01T12:00:00"
        }
    }

    m_open = mock_open(read_data=json.dumps(existing_cache))

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", m_open), \
         patch("json.load", return_value=existing_cache), \
         patch("json.dump") as mock_json_dump:

        save_token_to_cache(app_key, token, mock_datetime, mock_logger)

        # check that open was called twice (read and write)
        assert m_open.call_count == 2
        m_open.assert_any_call(KIS_TOKEN_CACHE_PATH, "r", encoding="utf-8")
        m_open.assert_any_call(KIS_TOKEN_CACHE_PATH, "w", encoding="utf-8")

        # Check if json.dump was called with the combined data
        expected_cache = {
            "existing_key": {
                "access_token": "old_token",
                "expires_at": "2022-01-01T12:00:00"
            },
            app_key: {
                "access_token": token,
                "expires_at": mock_datetime.isoformat()
            }
        }
        mock_json_dump.assert_called_once()
        args, kwargs = mock_json_dump.call_args
        assert args[0] == expected_cache

        # Verify logger.info was called
        mock_logger.info.assert_called_once()


def test_save_token_to_cache_exception_handling(mock_logger, mock_datetime):
    """Test exception handling during cache saving."""
    app_key = "test_app_key"
    token = "test_token"

    with patch("os.path.exists", side_effect=Exception("Test Error")):
        save_token_to_cache(app_key, token, mock_datetime, mock_logger)

        # Check if exception was logged
        mock_logger.warning.assert_called_once()
        log_message = mock_logger.warning.call_args[0][0]
        assert "Test Error" in log_message
        assert "토큰 캐시 저장 실패" in log_message
