import os
import json
from datetime import datetime, timedelta
from unittest.mock import patch, mock_open, MagicMock
import pytest

from src.infra.broker.kis_token_cache import load_token_from_cache

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

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(mock_cache_data))):
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

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(mock_cache_data))):
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

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=json.dumps(mock_cache_data))):
        result = load_token_from_cache(TEST_APP_KEY, mock_logger)
        assert result is None
        mock_logger.info.assert_called_once_with("[KisBroker] 캐시 토큰 만료됨, 재발급 필요")

def test_load_token_exception_handling(mock_logger):
    """Test exception handling during file reading/JSON parsing."""
    with patch("src.infra.broker.kis_token_cache.os.path.exists", return_value=True), \
         patch("src.infra.broker.kis_token_cache.open", side_effect=Exception("Read error")):
        result = load_token_from_cache(TEST_APP_KEY, mock_logger)
        assert result is None
        mock_logger.warning.assert_called_once()
        assert "[KisBroker] 토큰 캐시 로드 실패 (무시): Read error" in mock_logger.warning.call_args[0][0]
