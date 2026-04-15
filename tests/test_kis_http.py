import pytest
from unittest.mock import patch, MagicMock
from src.infra.broker.kis_http import build_header, fetch_hashkey


class TestKisHttpHelpers:
    def setup_method(self):
        self.base_url = "https://example.com"
        self.app_key = "test_app_key"
        self.app_secret = "test_app_secret"
        self.access_token = "test_token"
        self.tr_id = "test_tr_id"

    def test_build_header_no_data(self):
        headers = build_header(
            base_url=self.base_url,
            app_key=self.app_key,
            app_secret=self.app_secret,
            access_token=self.access_token,
            tr_id=self.tr_id
        )

        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert headers["authorization"] == f"Bearer {self.access_token}"
        assert headers["appkey"] == self.app_key
        assert headers["appsecret"] == self.app_secret
        assert headers["tr_id"] == self.tr_id
        assert headers["custtype"] == "P"
        assert "hashkey" not in headers

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
        )
        mock_response.raise_for_status.assert_called_once()

    @patch("src.infra.broker.kis_http._pkg.requests.post")
    def test_fetch_hashkey_failure(self, mock_post):
        mock_post.side_effect = Exception("Network Error")
        mock_logger = MagicMock()

        data = {"key": "value"}
        result = fetch_hashkey(self.base_url, self.app_key, self.app_secret, data, mock_logger)

        assert result is None
        mock_logger.error.assert_called_once()
        assert "HashKey 생성 실패: Network Error" in mock_logger.error.call_args[0][0]

    @patch("src.infra.broker.kis_http._pkg.requests.post")
    def test_fetch_hashkey_no_logger(self, mock_post):
        mock_post.side_effect = Exception("Mock exception")
        data = {"key": "value"}

        result = fetch_hashkey(self.base_url, self.app_key, self.app_secret, data, None)

        assert result is None

    @patch("src.infra.broker.kis_http._pkg.requests.post")
    def test_build_header_with_data_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"HASH": "fake_hash_456"}
        mock_post.return_value = mock_response

        data = {"price": 100}
        headers = build_header(
            base_url=self.base_url,
            app_key=self.app_key,
            app_secret=self.app_secret,
            access_token=self.access_token,
            tr_id=self.tr_id,
            data=data
        )

        assert headers["hashkey"] == "fake_hash_456"

    @patch("src.infra.broker.kis_http._pkg.requests.post")
    def test_build_header_with_data_failure(self, mock_post):
        mock_post.side_effect = Exception("API Error")

        data = {"price": 100}
        with pytest.raises(ValueError, match="HashKey 생성 실패로 주문 헤더를 생성할 수 없습니다."):
            build_header(
                base_url=self.base_url,
                app_key=self.app_key,
                app_secret=self.app_secret,
                access_token=self.access_token,
                tr_id=self.tr_id,
                data=data
            )
