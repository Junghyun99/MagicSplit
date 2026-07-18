#!/usr/bin/env python3
"""기간(월간) 결산 CLI.

snapshots.json(일별 자산 스냅샷)을 기반으로 지정한 기간의
기초자산 / 기말자산 / 순입금액 / 기간손익(금액) / 수익률(TWR)을 계산해 출력한다.
history.json의 실현손익 합계도 교차검증용으로 함께 표시한다.

결산 항등식: 기간손익 = 기말자산 - 기초자산 - 순입금액

해외(overseas)는 USD 결산과 함께, 각 스냅샷 시점의 그날 기준환율로 원화 환산한
결산(증권사 계좌평가와 동일한 방식)을 항상 두 버전으로 나란히 출력한다.

사용법:
    python -m scripts.monthly_settlement --market domestic --start 2026-04-01 --end 2026-04-28
    python -m scripts.monthly_settlement --market overseas --start 2026-04-01 --end 2026-04-30
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.core.settlement import compute_settlement, convert_snapshots_to_krw
from src.infra.repo import JsonRepository
from src.utils.currency import currency_code_for, format_money

DATE_FMT = "%Y-%m-%d"


def _valid_date(text: str) -> str:
    """YYYY-MM-DD 형식 검증 후 zero-pad 정규화된 문자열을 반환한다.

    strptime이 '2026-4-1' 같은 비패딩 입력도 허용하므로, 스냅샷 날짜와의
    사전식(lexicographic) 비교가 어긋나지 않도록 canonical form으로 통일한다.
    """
    try:
        dt = datetime.strptime(text, DATE_FMT)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"날짜 형식이 올바르지 않습니다: '{text}' (형식: YYYY-MM-DD, 예: 2026-04-01)"
        )
    return dt.strftime(DATE_FMT)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", required=True,
                        choices=["domestic", "overseas", "crypto"],
                        help="결산 대상 시장")
    parser.add_argument("--start", required=True, type=_valid_date,
                        help="조회 시작일 (YYYY-MM-DD, 예: 2026-04-01)")
    parser.add_argument("--end", required=True, type=_valid_date,
                        help="조회 종료일 (YYYY-MM-DD, 예: 2026-04-28)")
    parser.add_argument("--data-root", default="docs/data",
                        help="데이터 루트 경로 (기본: docs/data)")
    return parser.parse_args(argv)


def _realized_pnl_in_range(history: list, start: str, end: str) -> float:
    """history.json에서 [start, end] 기간 SELL 실현손익 합계 (교차검증용)."""
    total = 0.0
    for tx in history:
        for ex in (tx.get("executions") or []):
            if (ex.get("action") or "").upper() != "SELL":
                continue
            if ex.get("realized_pnl") is None:
                continue
            date_str = (ex.get("date") or tx.get("date") or "")[:10]
            if start <= date_str <= end:
                total += float(ex["realized_pnl"])
    return round(total, 2)


def build_report(result, realized_pnl: float, market: str,
                 display_currency: str = None, dropped_missing_count: int = 0) -> str:
    """결산 결과를 사람이 읽는 리포트 문자열로 조립한다.

    display_currency를 지정하면(예: 원화 환산 결산의 "KRW") 기초/기말/손익을 그 통화로
    표기한다. 실현손익 교차검증은 원본 체결 통화(market 기준)를 그대로 유지한다.
    """
    disp = display_currency or currency_code_for(market)
    native = currency_code_for(market)
    fm = lambda v: format_money(v, market, currency=disp)
    twr = "-" if result.twr_pct is None else f"{result.twr_pct:+.2f}%"
    profit_sign = "+" if result.profit >= 0 else ""
    header = f"=== 기간 결산 ({market}) ==="
    if disp != native:
        header = f"=== 기간 결산 ({market}, {disp} 환산) ==="
    lines = [
        header,
        f"기간           : {result.start_date} ~ {result.end_date}",
        f"스냅샷 개수    : {result.snapshot_count}건",
    ]
    if result.snapshot_count == 0:
        lines.append("")
        lines.append("해당 기간에 스냅샷 데이터가 없습니다. (거래/실행 이력 확인 필요)")
        return "\n".join(lines)
    lines += [
        f"기초자산일     : {result.base_date}",
        f"기말자산일     : {result.last_date}",
        "-" * 40,
        f"기초자산       : {fm(result.start_asset)}",
        f"기말자산       : {fm(result.end_asset)}",
        f"순입금액       : {fm(result.net_deposit)}",
        f"기간손익(금액) : {profit_sign}{fm(result.profit)}",
        f"수익률(TWR)    : {twr}",
        "-" * 40,
        f"[교차검증] 실현손익 합계 : {format_money(realized_pnl, market)}",
        "  (실현손익은 매도 확정분만 집계 - 평가손익 미포함, 참고용)",
    ]
    if disp != native:
        lines += [
            f"  (자산/손익은 각 시점 기준환율로 {disp} 환산 - 주가손익+환차손익 포함.",
            f"   실현손익 교차검증은 원본 체결 통화 {native} 기준)",
        ]
        if dropped_missing_count:
            lines.append(
                f"  주의: 기준환율이 없어 {dropped_missing_count}건의 스냅샷을 환산에서 제외했습니다 "
                f"(환율 저장 이전 구간은 소급 불가)."
            )
    return "\n".join(lines)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.start > args.end:
        print(f"오류: 시작일({args.start})이 종료일({args.end})보다 뒤입니다.",
              file=sys.stderr)
        return 2

    repo = JsonRepository(os.path.join(args.data_root, args.market))
    snapshots = repo.load_snapshots()
    history = repo._load_json(repo.history_file, default=[])
    realized_pnl = _realized_pnl_in_range(history, args.start, args.end)

    # 시장 통화(해외=USD, 국내=KRW) 기준 결산
    native_result = compute_settlement(snapshots, args.start, args.end)
    reports = [build_report(native_result, realized_pnl, args.market)]

    # 해외는 원화 환산 버전도 항상 함께 출력 (각 시점 기준환율, 증권사 방식)
    if args.market == "overseas":
        reports.append(_krw_report(snapshots, args.start, args.end, realized_pnl))

    print("\n\n".join(reports))
    return 0


def _krw_report(snapshots: list, start: str, end: str, realized_pnl: float) -> str:
    """해외 스냅샷을 각 시점 기준환율로 원화 환산한 결산 리포트를 만든다.

    환율이 저장된 스냅샷이 하나도 없으면(환율 기록 이전 구간) 안내 문구를 반환한다.
    """
    krw_snaps, dropped = convert_snapshots_to_krw(snapshots)
    if not krw_snaps:
        return ("=== 기간 결산 (overseas, KRW 환산) ===\n"
                "환산 가능한(기준환율이 저장된) 스냅샷이 없습니다. "
                "원화 결산은 환율을 저장하기 시작한 이후 구간부터 가능합니다.")
    krw_result = compute_settlement(krw_snaps, start, end)
    return build_report(krw_result, realized_pnl, "overseas",
                        display_currency="KRW", dropped_missing_count=dropped)


if __name__ == "__main__":
    raise SystemExit(main())
