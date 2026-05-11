# tests/test_infra_data.py
from unittest.mock import MagicMock, patch
from src.infra.data import YFinanceLoader


class TestYFinanceLoader:
    def test_fetch_error_returns_zero(self):
        """yfinance import 실패 또는 에러 시 0.0 반환"""
        logger = MagicMock()
        loader = YFinanceLoader(logger)

        with patch.dict("sys.modules", {"yfinance": None}):
            result = loader.fetch_current_price("INVALID")

        assert result == 0.0
        logger.error.assert_called_once()
