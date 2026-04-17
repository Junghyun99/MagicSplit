# tests/test_strategy_config.py
import json
import pytest
from src.strategy_config import StrategyConfig


class TestStrategyConfig:
    def test_load_valid_config(self, tmp_path):
        """유효한 config.json 로드"""
        config = {
            "stocks": [
                {
                    "ticker": "AAPL",
                    "exchange": "NAS",
                    "buy_threshold_pct": -5.0,
                    "sell_threshold_pct": 10.0,
                    "buy_amount": 500,
                    "max_lots": 10,
                    "enabled": True,
                }
            ],
            "global": {"check_interval_minutes": 60},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))

        assert len(sc.rules) == 1
        assert sc.rules[0].ticker == "AAPL"
        assert sc.rules[0].buy_threshold_pct == -5.0
        assert sc.rules[0].sell_threshold_pct == 10.0
        assert sc.rules[0].buy_amount == 500
        assert sc.rules[0].max_lots == 10
        assert sc.rules[0].enabled is True
        assert sc.rules[0].exchange == "NAS"

    def test_load_multiple_stocks(self, tmp_path):
        """여러 종목 로드"""
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "MSFT", "buy_amount": 1000},
            ]
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert len(sc.rules) == 2

    def test_file_not_found(self):
        """존재하지 않는 파일"""
        with pytest.raises(FileNotFoundError):
            StrategyConfig("/nonexistent/config.json")

    def test_empty_stocks(self, tmp_path):
        """stocks가 비어있으면 ValueError"""
        config = {"stocks": []}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="비어 있습니다"):
            StrategyConfig(str(config_file))

    def test_missing_ticker(self, tmp_path):
        """ticker가 없으면 ValueError"""
        config = {"stocks": [{"buy_amount": 500}]}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="ticker"):
            StrategyConfig(str(config_file))

    def test_default_values(self, tmp_path):
        """기본값 적용 확인"""
        config = {"stocks": [{"ticker": "AAPL"}]}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))

        assert sc.rules[0].buy_threshold_pct == -5.0
        assert sc.rules[0].sell_threshold_pct == 10.0
        assert sc.rules[0].buy_amount == 500
        assert sc.rules[0].max_lots == 10
        assert sc.rules[0].enabled is True

    def test_disabled_stock(self, tmp_path):
        """enabled: false인 종목도 로드됨 (필터링은 엔진에서)"""
        config = {"stocks": [{"ticker": "AAPL", "enabled": False}]}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].enabled is False

    def test_exchange_registered(self, tmp_path):
        """exchange 필드가 StockRule.exchange에 저장되고, TICKER_EXCHANGE_MAP은 변경되지 않음"""
        config = {
            "stocks": [{"ticker": "NEWSTOCK", "exchange": "NYS"}]
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))

        assert sc.rules[0].exchange == "NYS"
        from src.config import TICKER_EXCHANGE_MAP
        assert "NEWSTOCK" not in TICKER_EXCHANGE_MAP
