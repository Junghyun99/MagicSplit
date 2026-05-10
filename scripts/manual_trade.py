#!/usr/bin/env python3
"""수동 매매(Manual Trade) CLI.

`MagicSplitEngine.run_manual_trade()`를 호출하여 자동매매와 동일한
주문 -> 포지션 반영 -> 저장 파이프라인을 사용한다. 신호 평가(evaluate_stock)만
우회하고, 사용자가 지정한 ticker/action으로 즉시 매매를 강제한다.
수량은 자동매매와 동일하게 엔진이 도출한다:
  - BUY: rule.buy_amount_at(next_level) / 현재가
  - SELL: 최고 차수 lot 전량

사용법:
    python scripts/manual_trade.py --ticker 005930 --action buy
    python scripts/manual_trade.py --ticker TSLA --action sell
    python scripts/manual_trade.py --ticker AAPL --action buy --dry-run
"""
import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.config import Config
from src.strategy_config import StrategyConfig
from src.utils.logger import TradeLogger
from src.core.models import OrderAction, ExecutionStatus
from src.core.engine.base import MagicSplitEngine
from src.main import _create_broker
from src.infra.repo import JsonRepository
from src.infra.notifier import SlackNotifier


def parse_args():
    parser = argparse.ArgumentParser(description="수동 매매 스크립트")
    parser.add_argument("--ticker", required=True, help="종목 코드 (예: 005930, TSLA)")
    parser.add_argument(
        "--action", required=True, choices=["buy", "sell"],
        help="매수(buy) 또는 매도(sell). 수량은 자동 도출됨.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="실제 주문 없이 신호 생성 단계까지만 시뮬레이션",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    config = Config()
    strategy = StrategyConfig(config.CONFIG_JSON_PATH)

    target_rule = next(
        (r for r in strategy.rules if r.ticker == args.ticker), None
    )
    if target_rule is None:
        print(
            f"에러: 설정 파일({config.CONFIG_JSON_PATH})에 "
            f"'{args.ticker}' 종목이 없습니다."
        )
        sys.exit(1)
    # 매수만 비활성 종목 차단. 매도는 청산 목적으로 허용 (엔진과 동일 정책).
    if args.action == "buy" and not target_rule.enabled:
        print(
            f"에러: '{args.ticker}'는 비활성화 상태입니다. "
            f"매수하려면 설정 파일에서 enabled=true 로 변경하세요."
        )
        sys.exit(1)
    market_type = target_rule.market_type

    log_dir = os.path.join(config.LOG_PATH, market_type)
    logger = TradeLogger(log_dir)
    logger.info(
        f"=== Manual Trade CLI: {args.ticker} {args.action.upper()} ==="
    )

    broker = _create_broker(
        market_type=market_type,
        is_live=config.IS_LIVE,
        app_key=config.KIS_APP_KEY,
        app_secret=config.KIS_APP_SECRET,
        acc_no=config.KIS_ACC_NO,
        logger=logger,
    )
    repo = JsonRepository(
        os.path.join(config.DATA_PATH, market_type),
        max_history_records=config.MAX_HISTORY_RECORDS,
    )
    notifier = SlackNotifier(
        webhook_url=config.SLACK_WEBHOOK_URL,
        logger=logger,
        bot_token=config.SLACK_BOT_TOKEN,
        channel_id=config.SLACK_CHANNEL_ID,
    )
    engine = MagicSplitEngine(
        broker=broker,
        repo=repo,
        logger=logger,
        stock_rules=strategy.rules,
        notifier=notifier,
        is_live_trading=config.IS_LIVE,
    )

    action = OrderAction.BUY if args.action == "buy" else OrderAction.SELL

    try:
        result = engine.run_manual_trade(
            ticker=args.ticker,
            action=action,
            dry_run=args.dry_run,
        )
    except Exception as e:
        logger.error(f"수동매매 중단: {e}")
        sys.exit(1)

    if args.dry_run:
        logger.info("=== Manual Trade (DRY RUN) 완료 ===")
        sys.exit(0)

    if not result.executions:
        logger.error("주문이 실행되지 않았습니다.")
        sys.exit(1)
    if all(e.status == ExecutionStatus.REJECTED for e in result.executions):
        logger.error("모든 주문이 거절(REJECTED)되었습니다.")
        sys.exit(1)

    logger.info("=== Manual Trade 완료 ===")


if __name__ == "__main__":
    main()
