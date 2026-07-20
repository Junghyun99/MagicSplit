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
    UpbitLiveBroker,
    UpbitPaperBroker,
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
                   upbit_access_key: str = "", upbit_secret_key: str = ""):
    """(market_type, is_live) 조합에 따라 브로커를 생성.

    domestic/overseas -> KIS, crypto -> 업비트.
    """
    if market_type == "crypto":
        args = (upbit_access_key, upbit_secret_key, logger)
        return UpbitLiveBroker(*args) if is_live else UpbitPaperBroker(*args)
    args = (app_key, app_secret, acc_no, logger)
    if market_type == "domestic":
        return KisDomesticLiveBroker(*args) if is_live else KisDomesticPaperBroker(*args)
    return KisOverseasLiveBroker(*args) if is_live else KisOverseasPaperBroker(*args)


class MagicSplitBot:
    def __init__(self):
        # 1. 공용 설정 및 인프라
        self.config = Config()
        self.strategy = StrategyConfig(self.config.CONFIG_JSON_PATH)
        
        # 2. 매매 규칙 로드 및 마켓 타입 식별
        rules = [r for r in self.strategy.rules if r.enabled]
        if not rules:
            raise ValueError(
                "활성화된 종목이 없습니다. 설정 파일(config_domestic.json 또는 config_overseas.json)의 stocks 항목을 확인하세요."
            )
        self.market_type = rules[0].market_type

        # 3. 마켓별 로그 경로 설정 및 로거 초기화
        log_dir = os.path.join(self.config.LOG_PATH, self.market_type)
        self.logger = TradeLogger(log_dir)
        self.logger.info(f"=== Initializing MagicSplit Bot ({self.market_type}) ===")

        webhook_url, channel_id = self.config.slack_config_for(self.market_type)
        self.notifier = SlackNotifier(
            webhook_url=webhook_url,
            logger=self.logger,
            bot_token=self.config.SLACK_BOT_TOKEN,
            channel_id=channel_id,
        )

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
            upbit_access_key=self.config.UPBIT_ACCESS_KEY,
            upbit_secret_key=self.config.UPBIT_SECRET_KEY,
        )
        repo = JsonRepository(
            os.path.join(self.config.DATA_PATH, self.market_type),
            max_history_records=self.config.MAX_HISTORY_RECORDS,
        )
        market_data = self._create_market_data(rules)
        self.engine = MagicSplitEngine(
            broker=broker,
            repo=repo,
            logger=self.logger,
            stock_rules=rules,
            notifier=self.notifier,
            is_live_trading=self.config.IS_LIVE,
            market_data=market_data,
        )

    def _create_market_data(self, rules):
        """레짐 필터 사용 종목이 있으면 마켓별 과거 일봉 제공자를 생성한다.

        레짐 미사용이면 None (기존 동작 - 다운로드 비용 없음).
        domestic/overseas -> yfinance, crypto -> 업비트 공개 캔들 API.
        """
        regime_rules = [r for r in rules if r.regime_enabled]
        if not regime_rules:
            return None

        # ma_adx는 regime_min_bars(기본 200), channel은 channel_lookback(기본 63)만큼
        # 필요하므로 큰 쪽 기준 + 지표 워밍업 여유
        window_size = max(
            max((r.regime_min_bars for r in regime_rules), default=200),
            max((r.channel_lookback for r in regime_rules), default=63),
        ) + 60

        from src.infra.data import UpbitMarketDataProvider, YFinanceMarketDataProvider
        if self.market_type == "crypto":
            provider = UpbitMarketDataProvider(self.logger, window_size=window_size)
        else:
            provider = YFinanceMarketDataProvider(
                self.logger, window_size=window_size,
                tickers=[r.ticker for r in regime_rules],
            )
        self.logger.info(
            f"[MarketData] 레짐 필터 활성 종목 {len(regime_rules)}개 -> "
            f"{type(provider).__name__} 주입 (window {window_size}봉)"
        )
        return provider

    def run(self):
        """매매 사이클을 실행한다."""
        self.logger.info(f"=== Running {self.market_type} engine ===")
        self.engine.run_one_cycle()


if __name__ == "__main__":
    bot = MagicSplitBot()
    bot.run()
