# tests/test_config.py
import pytest
from src.config import Config, TICKER_EXCHANGE_MAP, EXCHANGE_CODE_SHORT_TO_FULL


class TestConfig:
    def test_default_values(self, monkeypatch):
        monkeypatch.delenv("CONFIG_JSON_PATH", raising=False)
        monkeypatch.delenv("IS_LIVE", raising=False)
        config = Config()
        assert config.DATA_PATH == "docs/data"
        assert config.LOG_PATH == "logs"
        assert config.CONFIG_JSON_PATH == "config_overseas.json"
        assert config.MAX_HISTORY_RECORDS == 100000
        assert config.IS_LIVE is False

    def test_ticker_exchange_map(self):
        assert "AAPL" in TICKER_EXCHANGE_MAP
        assert TICKER_EXCHANGE_MAP["AAPL"] == "NAS"

    def test_exchange_code_mapping(self):
        assert EXCHANGE_CODE_SHORT_TO_FULL["NAS"] == "NASD"
        assert EXCHANGE_CODE_SHORT_TO_FULL["NYS"] == "NYSE"
        assert EXCHANGE_CODE_SHORT_TO_FULL["AMS"] == "AMEX"
