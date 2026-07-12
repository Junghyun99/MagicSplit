#!/usr/bin/env python3
"""history.json -> snapshots.json 마이그레이션(백필) CLI.

기존 매매 로그(history.json)로부터 일별 자산 스냅샷(snapshots.json)을 생성한다.
하루에 거래가 여러 번인 경우 save_snapshot의 같은-날짜 누적 로직과 동일하게,
그날 마지막 자산값 + net_deposit 합으로 하루 1개의 스냅샷을 만든다.

기본은 백필 모드(기존 snapshots.json에 없는 날짜만 추가). --overwrite로 전체
재작성, --dry-run으로 미리보기(파일 미기록).

사용법:
    python -m scripts.migrate_snapshots --market domestic --dry-run
    python -m scripts.migrate_snapshots --market domestic
    python -m scripts.migrate_snapshots --market overseas --overwrite
"""
import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.infra.repo import JsonRepository
from src.utils.currency import format_money


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", required=True, choices=["domestic", "overseas"],
                        help="마이그레이션 대상 시장")
    parser.add_argument("--data-root", default="docs/data",
                        help="데이터 루트 경로 (기본: docs/data)")
    parser.add_argument("--overwrite", action="store_true",
                        help="기존 snapshots.json을 history 파생값으로 전체 재작성")
    parser.add_argument("--dry-run", action="store_true",
                        help="파일을 쓰지 않고 결과만 출력")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    repo = JsonRepository(os.path.join(args.data_root, args.market))

    if args.dry_run:
        history = repo._load_json(repo.history_file, default=[])
        derived = repo.snapshots_from_history(history)
        existing = repo.load_snapshots()
        print(f"=== 마이그레이션 미리보기 ({args.market}, dry-run) ===")
        print(f"history 레코드   : {len(history)}건")
        print(f"파생 스냅샷(일)  : {len(derived)}일")
        print(f"기존 snapshots   : {len(existing)}건")
        if derived:
            print("-" * 48)
            print("파생 스냅샷 (앞 3 / 뒤 3):")
            for s in derived[:3] + (["..."] if len(derived) > 6 else []) + derived[-3:]:
                if s == "...":
                    print("  ...")
                    continue
                print(f"  {s['date']}  자산={format_money(s['portfolio_value'], args.market):>16}"
                      f"  순입금={format_money(s['net_deposit'], args.market)}")
        print("\n(파일에 기록하려면 --dry-run 없이 실행)")
        return 0

    stats = repo.migrate_snapshots_from_history(overwrite=args.overwrite)
    mode = "전체 재작성" if args.overwrite else "백필(누락일 추가)"
    print(f"=== 마이그레이션 완료 ({args.market}, {mode}) ===")
    print(f"history 레코드   : {stats['history']}건")
    print(f"파생 스냅샷(일)  : {stats['derived']}일")
    print(f"기존 snapshots   : {stats['existing']}건")
    print(f"저장된 snapshots : {stats['written']}건 -> {repo.snapshots_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
