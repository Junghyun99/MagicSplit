import json
import os
from datetime import datetime, timedelta
from unittest.mock import patch, mock_open, MagicMock

import pytest

from src.infra.broker.kis_token_cache import load_token_from_cache, save_token_to_cache, KIS_TOKEN_CACHE_PATH

# Constants for testing
TEST_APP_KEY = "test_app_key"
VALID_TOKEN = "valid_access_token"

@pytest.fixture
def mock_logger():
    return MagicMock()

def test_load_token_cache_file_not_exists(mock_logger):
    """Test when the cache file does not exist."""
    with patch("src.infra.broker.kis_token_cache.os.path.exists", return_value=False):
        result = load_token_from_cache(TEST_APP_KEY, mock_logger)
        assert result is None
        mock_logger.info.assert_not_called()
        mock_logger.warning.assert_not_called()

def test_load_token_app_key_not_in_cache(mock_logger):
    """Test when the cache file exists but app_key is not present."""
    mock_cache_data = {"other_app_key": {"access_token": "some_token", "expires_at": "2024-01-01T00:00:00"}}

    with patch("src.infra.broker.kis_token_cache.os.path.exists", return_value=True), \
         patch("src.infra.broker.kis_token_cache.open", mock_open(read_data=json.dumps(mock_cache_data))):
        result = load_token_from_cache(TEST_APP_KEY, mock_logger)
        assert result is None

def test_load_token_valid_token(mock_logger):
    """Test when the app_key exists and the token is valid (not expired)."""
    # Set expiration time to 1 hour in the future
    future_time = datetime.now() + timedelta(hours=1)
    mock_cache_data = {
        TEST_APP_KEY: {
            "access_token": VALID_TOKEN,
            "expires_at": future_time.isoformat()
        }
    }

    with patch("src.infra.broker.kis_token_cache.os.path.exists", return_value=True), \
         patch("src.infra.broker.kis_token_cache.open", mock_open(read_data=json.dumps(mock_cache_data))):
        result = load_token_from_cache(TEST_APP_KEY, mock_logger)
        assert result is not None
        assert result["access_token"] == VALID_TOKEN
        assert result["expires_at"] == future_time.isoformat()

def test_load_token_expired_token(mock_logger):
    """Test when the app_key exists but the token is expired (or expires in < 60s)."""
    # Set expiration time to 30 seconds in the future (which should be considered expired due to 60s buffer)
    near_future_time = datetime.now() + timedelta(seconds=30)
    mock_cache_data = {
        TEST_APP_KEY: {
            "access_token": VALID_TOKEN,
            "expires_at": near_future_time.isoformat()
        }
    }

    with patch("src.infra.broker.kis_token_cache.os.path.exists", return_value=True), \
         patch("src.infra.broker.kis_token_cache.open", mock_open(read_data=json.dumps(mock_cache_data))):
        result = load_token_from_cache(TEST_APP_KEY, mock_logger)
        assert result is None
        mock_logger.info.assert_called_once_with("[KisBroker] 캐시 토큰 만료됨, 재발급 필요")

def test_load_token_exception_handling(mock_logger):
    """Test exception handling during file reading/JSON parsing."""
    with patch("src.infra.broker.kis_token_cache.os.path.exists", return_value=True), \
         patch("src.infra.broker.kis_token_cache.open", side_effect=Exception("Read error")):
        result = load_token_from_cache(TEST_APP_KEY, mock_logger)
        assert result is None
        mock_logger.warning.assert_called_once_with("[KisBroker] 토큰 캐시 로드 실패 (무시): Read error")


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
