#!/usr/bin/env python3
"""티커 표기 형식을 MagicSplit 새 표준으로 일괄 변환한다.

새 표준: 국내 종목은 접미사 없이 6자리 코드만 사용한다.
- '005930.KS' -> '005930'
- '058470.KQ' -> '058470'

해외 종목('AAPL' 등)은 변경되지 않는다.

대상 파일: docs/data/{market}/positions.json, history.json, status.json,
          last_sell_prices.json. 처리 전 .bak 백업을 생성한다.

사용법:
    python scripts/migrate_ticker_format.py docs/data/backtest
    python scripts/migrate_ticker_format.py docs/data/domestic docs/data/backtest
"""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

# 6자리 숫자 코드 뒤의 .KS/.KQ 접미사를 제거한다 (티커·lot_id 양쪽 대응).
SUFFIX_RE = re.compile(r"(?<=\d)\.(KS|KQ)")


def strip_suffix(value):
    """문자열 내부의 .KS/.KQ 접미사를 제거한다 (lot_id 등 포함 패턴 대응)."""
    if isinstance(value, str):
        return SUFFIX_RE.sub("", value)
    return value


def transform(node):
    """JSON 트리를 재귀적으로 순회하며 ticker/lot_id 등 모든 문자열을 변환한다.

    딕셔너리 키도 변환 대상이다 (status.json의 positions/realized_pnl_by_ticker는
    ticker를 키로 사용한다).
    """
    if isinstance(node, dict):
        return {strip_suffix(k): transform(v) for k, v in node.items()}
    if isinstance(node, list):
        return [transform(v) for v in node]
    return strip_suffix(node)


def migrate_file(path: Path) -> bool:
    """단일 JSON 파일을 변환한다. 변경이 있었으면 True 반환."""
    with open(path, "r", encoding="utf-8") as f:
        original_text = f.read()
    if not SUFFIX_RE.search(original_text):
        return False

    try:
        data = json.loads(original_text)
    except json.JSONDecodeError as e:
        print(f"[skip] {path}: invalid JSON ({e})")
        return False

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)

    migrated = transform(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(migrated, f, indent=4, ensure_ascii=False)
        f.write("\n")

    print(f"[ok]   {path} (backup: {backup.name})")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directories",
        nargs="+",
        help="변환할 디렉토리 (예: docs/data/backtest)",
    )
    args = parser.parse_args()

    targets = ["positions.json", "history.json", "status.json", "last_sell_prices.json"]
    changed = 0
    for d in args.directories:
        d_path = Path(d)
        if not d_path.exists():
            print(f"[warn] {d_path} 없음 — 건너뜀")
            continue
        for name in targets:
            p = d_path / name
            if p.exists():
                if migrate_file(p):
                    changed += 1
    print(f"\n총 {changed}개 파일 변환 완료.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
