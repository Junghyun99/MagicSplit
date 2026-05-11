import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.utils.ticker_reader import search_by_alias

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/search_ticker.py <query>")
        sys.exit(1)

    query = sys.argv[1]
    results = search_by_alias(query)

    if not results:
        print(f"'{query}'에 대한 검색 결과가 없습니다.")
        return

    print(f"'{query}' 검색 결과 ({len(results)}건):")
    print("-" * 60)
    print(f"{'Ticker':<10} | {'Alias':<20} | {'Exchange':<10} | {'Type':<10}")
    print("-" * 60)
    for r in results:
        print(f"{r['ticker']:<10} | {r['alias']:<20} | {r['exchange']:<10} | {r['asset_type']:<10}")

if __name__ == "__main__":
    main()
