#!/usr/bin/env python3
"""브로커 앱(MTS/HTS/업비트 앱)에서 직접 체결한 매매를 봇 데이터에 사후 반영한다.

봇을 거치지 않고 체결된 거래는 positions.json 에 남지 않아, 다음 실행에서
계좌 보유수량과 어긋난다(수량 불일치 -> 해당 종목 매매 중단). 이 스크립트는
"이미 체결된" 거래를 봇 장부에 기록해 정합성을 되돌린다.

scripts/manual_trade.py 와의 차이:
    manual_trade.py         - 지금 주문을 낸다 (브로커 API 호출)
    migrate_manual_trade.py - 이미 난 체결을 장부에만 반영한다 (주문 없음)

반영 대상:
    positions.json         매도는 고차수 lot부터 차감, 매수는 새 lot 추가
    history.json           체결 내역 1건 (차수별 실현손익 포함)
    last_sell_prices.json  매도 체결가 기록 (동적 재매수 기준 - 엔진과 동일)
    snapshots.json         체결분이 순입금(입출금)으로 잘못 잡힌 스냅샷 보정
    decisions.json         판단 내역에 체결 사유 1건 추가
    status.json            누적 실현손익 가산 + 보정된 포지션으로 대시보드 상태 재생성

사용법:
    python -m scripts.migrate_manual_trade --market crypto --dry-run --trades-json \\
        '[{"ticker":"KRW-ETH","action":"sell","quantity":0.7722,"price":2742000,
           "date":"2026-07-24"}]'

각 원소:
    ticker    (필수) 종목 코드
    action    (필수) "buy" | "sell"
    quantity  (필수) 체결 수량
    price     (필수) 체결 단가
    date      (선택) 체결일 YYYY-MM-DD. 없으면 --date, 그것도 없으면 오늘
    fee       (선택) 수수료 금액. 없으면 --fee-rate 로 계산
    level     (선택) 매수 시 부여할 차수. 없으면 기존 최고 차수 + 1
    reason    (선택) 기록에 남길 사유
"""
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.core.logic.position_reconciler import QTY_MATCH_TOL, drain_lots_by_qty
from src.core.models import (
    DEFAULT_QTY_PRECISION, ExecutionStatus, OrderAction, Portfolio,
    PositionLot, TradeExecution,
)
from src.core.logic.status_builder import build_dashboard_status
from src.infra.repo import JsonRepository, trade_cash_impact
from src.utils.currency import format_money, format_qty
from src.utils.ticker_reader import display_ticker

DATE_FMT = "%Y-%m-%d"
DEFAULT_REASON = "마이그레이션 - 브로커 앱 수동매매 사후 반영"


def _valid_date(text: str) -> str:
    try:
        return datetime.strptime(text, DATE_FMT).strftime(DATE_FMT)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"날짜 형식이 올바르지 않습니다: '{text}' (형식: YYYY-MM-DD)"
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--market", required=True,
                        choices=["domestic", "overseas", "crypto"],
                        help="반영 대상 시장 (docs/data/<market>/ 하위 파일)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trades-json", help="체결 목록 JSON 배열")
    group.add_argument("--trades-file", help="체결 목록 JSON 파일 경로")
    parser.add_argument("--date", type=_valid_date,
                        help="체결일 기본값 (원소별 date 가 없을 때 사용)")
    parser.add_argument("--fee-rate", type=float, default=0.0,
                        help="수수료율 %%. 원소별 fee 가 없을 때 "
                             "체결금액 * rate/100 으로 계산 (업비트 원화마켓: 0.05)")
    parser.add_argument("--cash", type=float,
                        help="체결 직후 실제 현금 잔고. 생략 시 직전 기록 + 체결 현금영향으로 "
                             "추정한다. 실제 값을 주면 차액이 순입금(입출금)으로 기록된다.")
    parser.add_argument("--reason", default=DEFAULT_REASON,
                        help="원소별 reason 이 없을 때 쓸 기본 사유")
    parser.add_argument("--config",
                        help="종목 설정 파일 경로 (기본: config_<market>.json). "
                             "status.json 리스크 지표 계산에 쓰인다")
    parser.add_argument("--skip-status-rebuild", action="store_true",
                        help="status.json 대시보드 상태 재생성을 건너뛴다 "
                             "(누적 실현손익만 갱신)")
    parser.add_argument("--data-root", default="docs/data", help="데이터 루트 경로")
    parser.add_argument("--skip-snapshot-fix", action="store_true",
                        help="snapshots.json 순입금 보정을 건너뛴다")
    parser.add_argument("--dry-run", action="store_true",
                        help="파일에 쓰지 않고 반영 결과만 출력")
    return parser.parse_args(argv)


def parse_trades(raw_text: str, default_date: Optional[str],
                 fee_rate: float) -> List[dict]:
    """입력 JSON을 검증된 체결 목록으로 정규화한다."""
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"체결 목록 JSON 파싱 실패: {e}")
    if not isinstance(raw, list) or not raw:
        raise ValueError("체결 목록은 비어있지 않은 JSON 배열이어야 합니다.")

    today = datetime.now().strftime(DATE_FMT)
    trades = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"trades[{i}] 는 객체여야 합니다.")
        ticker = str(item.get("ticker", "")).strip().upper()
        action = str(item.get("action", "")).strip().lower()
        if not ticker:
            raise ValueError(f"trades[{i}] 에 ticker 가 없습니다.")
        if action not in ("buy", "sell"):
            raise ValueError(f"trades[{i}] 의 action 은 buy/sell 이어야 합니다: {action!r}")
        try:
            quantity = float(item["quantity"])
            price = float(item["price"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"trades[{i}] 의 quantity/price 가 유효한 숫자가 아닙니다.")
        if quantity <= 0 or price <= 0:
            raise ValueError(f"trades[{i}] 의 quantity/price 는 양수여야 합니다.")

        date = item.get("date") or default_date or today
        try:
            date = datetime.strptime(str(date), DATE_FMT).strftime(DATE_FMT)
        except ValueError:
            raise ValueError(f"trades[{i}] 의 date 형식 오류: {date!r} (YYYY-MM-DD)")

        fee = item.get("fee")
        fee = float(fee) if fee is not None else quantity * price * fee_rate / 100.0

        level = item.get("level")
        trades.append({
            "ticker": ticker,
            "action": action,
            "quantity": quantity,
            "price": price,
            "date": date,
            "fee": fee,
            "level": int(level) if level is not None else None,
            "reason": item.get("reason"),
        })

    dates = {t["date"] for t in trades}
    if len(dates) > 1:
        raise ValueError(
            f"한 번에 처리하는 체결은 같은 날짜여야 합니다 (기록 1건으로 묶임): "
            f"{sorted(dates)}"
        )
    # 매도를 먼저 반영해 현금 흐름 순서를 실제 체결과 맞춘다 (프로젝트 규칙).
    trades.sort(key=lambda t: 1 if t["action"] == "buy" else 0)
    return trades


def _next_level(positions: List[PositionLot], ticker: str) -> int:
    levels = [l.level for l in positions if l.ticker == ticker]
    return (max(levels) if levels else 0) + 1


def apply_trades(positions: List[PositionLot], trades: List[dict],
                 last_sell_prices: Dict[str, float], market_type: str,
                 default_reason: str = DEFAULT_REASON,
                 warn=print) -> List[TradeExecution]:
    """체결 목록을 positions/last_sell_prices 에 in-place 반영하고 체결 내역을 만든다."""
    precision = DEFAULT_QTY_PRECISION.get(market_type, 0)
    executions: List[TradeExecution] = []

    for t in trades:
        exe = TradeExecution(
            ticker=t["ticker"],
            action=OrderAction.SELL if t["action"] == "sell" else OrderAction.BUY,
            quantity=t["quantity"],
            price=t["price"],
            fee=t["fee"],
            date=f"{t['date']} 00:00:00",
            status=ExecutionStatus.FILLED,
            reason=t["reason"] or default_reason,
        )

        if exe.action == OrderAction.SELL:
            _, consumed = drain_lots_by_qty(positions, exe.ticker, exe.quantity, exe)
            if consumed <= 0:
                raise ValueError(
                    f"[{exe.ticker}] 차감할 lot 이 없습니다. positions.json 을 확인하세요."
                )
            shortfall = exe.quantity - consumed
            if shortfall > QTY_MATCH_TOL:
                warn(
                    f"  ! [{exe.ticker}] 보유 lot({format_qty(consumed, market_type)})보다 "
                    f"매도 수량이 많습니다. 미차감 "
                    f"{format_qty(shortfall, market_type)} — 실현손익은 차감분 기준입니다."
                )
            last_sell_prices[exe.ticker] = exe.price
        else:
            level = t["level"] or _next_level(positions, exe.ticker)
            exe.level = level
            exe.buy_price = exe.price
            exe.lot_id = (
                f"lot_{t['date'].replace('-', '')}_000000_"
                f"{exe.ticker}_{level:03d}_migrate"
            )
            positions.append(PositionLot(
                lot_id=exe.lot_id,
                ticker=exe.ticker,
                buy_price=exe.price,
                quantity=exe.quantity,
                buy_date=t["date"],
                level=level,
            ))

        executions.append(exe)

    # 코인 부분 차감은 부동소수 잔차를 남긴다 -> 최소 주문단위로 정리
    if precision:
        for i, lot in enumerate(positions):
            rounded = round(lot.quantity, precision)
            if rounded != lot.quantity:
                positions[i] = PositionLot(
                    lot_id=lot.lot_id, ticker=lot.ticker, buy_price=lot.buy_price,
                    quantity=rounded, buy_date=lot.buy_date, level=lot.level,
                    trailing_highest_price=lot.trailing_highest_price,
                )

    return executions


def _estimate_prices(repo: JsonRepository, executions: List[TradeExecution]) -> Dict[str, float]:
    """직전 status.json 의 종목별 최근가에 이번 체결가를 덮어쓴다."""
    prices: Dict[str, float] = {}
    status = repo.load_status() or {}
    for h in (status.get("portfolio") or {}).get("holdings") or []:
        ticker, price = h.get("ticker"), h.get("price")
        if ticker and price:
            prices[ticker] = float(price)
    for exe in executions:
        prices[exe.ticker] = exe.price
    return prices


def fix_snapshot_net_deposit(snapshots: List[dict], trade_date: str,
                             cash_impact: float) -> Optional[dict]:
    """체결분이 순입금으로 잘못 잡힌 스냅샷을 찾아 보정하고 그 레코드를 반환한다.

    봇이 보지 못한 체결은, 체결일 이후 첫 실행의 스냅샷에서 현금 증감이 전부
    입출금으로 계상된다. 그 레코드의 net_deposit 에서 체결 현금영향을 빼면
    순수 입출금만 남는다. 보정 대상이 없으면 None (호출부가 새 스냅샷 기록).
    """
    for snap in snapshots:
        if str(snap.get("date", "")) >= trade_date:
            snap["net_deposit"] = round(
                float(snap.get("net_deposit") or 0.0) - cash_impact, 2,
            )
            return snap
    return None


def accrue_realized_pnl(status: dict, executions: List[TradeExecution]) -> Dict[str, float]:
    """status.json 의 누적 실현손익에 이번 체결의 실현손익을 가산한다.

    status.json 에 realized_pnl_by_ticker 키가 있으면 엔진은 history 를 다시 집계하지
    않고 그 값에 "그 실행의 체결분"만 누적한다. 사후 반영한 체결은 어떤 실행에도
    속하지 않으므로, 여기서 직접 더해주지 않으면 대시보드 실현손익에서 영구 누락된다.
    """
    accrued = dict(status.get("realized_pnl_by_ticker") or {})
    for exe in executions:
        if exe.realized_pnl:
            accrued[exe.ticker] = round(
                accrued.get(exe.ticker, 0.0) + exe.realized_pnl, 2,
            )
    return accrued


def compose_reason(executions: List[TradeExecution]) -> str:
    """엔진의 _build_reason 과 같은 형식으로 사유 문자열을 만든다.

    "TICKER:ACTION(사유)" 를 종목별로 줄바꿈 구분해 잇는다 (대시보드 표기 통일).
    """
    return ",\n".join(
        f"{display_ticker(e.ticker)}:{e.action.value}({e.reason})" for e in executions
    )


def rebuild_status(repo: JsonRepository, positions: List[PositionLot],
                   accrued_pnl: Dict[str, float], market_type: str,
                   stock_rules=None) -> Optional[dict]:
    """직전 status.json 의 계좌 상태 + 보정된 포지션으로 대시보드 상태를 다시 조립한다.

    status.json 은 매 실행마다 통째로 재생성되는 "최신 상태" 스냅샷이라, 사후
    반영 뒤 다음 실행 전까지 낡은 수량/불일치 경보를 그대로 보여준다. 계좌 쪽
    수치(현금/보유수량/현재가)는 원래 정확했으므로, 그대로 두고 봇 포지션만
    교체해 엔진과 같은 함수로 다시 만든다. 직전 상태가 없으면 None.
    """
    prev = repo.load_status() or {}
    pf_block = prev.get("portfolio") or {}
    rows = pf_block.get("holdings")
    if not rows or pf_block.get("cash_balance") is None:
        return None

    portfolio = Portfolio(
        total_cash=float(pf_block["cash_balance"]),
        holdings={r["ticker"]: r["qty"] for r in rows},
        current_prices={r["ticker"]: r["price"] for r in rows},
        exchange_rate=prev.get("exchange_rate"),
    )
    return build_dashboard_status(
        portfolio=portfolio,
        positions=positions,
        reason=prev.get("reason", ""),
        old_realized_pnl_by_ticker=accrued_pnl,
        recent_executions=[],
        enabled_tickers=prev.get("enabled_tickers") or [],
        # 새 실행 시각을 지어내지 않도록 직전 실행일을 그대로 쓴다.
        sim_date=prev.get("last_run_date"),
        stock_rules=stock_rules,
        last_trade_dates=repo.get_last_trade_dates(),
        market_type=market_type,
        regime_state_by_ticker=prev.get("regime_state_by_ticker") or {},
    )


def _load_enabled_rules(config_path: str, warn=print):
    """config_*.json 에서 활성 종목 규칙을 읽는다 (실패해도 진행)."""
    if not os.path.exists(config_path):
        warn(f"  ! 설정 파일 없음: {config_path} — 리스크 지표(최대 투입액 등) 생략")
        return None
    try:
        from src.strategy_config import StrategyConfig
        return [r for r in StrategyConfig(config_path).rules if r.enabled]
    except Exception as e:  # 설정 오류가 마이그레이션을 막지 않도록 격리
        warn(f"  ! 설정 파일 로드 실패({config_path}): {e} — 리스크 지표 생략")
        return None


def _print_plan(market_type: str, executions: List[TradeExecution],
                positions: List[PositionLot], portfolio: Portfolio,
                cash_impact: float, net_deposit: float) -> None:
    fm = lambda v: format_money(v, market_type)
    print("── 반영할 체결 ──")
    for exe in executions:
        line = (
            f"  {exe.ticker} {exe.action} {format_qty(exe.quantity, market_type)} "
            f"@ {fm(exe.price)} (수수료 {fm(exe.fee)})"
        )
        if exe.action == OrderAction.SELL:
            line += f" -> 실현손익 {fm(exe.realized_pnl)}"
            for lot in exe.liquidation_lots or []:
                line += (
                    f"\n      Lv{lot['level']} {lot['lot_id']}: "
                    f"{format_qty(lot['quantity'], market_type)} "
                    f"@ 매수가 {fm(lot['buy_price'])} -> {fm(lot['realized_pnl'])}"
                )
        else:
            line += f" -> 신규 lot {exe.lot_id} (Lv{exe.level})"
        print(line)

    print()
    print("── 반영 후 positions ──")
    by_ticker: Dict[str, List[PositionLot]] = {}
    for lot in positions:
        by_ticker.setdefault(lot.ticker, []).append(lot)
    for ticker in sorted(by_ticker):
        lots = by_ticker[ticker]
        total = sum(l.quantity for l in lots)
        levels = ",".join(f"Lv{l.level}" for l in sorted(lots, key=lambda l: l.level))
        print(f"  {ticker}: {format_qty(total, market_type)} ({len(lots)} lots: {levels})")

    print()
    print("── 현금/자산 ──")
    print(f"  체결 현금영향 : {fm(cash_impact)}")
    print(f"  현금 잔고     : {fm(portfolio.total_cash)}")
    print(f"  총 평가자산   : {fm(portfolio.total_value)}")
    print(f"  순입금(추정)  : {fm(net_deposit)}")


def main(argv=None) -> int:
    args = parse_args(argv)

    raw_text = args.trades_json
    if args.trades_file:
        try:
            with open(args.trades_file, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except OSError as e:
            print(f"에러: --trades-file 읽기 실패: {e}")
            return 1

    try:
        trades = parse_trades(raw_text, args.date, args.fee_rate)
    except ValueError as e:
        print(f"에러: {e}")
        return 1

    market_type = args.market
    trade_date = trades[0]["date"]
    repo = JsonRepository(os.path.join(args.data_root, market_type))

    print(f"=== migrate_manual_trade ({market_type}) {trade_date} ===")

    positions = repo.load_positions()
    last_sell_prices = repo.load_last_sell_prices()
    try:
        executions = apply_trades(
            positions, trades, last_sell_prices, market_type, args.reason,
        )
    except ValueError as e:
        print(f"에러: {e}")
        return 1

    reason = compose_reason(executions)
    cash_impact = trade_cash_impact(executions)

    history = repo.load_history()
    if history and str(history[-1].get("date", ""))[:10] > trade_date:
        print(f"  ! 마지막 기록({history[-1].get('date')})보다 이전 날짜입니다. "
              f"history.json 은 시간순 append 라 순서가 어긋납니다.")
    prev_cash = history[-1].get("cash_balance") if history else None
    if args.cash is not None:
        cash = args.cash
    elif prev_cash is not None:
        cash = prev_cash + cash_impact
    else:
        cash = cash_impact
    net_deposit = round(cash - (prev_cash or 0.0) - cash_impact, 2)

    # 평가자산은 잔여 lot x 최근 알려진 가격으로 추정한다 (대시보드 차트용 값).
    holdings: Dict[str, float] = {}
    for lot in positions:
        holdings[lot.ticker] = holdings.get(lot.ticker, 0.0) + lot.quantity
    portfolio = Portfolio(
        total_cash=cash, holdings=holdings,
        current_prices=_estimate_prices(repo, executions),
    )

    _print_plan(market_type, executions, positions, portfolio, cash_impact, net_deposit)

    status = repo.load_status() or {}
    accrued_pnl = accrue_realized_pnl(status, executions)
    if accrued_pnl != (status.get("realized_pnl_by_ticker") or {}):
        print()
        print("── 누적 실현손익 (status.json) ──")
        for ticker in sorted({e.ticker for e in executions if e.realized_pnl}):
            print(f"  {ticker}: {format_money(accrued_pnl[ticker], market_type)}")

    new_status = None
    if not args.skip_status_rebuild:
        print()
        print("── status.json 재생성 ──")
        rules = _load_enabled_rules(args.config or f"config_{market_type}.json")
        new_status = rebuild_status(repo, positions, accrued_pnl, market_type, rules)
        if new_status is None:
            print("  직전 status.json 에 계좌 정보 없음 — 실현손익만 갱신")
        else:
            risk = new_status["risk_summary"]
            print(f"  기준 시점  : {new_status['last_run_date']} (직전 실행 상태 재사용)")
            print(f"  sync_error : {risk['sync_error']}")
            print(f"  경보       : {risk['alerts'] or '없음'}")

    snapshots = repo.load_snapshots()
    fixed_snap = None
    if not args.skip_snapshot_fix:
        fixed_snap = fix_snapshot_net_deposit(snapshots, trade_date, cash_impact)
        print()
        print("── snapshots 보정 ──")
        if fixed_snap:
            print(f"  {fixed_snap['date']} net_deposit -> "
                  f"{format_money(fixed_snap['net_deposit'], market_type)} "
                  f"(체결 현금영향 제외)")
        else:
            print(f"  체결일 이후 스냅샷 없음 -> {trade_date} 스냅샷 신규 기록")

    if args.dry_run:
        print()
        print("--dry-run 모드: 저장하지 않습니다.")
        return 0

    repo.save_positions(positions)
    repo.save_last_sell_prices(last_sell_prices)
    repo.save_trade_history(executions, portfolio, reason, sim_date=trade_date)
    repo.save_decision_log(f"{trade_date} 00:00:00", reason)
    if new_status is not None:
        repo.save_status(new_status)
    elif status:
        status["realized_pnl_by_ticker"] = accrued_pnl
        repo.save_status(status)
    if not args.skip_snapshot_fix:
        if fixed_snap:
            repo.save_snapshots(snapshots)
        else:
            repo.save_snapshot(portfolio, executions, sim_date=trade_date)

    print()
    print("✓ 저장 완료 (positions / history / last_sell_prices / decisions / status"
          + ("" if args.skip_snapshot_fix else " / snapshots") + ").")
    print("  다음 실행 전 scripts/reconcile_positions.py 로 계좌 수량과 일치하는지 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
