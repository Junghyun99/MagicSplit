#!/usr/bin/env python3
"""One-time migration: add pre-existing BTC/ETH positions to crypto positions.json.

These coins were held before the crypto account was connected to MagicSplit.
The script replaces any existing lots for the target tickers and writes
a single Lv1 lot per coin with the broker's average cost basis.

Usage:
    python scripts/migrate_crypto_legacy.py              # dry-run (default)
    python scripts/migrate_crypto_legacy.py --apply      # actually write
"""
import argparse
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/..")

from src.infra.repo import JsonRepository

CRYPTO_DATA_PATH = os.path.join("docs", "data", "crypto")

LEGACY_POSITIONS = [
    {
        "ticker": "KRW-BTC",
        "quantity": 0.13703789,
        "buy_price": 114690236.0,
        "buy_date": "2026-07-17",
        "level": 1,
    },
    {
        "ticker": "KRW-ETH",
        "quantity": 1.54440048,
        "buy_price": 3847313.0,
        "buy_date": "2026-07-17",
        "level": 1,
    },
]

TICKERS_TO_REPLACE = {p["ticker"] for p in LEGACY_POSITIONS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write to positions.json (default is dry-run)",
    )
    args = parser.parse_args()

    repo = JsonRepository(CRYPTO_DATA_PATH)
    existing = repo.load_positions()

    print("=== crypto legacy migration ===")
    print()

    removed = [lot for lot in existing if lot.ticker in TICKERS_TO_REPLACE]
    kept = [lot for lot in existing if lot.ticker not in TICKERS_TO_REPLACE]

    if removed:
        print("Removing existing lots:")
        for lot in removed:
            print(f"  {lot.lot_id}  {lot.ticker} Lv{lot.level}  "
                  f"qty={lot.quantity}  price={lot.buy_price:,.0f}")
    else:
        print("No existing lots to remove for target tickers.")

    print()
    print("Adding legacy lots:")
    from src.core.models import PositionLot
    new_lots = []
    for p in LEGACY_POSITIONS:
        ts = p["buy_date"].replace("-", "")
        lot_id = f"lot_{ts}_000000_{p['ticker']}_{p['level']:03d}_migrate"
        lot = PositionLot(
            lot_id=lot_id,
            ticker=p["ticker"],
            buy_price=p["buy_price"],
            quantity=p["quantity"],
            buy_date=p["buy_date"],
            level=p["level"],
        )
        new_lots.append(lot)
        cost = p["buy_price"] * p["quantity"]
        print(f"  {lot_id}")
        print(f"    {p['ticker']} Lv{p['level']}  qty={p['quantity']}  "
              f"price={p['buy_price']:,.0f}  cost={cost:,.0f}")

    final = kept + new_lots

    print()
    print(f"Final positions ({len(final)} lots):")
    for lot in final:
        print(f"  {lot.ticker} Lv{lot.level}  qty={lot.quantity}  "
              f"price={lot.buy_price:,.0f}  [{lot.lot_id}]")

    if not args.apply:
        print()
        print("Dry-run mode. Use --apply to write changes.")
        return 0

    repo.save_positions(final)
    print()
    print(f"Written to {repo.positions_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
