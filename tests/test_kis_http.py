from unittest.mock import patch, MagicMock
from src.infra.broker.kis_http import fetch_hashkey

BASE_URL = "https://mock-api.com"
APP_KEY = "mock-app-key"
APP_SECRET = "mock-app-secret"
DATA = {"key": "value"}

def test_fetch_hashkey_error_path():
    logger = MagicMock()

    with patch('src.infra.broker.kis_http._pkg.requests.post') as mock_post:
        mock_post.side_effect = Exception("Mock exception")

        result = fetch_hashkey(BASE_URL, APP_KEY, APP_SECRET, DATA, logger)

        assert result is None
        logger.error.assert_called_once_with("[KisBroker] HashKey 생성 실패: Mock exception")

def test_fetch_hashkey_no_logger():
    with patch('src.infra.broker.kis_http._pkg.requests.post') as mock_post:
        mock_post.side_effect = Exception("Mock exception")

        result = fetch_hashkey(BASE_URL, APP_KEY, APP_SECRET, DATA, None)

        assert result is None

def test_fetch_hashkey_success():
    logger = MagicMock()

    with patch('src.infra.broker.kis_http._pkg.requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"HASH": "mock-hash-value"}
        mock_post.return_value = mock_response

        result = fetch_hashkey(BASE_URL, APP_KEY, APP_SECRET, DATA, logger)

        assert result == "mock-hash-value"
        mock_post.assert_called_once_with(
            "https://mock-api.com/uapi/hashkey",
            headers={
                "content-type": "application/json",
                "appkey": "mock-app-key",
                "appsecret": "mock-app-secret",
            },
            json={"key": "value"},
        )
        mock_response.raise_for_status.assert_called_once()
        logger.error.assert_not_called()
