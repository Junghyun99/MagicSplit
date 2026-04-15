import pytest
from unittest.mock import patch, MagicMock
from src.infra.broker.kis_domestic import KisDomesticLiveBroker
import src.infra.broker as _pkg
import requests

class TestKisDomesticBrokerTimeouts:
    @patch('src.infra.broker.kis_domestic.KisDomesticLiveBroker._auth', return_value="dummy_token")
    @patch('src.infra.broker.requests.get')
    def test_fetch_current_prices_timeout(self, mock_get, mock_auth):
        mock_logger = MagicMock()
        broker = KisDomesticLiveBroker("app_key", "app_secret", "1234567812", mock_logger)

        mock_get.side_effect = _pkg.requests.exceptions.Timeout("Connection timed out")

        prices = broker.fetch_current_prices(["005930"])

        assert prices == {"005930": 0.0}
        mock_logger.error.assert_called_with("[KisDomestic] Price fetch error 005930: Timeout")

    @patch('src.infra.broker.kis_domestic.KisDomesticLiveBroker._auth', return_value="dummy_token")
    @patch('src.infra.broker.requests.get')
    def test_get_portfolio_timeout(self, mock_get, mock_auth):
        mock_logger = MagicMock()
        broker = KisDomesticLiveBroker("app_key", "app_secret", "1234567812", mock_logger)

        mock_get.side_effect = _pkg.requests.exceptions.Timeout("Connection timed out")

        portfolio = broker.get_portfolio()

        assert portfolio.total_cash == 0.0
        assert portfolio.holdings == {}

        mock_logger.error.assert_any_call("[KisDomestic] Error getting portfolio: Timeout")

    @patch('src.infra.broker.kis_domestic.KisDomesticLiveBroker._auth', return_value="dummy_token")
    @patch('src.infra.broker.kis_base.KisBrokerCommon._get_hashkey', return_value="dummy_hash")
    @patch('src.infra.broker.kis_domestic.KisDomesticLiveBroker._fetch_asking_price', return_value=(70000.0, 70100.0))
    @patch('src.infra.broker.kis_http.fetch_hashkey', return_value="dummy_hash")
    @patch('src.infra.broker.requests.post')
    def test_send_order_and_wait_timeout(self, mock_post, mock_fetch_hashkey, mock_asking_price, mock_get_hashkey, mock_auth):
        from src.core.models import Order, OrderAction

        mock_logger = MagicMock()
        broker = KisDomesticLiveBroker("app_key", "app_secret", "1234567812", mock_logger)

        mock_post.side_effect = _pkg.requests.exceptions.Timeout("Connection timed out")

        order = Order(ticker="005930", action=OrderAction.BUY, quantity=1, price=70000.0)

        execution = broker._send_order_and_wait(order)

        assert execution is None
        mock_logger.error.assert_any_call("[KisDomestic] Order Error: Timeout")
