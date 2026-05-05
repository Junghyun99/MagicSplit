import os
import sqlite3
from typing import Optional, Dict

# 스크립트 파일과 동일한 위치의 tickers.db를 기본 경로로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "tickers.db")

def get_ticker_info(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict]:
    """
    티커를 사용하여 해당 종목의 전체 정보를 조회합니다.
    기본적으로 스크립트와 같은 폴더에 있는 tickers.db를 참조합니다.
    
    Args:
        ticker: 조회할 티커 코드 (예: '005930', 'AAPL')
        db_path: tickers.db 파일 경로 (기본값: 스크립트와 동일 경로)
        
    Returns:
        성공 시 정보가 담긴 Dict (ticker, exchange, alias, asset_type, currency),
        실패하거나 존재하지 않으면 None
    """
    if not os.path.exists(db_path):
        return None

    try:
        # uri=True와 mode='ro'를 사용하여 읽기 전용으로 연결 시도 (파일이 없으면 에러 발생)
        # 하지만 sqlite3 버전에 따라 다를 수 있으므로 일반 연결 후 조회 실패 시 처리
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            query = "SELECT ticker, exchange, alias, asset_type, currency FROM tickers WHERE ticker = ?"
            cur.execute(query, (ticker,))
            row = cur.fetchone()
            
            if row:
                return dict(row)
            return None
            
    except sqlite3.Error:
        return None

def get_alias(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[str]:
    """티커를 사용하여 해당 종목의 별칭(종목명)을 조회합니다."""
    info = get_ticker_info(ticker, db_path)
    return info["alias"] if info else None

def get_exchange(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[str]:
    """티커를 사용하여 해당 종목의 거래소 정보를 조회합니다."""
    info = get_ticker_info(ticker, db_path)
    return info["exchange"] if info else None


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
