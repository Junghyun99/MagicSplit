# src/main.py
import sys
import traceback
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from typing import List

from src.config import Config
from src.strategy_config import StrategyConfig
from src.account_config import AccountConfig, load_accounts
from src.core.engine import MagicSplitEngine  # noqa: F401  (레지스트리 등록)
from src.core.engine.registry import _ENGINE_REGISTRY
from src.utils.logger import TradeLogger
from src.infra.broker import (
    KisOverseasPaperBroker,
    KisOverseasLiveBroker,
    KisDomesticPaperBroker,
    KisDomesticLiveBroker,
)
from src.infra.notifier import SlackNotifier
from src.infra.repo import JsonRepository


def _resolve_engine_class(engine_name: str):
    """_ENGINE_REGISTRY에서 이름으로 엔진 클래스를 찾는다."""
    for name, cls in _ENGINE_REGISTRY:
        if name == engine_name:
            return cls
    registered = ", ".join(name for name, _ in _ENGINE_REGISTRY) or "(none)"
    raise ValueError(
        f"알 수 없는 엔진 '{engine_name}'. 등록된 엔진: {registered}"
    )


def _create_broker(acc: AccountConfig, logger):
    """(market_type, is_live) 조합에 따라 KIS 브로커를 생성."""
    args = (acc.app_key, acc.app_secret, acc.acc_no, logger)
    if acc.market_type == "domestic":
        return KisDomesticLiveBroker(*args) if acc.is_live else KisDomesticPaperBroker(*args)
    return KisOverseasLiveBroker(*args) if acc.is_live else KisOverseasPaperBroker(*args)


class AccountRunner:
    """한 계좌에 대한 broker/repo/engine 묶음."""
    def __init__(self, account: AccountConfig, engine, broker, repo):
        self.account = account
        self.engine = engine
        self.broker = broker
        self.repo = repo


class MagicSplitBot:
    def __init__(self):
        # 1. 공용 설정 및 인프라
        self.config = Config()
        self.strategy = StrategyConfig(self.config.CONFIG_JSON_PATH)
        self.logger = TradeLogger(self.config.LOG_PATH)
        self.logger.info("=== Initializing MagicSplit Bot (multi-account) ===")

        self.notifier = SlackNotifier(self.config.SLACK_WEBHOOK_URL, self.logger)

        # 2. accounts.yaml 로드
        accounts = load_accounts(self.config.ACCOUNTS_CONFIG_PATH)
        self.logger.info(
            f"Loaded {len(accounts)} account(s) from {self.config.ACCOUNTS_CONFIG_PATH}"
        )
        self.logger.info(
            f"Loaded {len(self.strategy.rules)} stock rule(s) from {self.config.CONFIG_JSON_PATH}"
        )

        # 3. 계좌별 러너 구성
        self.runners: List[AccountRunner] = []
        for acc in accounts:
            self.logger.info(
                f"[{acc.id}] engine={acc.engine_name} market={acc.market_type} "
                f"mode={'LIVE' if acc.is_live else 'PAPER'}"
            )
            engine_cls = _resolve_engine_class(acc.engine_name)
            broker = _create_broker(acc, self.logger)
            repo = JsonRepository(
                os.path.join(self.config.DATA_PATH, acc.id),
                max_history_records=self.config.MAX_HISTORY_RECORDS,
            )
            engine = engine_cls(
                broker=broker,
                repo=repo,
                logger=self.logger,
                stock_rules=self.strategy.rules,
                notifier=self.notifier,
                is_live_trading=acc.is_live,
            )
            self.runners.append(AccountRunner(acc, engine, broker, repo))

        if not self.runners:
            raise ValueError("등록된 계좌가 없습니다. accounts.yaml을 확인하세요.")

    # --- 하위 호환: 기존 테스트/코드가 bot.engine / bot.broker / bot.repo 접근 ---
    @property
    def engine(self):
        return self.runners[0].engine

    @property
    def broker(self):
        return self.runners[0].broker

    @property
    def repo(self):
        return self.runners[0].repo

    def _run_one_account(self, runner: AccountRunner):
        acc = runner.account
        try:
            runner.engine.run_one_cycle()
        except Exception as e:
            error_msg = f"[{acc.id}] Critical Error:\n{traceback.format_exc()}"
            self.logger.error(error_msg)
            self.notifier.send_alert(f"[{acc.id}] Bot Crashed!\n{str(e)}")
            raise

    def run(self):
        """모든 계좌를 순차적으로 실행. 한 계좌 실패 시 다른 계좌는 계속 진행."""
        last_exc: Exception | None = None
        for runner in self.runners:
            try:
                self._run_one_account(runner)
            except Exception as e:
                last_exc = e
        if last_exc is not None:
            raise last_exc


if __name__ == "__main__":
    bot = MagicSplitBot()
    bot.run()
