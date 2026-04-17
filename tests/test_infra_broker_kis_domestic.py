import pytest
from unittest.mock import patch, MagicMock
from src.infra.broker.kis_domestic import KisDomesticPaperBroker
from src.infra.broker.kis_domestic import _to_kis_code, _to_yf_ticker
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


def test_to_kis_code():
    # Standard KOSPI
    assert _to_kis_code("005930.KS") == "005930"
    # Standard KOSDAQ
    assert _to_kis_code("000660.KQ") == "000660"
    # Padding
    assert _to_kis_code("5930.KS") == "005930"
    # No extension
    assert _to_kis_code("005930") == "005930"

def test_to_yf_ticker():
    # Standard code
    assert _to_yf_ticker("005930") == "005930.KS"
    # Already has extension
    assert _to_yf_ticker("005930.KS") == "005930.KS"


def test_paper_broker_uses_mock_pending_tr_id():
    assert KisDomesticPaperBroker.PENDING_TR_ID == "VTTC0084R"
