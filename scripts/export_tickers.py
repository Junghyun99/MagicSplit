import json
import sqlite3
import os
import sys

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.utils.ticker_reader import DEFAULT_DB_PATH

def export():
    if not os.path.exists(DEFAULT_DB_PATH):
        print(f"Error: {DEFAULT_DB_PATH} not found.")
        return

    output_path = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "tickers.json")
    
    try:
        with sqlite3.connect(DEFAULT_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            # Export all tickers in a compact array format: [ticker, alias, exchange]
            cur.execute("SELECT ticker, alias, exchange FROM tickers")
            rows = cur.fetchall()
            
            data = [[r['ticker'], r['alias'], r['exchange']] for r in rows]
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                # Remove indent and spaces to minimize file size
                json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
                
            print(f"Success: Exported {len(data)} tickers to {output_path}")
            
    except sqlite3.Error as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    export()
