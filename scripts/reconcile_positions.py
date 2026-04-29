#!/usr/bin/env python3
"""브로커 보유수량과 positions.json 의 차수별 수량 합 불일치를 수동으로 보정하는 CLI.

엔진은 불일치 감지 시 해당 종목 매매를 중단한다. 이 스크립트로 사용자가
직접 lot 데이터를 조정한 뒤 엔진을 재개한다.

동작:
    1. 브로커 portfolio 조회 + positions.json 로드
    2. detect_mismatches() 로 불일치 종목 나열
    3. 각 종목마다 사용자에게 선택지 제시:
        [s] shrink: positions 가 많을 때 최고 차수부터 축소/제거
        [p] pad   : broker 가 많을 때 새 lot 추가
        [r] ratio : 주식분할/병합 비율 적용 (모든 lot quantity/price 일괄 조정)
        [k] keep  : 그대로 두기
    4. 변경 사항 dry-run 출력 -> 최종 확인 -> 저장

사용:
    python scripts/reconcile_positions.py                 # 대화형
    python scripts/reconcile_positions.py --dry-run       # 저장 없이 미리보기만
"""
import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.config import Config
from src.core.logic.position_reconciler import QuantityMismatch, detect_mismatches
from src.core.models import PositionLot
from src.infra.broker import (
    KisDomesticLiveBroker,
    KisDomesticPaperBroker,
    KisOverseasLiveBroker,
    KisOverseasPaperBroker,
)
from src.infra.repo import JsonRepository
from src.strategy_config import StrategyConfig
from src.utils.logger import TradeLogger


def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{msg}{suffix}: ").strip()
    return val or default


def _prompt_int(msg: str, default: Optional[int] = None) -> Optional[int]:
    default_str = str(default) if default is not None else ""
    val = _prompt(msg, default_str)
    if not val:
        return None
    try:
        return int(val)
    except ValueError:
        print(f"  ⚠️  정수가 아닙니다: {val!r}")
        return _prompt_int(msg, default)


def _prompt_float(msg: str, default: Optional[float] = None) -> Optional[float]:
    default_str = str(default) if default is not None else ""
    val = _prompt(msg, default_str)
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        print(f"  ⚠️  숫자가 아닙니다: {val!r}")
        return _prompt_float(msg, default)


def _shrink_to(lots: List[PositionLot], ticker: str, target_qty: int) -> List[PositionLot]:
    """최고 차수 lot 부터 수량을 축소하여 전체 합을 target_qty 로 맞춘다."""
    ticker_lots = sorted(
        [l for l in lots if l.ticker == ticker],
        key=lambda l: l.level,
        reverse=True,
    )
    remaining = sum(l.quantity for l in ticker_lots) - target_qty
    if remaining < 0:
        raise ValueError(
            f"target_qty({target_qty}) 가 현재 수량 합보다 큽니다. pad 를 사용하세요."
        )

    out = [l for l in lots if l.ticker != ticker]
    for lot in ticker_lots:
        if remaining <= 0:
            out.append(lot)
            continue
        if remaining >= lot.quantity:
            remaining -= lot.quantity
            continue
        out.append(PositionLot(
            lot_id=lot.lot_id,
            ticker=lot.ticker,
            buy_price=lot.buy_price,
            quantity=lot.quantity - remaining,
            buy_date=lot.buy_date,
            level=lot.level,
        ))
        remaining = 0
    return out


def _pad_with_lot(
    lots: List[PositionLot],
    ticker: str,
    level: int,
    quantity: int,
    buy_price: float,
) -> List[PositionLot]:
    """브로커 초과분을 포함하는 새 lot 을 추가한다."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_lot = PositionLot(
        lot_id=f"lot_{ts}_{ticker}_{level:03d}_reconcile",
        ticker=ticker,
        buy_price=buy_price,
        quantity=quantity,
        buy_date=datetime.now().strftime("%Y-%m-%d"),
        level=level,
    )
    return lots + [new_lot]


def _apply_split_ratio(
    lots: List[PositionLot],
    ticker: str,
    num: int,
    den: int,
) -> List[PositionLot]:
    """주식분할/병합 비율 적용 (num:den, 예 1:2 -> 1주를 2주로)."""
    if num <= 0 or den <= 0:
        raise ValueError("비율은 양수여야 합니다.")
    out: List[PositionLot] = []
    for lot in lots:
        if lot.ticker != ticker:
            out.append(lot)
            continue
        new_qty = lot.quantity * den // num
        new_price = lot.buy_price * num / den
        out.append(PositionLot(
            lot_id=lot.lot_id,
            ticker=lot.ticker,
            buy_price=round(new_price, 4),
            quantity=new_qty,
            buy_date=lot.buy_date,
            level=lot.level,
        ))
    return out


def _print_mismatch(m: QuantityMismatch) -> None:
    print()
    print(f"── [{m.ticker}] ─────────────────────────────")
    print(f"  broker_qty   : {m.broker_qty}")
    print(f"  positions_qty: {m.positions_qty}  (lots={m.lot_count}, levels={m.levels})")
    print(f"  diff         : {m.diff:+d}")


def _handle_ticker(
    mismatch: QuantityMismatch,
    lots: List[PositionLot],
) -> List[PositionLot]:
    _print_mismatch(mismatch)
    while True:
        choice = _prompt(
            "  액션 [s=shrink / p=pad / r=ratio / k=keep]", default="k",
        ).lower()

        if choice == "k":
            return lots

        if choice == "s":
            target = _prompt_int(
                "  목표 수량(브로커와 일치시킬 값)", default=mismatch.broker_qty,
            )
            if target is None:
                continue
            try:
                return _shrink_to(lots, mismatch.ticker, target)
            except ValueError as e:
                print(f"  ⚠️  {e}")
                continue

        if choice == "p":
            qty = _prompt_int(
                "  새 lot 수량", default=max(0, mismatch.diff),
            )
            if qty is None or qty <= 0:
                continue
            price = _prompt_float("  매수가 (buy_price)")
            if price is None or price <= 0:
                continue
            next_level = (max(mismatch.levels) if mismatch.levels else 0) + 1
            level = _prompt_int("  차수(level)", default=next_level) or next_level
            return _pad_with_lot(lots, mismatch.ticker, level, qty, price)

        if choice == "r":
            ratio = _prompt("  분할/병합 비율 (num:den, 예 1:2)")
            if ":" not in ratio:
                print("  ⚠️  형식 오류")
                continue
            try:
                num, den = [int(x) for x in ratio.split(":", 1)]
            except ValueError:
                print("  ⚠️  정수 형식 오류")
                continue
            try:
                return _apply_split_ratio(lots, mismatch.ticker, num, den)
            except ValueError as e:
                print(f"  ⚠️  {e}")
                continue

        print("  ⚠️  알 수 없는 액션")


def _build_repo_and_broker(logger):
    config = Config()
    strategy = StrategyConfig(config.CONFIG_JSON_PATH)
    # detect_mismatches 는 enabled 여부와 무관하게 모든 rule ∪ positions 티커를
    # 검사하므로, 여기서는 전체 rule 을 그대로 전달한다.
    rules = strategy.rules
    if not rules:
        raise ValueError("설정 파일(config_*.json)에 종목이 없습니다.")
    market_type = rules[0].market_type

    if market_type == "domestic":
        broker_cls = KisDomesticLiveBroker if config.IS_LIVE else KisDomesticPaperBroker
    else:
        broker_cls = KisOverseasLiveBroker if config.IS_LIVE else KisOverseasPaperBroker
    broker = broker_cls(
        config.KIS_APP_KEY, config.KIS_APP_SECRET, config.KIS_ACC_NO, logger,
    )
    repo = JsonRepository(
        os.path.join(config.DATA_PATH, market_type),
        max_history_records=config.MAX_HISTORY_RECORDS,
    )
    return repo, broker, strategy.rules, market_type


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="positions.json 에 저장하지 않고 미리보기만 출력",
    )
    args = parser.parse_args()

    logger = TradeLogger(Config().LOG_PATH)
    repo, broker, rules, market_type = _build_repo_and_broker(logger)

    print(f"=== reconcile_positions ({market_type}) ===")
    portfolio = broker.get_portfolio()
    positions = repo.load_positions()
    mismatches = detect_mismatches(positions, portfolio, rules)

    if not mismatches:
        print("✓ 불일치 없음. 수량이 모두 일치합니다.")
        return 0

    print(f"불일치 {len(mismatches)}건:")
    lots = list(positions)
    for m in mismatches:
        lots = _handle_ticker(m, lots)

    print()
    print("── 변경 후 수량 요약 ──")
    after_mismatches = detect_mismatches(lots, portfolio, rules)
    for ticker in sorted({m.ticker for m in mismatches}):
        after = [a for a in after_mismatches if a.ticker == ticker]
        if after:
            print(f"  {ticker}: 여전히 불일치 {after[0].diff:+d}")
        else:
            print(f"  {ticker}: ✓ 일치")

    if args.dry_run:
        print()
        print("--dry-run 모드: 저장하지 않습니다.")
        return 0

    confirm = _prompt("positions.json 에 저장할까요? (y/N)", default="N").lower()
    if confirm != "y":
        print("취소. 변경 사항을 버립니다.")
        return 0

    repo.save_positions(lots)
    print("✓ 저장 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
