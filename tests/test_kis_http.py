import pytest
from unittest.mock import patch, MagicMock
from src.infra.broker.kis_http import fetch_hashkey
from src.config import DEFAULT_HTTP_TIMEOUT

class TestKisHttpHelpers:
    def setup_method(self):
        self.base_url = "https://example.com"
        self.app_key = "test_app_key"
        self.app_secret = "test_app_secret"

    @patch("src.infra.broker.kis_http._pkg.requests.post")
    def test_fetch_hashkey_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"HASH": "fake_hash_123"}
        mock_post.return_value = mock_response

        data = {"key": "value"}
        result = fetch_hashkey(self.base_url, self.app_key, self.app_secret, data, None)

        assert result == "fake_hash_123"
        mock_post.assert_called_once_with(
            f"{self.base_url}/uapi/hashkey",
            headers={
                "content-type": "application/json",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            json=data,
            timeout=DEFAULT_HTTP_TIMEOUT,
        )
