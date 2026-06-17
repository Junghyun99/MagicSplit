"""
Patch script: correct cash_balance and net_deposit in history.json
using log-derived before-trade cash values as ground truth.

Formula:
  cash_balance[i] = before_cash[i] + trade_cash_impact[i]
  net_deposit[i]  = before_cash[i] - cash_balance[i-1]   (0 for first record)
"""
import json
import re

# D+2 예수금 observed in KIS logs just before each record's trades execute.
# Entries with "(API fail)" used Available Cash as the best proxy.
BEFORE_CASH = {
    "tx_20260511_003940": 802066.0,    # before midnight manual BUY
    "tx_20260511_004126": 521361.0,    # before midnight manual SELL #1
    "tx_20260511_004308": 801931.0,    # before midnight manual SELL #2
    "tx_20260511_093805": 1082826.0,   # before morning auto BUY
    "tx_20260518_095353": 1082556.0,   # before May 18 BUY
    "tx_20260519_143406": 31050446.0,  # before May 19 big BUY batch (790K+30M already landed)
    "tx_20260520_145432": 20555841.0,  # before May 20 BUY
    "tx_20260526_101747": 20066181.0,  # before May 26 morning BUY
    "tx_20260526_145500": 19118251.0,  # before May 26 afternoon BUYs
    "tx_20260527_150559": 17751561.0,  # before May 27 mixed trades
    "tx_20260528_094547": 17406254.0,  # before May 28 morning BUYs
    "tx_20260528_145652": 15570299.0,  # before May 28 afternoon SELL
    "tx_20260529_090500": 16604784.0,  # before May 29 morning BUYs (-8M withdrawal not yet applied)
    "tx_20260529_150018": 6740534.0,   # before May 29 afternoon BUY (-8M withdrawal already applied)
    "tx_20260602_024338": 6259879.0,   # API fail; Available Cash used as proxy
    "tx_20260602_090631": 6259879.0,   # D+2 before auto trades (02:43 BUY not yet settled in D+2)
    "tx_20260604_142946": 2304074.0,   # before Jun 4 SELL
    "tx_20260605_104902": 3008572.0,   # before Jun 5 morning SELLs
    "tx_20260605_140617": 8352730.0,   # before Jun 5 afternoon BUYs
    "tx_20260608_003712": 4211048.0,   # API fail; Available Cash used as proxy
    "tx_20260608_142200": 3559958.0,   # before Jun 8 auto trades
    "tx_20260609_142505": 3257947.0,   # before Jun 9 BUYs
    "tx_20260610_140833": 1826567.0,   # before Jun 10 BUYs
    "tx_20260615_100114": 329367.0,    # before Jun 15 trades
    "tx_20260616_100110": 34386.0,     # before Jun 16 10:01 SELL
    "tx_20260616_120110": 1821016.0,   # before Jun 16 12:01 BUY
}


def calc_trade_cash_impact(executions):
    """BUY decreases cash (cost + fee), SELL increases cash (proceeds - fee)."""
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


PATH = "docs/data/domestic/history.json"

with open(PATH, "r", encoding="utf-8") as f:
    records = json.load(f)

records.sort(key=lambda r: r["date"])

prev_cash = None
print(f"{'ID':<30} {'before':>12} {'trade':>14} {'cash_bal':>14} {'net_dep':>12}")
print("-" * 86)

for record in records:
    tx_id = record["id"]
    before_cash = BEFORE_CASH.get(tx_id)
    if before_cash is None:
        print(f"WARNING: no before_cash for {tx_id} — skipping")
        continue

    trade_impact = calc_trade_cash_impact(record.get("executions", []))
    cash_balance = before_cash + trade_impact
    net_deposit = round(before_cash - prev_cash, 2) if prev_cash is not None else 0.0

    record["cash_balance"] = round(cash_balance, 2)
    record["net_deposit"] = net_deposit

    if record.get("portfolio_value") == 0.0:
        record["portfolio_value"] = None
        print(f"  -> {tx_id}: portfolio_value 0.0 -> null")

    print(f"{tx_id:<30} {before_cash:>12,.0f} {trade_impact:>+14,.2f} {cash_balance:>14,.2f} {net_deposit:>+12,.2f}")
    prev_cash = cash_balance

# Write back with same compact-array formatting as repo._save_json
content = json.dumps(records, indent=4, ensure_ascii=False)
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

with open(PATH, "w", encoding="utf-8") as f:
    f.write(content)

print(f"\nWrote {len(records)} records to {PATH}")
