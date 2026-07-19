# tests/test_strategy_config.py
import json
import os
import pytest
from src.strategy_config import StrategyConfig

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestStrategyConfig:
    def test_load_valid_config(self, tmp_path):
        """유효한 config_overseas.json 로드"""
        config = {
            "stocks": [
                {
                    "ticker": "AAPL",
                    "buy_threshold_pct": -5.0,
                    "sell_threshold_pct": 10.0,
                    "buy_amount": 500,
                    "max_lots": 10,
                    "enabled": True,
                }
            ],
            "global": { },
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))

        assert len(sc.rules) == 1
        assert sc.rules[0].ticker == "AAPL"
        assert sc.rules[0].buy_threshold_pct == -5.0
        assert sc.rules[0].sell_threshold_pct == 10.0
        assert sc.rules[0].buy_amount == 500
        assert sc.rules[0].max_lots == 10
        assert sc.rules[0].enabled is True

    def test_load_multiple_stocks(self, tmp_path):
        """여러 종목 로드"""
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "MSFT", "buy_amount": 1000},
            ]
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert len(sc.rules) == 2

    def test_priority_parsed(self, tmp_path):
        """priority 필드가 있으면 파싱되고, 없으면 None이다."""
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500, "priority": 1},
                {"ticker": "MSFT", "buy_amount": 500},
            ]
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].priority == 1
        assert sc.rules[1].priority is None

    def test_file_not_found(self):
        """존재하지 않는 파일"""
        with pytest.raises(FileNotFoundError):
            StrategyConfig("/nonexistent/config_overseas.json")

    def test_empty_stocks(self, tmp_path):
        """stocks가 비어있으면 ValueError"""
        config = {"stocks": []}
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="비어 있습니다"):
            StrategyConfig(str(config_file))

    def test_missing_ticker(self, tmp_path):
        """ticker가 없으면 ValueError"""
        config = {"stocks": [{"buy_amount": 500}]}
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="ticker"):
            StrategyConfig(str(config_file))

    def test_default_values(self, tmp_path):
        """기본값 적용 확인"""
        config = {"stocks": [{"ticker": "AAPL"}]}
        config_file = tmp_path / "config_overseas.json"
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
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].enabled is False

    def test_unknown_ticker_raises(self, tmp_path):
        """tickers.db에 등록되지 않은 티커는 ValueError로 거부."""
        config = {"stocks": [{"ticker": "NEWSTOCK"}]}
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="NEWSTOCK"):
            StrategyConfig(str(config_file))


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
        config_file = tmp_path / "config_overseas.json"
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
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        rule = StrategyConfig(str(config_file)).rules[0]
        assert rule.buy_threshold_pct == -5.0
        assert rule.buy_threshold_pcts == [-3.0, -5.0, -7.0]
        assert rule.buy_threshold_at(1) == -3.0  # 배열 우선


class TestPresets:
    """공유 프리셋 파일 로딩 및 병합"""

    def _write(self, tmp_path, cfg: dict, presets: dict | None = None):
        cfg_file = tmp_path / "config_overseas.json"
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
        cfg = {"stocks": [{"ticker": "AAPL", "preset": "large_cap_us"}]}
        cfg_path = self._write(tmp_path, cfg, presets)

        sc = StrategyConfig(cfg_path)
        rule = sc.rules[0]
        assert rule.ticker == "AAPL"
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
        cfg_file = tmp_path / "config_overseas.json"
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
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].max_exposure_pct == 20.0
        assert sc.rules[1].max_exposure_pct == 20.0

    def test_individual_overrides_global(self, tmp_path):
        """종목별 max_exposure_pct가 글로벌 설정을 오버라이드"""
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "TSLA", "buy_amount": 500, "max_exposure_pct": 5.0},
            ],
            "global": {"max_exposure_pct": 20.0},
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].max_exposure_pct == 20.0  # 글로벌 상속
        assert sc.rules[1].max_exposure_pct == 5.0   # 개별 오버라이드

    def test_no_global_no_individual_means_none(self, tmp_path):
        """글로벌도 개별도 설정 안 하면 None (비중 제한 없음)"""
        config = {
            "stocks": [{"ticker": "AAPL", "buy_amount": 500}],
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].max_exposure_pct is None


class TestTrailingDropConfig:
    """글로벌/개별 trailing_drop_pct 로딩 테스트"""

    def test_global_trailing_drop_applied_to_all(self, tmp_path):
        """global.trailing_drop_pct가 모든 종목에 상속됨"""
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "MSFT", "buy_amount": 1000},
            ],
            "global": {"trailing_drop_pct": 2.5},
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].trailing_drop_pct == 2.5
        assert sc.rules[1].trailing_drop_pct == 2.5

    def test_individual_overrides_global(self, tmp_path):
        """종목별 trailing_drop_pct가 글로벌 설정을 오버라이드"""
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "TSLA", "buy_amount": 500, "trailing_drop_pct": 5.0},
            ],
            "global": {"trailing_drop_pct": 2.5},
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].trailing_drop_pct == 2.5  # 글로벌 상속
        assert sc.rules[1].trailing_drop_pct == 5.0   # 개별 오버라이드

    def test_per_level_arrays(self, tmp_path):
        """trailing_drop_pcts 배열 로딩 확인"""
        config = {
            "stocks": [{
                "ticker": "AAPL",
                "trailing_drop_pcts": [1.0, 1.5, 2.0],
            }]
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        rule = sc.rules[0]
        assert rule.trailing_drop_pcts == [1.0, 1.5, 2.0]
        assert rule.trailing_drop_at(1) == 1.0
        assert rule.trailing_drop_at(2) == 1.5
        assert rule.trailing_drop_at(10) == 2.0  # clamp to last


class TestSpreadThresholdConfig:
    """글로벌/개별 spread_threshold_pct 로딩 및 검증 테스트"""

    def test_global_spread_threshold_applied_to_all(self, tmp_path):
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "MSFT", "buy_amount": 1000},
            ],
            "global": {"spread_threshold_pct": 1.5},
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].spread_threshold_pct == 1.5
        assert sc.rules[1].spread_threshold_pct == 1.5

    def test_individual_overrides_global(self, tmp_path):
        config = {
            "stocks": [
                {"ticker": "AAPL", "buy_amount": 500},
                {"ticker": "TSLA", "buy_amount": 500, "spread_threshold_pct": 2.0},
            ],
            "global": {"spread_threshold_pct": 1.5},
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].spread_threshold_pct == 1.5   # 글로벌 상속
        assert sc.rules[1].spread_threshold_pct == 2.0   # 개별 오버라이드

    def test_no_global_no_individual_means_none(self, tmp_path):
        config = {"stocks": [{"ticker": "AAPL", "buy_amount": 500}]}
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        sc = StrategyConfig(str(config_file))
        assert sc.rules[0].spread_threshold_pct is None

    def test_negative_value_raises(self, tmp_path):
        config = {
            "stocks": [{"ticker": "AAPL", "buy_amount": 500, "spread_threshold_pct": -1.0}],
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="spread_threshold_pct"):
            StrategyConfig(str(config_file))

    def test_negative_global_raises(self, tmp_path):
        config = {
            "stocks": [{"ticker": "AAPL", "buy_amount": 500}],
            "global": {"spread_threshold_pct": -0.5},
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="spread_threshold_pct"):
            StrategyConfig(str(config_file))


class TestStrategyConfigRegime:
    def test_regime_absent_defaults_off(self, tmp_path):
        config = {"stocks": [{"ticker": "AAPL"}]}
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        rule = StrategyConfig(str(config_file)).rules[0]
        assert rule.regime_enabled is False
        assert rule.regime_adx_trend == 25.0
        assert rule.uptrend_add_amounts is None

    def test_regime_fields_parsed_from_stock(self, tmp_path):
        config = {
            "stocks": [{
                "ticker": "AAPL",
                "regime_enabled": True,
                "regime_adx_trend": 30,
                "regime_adx_range": 18,
                "regime_min_bars": 150,
                "uptrend_pullback_band_pct": 2.0,
                "uptrend_max_adds": 4,
                "uptrend_add_amounts": [1500, 1000, 600],
                "uptrend_swing_lookback": 12,
                "trendbreak_chandelier_k": 2.5,
                "trendbreak_chandelier_lookback": 20,
                "trendbreak_use_sma50": False,
            }]
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        rule = StrategyConfig(str(config_file)).rules[0]
        assert rule.regime_enabled is True
        assert rule.regime_adx_trend == 30.0
        assert rule.regime_adx_range == 18.0
        assert rule.regime_min_bars == 150
        assert rule.uptrend_pullback_band_pct == 2.0
        assert rule.uptrend_max_adds == 4
        assert rule.uptrend_add_amounts == [1500.0, 1000.0, 600.0]
        assert rule.uptrend_swing_lookback == 12
        assert rule.trendbreak_chandelier_k == 2.5
        assert rule.trendbreak_chandelier_lookback == 20
        assert rule.trendbreak_use_sma50 is False

    def test_regime_global_inheritance_and_override(self, tmp_path):
        config = {
            "stocks": [
                {"ticker": "AAPL"},                          # 글로벌 상속
                {"ticker": "MSFT", "regime_adx_trend": 35},  # 개별 오버라이드
            ],
            "global": {"regime_enabled": True, "regime_adx_trend": 28},
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        rules = StrategyConfig(str(config_file)).rules
        assert rules[0].regime_enabled is True
        assert rules[0].regime_adx_trend == 28.0   # 글로벌
        assert rules[1].regime_enabled is True      # 글로벌 상속
        assert rules[1].regime_adx_trend == 35.0    # 개별 오버라이드

    def test_regime_via_preset(self, tmp_path):
        presets = {
            "trend_us": {
                "buy_amount": 1000,
                "regime_enabled": True,
                "uptrend_add_amounts": [1500, 1000, 600],
            }
        }
        config = {"stocks": [{"ticker": "AAPL", "preset": "trend_us"}]}
        (tmp_path / "presets.json").write_text(json.dumps(presets))
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        rule = StrategyConfig(str(config_file)).rules[0]
        assert rule.regime_enabled is True
        assert rule.uptrend_add_amounts == [1500.0, 1000.0, 600.0]

    def test_channel_fields_parsed_from_stock(self, tmp_path):
        config = {
            "stocks": [{
                "ticker": "AAPL",
                "regime_enabled": True,
                "regime_algo": "channel",
                "channel_lookback": 40,
                "channel_stddev_k": 1.5,
                "channel_slope_band_pct": 3.0,
                "channel_breakdown_tolerance_pct": 1.0,
            }]
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        rule = StrategyConfig(str(config_file)).rules[0]
        assert rule.regime_algo == "channel"
        assert rule.channel_lookback == 40
        assert rule.channel_stddev_k == 1.5
        assert rule.channel_slope_band_pct == 3.0
        assert rule.channel_breakdown_tolerance_pct == 1.0

    def test_channel_absent_defaults_ma_adx(self, tmp_path):
        config = {"stocks": [{"ticker": "AAPL", "regime_enabled": True}]}
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        rule = StrategyConfig(str(config_file)).rules[0]
        assert rule.regime_algo == "ma_adx"
        assert rule.channel_lookback == 63

    def test_channel_algo_global_inheritance_and_override(self, tmp_path):
        config = {
            "stocks": [
                {"ticker": "AAPL"},                        # 글로벌 상속
                {"ticker": "MSFT", "regime_algo": "ma_adx"},  # 개별 오버라이드
            ],
            "global": {
                "regime_enabled": True,
                "regime_algo": "channel",
                "channel_slope_band_pct": 4.0,
            },
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        rules = StrategyConfig(str(config_file)).rules
        assert rules[0].regime_algo == "channel"
        assert rules[0].channel_slope_band_pct == 4.0
        assert rules[1].regime_algo == "ma_adx"
        assert rules[1].channel_slope_band_pct == 4.0  # 글로벌 값 (미사용)

    def test_invalid_channel_algo_raises(self, tmp_path):
        config = {
            "stocks": [{
                "ticker": "AAPL",
                "regime_enabled": True,
                "regime_algo": "slope",
            }]
        }
        config_file = tmp_path / "config_overseas.json"
        config_file.write_text(json.dumps(config))

        with pytest.raises(ValueError, match="regime_algo"):
            StrategyConfig(str(config_file))


class TestRepoConfigRegimeSeparation:
    """백테스트 전용 config_test_*는 레짐 ON 불변식."""

    def test_backtest_overseas_regime_on(self):
        sc = StrategyConfig(os.path.join(REPO_ROOT, "config_test_overseas.json"))
        assert sc.rules and all(r.regime_enabled is True for r in sc.rules)

    def test_backtest_domestic_regime_on(self):
        sc = StrategyConfig(os.path.join(REPO_ROOT, "config_test_domestic.json"))
        assert sc.rules and all(r.regime_enabled is True for r in sc.rules)


class TestRepoCryptoConfig:
    """저장소 config_crypto.json 이 정상 로드되고 crypto 마켓 규칙인지 검증."""

    def test_crypto_config_loads(self):
        sc = StrategyConfig(os.path.join(REPO_ROOT, "config_crypto.json"))
        assert sc.rules, "config_crypto.json 규칙이 비어있음"
        # 전부 crypto 마켓, 소수 수량(정밀도 8)
        assert all(r.market_type == "crypto" for r in sc.rules)
        assert all(r.effective_qty_precision() == 8 for r in sc.rules)
        # 최소 하나는 활성화되어 있고 티커는 업비트 마켓 코드 형식
        active = [r for r in sc.rules if r.enabled]
        assert active, "활성화된 crypto 종목이 없음"
        assert all(r.ticker.startswith("KRW-") for r in sc.rules)
        # 매수 금액이 업비트 최소주문(5000 KRW) 이상
        assert all(r.buy_amount_at(1) >= 5000 for r in sc.rules)


class TestCryptoMarketAndQtyPrecision:
    """코인 마켓(crypto) 수용 및 qty_precision 파싱."""

    def _write(self, tmp_path, stock, fname="config_crypto.json"):
        config = {"stocks": [stock], "global": {}}
        f = tmp_path / fname
        f.write_text(json.dumps(config))
        return str(f)

    def test_crypto_market_type_accepted(self, tmp_path):
        path = self._write(tmp_path, {
            "ticker": "KRW-BTC",
            "buy_threshold_pct": -5.0,
            "sell_threshold_pct": 10.0,
            "buy_amount": 100000,
            "market_type": "crypto",
        })
        rule = StrategyConfig(path).rules[0]
        assert rule.market_type == "crypto"
        # 기본 정밀도 8 -> 소수 수량 지원
        assert rule.min_order_qty() == 10 ** -8

    def test_invalid_market_type_rejected(self, tmp_path):
        path = self._write(tmp_path, {
            "ticker": "XXX",
            "buy_threshold_pct": -5.0,
            "sell_threshold_pct": 10.0,
            "buy_amount": 100000,
            "market_type": "futures",
        })
        with pytest.raises(ValueError, match="market_type"):
            StrategyConfig(path)

    def test_qty_precision_override_parsed(self, tmp_path):
        path = self._write(tmp_path, {
            "ticker": "KRW-XRP",
            "buy_threshold_pct": -5.0,
            "sell_threshold_pct": 10.0,
            "buy_amount": 100000,
            "market_type": "crypto",
            "qty_precision": 4,
        })
        rule = StrategyConfig(path).rules[0]
        assert rule.qty_precision == 4
        assert rule.quantize_qty(0.123456) == pytest.approx(0.1234, abs=1e-12)

    def test_negative_qty_precision_rejected(self, tmp_path):
        path = self._write(tmp_path, {
            "ticker": "KRW-BTC",
            "buy_threshold_pct": -5.0,
            "sell_threshold_pct": 10.0,
            "buy_amount": 100000,
            "market_type": "crypto",
            "qty_precision": -1,
        })
        with pytest.raises(ValueError, match="qty_precision"):
            StrategyConfig(path)
