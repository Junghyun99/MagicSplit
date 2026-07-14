#!/usr/bin/env python3
"""수동 매매(Manual Trade) CLI.

`MagicSplitEngine.run_manual_trade()`를 호출하여 자동매매와 동일한
주문 -> 포지션 반영 -> 저장 파이프라인을 사용한다. 신호 평가(evaluate_stock)만
우회하고, 사용자가 지정한 ticker/action으로 즉시 매매를 강제한다.
수량은 자동매매와 동일하게 엔진이 도출한다:
  - BUY: --amount 지정 시 해당 금액, 없으면 rule.buy_amount_at(next_level) / 현재가
  - SELL: 최고 차수 lot 전량

단일 매매:
    python scripts/manual_trade.py --ticker 005930 --action buy
    python scripts/manual_trade.py --ticker 005930 --action buy --amount 500000
    python scripts/manual_trade.py --ticker TSLA --action sell

다종목 배치(브로커/엔진/KIS 세션을 1회만 생성하고 순회 체결):
    python scripts/manual_trade.py --trades-json \
        '[{"ticker":"005930","action":"sell"},{"ticker":"000660","action":"buy","amount":500000}]'

배치는 매도(sell/sell_all)를 먼저 실행한 뒤 매수를 진행해 현금 부족을 방지하며,
한 종목이 실패해도 나머지는 계속 진행한다(부분 성공 허용).
"""
import argparse
import json
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
    parser.add_argument("--ticker", help="종목 코드 (예: 005930, TSLA). 단일 매매용.")
    parser.add_argument(
        "--action", choices=["buy", "sell", "sell_all"],
        help="매수(buy), 매도(sell), 일괄매도(sell_all). 수량은 자동 도출됨.",
    )
    parser.add_argument(
        "--amount", type=float, default=None,
        help="매수 금액 직접 지정 (원 또는 USD). 생략 시 config buy_amount 사용.",
    )
    parser.add_argument(
        "--trades-json", default=None,
        help='다종목 배치. JSON 배열 [{"ticker","action","amount"?}]. '
             "지정 시 --ticker/--action은 무시된다.",
    )
    return parser.parse_args()


def _parse_trades(args):
    """CLI 인자를 정규화된 trade 목록으로 변환한다.

    반환: [{"ticker": str, "action": str, "amount": Optional[float]}]
    """
    if args.trades_json:
        try:
            raw = json.loads(args.trades_json)
        except json.JSONDecodeError as e:
            raise SystemExit(f"에러: --trades-json 파싱 실패: {e}")
        if not isinstance(raw, list) or not raw:
            raise SystemExit("에러: --trades-json 은 비어있지 않은 JSON 배열이어야 합니다.")
        trades = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise SystemExit(f"에러: --trades-json[{i}] 는 객체여야 합니다.")
            ticker = str(item.get("ticker", "")).strip()
            action = str(item.get("action", "")).strip().lower()
            if not ticker or action not in ("buy", "sell", "sell_all"):
                raise SystemExit(
                    f"에러: --trades-json[{i}] 의 ticker/action 이 유효하지 않습니다: {item}"
                )
            amount = item.get("amount", None)
            trades.append({
                "ticker": ticker,
                "action": action,
                "amount": float(amount) if amount not in (None, "") else None,
            })
        return trades

    if not args.ticker or not args.action:
        raise SystemExit("에러: --ticker 와 --action 을 지정하거나 --trades-json 을 사용하세요.")
    return [{"ticker": args.ticker, "action": args.action, "amount": args.amount}]


def _execute_one(engine, logger, ticker, action_str, amount):
    """단일 trade를 엔진으로 실행하고 체결 여부를 반환한다."""
    action = OrderAction.BUY if action_str == "buy" else OrderAction.SELL
    is_sell_all = action_str == "sell_all"
    result = engine.run_manual_trade(
        ticker=ticker,
        action=action,
        override_amount=amount,
        force=True,
        sell_all=is_sell_all,
    )
    if not result.executions:
        logger.error(f"[{ticker}] 주문이 실행되지 않았습니다.")
        return False
    if all(e.status == ExecutionStatus.REJECTED for e in result.executions):
        logger.error(f"[{ticker}] 모든 주문이 거절(REJECTED)되었습니다.")
        return False
    return True


def main():
    args = parse_args()
    trades = _parse_trades(args)

    # 매도(sell/sell_all)를 먼저 실행한 뒤 매수 진행 (현금 부족 방지, 프로젝트 규칙).
    trades.sort(key=lambda t: 1 if t["action"] == "buy" else 0)

    config = Config()
    strategy = StrategyConfig(config.CONFIG_JSON_PATH)

    if not strategy.rules:
        print(f"에러: 설정 파일({config.CONFIG_JSON_PATH})에 종목이 없습니다.")
        sys.exit(1)
    # config 파일은 단일 마켓(domestic/overseas)이므로 마켓 타입은 규칙 전체가 공유한다.
    market_type = strategy.rules[0].market_type

    log_dir = os.path.join(config.LOG_PATH, market_type)
    logger = TradeLogger(log_dir)
    summary = ", ".join(
        f"{t['ticker']}:{t['action']}"
        + (f"({t['amount']})" if t["amount"] is not None else "")
        for t in trades
    )
    logger.info(f"=== Manual Trade CLI ({market_type}) [{len(trades)}건]: {summary} ===")

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

    filled, failed = [], []
    for t in trades:
        label = f"{t['ticker']} {t['action'].upper()}"
        try:
            ok = _execute_one(engine, logger, t["ticker"], t["action"], t["amount"])
        except Exception as e:  # 한 종목 실패가 나머지를 막지 않도록 격리
            logger.error(f"[{t['ticker']}] 수동매매 실패: {e}")
            ok = False
        (filled if ok else failed).append(label)

    logger.info(
        f"=== Manual Trade 완료: 체결 {len(filled)}건"
        + (f" [{', '.join(filled)}]" if filled else "")
        + f", 실패 {len(failed)}건"
        + (f" [{', '.join(failed)}]" if failed else "")
        + " ==="
    )

    # 하나도 체결되지 않았을 때만 실패 종료. 부분 성공은 데이터 커밋을 위해 정상 종료.
    if not filled:
        sys.exit(1)


if __name__ == "__main__":
    main()
