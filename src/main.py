# src/main.py
import os
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
                    app_key: str, app_secret: str, acc_no: str, logger,
                    exchange_map: dict | None = None,
                    known_tickers: list[str] | None = None):
    """(market_type, is_live) 조합에 따라 KIS 브로커를 생성."""
    args = (app_key, app_secret, acc_no, logger)
    if market_type == "domestic":
        return (KisDomesticLiveBroker(*args, known_tickers=known_tickers)
                if is_live else KisDomesticPaperBroker(*args, known_tickers=known_tickers))
    return (KisOverseasLiveBroker(*args, exchange_map=exchange_map)
            if is_live else KisOverseasPaperBroker(*args, exchange_map=exchange_map))


class MagicSplitBot:
    def __init__(self):
        # 1. 공용 설정 및 인프라
        self.config = Config()
        self.strategy = StrategyConfig(self.config.CONFIG_JSON_PATH)
        
        # 2. 매매 규칙 로드 및 마켓 타입 식별
        rules = [r for r in self.strategy.rules if r.enabled]
        if not rules:
            raise ValueError(
                "활성화된 종목이 없습니다. config.json의 stocks 항목을 확인하세요."
            )
        self.market_type = rules[0].market_type

        # 3. 마켓별 로그 경로 설정 및 로거 초기화
        log_dir = os.path.join(self.config.LOG_PATH, self.market_type)
        self.logger = TradeLogger(log_dir)
        self.logger.info(f"=== Initializing MagicSplit Bot ({self.market_type}) ===")

        self.notifier = SlackNotifier(self.config.SLACK_WEBHOOK_URL, self.logger)

        self.logger.info(
            f"Loaded {len(self.strategy.rules)} stock rule(s) from {self.config.CONFIG_JSON_PATH}"
        )

        self.logger.info(
            f"[{self.market_type}] {len(rules)} rule(s), "
            f"mode={'LIVE' if self.config.IS_LIVE else 'PAPER'}"
        )

        broker = _create_broker(
            market_type=self.market_type,
            is_live=self.config.IS_LIVE,
            app_key=self.config.KIS_APP_KEY,
            app_secret=self.config.KIS_APP_SECRET,
            acc_no=self.config.KIS_ACC_NO,
            logger=self.logger,
            exchange_map=self.strategy.get_exchange_map(),
            known_tickers=[r.ticker for r in rules],
        )
        repo = JsonRepository(
            os.path.join(self.config.DATA_PATH, self.market_type),
            max_history_records=self.config.MAX_HISTORY_RECORDS,
        )
        self.engine = MagicSplitEngine(
            broker=broker,
            repo=repo,
            logger=self.logger,
            stock_rules=rules,
            notifier=self.notifier,
            is_live_trading=self.config.IS_LIVE,
        )

    def run(self):
        """매매 사이클을 실행한다."""
        self.logger.info(f"=== Running {self.market_type} engine ===")
        self.engine.run_one_cycle()


if __name__ == "__main__":
    bot = MagicSplitBot()
    bot.run()
