import pytest
from unittest.mock import patch, MagicMock
from src.infra.broker.kis_base import KisBrokerCommon
from src.config import DEFAULT_HTTP_TIMEOUT

class TestKisTokenCache:
    @patch("src.infra.broker.kis_base._pkg.requests.post")
    @patch("src.infra.broker.kis_base.kis_token_cache.load_token_from_cache", return_value=None)
    @patch("src.infra.broker.kis_base.kis_token_cache.save_token_to_cache")
    def test_auth_timeout(self, mock_save, mock_load, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_token",
            "expires_in": 3600
        }
        mock_post.return_value = mock_response

        logger = MagicMock()
        # KisBrokerCommon is abstract-ish, but let's see if we can instantiate it for testing _auth
        with patch.multiple(KisBrokerCommon, __abstractmethods__=set()):
            broker = KisBrokerCommon("key", "secret", "acc", logger)
            # KisBrokerCommon calls _auth in __init__

            args, kwargs = mock_post.call_args
            assert kwargs["timeout"] == DEFAULT_HTTP_TIMEOUT
