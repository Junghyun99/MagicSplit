"""업비트 KRW 마켓 목록을 내려받아 docs/data/upbit_markets.json 으로 저장한다.

config-editor의 코인 티커 검색(자동완성) 데이터 소스. 주식의
docs/data/tickers.json 과 동일한 [코드, 이름, 거래소] 3-튜플 형식으로 저장한다.
  예: ["KRW-BTC", "비트코인", "KRW"]

업비트 마켓 조회(/v1/market/all)는 공개 API(무인증)이므로 GitHub 호스티드
러너(ubuntu)에서 서버사이드로 호출한다 -> 브라우저 CORS 문제 없음.

사용:
    python scripts/download_upbit_markets.py
"""
import json
import os
import sys

import requests

UPBIT_MARKET_ALL_URL = "https://api.upbit.com/v1/market/all?isDetails=false"
OUTPUT_PATH = os.path.join("docs", "data", "upbit_markets.json")
HTTP_TIMEOUT = 15


def fetch_krw_markets():
    """업비트 KRW 마켓을 [코드, 한글명, 'KRW'] 리스트로 반환 (코드순 정렬)."""
    res = requests.get(UPBIT_MARKET_ALL_URL, timeout=HTTP_TIMEOUT)
    res.raise_for_status()
    data = res.json()
    if not isinstance(data, list):
        raise RuntimeError(f"예상치 못한 응답 형식: {data}")

    rows = []
    for m in data:
        if not isinstance(m, dict):
            continue
        market = m.get("market", "")
        if not market.startswith("KRW-"):
            continue
        korean = m.get("korean_name") or market
        rows.append([market, korean, "KRW"])

    rows.sort(key=lambda r: r[0])
    return rows


def main():
    rows = fetch_krw_markets()
    if not rows:
        print("에러: KRW 마켓을 하나도 받지 못했습니다.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=0)
        f.write("\n")

    print(f"저장 완료: {OUTPUT_PATH} (KRW 마켓 {len(rows)}개)")


if __name__ == "__main__":
    main()
