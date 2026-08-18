"""Validate raw OHLC coverage before running a common-period backtest.

Example (the default 312 bars covers a 252-bar long channel plus 60 bars):

    python scripts/validate_backtest_ohlc.py \
        --config config_test_domestic.json --market-type domestic \
        --start 2016-01-01 --end 2026-08-14 \
        --csv docs/data/backtest/ohlc_validation.csv

The command exits with status 1 if any enabled ticker fails.  It never writes
to the backtest OHLC cache and never forward-fills downloaded values.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import certifi
import pandas as pd

WORKSPACE = Path(__file__).resolve().parent.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))


def _configure_ca_bundle() -> Path:
    """Give curl_cffi an ASCII-only CA path on Windows.

    curl may fail to open certifi's bundle when Python is installed below a
    non-ASCII user profile directory.  Keep TLS verification enabled and copy
    the public certifi bundle to the repository's ignored ``.cache`` instead.
    A caller-supplied CURL_CA_BUNDLE is respected.
    """
    configured = os.environ.get("CURL_CA_BUNDLE")
    if configured:
        return Path(configured)

    source = Path(certifi.where())
    target = WORKSPACE / ".cache" / "certifi" / "cacert.pem"
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or source.stat().st_mtime_ns != target.stat().st_mtime_ns:
            shutil.copy2(source, target)
        # curl_cffi reads CURL_CA_BUNDLE; SSL_CERT_FILE also covers dependent
        # HTTPS clients used during the same command.
        os.environ["CURL_CA_BUNDLE"] = str(target)
        os.environ["SSL_CERT_FILE"] = str(target)
    return target


CA_BUNDLE = _configure_ca_bundle()

import yfinance as yf

# yfinance stores its cookie/timezone SQLite databases under the user profile by
# default.  Keep the validation command self-contained when that location is
# not writable (for example, CI or a locked-down Windows profile).
yf.set_tz_cache_location(str(WORKSPACE / ".cache" / "yfinance"))

from src.backtest.ohlc_validation import OHLC_FIELDS, validate_raw_ohlc
from src.strategy_config import StrategyConfig
from src.utils.ticker_reader import to_yfinance_ticker


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict raw OHLC coverage validation")
    parser.add_argument("--config", required=True, help="MagicSplit JSON configuration")
    parser.add_argument("--market-type", required=True, choices=("domestic", "overseas"))
    parser.add_argument("--start", required=True, help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--warmup-bars", type=int, default=312,
                        help="Required completed bars before --start (default: 252 + 60)")
    parser.add_argument("--date-tolerance-days", type=int, default=7)
    parser.add_argument("--max-missing-sessions", type=int, default=0)
    parser.add_argument("--max-consecutive-missing", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--csv", type=Path, default=Path("docs/data/backtest/ohlc_validation.csv"))
    parser.add_argument("--json", type=Path, default=Path("docs/data/backtest/ohlc_validation.json"))
    return parser.parse_args()


def _required_start(start: str, warmup_bars: int) -> pd.Timestamp:
    # Match the backtest runner's conservative calendar-day conversion.
    return pd.Timestamp(start) - pd.Timedelta(days=int(warmup_bars * 1.6))


def _normalise_download(frame: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    columns = pd.MultiIndex.from_product([OHLC_FIELDS, symbols])
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    if isinstance(frame.columns, pd.MultiIndex):
        available = frame.columns
        result = pd.DataFrame(index=frame.index, columns=columns, dtype=float)
        for field in OHLC_FIELDS:
            for symbol in symbols:
                key = (field, symbol)
                if key in available:
                    result[key] = frame[key]
        return result
    # yfinance may use a single-level response for a single requested symbol.
    result = pd.DataFrame(index=frame.index, columns=columns, dtype=float)
    for field in OHLC_FIELDS:
        if field in frame.columns:
            result[(field, symbols[0])] = frame[field]
    return result


def download_raw_ohlc(symbols: list[str], start: str, end: str, batch_size: int) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset:offset + batch_size]
        print(f"Downloading {offset + 1}-{offset + len(batch)} / {len(symbols)}: {', '.join(batch)}")
        # A single worker avoids yfinance cache contention while downloading a
        # comparatively small validation universe.
        raw = yf.download(
            batch, start=start, end=end, auto_adjust=False, progress=False, threads=False,
        )
        frames.append(_normalise_download(raw, batch))
    return pd.concat(frames, axis=1).sort_index() if frames else pd.DataFrame()


def main() -> int:
    args = _parse_args()
    if args.warmup_bars < 0 or args.batch_size < 1:
        raise SystemExit("--warmup-bars must be >= 0 and --batch-size must be >= 1")

    config = StrategyConfig(config_path=args.config)
    rules = [rule for rule in config.get_rules_by_market(args.market_type) if rule.enabled]
    tickers = [rule.ticker for rule in rules]
    if not tickers:
        raise SystemExit("No enabled tickers found for the selected market type.")

    try:
        symbols = [to_yfinance_ticker(ticker) for ticker in tickers]
    except ValueError as exc:
        raise SystemExit(f"Ticker registry error: {exc}") from exc

    required_start = _required_start(args.start, args.warmup_bars)
    # yfinance end is exclusive; one extra day includes the requested final day.
    raw = download_raw_ohlc(symbols, required_start.strftime("%Y-%m-%d"),
                            (pd.Timestamp(args.end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                            args.batch_size)
    raw = raw.rename(columns=dict(zip(symbols, tickers)), level=1)
    results = validate_raw_ohlc(
        raw, tickers,
        required_start=required_start,
        required_end=args.end,
        date_tolerance_days=args.date_tolerance_days,
        max_missing_sessions=args.max_missing_sessions,
        max_consecutive_missing=args.max_consecutive_missing,
    )

    report = pd.DataFrame(results)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(args.csv, index=False, encoding="utf-8-sig")
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps({
        "config": args.config, "market_type": args.market_type,
        "backtest_start": args.start, "backtest_end": args.end,
        "required_start": required_start.date().isoformat(),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = report[report["status"] == "FAIL"]
    print(report.to_string(index=False))
    print(f"\nReport: {args.csv} / {args.json}")
    if not failed.empty:
        print(f"FAIL: {len(failed)} / {len(report)} tickers")
        return 1
    print(f"PASS: all {len(report)} tickers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
