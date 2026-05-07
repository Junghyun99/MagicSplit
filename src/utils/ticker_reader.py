import os
import sqlite3
from typing import Optional, Dict
from .ticker_reader_base import get_ticker_info, get_alias, get_exchange, DEFAULT_DB_PATH

def to_yfinance_ticker(ticker: str, db_path: str = DEFAULT_DB_PATH) -> str:
    """MagicSplit 표준 티커 -> yfinance 티커.

    국내(KS/KQ)는 거래소 접미사를 부여하고, 해외는 그대로 반환한다.
    DB에 등록되지 않은 티커는 ValueError를 발생시킨다.
    """
    info = get_ticker_info(ticker, db_path)
    if info is None:
        raise ValueError(f"Unknown ticker: {ticker!r}")
    ex = info["exchange"]
    if ex in ("KS", "KQ"):
        return f"{ticker}.{ex}"
    return ticker


def display_ticker(ticker: str, db_path: str = DEFAULT_DB_PATH) -> str:
    """로그/알림용 표시 문자열.

    alias가 ticker와 다르면 'alias(ticker)' 형식 (국내 종목 한글명),
    같거나 미등록이면 ticker 그대로 반환한다.
    """
    alias = get_alias(ticker, db_path)
    if alias and alias != ticker:
        return f"{alias}({ticker})"
    return ticker


def search_by_alias(query: str, db_path: str = DEFAULT_DB_PATH) -> list[Dict]:
    """별칭(종목명)의 일부를 사용하여 티커 목록을 검색합니다."""
    if not os.path.exists(db_path):
        return []

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # LIKE를 사용하여 부분 일치 검색
            sql = "SELECT ticker, exchange, alias, asset_type, currency FROM tickers WHERE alias LIKE ?"
            cur.execute(sql, (f"%{query}%",))
            return [dict(row) for row in cur.fetchall()]

    except sqlite3.Error:
        return []
