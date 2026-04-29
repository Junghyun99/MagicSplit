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
            "global": { },
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


class TestPerLevelArrays:
    """차수별 배열 필드 로딩"""

    def test_arrays_loaded(self, tmp_path):
        config = {
            "stocks": [{
                "ticker": "AAPL",
                "buy_threshold_pcts": [-3, -5, -7, -10],
                "sell_threshold_pcts": [5, 7, 10, 15],
                "buy_amounts": [1000, 1500, 2000, 3000],
                "max_lots": 10,
            }]
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        rule = sc.rules[0]

        assert rule.buy_threshold_pcts == [-3.0, -5.0, -7.0, -10.0]
        assert rule.sell_threshold_pcts == [5.0, 7.0, 10.0, 15.0]
        assert rule.buy_amounts == [1000.0, 1500.0, 2000.0, 3000.0]
        assert rule.buy_threshold_pct is None
        assert rule.sell_threshold_pct is None
        assert rule.buy_amount is None

    def test_mixed_array_and_scalar(self, tmp_path):
        """배열과 단일값이 공존하면 둘 다 저장 (접근자는 배열 우선)."""
        config = {
            "stocks": [{
                "ticker": "AAPL",
                "buy_threshold_pct": -5.0,
                "buy_threshold_pcts": [-3, -5, -7],
                "sell_threshold_pct": 10.0,
                "buy_amount": 500,
            }]
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        rule = StrategyConfig(str(config_file)).rules[0]
        assert rule.buy_threshold_pct == -5.0
        assert rule.buy_threshold_pcts == [-3.0, -5.0, -7.0]
        assert rule.buy_threshold_at(1) == -3.0  # 배열 우선


class TestPresets:
    """공유 프리셋 파일 로딩 및 병합"""

    def _write(self, tmp_path, cfg: dict, presets: dict | None = None):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(cfg))
        if presets is not None:
            (tmp_path / "presets.json").write_text(json.dumps(presets))
        return str(cfg_file)

    def test_preset_applied(self, tmp_path):
        presets = {
            "large_cap_us": {
                "buy_threshold_pcts": [-3, -5, -7, -10],
                "sell_threshold_pcts": [5, 7, 10, 15],
                "buy_amounts": [1000, 1500, 2000, 3000],
                "max_lots": 10,
            }
        }
        cfg = {"stocks": [{"ticker": "AAPL", "exchange": "NAS", "preset": "large_cap_us"}]}
        cfg_path = self._write(tmp_path, cfg, presets)

        sc = StrategyConfig(cfg_path)
        rule = sc.rules[0]
        assert rule.ticker == "AAPL"
        assert rule.exchange == "NAS"
        assert rule.buy_threshold_pcts == [-3.0, -5.0, -7.0, -10.0]
        assert rule.buy_amounts == [1000.0, 1500.0, 2000.0, 3000.0]
        assert rule.max_lots == 10

    def test_stock_overrides_preset(self, tmp_path):
        presets = {
            "large_cap_us": {
                "buy_threshold_pcts": [-3, -5, -7, -10],
                "sell_threshold_pcts": [5, 7, 10, 15],
                "buy_amounts": [1000, 1500, 2000, 3000],
                "max_lots": 10,
            }
        }
        cfg = {"stocks": [{
            "ticker": "TSLA",
            "preset": "large_cap_us",
            "sell_threshold_pcts": [7, 10, 15, 25],
            "max_lots": 6,
        }]}
        cfg_path = self._write(tmp_path, cfg, presets)

        rule = StrategyConfig(cfg_path).rules[0]
        assert rule.sell_threshold_pcts == [7.0, 10.0, 15.0, 25.0]
        assert rule.buy_threshold_pcts == [-3.0, -5.0, -7.0, -10.0]  # from preset
        assert rule.max_lots == 6

    def test_unknown_preset_raises(self, tmp_path):
        presets = {"large_cap_us": {"buy_threshold_pcts": [-5], "sell_threshold_pcts": [10], "buy_amounts": [500]}}
        cfg = {"stocks": [{"ticker": "AAPL", "preset": "nonexistent"}]}
        cfg_path = self._write(tmp_path, cfg, presets)

        with pytest.raises(KeyError, match="nonexistent"):
            StrategyConfig(cfg_path)

    def test_missing_presets_file_ok_when_unused(self, tmp_path):
        """presets.json이 없어도 preset을 쓰지 않으면 정상 로드."""
        cfg = {"stocks": [{"ticker": "AAPL", "buy_amount": 500}]}
        cfg_path = self._write(tmp_path, cfg)

        sc = StrategyConfig(cfg_path)
        assert sc.presets == {}
        assert sc.rules[0].buy_amount == 500

    def test_missing_presets_file_fails_when_referenced(self, tmp_path):
        cfg = {"stocks": [{"ticker": "AAPL", "preset": "large_cap_us"}]}
        cfg_path = self._write(tmp_path, cfg)

        with pytest.raises(FileNotFoundError):
            StrategyConfig(cfg_path)

    def test_empty_presets_file_raises_key_error_not_file_not_found(self, tmp_path):
        """파일은 존재하나 비어 있으면 FileNotFoundError가 아니라 KeyError(미존재 preset)로 실패."""
        cfg = {"stocks": [{"ticker": "AAPL", "preset": "large_cap_us"}]}
        cfg_path = self._write(tmp_path, cfg, presets={})

        with pytest.raises(KeyError, match="large_cap_us"):
            StrategyConfig(cfg_path)

    def test_explicit_presets_path(self, tmp_path):
        presets_file = tmp_path / "custom_presets.json"
        presets_file.write_text(json.dumps({
            "p1": {"buy_threshold_pcts": [-5], "sell_threshold_pcts": [10], "buy_amounts": [500]}
        }))
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"stocks": [{"ticker": "AAPL", "preset": "p1"}]}))

        sc = StrategyConfig(str(cfg_file), presets_path=str(presets_file))
        assert sc.rules[0].buy_amounts == [500.0]


class TestMaxExposureConfig:
    """글로벌/개별 max_exposure_pct 로딩 테스트"""

    def test_global_max_exposure_applied_to_all(self, tmp_path):
        """global.max_exposure_pct가 모든 종목에 상속됨"""
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "MSFT", "buy_amount": 1000},
            ],
            "global": {"max_exposure_pct": 20.0},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].max_exposure_pct == 20.0
        assert sc.rules[1].max_exposure_pct == 20.0

    def test_individual_overrides_global(self, tmp_path):
        """종목별 max_exposure_pct가 글로벌 설정을 오버라이드"""
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "RISKY", "buy_amount": 500, "max_exposure_pct": 5.0},
            ],
            "global": {"max_exposure_pct": 20.0},
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].max_exposure_pct == 20.0  # 글로벌 상속
        assert sc.rules[1].max_exposure_pct == 5.0   # 개별 오버라이드

    def test_no_global_no_individual_means_none(self, tmp_path):
        """글로벌도 개별도 설정 안 하면 None (비중 제한 없음)"""
        config = {
            "stocks": [{"ticker": "AAPL", "buy_amount": 500}],
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].max_exposure_pct is None

