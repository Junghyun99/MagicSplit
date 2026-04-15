import pytest
from unittest.mock import patch, MagicMock
from src.infra.broker.kis_domestic import KisDomesticPaperBroker
from src.config import DEFAULT_HTTP_TIMEOUT

class TestKisDomesticBroker:
    @patch("src.infra.broker.kis_domestic._pkg.requests.get")
    def test_fetch_current_prices_timeout(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'rt_cd': '0',
            'output': {'stck_prpr': '100'}
        }
        mock_get.return_value = mock_response

        logger = MagicMock()
        # Mock auth to avoid real API call
        with patch.object(KisDomesticPaperBroker, "_auth", return_value="fake_token"):
            broker = KisDomesticPaperBroker("key", "secret", "acc", logger)
            broker.token_expires_at = None # ensure _auth not called again unnecessarily if not needed
            prices = broker.fetch_current_prices(["069500.KS"])

            assert prices["069500.KS"] == 100.0
            args, kwargs = mock_get.call_args
            assert kwargs["timeout"] == DEFAULT_HTTP_TIMEOUT
