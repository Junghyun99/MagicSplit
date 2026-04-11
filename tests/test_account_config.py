# tests/test_account_config.py
import os
import pytest
from unittest.mock import patch
from src.account_config import load_accounts, AccountConfig


class TestAccountConfig:
    def test_dataclass_creation(self):
        acc = AccountConfig(
            id="test",
            market_type="overseas",
            is_live=False,
            engine_name="MagicSplitEngine",
            app_key="key",
            app_secret="secret",
            acc_no="12345678",
        )
        assert acc.id == "test"
        assert acc.market_type == "overseas"

    def test_invalid_market_type(self):
        with pytest.raises(ValueError, match="market_type"):
            AccountConfig(
                id="test",
                market_type="invalid",
                is_live=False,
                engine_name="MagicSplitEngine",
                app_key="key",
                app_secret="secret",
                acc_no="12345678",
            )

    def test_domestic_market_type(self):
        acc = AccountConfig(
            id="test",
            market_type="domestic",
            is_live=True,
            engine_name="MagicSplitEngine",
            app_key="key",
            app_secret="secret",
            acc_no="12345678",
        )
        assert acc.market_type == "domestic"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_accounts("/nonexistent/accounts.yaml")

    def test_load_valid_accounts(self, tmp_path):
        """유효한 accounts.yaml 로드"""
        yaml_content = """
accounts:
  - id: test_acc
    market_type: overseas
    is_live: false
    engine: MagicSplitEngine
    kis_env_prefix: TEST
"""
        config_file = tmp_path / "accounts.yaml"
        config_file.write_text(yaml_content)

        env_vars = {
            "TEST_KIS_APP_KEY": "test_key",
            "TEST_KIS_APP_SECRET": "test_secret",
            "TEST_KIS_ACC_NO": "12345678",
        }
        with patch.dict(os.environ, env_vars):
            accounts = load_accounts(str(config_file))

        assert len(accounts) == 1
        assert accounts[0].id == "test_acc"
        assert accounts[0].engine_name == "MagicSplitEngine"

    def test_missing_env_vars(self, tmp_path):
        """환경변수 누락 시 ValueError"""
        yaml_content = """
accounts:
  - id: test_acc
    market_type: overseas
    is_live: false
    engine: MagicSplitEngine
    kis_env_prefix: MISSING
"""
        config_file = tmp_path / "accounts.yaml"
        config_file.write_text(yaml_content)

        with pytest.raises(ValueError, match="MISSING_KIS_APP_KEY"):
            load_accounts(str(config_file))

    def test_empty_accounts(self, tmp_path):
        """accounts가 비어있으면 ValueError"""
        yaml_content = "accounts: []\n"
        config_file = tmp_path / "accounts.yaml"
        config_file.write_text(yaml_content)

        with pytest.raises(ValueError, match="비어 있습니다"):
            load_accounts(str(config_file))

    def test_duplicate_id(self, tmp_path):
        """중복 id ValueError"""
        yaml_content = """
accounts:
  - id: same_id
    market_type: overseas
    is_live: false
    engine: MagicSplitEngine
    kis_env_prefix: ACC1
  - id: same_id
    market_type: overseas
    is_live: false
    engine: MagicSplitEngine
    kis_env_prefix: ACC2
"""
        config_file = tmp_path / "accounts.yaml"
        config_file.write_text(yaml_content)

        env_vars = {
            "ACC1_KIS_APP_KEY": "k", "ACC1_KIS_APP_SECRET": "s", "ACC1_KIS_ACC_NO": "n",
            "ACC2_KIS_APP_KEY": "k", "ACC2_KIS_APP_SECRET": "s", "ACC2_KIS_ACC_NO": "n",
        }
        with patch.dict(os.environ, env_vars):
            with pytest.raises(ValueError, match="중복"):
                load_accounts(str(config_file))
