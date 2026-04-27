#!/usr/bin/env python3
"""수동 매매(Manual Trade) 실행 및 상태 동기화 CLI 스크립트.

이 스크립트는 KIS 브로커 API를 직접 호출하여 매수/매도 주문을 실행하고,
체결이 완료되면 즉시 `positions.json`, `history.json` 등 로컬 상태를 업데이트합니다.
이를 통해 MTS에서 직접 매매하여 발생하는 불일치(Mismatch) 에러 없이 봇과 상태를 동기화할 수 있습니다.

사용법:
    python scripts/manual_trade.py --ticker 005930 --action buy --qty 10 --price 80000 --level 4
    python scripts/manual_trade.py --ticker TSLA --action sell --qty 5
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.config import Config
from src.strategy_config import StrategyConfig
from src.utils.logger import TradeLogger
from src.core.models import Order, OrderAction, PositionLot, ExecutionStatus, SplitSignal
from src.main import _create_broker
from src.infra.repo import JsonRepository

def parse_args():
    parser = argparse.ArgumentParser(description="수동 매매 스크립트")
    parser.add_argument("--ticker", required=True, help="종목 코드 (예: 005930, TSLA)")
    parser.add_argument("--action", required=True, choices=["buy", "sell"], help="매수(buy) 또는 매도(sell)")
    parser.add_argument("--qty", required=True, type=int, help="주문 수량")
    parser.add_argument("--price", type=float, default=0.0, help="주문 가격 (0 입력 시 시장가 주문)")
    parser.add_argument("--level", type=int, help="할당할 차수. 미입력 시 매수=기존 최고차수+1, 매도=가장 높은 차수로 자동 처리")
    parser.add_argument("--dry-run", action="store_true", help="실제 주문을 넣지 않고 시뮬레이션만 수행")
    return parser.parse_args()

def main():
    args = parse_args()
    config = Config()
    logger = TradeLogger(config.LOG_PATH)
    logger.info(f"=== 수동 매매(Manual Trade) 시작: {args.ticker} {args.action.upper()} {args.qty}주 ===")

    strategy = StrategyConfig(config.CONFIG_JSON_PATH)
    
    # 1. 룰에서 티커 검색 (마켓 타입 및 exchange_map을 얻기 위함)
    target_rule = next((r for r in strategy.rules if r.ticker == args.ticker), None)
    if not target_rule:
        logger.error(f"설정 파일({config.CONFIG_JSON_PATH})에 '{args.ticker}' 종목이 없습니다.")
        sys.exit(1)

    market_type = target_rule.market_type
    logger.info(f"Market Type: {market_type}")

    # 2. 브로커 및 저장소 초기화
    broker = _create_broker(
        market_type=market_type,
        is_live=config.IS_LIVE,
        app_key=config.KIS_APP_KEY,
        app_secret=config.KIS_APP_SECRET,
        acc_no=config.KIS_ACC_NO,
        logger=logger,
        exchange_map=strategy.get_exchange_map(),
        known_tickers=[r.ticker for r in strategy.rules],
    )
    repo = JsonRepository(
        os.path.join(config.DATA_PATH, market_type),
        max_history_records=config.MAX_HISTORY_RECORDS,
    )

    # 3. 기존 포지션 조회
    positions = repo.load_positions()
    ticker_lots = [lot for lot in positions if lot.ticker == args.ticker]
    current_highest_level = max([lot.level for lot in ticker_lots]) if ticker_lots else 0

    # 4. 차수(Level) 자동 할당
    level = args.level
    if level is None:
        if args.action == "buy":
            level = current_highest_level + 1
        else: # sell
            level = current_highest_level
            if level == 0:
                logger.error("매도할 포지션이 존재하지 않습니다.")
                sys.exit(1)

    # 5. 주문 객체 생성
    action_enum = OrderAction.BUY if args.action == "buy" else OrderAction.SELL
    order = Order(
        ticker=args.ticker,
        action=action_enum,
        quantity=args.qty,
        price=args.price,
    )

    if args.dry_run:
        logger.info(f"[DRY RUN] 다음 주문이 실행될 예정입니다: {order}")
        logger.info(f"[DRY RUN] 할당될 차수(Level): {level}")
        sys.exit(0)

    # 6. 매매 실행
    logger.info(">>> 주문 실행 중...")
    executions = broker.execute_orders([order])
    
    if not executions:
        logger.error("주문 실행 실패: 브로커에서 응답을 받지 못했습니다.")
        sys.exit(1)

    execution = executions[0]
    if execution.status == ExecutionStatus.REJECTED:
        logger.error("주문 거절됨.")
        sys.exit(1)

    logger.info(f"주문 체결 완료! (가격: {execution.price}, 수량: {execution.quantity})")

    # 7. 포지션(positions.json) 업데이트
    updated_positions = list(positions)
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    if args.action == "buy":
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_lot = PositionLot(
            lot_id=f"lot_{ts}_{args.ticker}_{level:03d}_manual",
            ticker=execution.ticker,
            buy_price=execution.price,
            quantity=execution.quantity,
            buy_date=today_str,
            level=level,
        )
        updated_positions.append(new_lot)
        logger.info(f"[Position] New lot 추가됨: Lv{level} / {execution.quantity}주 @ {execution.price}")
    else:
        # 매도 처리 로직: 지정된 level의 lot을 찾아 수량 차감 또는 제거
        # 동일한 level의 lot이 여러 개일 수 있으므로 (보통은 1개), 수량 합계를 비교하며 차감
        target_lots = sorted([lot for lot in updated_positions if lot.ticker == args.ticker and lot.level == level], key=lambda x: x.buy_date)
        if not target_lots:
            # 지정된 level이 없으면 그냥 가장 높은 level부터 차감 (fallback)
            target_lots = sorted([lot for lot in updated_positions if lot.ticker == args.ticker], key=lambda x: x.level, reverse=True)
            
        remaining_qty = execution.quantity
        for target_lot in target_lots:
            if remaining_qty <= 0:
                break
            
            if target_lot.quantity <= remaining_qty:
                # 해당 Lot 전량 매도
                remaining_qty -= target_lot.quantity
                updated_positions.remove(target_lot)
                logger.info(f"[Position] Lot 제거됨: {target_lot.lot_id} (Lv{target_lot.level})")
            else:
                # 부분 매도
                target_lot.quantity -= remaining_qty
                remaining_qty = 0
                logger.info(f"[Position] Lot 부분 매도됨: {target_lot.lot_id} (남은 수량: {target_lot.quantity})")
                
        if remaining_qty > 0:
            logger.warning(f"매도 수량({execution.quantity})이 포지션 수량보다 많습니다. 초과분({remaining_qty})은 무시됩니다.")

    # 8. 포트폴리오 상태 및 히스토리 업데이트
    logger.info(">>> 상태 저장 중...")
    portfolio = broker.get_portfolio()
    repo.save_positions(updated_positions)
    
    # Fake Signal for history reason
    reason_str = "수동 매매(Manual Trade)"
    signals = [SplitSignal(
        ticker=args.ticker,
        lot_id=None,
        action=action_enum,
        quantity=execution.quantity,
        price=execution.price,
        reason=reason_str,
        pct_change=0.0,
        level=level
    )]
    
    repo.save_trade_history(executions, portfolio, reason_str, signals=signals)
    repo.update_status(portfolio, updated_positions, reason_str)
    
    logger.info("=== 수동 매매 및 상태 동기화 완료 ===")

if __name__ == "__main__":
    main()
