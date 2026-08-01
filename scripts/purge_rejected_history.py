#!/usr/bin/env python3
"""history.json에 잘못 기록된 거절(REJECTED) 체결을 제거하고 파생값을 보정한다.

save_trade_history가 상태를 가리지 않고 저장하던 시절의 데이터를 정리하는
일회성 마이그레이션이다. 저장 경로는 이미 고쳐졌으므로 새로 쌓이지는 않는다.

거절 레코드가 남기는 오염:
  - 유령 매매(수량 0 SELL)가 History 탭에 표시된다
  - 사전 차단(스프레드 비정상 등)은 '요청 수량'을 그대로 담아 저장돼
    total_trade_amount와 net_deposit(순입금)까지 틀어진다
  - get_last_trade_dates가 거래일로 세어 '장기 정체 종목'이 리셋된다
  - snapshots.json의 net_deposit도 같은 식으로 계산돼 월간 결산이 틀어진다

보정 방식은 '영향받은 레코드만 최소 수정'이다. 전 구간 재계산은 하지 않는다.
과거에 다른 마이그레이션 스크립트가 손댄 레코드나 cash_balance가 없는 레코드가
섞여 있어, 처음부터 다시 계산하면 멀쩡한 값까지 바뀌기 때문이다.

  net_deposit = 현금변화 - 거래현금영향
  거절분을 빼면 거래현금영향이 그만큼 줄므로 -> net_deposit += 제거된_현금영향

레코드가 통째로 사라지는 경우(체결이 전부 거절), 그 구간의 순입금을 잃지 않도록
보정된 net_deposit을 다음(없으면 이전) 생존 레코드로 이월한다.

사용법:
    python -m scripts.purge_rejected_history --dry-run          # 전체 마켓 미리보기
    python -m scripts.purge_rejected_history --market domestic  # 특정 마켓만
    python -m scripts.purge_rejected_history                    # 실제 적용
"""
import argparse
import copy
import json
import os
import sys

MARKETS = ("domestic", "overseas", "crypto", "backtest")
REJECTED = "REJECTED"


def cash_impact(executions) -> float:
    """저장된 체결 레코드의 현금 변동. repo.trade_cash_impact와 같은 규칙."""
    total = 0.0
    for e in executions:
        qty = e.get("quantity") or 0
        if qty <= 0:
            continue
        amount = (e.get("price") or 0.0) * qty
        fee = e.get("fee") or 0.0
        total += (-amount - fee) if e.get("action") == "BUY" else (amount - fee)
    return total


def purge(records):
    """거절 체결을 제거하고 영향받은 레코드만 보정한다.

    Returns:
        (정리된 레코드, 제거된 체결 수, 제거된 레코드 수, 제거된 현금영향 합)
    """
    records = copy.deepcopy(records)
    cleaned = []
    removed_execs = 0
    removed_records = 0
    total_removed_impact = 0.0
    carry = 0.0  # 삭제된 레코드에서 이월할 순입금

    for rec in records:
        execs = rec.get("executions", [])
        kept = [e for e in execs if e.get("status") != REJECTED]
        dropped = [e for e in execs if e.get("status") == REJECTED]

        if not dropped:
            if carry and rec.get("net_deposit") is not None:
                rec["net_deposit"] = round(rec["net_deposit"] + carry, 2)
                carry = 0.0
            cleaned.append(rec)
            continue

        removed_execs += len(dropped)
        removed_impact = cash_impact(dropped)
        total_removed_impact += removed_impact

        # 거절분을 빼면 거래현금영향이 줄어든 만큼 순입금이 올라간다.
        corrected_nd = rec.get("net_deposit")
        if corrected_nd is not None:
            corrected_nd = round(corrected_nd + removed_impact, 2)

        # 원래 체결이 있었는데 전부 거절이면 매매가 없던 사이클이다 -> 레코드 삭제
        if execs and not kept:
            removed_records += 1
            if corrected_nd:
                carry += corrected_nd
            continue

        rec["executions"] = kept
        rec["total_trade_amount"] = sum(
            (e.get("price") or 0.0) * (e.get("quantity") or 0) for e in kept
        )
        if corrected_nd is not None:
            rec["net_deposit"] = round(corrected_nd + carry, 2)
            carry = 0.0
        cleaned.append(rec)

    # 마지막까지 이월분이 남으면 직전 생존 레코드에 붙인다 (총합 보존).
    if carry and cleaned:
        for rec in reversed(cleaned):
            if rec.get("net_deposit") is not None:
                rec["net_deposit"] = round(rec["net_deposit"] + carry, 2)
                break

    return cleaned, removed_execs, removed_records, total_removed_impact


def removed_impact_by_date(records):
    """날짜(YYYY-MM-DD)별 '거절이 만들어낸 가짜 현금영향'을 모은다.

    snapshots.json은 체결 내역을 담지 않아 스스로 재계산할 수 없다.
    history.json의 거절 체결에서 날짜별 오염량을 뽑아 스냅샷에 되돌려준다.
    """
    by_date = {}
    for rec in records:
        rec_date = (rec.get("date") or "")[:10]
        for e in rec.get("executions", []):
            if e.get("status") != REJECTED:
                continue
            date = (e.get("date") or "")[:10] or rec_date
            if not date:
                continue
            by_date[date] = by_date.get(date, 0.0) + cash_impact([e])
    return {d: v for d, v in by_date.items() if v}


def patch_snapshots(path, by_date, dry_run):
    """스냅샷의 net_deposit에서 가짜 현금영향을 걷어낸다."""
    if not by_date or not os.path.exists(path):
        return False

    with open(path, encoding="utf-8") as f:
        snapshots = json.load(f)

    changed = []
    for snap in snapshots:
        delta = by_date.get(snap.get("date"))
        if not delta or snap.get("net_deposit") is None:
            continue
        old = snap["net_deposit"]
        snap["net_deposit"] = round(old + delta, 2)
        changed.append((snap["date"], old, snap["net_deposit"]))

    if not changed:
        return False

    print(f"    스냅샷 순입금 정정: {len(changed)}건  ({path})")
    for date, old, new in changed:
        print(f"      [정정] {date}: {old:,.2f} -> {new:,.2f}")

    if dry_run:
        return False

    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, indent=4, ensure_ascii=False)
    return True


def check_invariant(before, after, removed_impact):
    """순입금 총합은 정확히 '제거된 현금영향'만큼 늘어야 한다.

    net_deposit = 현금변화 - 거래현금영향 이고 현금변화 총합은 그대로이므로,
    유령 거래를 걷어낸 만큼만 순입금이 복원되는 게 유일한 정답이다.
    """
    sum_before = sum(r.get("net_deposit") or 0 for r in before)
    sum_after = sum(r.get("net_deposit") or 0 for r in after)
    expected = sum_before + removed_impact
    return sum_after, expected, abs(sum_after - expected) <= 0.05


def process(market_dir, dry_run):
    path = os.path.join(market_dir, "history.json")
    if not os.path.exists(path):
        print(f"  건너뜀 (파일 없음): {path}")
        return False

    with open(path, encoding="utf-8") as f:
        before = json.load(f)

    after, removed_execs, removed_records, removed_impact = purge(before)

    if not removed_execs:
        print(f"  변경 없음: {path}")
        return False

    got, expected, ok = check_invariant(before, after, removed_impact)

    print(f"  {path}")
    print(f"    거절 체결 제거   : {removed_execs}건")
    print(f"    빈 레코드 제거   : {removed_records}건")
    print(f"    제거된 현금영향  : {removed_impact:,.2f}")
    print(f"    순입금 총합      : {sum(r.get('net_deposit') or 0 for r in before):,.2f}"
          f" -> {got:,.2f} (기대 {expected:,.2f})")

    if not ok:
        print("    !! 중단: 순입금 총합이 기대값과 다릅니다. 적용하지 않습니다.")
        return False

    by_id = {r.get("id"): r for r in after}
    for rec in before:
        new = by_id.get(rec.get("id"))
        if new is None:
            print(f"      [삭제] {rec['date']} (체결 전부 거절)")
        elif new.get("net_deposit") != rec.get("net_deposit"):
            print(f"      [정정] {rec['date']}: net_deposit "
                  f"{rec.get('net_deposit'):,.2f} -> {new.get('net_deposit'):,.2f}")

    # 스냅샷은 체결 내역이 없어 스스로 못 고친다. 여기서 함께 보정한다.
    patch_snapshots(
        os.path.join(market_dir, "snapshots.json"),
        removed_impact_by_date(before), dry_run,
    )

    if dry_run:
        print("    (dry-run - 저장하지 않음)")
        return False

    with open(path, "w", encoding="utf-8") as f:
        json.dump(after, f, indent=4, ensure_ascii=False)
    print("    저장 완료")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=MARKETS, help="특정 마켓만 처리")
    parser.add_argument("--root", default="docs/data", help="데이터 루트 (기본: docs/data)")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 미리보기")
    args = parser.parse_args()

    markets = [args.market] if args.market else list(MARKETS)
    print(f"거절 체결 정리 {'(dry-run)' if args.dry_run else ''}")
    changed = False
    for market in markets:
        if process(os.path.join(args.root, market), args.dry_run):
            changed = True

    if args.dry_run:
        print("\ndry-run 이었습니다. 실제로 적용하려면 --dry-run 없이 다시 실행하세요.")
    elif not changed:
        print("\n변경된 파일이 없습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
