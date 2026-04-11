# tests/test_main.py
"""MagicSplitBot 초기화/실행 테스트 — KIS 브로커는 목으로 대체."""
import os
import json
import pytest
from unittest.mock import patch, MagicMock

from src.main import (
    _resolve_engine_class,
    _create_broker,
    AccountRunner,
    MagicSplitBot,
)
from src.account_config import AccountConfig


def _make_account(
    id_="acc1",
    market_type="overseas",
    is_live=False,
    engine_name="MagicSplitEngine",
):
    return AccountConfig(
        id=id_,
        market_type=market_type,
        is_live=is_live,
        engine_name=engine_name,
        app_key="key",
        app_secret="secret",
        acc_no="12345678",
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
        acc = _make_account(market_type="overseas", is_live=False)
        logger = MagicMock()
        with patch("src.main.KisOverseasPaperBroker") as mock_cls:
            _create_broker(acc, logger)
            mock_cls.assert_called_once()

    def test_overseas_live(self):
        acc = _make_account(market_type="overseas", is_live=True)
        logger = MagicMock()
        with patch("src.main.KisOverseasLiveBroker") as mock_cls:
            _create_broker(acc, logger)
            mock_cls.assert_called_once()

    def test_domestic_paper(self):
        acc = _make_account(market_type="domestic", is_live=False)
        logger = MagicMock()
        with patch("src.main.KisDomesticPaperBroker") as mock_cls:
            _create_broker(acc, logger)
            mock_cls.assert_called_once()

    def test_domestic_live(self):
        acc = _make_account(market_type="domestic", is_live=True)
        logger = MagicMock()
        with patch("src.main.KisDomesticLiveBroker") as mock_cls:
            _create_broker(acc, logger)
            mock_cls.assert_called_once()


class TestAccountRunner:
    def test_holds_components(self):
        acc = _make_account()
        engine, broker, repo = MagicMock(), MagicMock(), MagicMock()
        runner = AccountRunner(acc, engine, broker, repo)
        assert runner.account is acc
        assert runner.engine is engine
        assert runner.broker is broker
        assert runner.repo is repo


class TestMagicSplitBot:
    @pytest.fixture
    def bot_env(self, tmp_path, monkeypatch):
        """config.json과 accounts.yaml을 tmp_path에 준비한 환경."""
        # config.json
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "stocks": [
                {
                    "ticker": "AAPL",
                    "buy_threshold_pct": -5.0,
                    "sell_threshold_pct": 10.0,
                    "buy_amount": 500,
                    "max_lots": 10,
                    "enabled": True,
                },
            ],
            "global": {"check_interval_minutes": 60},
        }))

        # accounts.yaml
        acc_path = tmp_path / "accounts.yaml"
        acc_path.write_text(
            "accounts:\n"
            "  - id: acc1\n"
            "    market_type: overseas\n"
            "    is_live: false\n"
            "    engine: MagicSplitEngine\n"
            "    kis_env_prefix: TEST\n"
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CONFIG_JSON_PATH", str(cfg_path))
        monkeypatch.setenv("ACCOUNTS_CONFIG_PATH", str(acc_path))
        monkeypatch.setenv("TEST_KIS_APP_KEY", "k")
        monkeypatch.setenv("TEST_KIS_APP_SECRET", "s")
        monkeypatch.setenv("TEST_KIS_ACC_NO", "12345678")

        return tmp_path

    def test_init_and_run(self, bot_env):
        with patch("src.main.KisOverseasPaperBroker") as broker_cls, \
             patch("src.main.SlackNotifier") as notifier_cls:
            broker_cls.return_value = MagicMock()
            notifier_cls.return_value = MagicMock()

            bot = MagicSplitBot()
            assert len(bot.runners) == 1
            assert bot.engine is bot.runners[0].engine
            assert bot.broker is bot.runners[0].broker
            assert bot.repo is bot.runners[0].repo

            # run_one_cycle 자체는 엔진이 호출. 엔진을 목으로 갈아끼워 run() 검증
            bot.runners[0].engine = MagicMock()
            bot.run()
            bot.runners[0].engine.run_one_cycle.assert_called_once()

    def test_run_one_account_failure_raises(self, bot_env):
        with patch("src.main.KisOverseasPaperBroker"), \
             patch("src.main.SlackNotifier") as notifier_cls:
            notifier_cls.return_value = MagicMock()
            bot = MagicSplitBot()

            # 엔진이 예외를 던지도록 설정
            bot.runners[0].engine = MagicMock()
            bot.runners[0].engine.run_one_cycle.side_effect = RuntimeError("boom")

            with pytest.raises(RuntimeError, match="boom"):
                bot.run()
