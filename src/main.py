# src/main.py
import sys
import traceback
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from typing import List, Tuple

from src.config import Config
from src.strategy_config import StrategyConfig
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


def _create_broker(market_type: str, is_live: bool,
                    app_key: str, app_secret: str, acc_no: str, logger):
    """(market_type, is_live) 조합에 따라 KIS 브로커를 생성."""
    args = (app_key, app_secret, acc_no, logger)
    if market_type == "domestic":
        return KisDomesticLiveBroker(*args) if is_live else KisDomesticPaperBroker(*args)
    return KisOverseasLiveBroker(*args) if is_live else KisOverseasPaperBroker(*args)


class MagicSplitBot:
    def __init__(self):
        # 1. 공용 설정 및 인프라
        self.config = Config()
        self.strategy = StrategyConfig(self.config.CONFIG_JSON_PATH)
        self.logger = TradeLogger(self.config.LOG_PATH)
        self.logger.info("=== Initializing MagicSplit Bot (single account) ===")

        self.notifier = SlackNotifier(self.config.SLACK_WEBHOOK_URL, self.logger)

        self.logger.info(
            f"Loaded {len(self.strategy.rules)} stock rule(s) from {self.config.CONFIG_JSON_PATH}"
        )

        # 2. 마켓별 엔진 생성 (국내/해외 독립 운용)
        self.engines: List[Tuple[str, MagicSplitEngine]] = []

        for market_type in ("domestic", "overseas"):
            rules = [r for r in self.strategy.get_rules_by_market(market_type)
                     if r.enabled]
            if not rules:
                continue

            self.logger.info(
                f"[{market_type}] {len(rules)} rule(s), "
                f"mode={'LIVE' if self.config.IS_LIVE else 'PAPER'}"
            )

            broker = _create_broker(
                market_type=market_type,
                is_live=self.config.IS_LIVE,
                app_key=self.config.KIS_APP_KEY,
                app_secret=self.config.KIS_APP_SECRET,
                acc_no=self.config.KIS_ACC_NO,
                logger=self.logger,
            )
            repo = JsonRepository(
                os.path.join(self.config.DATA_PATH, market_type),
                max_history_records=self.config.MAX_HISTORY_RECORDS,
            )
            engine = MagicSplitEngine(
                broker=broker,
                repo=repo,
                logger=self.logger,
                stock_rules=rules,
                notifier=self.notifier,
                is_live_trading=self.config.IS_LIVE,
            )
            self.engines.append((market_type, engine))

        if not self.engines:
            raise ValueError(
                "활성화된 종목이 없습니다. config.json의 stocks 항목을 확인하세요."
            )

    def run(self):
        """모든 마켓 엔진을 순차적으로 실행. 한 마켓 실패 시 다른 마켓은 계속 진행."""
        last_exc: Exception | None = None
        for market_type, engine in self.engines:
            try:
                self.logger.info(f"=== Running {market_type} engine ===")
                engine.run_one_cycle()
            except Exception as e:
                error_msg = f"[{market_type}] Critical Error:\n{traceback.format_exc()}"
                self.logger.error(error_msg)
                self.notifier.send_alert(f"[{market_type}] Bot Crashed!\n{str(e)}")
                last_exc = e
        if last_exc is not None:
            raise last_exc


if __name__ == "__main__":
    bot = MagicSplitBot()
    bot.run()
