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


class TestSlackConfigFor:
    def test_market_specific_overrides_generic(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://generic")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_GENERIC")
        monkeypatch.setenv("SLACK_WEBHOOK_DOMESTIC", "https://domestic")
        monkeypatch.setenv("SLACK_CHANNEL_ID_DOMESTIC", "C_DOMESTIC")
        config = Config()
        webhook, channel = config.slack_config_for("domestic")
        assert webhook == "https://domestic"
        assert channel == "C_DOMESTIC"

    def test_falls_back_to_generic(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://generic")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_GENERIC")
        monkeypatch.delenv("SLACK_WEBHOOK_OVERSEAS", raising=False)
        monkeypatch.delenv("SLACK_CHANNEL_ID_OVERSEAS", raising=False)
        config = Config()
        webhook, channel = config.slack_config_for("overseas")
        assert webhook == "https://generic"
        assert channel == "C_GENERIC"

    def test_partial_override(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://generic")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_GENERIC")
        monkeypatch.setenv("SLACK_CHANNEL_ID_CRYPTO", "C_CRYPTO")
        monkeypatch.delenv("SLACK_WEBHOOK_CRYPTO", raising=False)
        config = Config()
        webhook, channel = config.slack_config_for("crypto")
        assert webhook == "https://generic"
        assert channel == "C_CRYPTO"

    def test_unknown_market_falls_back(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://generic")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_GENERIC")
        config = Config()
        webhook, channel = config.slack_config_for("unknown")
        assert webhook == "https://generic"
        assert channel == "C_GENERIC"


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
