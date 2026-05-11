import sys
import os
import json
from typing import Optional

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.utils.ticker_reader import search_by_alias

def add_to_config(config_path: str, ticker: str, alias: str, market_type: str):
    if not os.path.exists(config_path):
        # Create it if it doesn't exist? Better not to, just error.
        print(f"Error: {config_path} not found.")
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error reading {config_path}: {e}")
        return

    # Check if already exists
    if any(s["ticker"] == ticker for s in config.get("stocks", [])):
        print(f"Info: Ticker {ticker} ({alias}) already exists in {config_path}.")
        return

    # Add new stock entry with default values
    new_stock = {
        "ticker": ticker,
        "market_type": market_type,
        "buy_threshold_pct": -5.0,
        "sell_threshold_pct": 10.0,
        "buy_amount": 500000 if market_type == "domestic" else 500,
        "max_lots": 20,
        "enabled": True,
        "trailing_drop_pct": 2.0
    }

    if "stocks" not in config:
        config["stocks"] = []
    
    config["stocks"].append(new_stock)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    
    print(f"Success: Added {alias} ({ticker}) to {config_path}.")

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/add_stock.py <stock_name>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    results = search_by_alias(query)

    if not results:
        print(f"'{query}'에 대한 검색 결과가 없습니다.")
        return

    selected = None
    if len(results) > 1:
        print(f"여러 개의 검색 결과가 있습니다. 번호를 선택하세요:")
        for i, r in enumerate(results):
            print(f"[{i+1}] {r['ticker']} | {r['alias']} ({r['exchange']})")
        
        try:
            choice_str = input("선택 (취소: Enter): ").strip()
            if not choice_str: return
            choice = int(choice_str)
            if choice == 0: return
            selected = results[choice-1]
        except (ValueError, IndexError):
            print("잘못된 선택입니다.")
            return
    else:
        selected = results[0]
        print(f"선택된 종목: {selected['alias']} ({selected['ticker']})")

    if not selected: return

    ticker = selected['ticker']
    alias = selected['alias']
    market_type = "domestic" if selected['exchange'] in ("KS", "KQ") else "overseas"
    config_file = "config_domestic.json" if market_type == "domestic" else "config_overseas.json"
    
    add_to_config(config_file, ticker, alias, market_type)

if __name__ == "__main__":
    main()
