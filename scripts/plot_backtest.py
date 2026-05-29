"""
Backtest result visualizer.

Reads log/backtest/2026-05-29_32_domestic.log and docs/data/backtest/history.json,
then produces a two-panel chart (Samsung + KODEX Inverse) saved to
docs/data/backtest/backtest_chart.png.

Price series are extracted directly from the log file (no yfinance required).
Dates are approximated by mapping 1552 trading steps evenly across 2020-01-02
to 2026-04-30; trade markers use exact dates from history.json.
"""

import re
import sys
import json
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
workspace = Path(__file__).resolve().parent.parent
HISTORY_PATH = workspace / "docs" / "data" / "backtest" / "history.json"
LOG_PATH = workspace / "logs" / "backtest" / "2026-05-29_32_domestic.log"
OUTPUT_IMAGE = workspace / "docs" / "data" / "backtest" / "backtest_chart.png"

# Use a Latin font to avoid missing-glyph boxes on Linux
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False

# ---------------------------------------------------------------------------
# 1. Parse the log to extract per-step prices and inverse block state
# ---------------------------------------------------------------------------
# Log structure: each trading day starts with ">>> Step 1: Portfolio & Price Fetch"
# Prices appear as "current KRW X,XXX" (Korean) or "@KRW X,XXX" (order) within
# the same step block.

PRICE_PATTERNS = [
    re.compile(r"이제 KRW ([\d,]+)"),          # 현재 KRW
    re.compile(r"현재 KRW ([\d,]+)"),          # 현재 KRW (alternate encoding)
    re.compile(r"@KRW ([\d,]+)"),                       # @KRW (order price)
    re.compile(r"현재KRW[\s]*([\d,]+)"),       # edge-case no space
    re.compile(r"KRW ([\d,]+)"),                        # generic KRW fallback
]

# Simpler byte-level approach: use re on raw text
_PRICE_RE = re.compile(r"(?:현재\s*KRW|현재가\s*KRW|@KRW)\s*([\d,]+)")
_STEP_MARKER = ">>> Step 1: Portfolio & Price Fetch"

step_prices_005930: dict[int, int] = {}
step_prices_114800: dict[int, int] = {}
# inv_block_state[step] -> "blocked" | "unblocked"
inv_block_state: dict[int, str] = {}

step_idx = -1

with open(LOG_PATH, "r", encoding="utf-8") as fh:
    for raw_line in fh:
        line = raw_line.rstrip()

        if _STEP_MARKER in line:
            step_idx += 1
            continue

        if step_idx < 0:
            continue

        # ------------------------------------------------------------------
        # Samsung (005930) price extraction
        # ------------------------------------------------------------------
        if "005930" in line or "삼성전자" in line:  # 삼성전자
            m = _PRICE_RE.search(line)
            if m and step_idx not in step_prices_005930:
                step_prices_005930[step_idx] = int(m.group(1).replace(",", ""))

        # ------------------------------------------------------------------
        # Inverse ETF (114800) price extraction + block state
        # ------------------------------------------------------------------
        if "114800" in line or "KODEX" in line:
            m = _PRICE_RE.search(line)
            if m and step_idx not in step_prices_114800:
                step_prices_114800[step_idx] = int(m.group(1).replace(",", ""))

            # Block / unblock events
            if ("DOWNTREND" in line and "차단" in line):  # 차단
                inv_block_state[step_idx] = "blocked"
            elif "하락 추세 해제" in line:  # 하락 추세 해제
                inv_block_state[step_idx] = "unblocked"
            elif "강한 상승 추세 진입 확정" in line:  # 강한 상승 추세 진입 확정
                inv_block_state[step_idx] = "unblocked"

total_steps = step_idx + 1
print(f"Total steps parsed: {total_steps}")
print(f"005930 prices: {len(step_prices_005930)}, 114800 prices: {len(step_prices_114800)}")
print(f"Block state events: {len(inv_block_state)}")

# ---------------------------------------------------------------------------
# 2. Build approximate date index
#    Map 1552 trading steps evenly from 2020-01-02 to 2026-04-30.
#    Both endpoints are anchored; mid-point error < 2 weeks.
# ---------------------------------------------------------------------------
approx_dates = pd.date_range("2020-01-02", "2026-04-30", periods=total_steps)

# ---------------------------------------------------------------------------
# 3. Build price Series with forward-fill for missing steps
# ---------------------------------------------------------------------------

def make_price_series(step_dict: dict, total: int, date_idx: pd.DatetimeIndex) -> pd.Series:
    raw = pd.Series(
        {date_idx[i]: v for i, v in step_dict.items() if i < len(date_idx)},
        dtype=float,
    )
    full = raw.reindex(date_idx).ffill().bfill()
    return full


close_005930 = make_price_series(step_prices_005930, total_steps, approx_dates)
close_114800 = make_price_series(step_prices_114800, total_steps, approx_dates)

# ---------------------------------------------------------------------------
# 4. Compute moving averages
# ---------------------------------------------------------------------------
ema20_005930 = close_005930.ewm(span=20, adjust=False).mean()
ma50_005930  = close_005930.rolling(50).mean()
ma200_005930 = close_005930.rolling(200).mean()

ema20_114800 = close_114800.ewm(span=20, adjust=False).mean()
ma50_114800  = close_114800.rolling(50).mean()

# ---------------------------------------------------------------------------
# 5. Build continuous block-state series for inverse (forward-filled)
# ---------------------------------------------------------------------------
block_raw = pd.Series(dtype=object, index=approx_dates)
for step, state in inv_block_state.items():
    if step < len(approx_dates):
        block_raw.iloc[step] = state

# Forward-fill; initial state assumed "blocked" (early 2020 = market uptrend)
block_series = block_raw.ffill().fillna("blocked")

# ---------------------------------------------------------------------------
# 6. Load trade history
# ---------------------------------------------------------------------------
with open(HISTORY_PATH, "r", encoding="utf-8") as fh:
    history = json.load(fh)

# Samsung
sam_buys_x,       sam_buys_y       = [], []
sam_regime_x,     sam_regime_y     = [], []
sam_sells_x,      sam_sells_y      = [], []
sam_bulk_x,       sam_bulk_y       = [], []

# Inverse
inv_buys_x,       inv_buys_y       = [], []
inv_regime_x,     inv_regime_y     = [], []
inv_bulk_x,       inv_bulk_y       = [], []
inv_normal_sell_x, inv_normal_sell_y = [], []

CHART_START = pd.Timestamp("2020-01-01")
CHART_END   = pd.Timestamp("2026-04-30")

for tx in history:
    tx_date = pd.to_datetime(tx["date"])
    if tx_date < CHART_START or tx_date > CHART_END:
        continue

    reason  = tx["reason"]
    is_bulk = any(k in reason for k in ("Bulk Sell", "일괄 청산", "전량 청산(Bulk)"))
    is_regime = any(k in reason for k in ("상승장 누적 매수", "20EMA 눈림"))

    for ex in tx["executions"]:
        ticker = ex["ticker"]
        action = ex["action"]
        price  = float(ex["price"])

        if ticker == "005930":
            if action == "BUY":
                if is_regime:
                    sam_regime_x.append(tx_date); sam_regime_y.append(price)
                else:
                    sam_buys_x.append(tx_date);   sam_buys_y.append(price)
            elif action == "SELL":
                if is_bulk:
                    sam_bulk_x.append(tx_date);   sam_bulk_y.append(price)
                else:
                    sam_sells_x.append(tx_date);  sam_sells_y.append(price)

        elif ticker == "114800":
            if action == "BUY":
                if is_regime:
                    inv_regime_x.append(tx_date); inv_regime_y.append(price)
                else:
                    inv_buys_x.append(tx_date);   inv_buys_y.append(price)
            elif action == "SELL":
                if is_bulk:
                    inv_bulk_x.append(tx_date);   inv_bulk_y.append(price)
                else:
                    inv_normal_sell_x.append(tx_date); inv_normal_sell_y.append(price)

print(f"Samsung - buys:{len(sam_buys_x)}, regime:{len(sam_regime_x)}, sells:{len(sam_sells_x)}, bulk:{len(sam_bulk_x)}")
print(f"Inverse - buys:{len(inv_buys_x)}, regime:{len(inv_regime_x)}, bulk_sell:{len(inv_bulk_x)}")

# ---------------------------------------------------------------------------
# 7. Plotting: two-panel chart
# ---------------------------------------------------------------------------

def shade_regions(ax, mask_series: pd.Series, color: str, alpha: float = 0.4):
    """Shade background where mask_series is True."""
    in_region = False
    region_start = None
    for dt, val in mask_series.items():
        if val and not in_region:
            region_start = dt
            in_region = True
        elif not val and in_region:
            ax.axvspan(region_start, dt, color=color, alpha=alpha, zorder=0)
            in_region = False
    if in_region and region_start is not None:
        ax.axvspan(region_start, mask_series.index[-1], color=color, alpha=alpha, zorder=0)


fig, (ax1, ax2) = plt.subplots(
    2, 1, figsize=(22, 16), dpi=130,
    gridspec_kw={"height_ratios": [1.1, 1]},
)
fig.suptitle(
    "Samsung(005930) vs KODEX Inverse(114800) Hedge Backtest  2020-2026",
    fontsize=16, fontweight="bold", y=0.99,
)

# ---- Panel 1: Samsung Electronics ----------------------------------------

# Regime background (EMA20 > MA50 > MA200 = uptrend = green)
regime_005930 = (ema20_005930 > ma50_005930) & (ma50_005930 > ma200_005930)
shade_regions(ax1, regime_005930, "#c8e6c9", alpha=0.45)

ax1.plot(close_005930.index,  close_005930.values,  label="Samsung Close",   color="#1565c0", linewidth=2.2, zorder=2)
ax1.plot(ema20_005930.index,  ema20_005930.values,  label="EMA20",           color="#f57c00", linestyle="--", linewidth=1.2, alpha=0.85)
ax1.plot(ma50_005930.index,   ma50_005930.values,   label="MA50",            color="#c62828", linestyle="-.", linewidth=1.2, alpha=0.85)
ax1.plot(ma200_005930.index,  ma200_005930.values,  label="MA200",           color="#757575", linestyle=":",  linewidth=1.2, alpha=0.65)

if sam_buys_x:
    ax1.scatter(sam_buys_x,   sam_buys_y,   color="#43a047", edgecolor="darkgreen", marker="^", s=90,  zorder=4, label="Buy (initial/split)")
if sam_regime_x:
    ax1.scatter(sam_regime_x, sam_regime_y, color="#8e24aa", edgecolor="indigo",    marker="D", s=80,  zorder=4, label="Buy (regime add)")
if sam_sells_x:
    ax1.scatter(sam_sells_x,  sam_sells_y,  color="#e53935",                        marker="v", s=70,  zorder=4, label="Partial sell", alpha=0.75)
if sam_bulk_x:
    ax1.scatter(sam_bulk_x,   sam_bulk_y,   color="gold",    edgecolor="darkorange",marker="*", s=260, zorder=5, label="Bulk sell (trend break)")

up_patch = mpatches.Patch(color="#c8e6c9", label="Uptrend zone (EMA20>MA50>MA200)")
h1, l1 = ax1.get_legend_handles_labels()
ax1.legend([*h1, up_patch], [*l1, "Uptrend zone"], loc="upper left", fontsize=8.5, ncol=2)

ax1.set_title("Samsung Electronics (005930)", fontsize=13, fontweight="bold")
ax1.set_ylabel("Price (KRW)", fontsize=10)
ax1.grid(True, linestyle="--", alpha=0.3)
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
ax1.xaxis.set_major_locator(mdates.YearLocator())
plt.setp(ax1.xaxis.get_majorticklabels(), rotation=20)

# ---- Panel 2: KODEX Inverse ETF ------------------------------------------

# Background strategy:
#   Green  = Samsung uptrend (EMA20>MA50>MA200) -> inverse expected to be blocked
#   Orange = Samsung downtrend                 -> hedge opportunity, inverse should be active
#
# This lets you visually check: do inverse buy markers fall in ORANGE zones?
# Explicit DOWNTREND-block events for the inverse are shown as thin red bars.

shade_regions(ax2, regime_005930,   "#c8e6c9", alpha=0.35)  # green = Samsung uptrend
shade_regions(ax2, ~regime_005930,  "#ffe0b2", alpha=0.35)  # orange = Samsung downtrend

# Explicit inverse-ETF downtrend blocks: thin red line at the top of the panel
blocked_mask = (block_series == "blocked")
ymin2, ymax2 = close_114800.min() * 0.98, close_114800.max() * 1.02
bar_h = (ymax2 - ymin2) * 0.025
in_block = False
b_start = None
for dt, val in blocked_mask.items():
    if val and not in_block:
        b_start = dt
        in_block = True
    elif not val and in_block:
        ax2.axvspan(b_start, dt, ymin=0.97, ymax=1.0, color="#c62828", alpha=0.85, zorder=3, clip_on=False)
        in_block = False
if in_block and b_start is not None:
    ax2.axvspan(b_start, blocked_mask.index[-1], ymin=0.97, ymax=1.0, color="#c62828", alpha=0.85, zorder=3, clip_on=False)

ax2.plot(close_114800.index,  close_114800.values,  label="KODEX Inverse Close", color="#b71c1c", linewidth=2.2, zorder=2)
ax2.plot(ema20_114800.index,  ema20_114800.values,  label="EMA20",               color="#f57c00", linestyle="--", linewidth=1.2, alpha=0.85)
ax2.plot(ma50_114800.index,   ma50_114800.values,   label="MA50",                color="#4e342e", linestyle="-.", linewidth=1.2, alpha=0.80)

if inv_buys_x:
    ax2.scatter(inv_buys_x,   inv_buys_y,   color="#43a047", edgecolor="darkgreen", marker="^", s=120, zorder=5, label="Inverse BUY (initial Lv1)")
if inv_regime_x:
    ax2.scatter(inv_regime_x, inv_regime_y, color="#8e24aa", edgecolor="indigo",    marker="D", s=100, zorder=5, label="Inverse BUY (regime add)")
if inv_bulk_x:
    ax2.scatter(inv_bulk_x,   inv_bulk_y,   color="gold",    edgecolor="darkorange",marker="*", s=280, zorder=6, label="Inverse SELL (bulk)")
if inv_normal_sell_x:
    ax2.scatter(inv_normal_sell_x, inv_normal_sell_y, color="red",               marker="v", s=80,  zorder=5, label="Inverse SELL (partial)", alpha=0.7)

green_patch  = mpatches.Patch(color="#c8e6c9", label="Samsung uptrend zone  (inverse expected blocked)")
orange_patch = mpatches.Patch(color="#ffe0b2", label="Samsung downtrend zone (hedge opportunity)")
red_patch    = mpatches.Patch(color="#c62828", label="Inverse DOWNTREND confirmed (top bar = entry blocked)")
h2, l2 = ax2.get_legend_handles_labels()
ax2.legend([*h2, green_patch, orange_patch, red_patch],
           [*l2, "Samsung uptrend", "Samsung downtrend", "Inv DOWNTREND block"],
           loc="upper right", fontsize=8.5, ncol=2)

ax2.set_title("KODEX Inverse ETF (114800) - Downtrend Hedge", fontsize=13, fontweight="bold")
ax2.set_ylabel("Price (KRW)", fontsize=10)
ax2.set_xlabel("Date (approximate)", fontsize=10)
ax2.grid(True, linestyle="--", alpha=0.3)
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
ax2.xaxis.set_major_locator(mdates.YearLocator())
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=20)

# shared x-range
for ax in (ax1, ax2):
    ax.set_xlim(pd.Timestamp("2020-01-01"), pd.Timestamp("2026-05-01"))

fig.align_ylabels([ax1, ax2])
plt.tight_layout(rect=[0, 0, 1, 0.99])

OUTPUT_IMAGE.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(OUTPUT_IMAGE, bbox_inches="tight")
print(f"Chart saved: {OUTPUT_IMAGE}")
