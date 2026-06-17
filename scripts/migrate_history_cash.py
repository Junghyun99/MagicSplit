"""
One-time migration script: add cash_balance and net_deposit to history.json.

Usage:
    python scripts/migrate_history_cash.py --path docs/data/domestic/history.json \
        --initial-cash 300000 \
        --deposits "2026-05-05:1000000" "2026-05-18:790000" "2026-05-19:30000000" \
                   "2026-05-29:-8000000" "2026-06-08:3000000" "2026-06-15:400000"

For each record:
  - net_deposit  = sum of external cash flows (deposits/withdrawals) since the previous record
  - cash_balance = previous cash + net_deposit + trade_cash_impact
  - portfolio_value = null if originally 0.0 (API failure)
"""
import argparse
import json
import os
import copy


def calc_trade_cash_impact(executions):
    """BUY decreases cash, SELL increases cash (after fee)."""
    impact = 0.0
    for e in executions:
        qty = e.get("quantity") or 0
        price = e.get("price") or 0
        fee = e.get("fee") or 0
        action = (e.get("action") or "").upper()
        if qty > 0:
            if action == "BUY":
                impact -= price * qty + fee
            elif action == "SELL":
                impact += price * qty - fee
    return impact


def migrate(path, initial_cash, deposit_events):
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    # Sort chronologically (file may not be in order)
    records.sort(key=lambda r: r["date"])

    # Deposit events: list of (date_str "YYYY-MM-DD", amount)
    # Each deposit is consumed exactly once when the first record on/after its date is processed.
    remaining = list(deposit_events)

    cash = float(initial_cash)
    prev_date = "2000-01-01"

    print(f"Initial cash: {cash:,.0f}")
    print()

    for record in records:
        rec_date = record["date"][:10]

        # Collect deposits between prev_date (exclusive) and rec_date (inclusive)
        net_deposit = 0.0
        unconsumed = []
        for dep_date, dep_amount in remaining:
            if prev_date < dep_date <= rec_date:
                net_deposit += dep_amount
                print(f"  [{dep_date}] Deposit applied to {record['id']}: {dep_amount:+,.0f}")
            else:
                unconsumed.append((dep_date, dep_amount))
        remaining = unconsumed

        trade_impact = calc_trade_cash_impact(record.get("executions", []))
        cash = cash + net_deposit + trade_impact

        record["cash_balance"] = round(cash, 2)
        record["net_deposit"] = round(net_deposit, 2)

        # portfolio_value == 0.0 means the API failed; mark as null so the equity curve skips it
        if record.get("portfolio_value") == 0.0:
            old_pv = record["portfolio_value"]
            record["portfolio_value"] = None
            print(f"  [{record['id']}] portfolio_value {old_pv} -> null (API failure)")

        print(
            f"  {record['date']}  deposit={net_deposit:+,.0f}  trade={trade_impact:+,.0f}"
            f"  cash={cash:,.2f}"
        )
        prev_date = rec_date

    if remaining:
        print(f"\nWARNING: {len(remaining)} deposit event(s) not applied (after last record):")
        for dep_date, dep_amount in remaining:
            print(f"  {dep_date}: {dep_amount:+,.0f}")

    print(f"\nFinal cash balance: {cash:,.2f}")

    # Write back with same formatting as repo._save_json (4-space indent, ensure_ascii=False)
    import re
    content = json.dumps(records, indent=4, ensure_ascii=False)
    # Compress simple numeric/string arrays to one line (mirrors repo._save_json)
    content = re.sub(
        r'\[\s+((?:-?\d+(?:\.\d+)?(?:,\s+)?)+)\s+\]',
        lambda m: "[" + re.sub(r'\s+', ' ', m.group(1)) + "]",
        content,
    )
    content = re.sub(
        r'\[\s+((?:"[^"]*"(?:,\s+)?)+)\s+\]',
        lambda m: "[" + re.sub(r'\s+', ' ', m.group(1)) + "]",
        content,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\nWrote {len(records)} records to {path}")


def parse_deposit(s):
    date, amount = s.rsplit(":", 1)
    return date.strip(), float(amount.strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add cash_balance/net_deposit to history.json")
    parser.add_argument("--path", required=True, help="Path to history.json")
    parser.add_argument("--initial-cash", type=float, required=True, help="Starting cash before any records")
    parser.add_argument("--deposits", nargs="*", default=[], metavar="DATE:AMOUNT",
                        help='Deposit events, e.g. "2026-05-05:1000000" (negative for withdrawal)')
    args = parser.parse_args()

    deposit_events = [parse_deposit(d) for d in args.deposits]
    migrate(args.path, args.initial_cash, deposit_events)
