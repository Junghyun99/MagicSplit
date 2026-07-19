# tests/test_main.py
"""MagicSplitBot 초기화/실행 테스트 — KIS 브로커는 목으로 대체."""
import os
import json
import pytest
from unittest.mock import patch, MagicMock

from src.main import (
    _resolve_engine_class,
    _create_broker,
    MagicSplitBot,
)


class TestResolveEngineClass:
    def test_resolves_registered_engine(self):
        cls = _resolve_engine_class("MagicSplitEngine")
        assert cls is not None

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError, match="알 수 없는 엔진"):
            _resolve_engine_class("NonExistentEngine")


class TestCreateBroker:
    def test_overseas_paper(self):
        logger = MagicMock()
        with patch("src.main.KisOverseasPaperBroker") as mock_cls:
            _create_broker("overseas", False, "k", "s", "12345678", logger)
            mock_cls.assert_called_once()

    def test_overseas_live(self):
        logger = MagicMock()
        with patch("src.main.KisOverseasLiveBroker") as mock_cls:
            _create_broker("overseas", True, "k", "s", "12345678", logger)
            mock_cls.assert_called_once()

    def test_domestic_paper(self):
        logger = MagicMock()
        with patch("src.main.KisDomesticPaperBroker") as mock_cls:
            _create_broker("domestic", False, "k", "s", "12345678", logger)
            mock_cls.assert_called_once()

    def test_domestic_live(self):
        logger = MagicMock()
        with patch("src.main.KisDomesticLiveBroker") as mock_cls:
            _create_broker("domestic", True, "k", "s", "12345678", logger)
            mock_cls.assert_called_once()


class TestMagicSplitBot:
    @pytest.fixture
    def bot_env(self, tmp_path, monkeypatch):
        """config_overseas.json과 단일 계좌 env vars를 준비한 환경."""
        # config.json
        cfg_path = tmp_path / "config_overseas.json"
        cfg_path.write_text(json.dumps({
            "stocks": [
                {
                    "ticker": "AAPL",
                    "market_type": "overseas",
                    "buy_threshold_pct": -5.0,
                    "sell_threshold_pct": 10.0,
                    "buy_amount": 500,
                    "max_lots": 10,
                    "enabled": True,
                },
            ],
            "global": { },
        }))

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CONFIG_JSON_PATH", str(cfg_path))
        monkeypatch.setenv("KIS_APP_KEY", "k")
        monkeypatch.setenv("KIS_APP_SECRET", "s")
        monkeypatch.setenv("KIS_ACC_NO", "12345678")
        monkeypatch.setenv("IS_LIVE", "false")

        return tmp_path

    def test_init_and_run(self, bot_env):
        with patch("src.main.KisOverseasPaperBroker") as broker_cls, \
             patch("src.main.SlackNotifier") as notifier_cls:
            broker_cls.return_value = MagicMock()
            notifier_cls.return_value = MagicMock()

            bot = MagicSplitBot()
            assert bot.market_type == "overseas"
            assert bot.engine is not None

            # run_one_cycle 자체는 엔진이 호출. 엔진을 목으로 갈아끼워 run() 검증
            bot.engine = MagicMock()
            bot.run()
            bot.engine.run_one_cycle.assert_called_once()

    def test_run_engine_failure_raises(self, bot_env):
        with patch("src.main.KisOverseasPaperBroker"), \
             patch("src.main.SlackNotifier") as notifier_cls:
            notifier_cls.return_value = MagicMock()
            bot = MagicSplitBot()

            # 엔진이 예외를 던지도록 설정
            mock_engine = MagicMock()
            mock_engine.run_one_cycle.side_effect = RuntimeError("boom")
            bot.engine = mock_engine

            with pytest.raises(RuntimeError, match="boom"):
                bot.run()

    def test_no_active_stocks_raises(self, tmp_path, monkeypatch):
        """활성 종목이 없으면 ValueError"""
        cfg_path = tmp_path / "config_overseas.json"
        cfg_path.write_text(json.dumps({
            "stocks": [
                {
                    "ticker": "AAPL",
                    "market_type": "overseas",
                    "buy_threshold_pct": -5.0,
                    "sell_threshold_pct": 10.0,
                    "buy_amount": 500,
                    "max_lots": 10,
                    "enabled": False,
                },
            ],
            "global": {},
        }))

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CONFIG_JSON_PATH", str(cfg_path))
        monkeypatch.setenv("KIS_APP_KEY", "k")
        monkeypatch.setenv("KIS_APP_SECRET", "s")
        monkeypatch.setenv("KIS_ACC_NO", "12345678")

        with pytest.raises(ValueError, match="활성화된 종목이 없습니다"):
            MagicSplitBot()


class TestCreateMarketData:
    """레짐 필터 사용 여부에 따른 과거 일봉 제공자 주입 판단."""

    def _bot_stub(self, market_type):
        bot = MagicSplitBot.__new__(MagicSplitBot)  # __init__ 우회
        bot.market_type = market_type
        bot.logger = MagicMock()
        return bot

    def _rule(self, **over):
        from src.core.models import StockRule
        base = dict(ticker="AAPL", buy_threshold_pct=-5.0, sell_threshold_pct=10.0,
                    buy_amount=500, max_lots=10)
        base.update(over)
        return StockRule(**base)

    def test_none_when_regime_disabled(self):
        bot = self._bot_stub("overseas")
        assert bot._create_market_data([self._rule()]) is None

    def test_yfinance_provider_for_overseas(self):
        from src.infra.data import YFinanceMarketDataProvider
        bot = self._bot_stub("overseas")
        rules = [self._rule(regime_enabled=True, regime_algo="channel")]
        provider = bot._create_market_data(rules)
        assert isinstance(provider, YFinanceMarketDataProvider)
        # ma_adx 기본 min_bars 200 + 60 여유
        assert provider.window_size == 260

    def test_upbit_provider_for_crypto(self):
        from src.infra.data import UpbitMarketDataProvider
        bot = self._bot_stub("crypto")
        rules = [self._rule(ticker="KRW-BTC", market_type="crypto",
                            regime_enabled=True, regime_algo="channel")]
        provider = bot._create_market_data(rules)
        assert isinstance(provider, UpbitMarketDataProvider)
