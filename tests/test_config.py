# tests/test_config.py
import pytest
from src.config import Config, EXCHANGE_CODE_SHORT_TO_FULL, _parse_http_timeout


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

    def test_exchange_code_mapping(self):
        assert EXCHANGE_CODE_SHORT_TO_FULL["NAS"] == "NASD"
        assert EXCHANGE_CODE_SHORT_TO_FULL["NYS"] == "NYSE"
        assert EXCHANGE_CODE_SHORT_TO_FULL["AMS"] == "AMEX"


class TestParseHttpTimeout:
    def test_valid_value(self):
        assert _parse_http_timeout("30") == 30.0
        assert _parse_http_timeout("2.5") == 2.5

    def test_invalid_string_falls_back_to_default(self):
        assert _parse_http_timeout("abc") == 10.0
        assert _parse_http_timeout("") == 10.0
        assert _parse_http_timeout(None) == 10.0

    def test_non_positive_falls_back_to_default(self):
        assert _parse_http_timeout("0") == 10.0
        assert _parse_http_timeout("-5") == 10.0
