#!/usr/bin/env python3
"""기존 snapshots.json에 과거 기준환율(exchange_rate)을 채워 넣는 마이그레이션.

해외(overseas) 스냅샷은 초기에 환율 없이 USD로만 저장되어, 원화 환산 결산이
불가능하다. 이 스크립트는 각 스냅샷 날짜의 원/달러(USD/KRW) 환율을 조회해
exchange_rate 필드를 채운다. 이후 monthly_settlement의 원화 결산이 소급 적용된다.

환율 출처: Yahoo Finance 일봉(KRW=X) 종가. KIS의 실시간 기준환율(최초고시환율)과
정확히 같지는 않은 근사치(과거 스팟 종가)이므로 참고 목적임을 유의한다.

거래일이 아닌 날짜(주말/공휴일)는 직전 거래일 종가로 forward-fill 한다.

사용법:
    # 미리보기 (파일 미변경)
    python -m scripts.migrate_snapshot_fx --market overseas --dry-run
    # 실제 적용 (환율이 없는 스냅샷만 채움)
    python -m scripts.migrate_snapshot_fx --market overseas
    # 기존 값까지 모두 재조회
    python -m scripts.migrate_snapshot_fx --market overseas --overwrite
"""
import argparse
import bisect
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.infra.repo import JsonRepository

DATE_FMT = "%Y-%m-%d"
FX_TICKER = "KRW=X"  # USD/KRW 스팟
_YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def _to_epoch(date_str: str) -> int:
    """YYYY-MM-DD 00:00 UTC 를 유닉스 초로 변환."""
    dt = datetime.strptime(date_str, DATE_FMT).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_usdkrw_rates(start: str, end: str) -> dict:
    """[start, end] 구간 USD/KRW 일봉 종가를 {YYYY-MM-DD: rate}로 반환한다.

    Yahoo Finance 차트 API를 requests로 직접 호출한다(프록시/CA 준수). yfinance의
    curl_cffi 임퍼서네이션은 사내 프록시와 충돌할 수 있어 사용하지 않는다.
    """
    import requests

    period1 = _to_epoch(start)
    period2 = _to_epoch(end) + 86400  # end 당일 포함되도록 하루 여유
    params = {"period1": period1, "period2": period2, "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0"}

    last_err = None
    for host in _YAHOO_HOSTS:
        url = f"https://{host}/v8/finance/chart/{FX_TICKER}"
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            payload = r.json()
            result = (payload.get("chart") or {}).get("result") or []
            if not result:
                last_err = "빈 응답(result 없음)"
                continue
            res = result[0]
            timestamps = res.get("timestamp") or []
            closes = (((res.get("indicators") or {}).get("quote") or [{}])[0]
                      .get("close") or [])
            rates = {}
            for ts, close in zip(timestamps, closes):
                if close is None:
                    continue
                day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(DATE_FMT)
                rates[day] = round(float(close), 4)
            if rates:
                return rates
            last_err = "종가 데이터 없음"
        except Exception as e:  # noqa: BLE001 - 호스트 폴백을 위해 광범위 캐치
            last_err = e
    raise RuntimeError(f"USD/KRW 환율 조회 실패: {last_err}")


def resolve_rate(date_str: str, sorted_days: list, rates: dict):
    """date_str 이하의 가장 최근 거래일 환율을 반환한다(forward-fill). 없으면 None.

    sorted_days는 rates의 키를 오름차순 정렬한 리스트여야 한다.
    """
    idx = bisect.bisect_right(sorted_days, date_str) - 1
    if idx < 0:
        return None
    return rates[sorted_days[idx]]


def apply_rates(snapshots: list, rates: dict, overwrite: bool = False):
    """스냅샷에 exchange_rate를 채운다(in-place). (updated, missing) 튜플 반환.

    overwrite=False면 값이 없는(None/부재) 스냅샷만 채운다.
    forward-fill로도 환율을 못 찾은 날짜는 missing 리스트에 담아 반환한다.
    """
    sorted_days = sorted(rates.keys())
    updated = 0
    missing = []
    for s in snapshots:
        if not overwrite and s.get("exchange_rate") is not None:
            continue
        date_key = (s.get("date") or "")[:10]
        rate = resolve_rate(date_key, sorted_days, rates)
        if rate is None:
            missing.append(date_key)
            continue
        s["exchange_rate"] = rate
        updated += 1
    return updated, missing


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="overseas", choices=["domestic", "overseas"],
                        help="대상 시장 (기본: overseas). domestic은 원화라 환율 불필요")
    parser.add_argument("--data-root", default="docs/data",
                        help="데이터 루트 경로 (기본: docs/data)")
    parser.add_argument("--overwrite", action="store_true",
                        help="이미 환율이 있는 스냅샷도 재조회해 덮어쓴다")
    parser.add_argument("--dry-run", action="store_true",
                        help="파일을 저장하지 않고 변경 예정 내역만 출력한다")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.market == "domestic":
        print("domestic은 원화(KRW) 계좌라 환율 환산이 필요 없습니다. 작업을 건너뜁니다.")
        return 0

    repo = JsonRepository(os.path.join(args.data_root, args.market))
    snapshots = repo.load_snapshots()
    if not snapshots:
        print("스냅샷이 없습니다. 마이그레이션할 대상이 없습니다.")
        return 0

    targets = [s for s in snapshots
               if args.overwrite or s.get("exchange_rate") is None]
    if not targets:
        print("모든 스냅샷에 이미 환율이 있습니다. (--overwrite로 강제 재조회 가능)")
        return 0

    dates = sorted((s.get("date") or "")[:10] for s in snapshots if s.get("date"))
    # forward-fill 여유를 위해 시작일보다 10일 앞에서부터 조회
    start = (datetime.strptime(dates[0], DATE_FMT) - timedelta(days=10)).strftime(DATE_FMT)
    end = dates[-1]
    print(f"USD/KRW 환율 조회: {start} ~ {end} ...")
    rates = fetch_usdkrw_rates(start, end)
    print(f"  조회된 거래일: {len(rates)}건")

    updated, missing = apply_rates(snapshots, rates, overwrite=args.overwrite)

    print(f"환율 적용 대상: {len(targets)}건 -> 적용 {updated}건")
    if missing:
        print(f"  경고: {len(missing)}건은 환율을 찾지 못했습니다 (조회 구간 이전 등): "
              f"{', '.join(missing)}")

    # 적용 결과 미리보기 (앞뒤 몇 건)
    applied = [s for s in snapshots if s.get("exchange_rate") is not None]
    preview = applied[:3] + (["..."] if len(applied) > 6 else []) + applied[-3:] \
        if len(applied) > 6 else applied
    for s in preview:
        if s == "...":
            print("  ...")
        else:
            print(f"  {s['date']}: USD {s['portfolio_value']:>10,.2f}  x  "
                  f"{s['exchange_rate']:>9,.2f}  = KRW {s['portfolio_value']*s['exchange_rate']:>14,.0f}")

    if args.dry_run:
        print("[dry-run] 파일을 저장하지 않았습니다.")
        return 0

    if updated == 0:
        print("변경된 내용이 없어 저장하지 않습니다.")
        return 0

    repo._save_json(repo.snapshots_file, snapshots)
    print(f"저장 완료: {repo.snapshots_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
