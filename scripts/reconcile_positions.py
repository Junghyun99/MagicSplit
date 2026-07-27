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
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.config import Config
from src.core.logic.position_reconciler import (
    QTY_MATCH_TOL, QuantityMismatch, detect_mismatches,
)
from src.core.models import DEFAULT_QTY_PRECISION, PositionLot
from src.main import _create_broker
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


def _prompt_qty(msg: str, market_type: str,
                default: Optional[float] = None) -> Optional[float]:
    """수량 입력. 주식은 정수, 코인(crypto)은 소수 수량을 허용한다."""
    default_str = _fmt_qty(default, market_type) if default is not None else ""
    val = _prompt(msg, default_str)
    if not val:
        return None
    try:
        qty = float(val)
    except ValueError:
        print(f"  ⚠️  숫자가 아닙니다: {val!r}")
        return _prompt_qty(msg, market_type, default)
    if market_type != "crypto":
        if qty != int(qty):
            print(f"  ⚠️  주식 수량은 정수여야 합니다: {val!r}")
            return _prompt_qty(msg, market_type, default)
        return float(int(qty))
    return qty


def _fmt_qty(value: float, market_type: str) -> str:
    """수량을 마켓 정밀도에 맞춰 표기한다 (지수표기/부동소수 잡음 제거)."""
    if market_type == "crypto":
        return f"{value:.8f}".rstrip("0").rstrip(".") or "0"
    return f"{value:.0f}"


def _fmt_diff(value: float, market_type: str) -> str:
    """부호를 유지한 수량 차이 표기. 수량이 float이므로 ':+d'를 쓰면 안 된다."""
    sign = "+" if value >= 0 else "-"
    return f"{sign}{_fmt_qty(abs(value), market_type)}"


def _shrink_to(lots: List[PositionLot], ticker: str, target_qty: float,
               market_type: str = "overseas") -> List[PositionLot]:
    """최고 차수 lot 부터 수량을 축소하여 전체 합을 target_qty 로 맞춘다."""
    ticker_lots = sorted(
        [l for l in lots if l.ticker == ticker],
        key=lambda l: l.level,
        reverse=True,
    )
    remaining = sum(l.quantity for l in ticker_lots) - target_qty
    if remaining < -QTY_MATCH_TOL:
        raise ValueError(
            f"target_qty({target_qty}) 가 현재 수량 합보다 큽니다. pad 를 사용하세요."
        )

    out = [l for l in lots if l.ticker != ticker]
    for lot in ticker_lots:
        if remaining <= QTY_MATCH_TOL:
            out.append(lot)
            continue
        if remaining >= lot.quantity - QTY_MATCH_TOL:
            remaining -= lot.quantity
            continue
        out.append(PositionLot(
            lot_id=lot.lot_id,
            ticker=lot.ticker,
            buy_price=lot.buy_price,
            # 코인은 소수 수량 -> 뺄셈 잔차(1e-17 등)를 최소 주문단위로 정리
            quantity=_round_qty(lot.quantity - remaining, market_type),
            buy_date=lot.buy_date,
            level=lot.level,
            trailing_highest_price=lot.trailing_highest_price,
        ))
        remaining = 0
    return out


def _round_qty(value: float, market_type: str) -> float:
    """마켓 정밀도(주식=정수, 코인=소수 8자리)로 수량을 정규화한다."""
    precision = DEFAULT_QTY_PRECISION.get(market_type, 0)
    return round(value, precision) if precision else float(round(value))


def _pad_with_lot(
    lots: List[PositionLot],
    ticker: str,
    level: int,
    quantity: float,
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
    last_sell_prices: Optional[Dict[str, float]] = None,
    market_type: str = "overseas",
) -> List[PositionLot]:
    """주식분할/병합 비율 적용 (num:den, 예 1:2 -> 1주를 2주로)."""
    if num <= 0 or den <= 0:
        raise ValueError("비율은 양수여야 합니다.")

    # 1. 포지션 단가/수량/최고가 조정
    out: List[PositionLot] = []
    for lot in lots:
        if lot.ticker != ticker:
            out.append(lot)
            continue
        # 주식은 단주가 생기지 않도록 내림, 코인은 소수 수량을 유지한다.
        if market_type == "crypto":
            new_qty = _round_qty(lot.quantity * den / num, market_type)
        else:
            new_qty = lot.quantity * den // num
        new_price = lot.buy_price * num / den
        
        # 트레일링 최고가도 비율에 맞춰 조정
        new_high = None
        if lot.trailing_highest_price is not None:
            new_high = round(lot.trailing_highest_price * num / den, 4)

        if new_qty > 0:
            out.append(PositionLot(
                lot_id=lot.lot_id,
                ticker=lot.ticker,
                buy_price=round(new_price, 4),
                quantity=new_qty,
                buy_date=lot.buy_date,
                level=lot.level,
                trailing_highest_price=new_high,
            ))
        else:
            print(f"  ⚠️  수량이 0이 되어 로트 제거: {lot.lot_id} (Lv.{lot.level})")

    # 2. 직전 매도가(last_sell_price)도 비율에 맞춰 조정 (망령 데이터 방지)
    if last_sell_prices and ticker in last_sell_prices:
        old_sell_price = last_sell_prices[ticker]
        new_sell_price = round(old_sell_price * num / den, 4)
        last_sell_prices[ticker] = new_sell_price
        print(f"  💡 직전 매도가 조정: ${old_sell_price:,.2f} -> ${new_sell_price:,.2f}")

    return out


def _print_mismatch(m: QuantityMismatch, market_type: str) -> None:
    print()
    print(f"── [{m.ticker}] ─────────────────────────────")
    print(f"  broker_qty   : {_fmt_qty(m.broker_qty, market_type)}")
    print(f"  positions_qty: {_fmt_qty(m.positions_qty, market_type)}"
          f"  (lots={m.lot_count}, levels={m.levels})")
    print(f"  diff         : {_fmt_diff(m.diff, market_type)}")


def _handle_ticker(
    mismatch: QuantityMismatch,
    lots: List[PositionLot],
    last_sell_prices: Optional[Dict[str, float]] = None,
    market_type: str = "overseas",
) -> List[PositionLot]:
    _print_mismatch(mismatch, market_type)
    while True:
        choice = _prompt(
            "  액션 [s=shrink / p=pad / r=ratio / k=keep]", default="k",
        ).lower()

        if choice == "k":
            return lots

        if choice == "s":
            target = _prompt_qty(
                "  목표 수량(브로커와 일치시킬 값)", market_type,
                default=mismatch.broker_qty,
            )
            if target is None:
                continue
            try:
                return _shrink_to(lots, mismatch.ticker, target, market_type)
            except ValueError as e:
                print(f"  ⚠️  {e}")
                continue

        if choice == "p":
            qty = _prompt_qty(
                "  새 lot 수량", market_type, default=max(0.0, mismatch.diff),
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
                return _apply_split_ratio(
                    lots, mismatch.ticker, num, den, last_sell_prices, market_type,
                )
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

    broker = _create_broker(
        market_type=market_type,
        is_live=config.IS_LIVE,
        app_key=config.KIS_APP_KEY,
        app_secret=config.KIS_APP_SECRET,
        acc_no=config.KIS_ACC_NO,
        logger=logger,
        upbit_access_key=config.UPBIT_ACCESS_KEY,
        upbit_secret_key=config.UPBIT_SECRET_KEY,
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
    last_sell_prices = repo.load_last_sell_prices()

    for m in mismatches:
        lots = _handle_ticker(m, lots, last_sell_prices, market_type)

    print()
    print("── 변경 후 수량 요약 ──")
    after_mismatches = detect_mismatches(lots, portfolio, rules)
    for ticker in sorted({m.ticker for m in mismatches}):
        after = [a for a in after_mismatches if a.ticker == ticker]
        if after:
            print(f"  {ticker}: 여전히 불일치 {_fmt_diff(after[0].diff, market_type)}")
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
    repo.save_last_sell_prices(last_sell_prices)
    print("✓ 저장 완료 (positions.json & last_sell_prices.json).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
