"""
Patch script: fill cash_balance, net_deposit, and principal_flow in
docs/data/overseas/history.json using log-derived before-trade cash values.

Formula:
  cash_balance[i] = before_cash[i] + trade_cash_impact[i]
  net_deposit[i]  = before_cash[i] - cash_balance[i-1]   (unless overridden)
  net_deposit[0]  = before_cash[0]   (first record)

All amounts in USD. fee=0.0 for all overseas executions.

Key special cases:
  tx_20260512_003639  net_deposit override=0: cash jump came from manual stock
                      sales before the algo era, not an external deposit.
  tx_20260529_212059  net_deposit override=5313: actual external deposit (user-confirmed).
  tx_20260615_150000  net_deposit override=0: no external deposit; BEFORE_CASH
                      estimated from end of 6/6 algo run (22.84) since no log exists.
  tx_20260617_000153  net_deposit override=0: 935 formula value is a T+1 settlement
                      timing artifact; no real deposit occurred.

principal_flow tracks actual external capital invested/withdrawn:
  tx_20260508_171005  +6193: cumulative pre-algorithm deposits (2024-09 to 2026-05-07)
  tx_20260529_212059  +5313: external deposit during algo era
"""
import json
import re

# Cash (USD) observed in KIS just before each record's trades execute.
# Source: KIS log "Portfolio: Cash" field captured at Step1.
BEFORE_CASH = {
    "tx_20260508_171005": 959.40,      # log 17:09:xx
    "tx_20260512_003639": 7198.94,     # log 00:34:43
    "tx_20260512_143913": 2664.37,     # log 14:39:04
    "tx_20260513_234029": 2230.66,     # log 23:40:18
    "tx_20260516_013043": 1423.12,     # log 01:30:33
    "tx_20260519_022232": 980.69,      # log 02:22:11
    "tx_20260529_025415": 218.50,      # log 02:54:06
    "tx_20260529_212059": 5870.03,     # log 13:04:07 (last before evening trade)
    # tx_20260529_212304 is omitted: derived at runtime from 212059 cash_balance
    "tx_20260604_033902": 185.46,      # log 03:38:26
    "tx_20260605_023749": 563.85,      # log 02:36:58
    "tx_20260605_165724": 138.56,      # log 16:57:16
    "tx_20260605_165958": 556.34,      # log 16:59:50
    "tx_20260606_020632": 1330.33,     # log 02:05:55
    "tx_20260615_150000": 22.84,       # estimated: cash_balance end of 6/6 run (no 6/15 log)
    "tx_20260617_000153": 4660.36,     # log 00:01:23
    "tx_20260618_000140": 2251.34,     # log 00:01:19
}

# Records whose BEFORE_CASH equals the cash_balance of a prior record in the
# same trading session (no log available; derived at runtime).
# Maps tx_id -> tx_id of the record whose computed cash_balance is used.
BEFORE_CASH_FROM_PREV = {
    "tx_20260529_212304": "tx_20260529_212059",  # 3-min gap, same session
}

# Override net_deposit for specific records (see module docstring for reasons).
NET_DEPOSIT_OVERRIDE = {
    "tx_20260512_003639": 0.0,
    "tx_20260529_212059": 5313.0,
    "tx_20260529_212304": 0.0,   # same session as 212059, no deposit in between
    "tx_20260615_150000": 0.0,
    "tx_20260617_000153": 0.0,
}

# Actual external capital flows (deposits positive, withdrawals negative).
# These represent real money moved into/out of the account.
PRINCIPAL_FLOW = {
    "tx_20260508_171005": 6193.0,   # sum of pre-algorithm deposits 2024-09~2026-05-07
    "tx_20260529_212059": 5313.0,   # external deposit during algo era
}


COMMISSION_RATE = 0.0025  # KIS overseas brokerage: 0.25%


def calc_trade_cash_impact(executions):
    """BUY decreases cash, SELL increases cash.
    Overseas fee is stored as 0.0 in records; apply 0.25% commission explicitly.
    """
    impact = 0.0
    for e in executions:
        qty = e.get("quantity") or 0
        price = e.get("price") or 0
        action = (e.get("action") or "").upper()
        if qty > 0:
            fee = round(price * qty * COMMISSION_RATE, 2)
            if action == "BUY":
                impact -= price * qty + fee
            elif action == "SELL":
                impact += price * qty - fee
    return impact


PATH = "docs/data/overseas/history.json"

with open(PATH, "r", encoding="utf-8") as f:
    records = json.load(f)

prev_cash = None
computed_cash = {}  # tx_id -> cash_balance, for BEFORE_CASH_FROM_PREV lookups
print(f"{'ID':<30} {'before':>10} {'trade':>12} {'cash_bal':>12} {'net_dep':>10} {'principal':>10}")
print("-" * 90)

for record in records:
    tx_id = record["id"]
    before_cash = BEFORE_CASH.get(tx_id)
    if before_cash is None:
        src = BEFORE_CASH_FROM_PREV.get(tx_id)
        if src and src in computed_cash:
            before_cash = computed_cash[src]
    if before_cash is None:
        print(f"WARNING: no before_cash for {tx_id} -- skipping")
        if "cash_balance" in record:
            prev_cash = record["cash_balance"]
        continue

    trade_impact = calc_trade_cash_impact(record.get("executions", []))
    cash_balance = round(before_cash + trade_impact, 2)
    computed_cash[tx_id] = cash_balance

    if tx_id in NET_DEPOSIT_OVERRIDE:
        net_deposit = NET_DEPOSIT_OVERRIDE[tx_id]
    elif prev_cash is None:
        net_deposit = round(before_cash, 2)
    else:
        net_deposit = round(before_cash - prev_cash, 2)

    record["cash_balance"] = cash_balance
    record["net_deposit"] = net_deposit

    if tx_id in PRINCIPAL_FLOW:
        record["principal_flow"] = PRINCIPAL_FLOW[tx_id]

    principal_str = f"{PRINCIPAL_FLOW[tx_id]:>+10,.2f}" if tx_id in PRINCIPAL_FLOW else " " * 10
    print(f"{tx_id:<30} {before_cash:>10,.2f} {trade_impact:>+12,.2f} {cash_balance:>12,.2f} {net_deposit:>+10,.2f} {principal_str}")
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
