# src/utils/currency.py
"""시장(market_type)에 따라 금액 문자열을 통화 단위(KRW/USD)에 맞춰 포매팅한다.

로그/알림용 ASCII 표기를 사용한다 (CLAUDE.md 인코딩 규칙).
- domestic -> "KRW 1,234,567"
- overseas -> "USD 1,234.56"
"""
from typing import Optional


def currency_code_for(market_type: str) -> str:
    """market_type -> 통화 코드 (KRW/USD). 코인(crypto)은 업비트 원화마켓 -> KRW."""
    return "KRW" if market_type in ("domestic", "crypto") else "USD"


def format_money(value: Optional[float], market_type: str,
                 currency: Optional[str] = None) -> str:
    """금액을 통화 단위와 함께 ASCII 문자열로 포매팅한다.

    domestic은 소수점 없이, overseas는 소수점 2자리로 출력한다.
    None은 "-"로 표시한다.

    currency를 지정하면 market_type이 아니라 그 통화 코드로 표기한다
    (예: 해외 자산을 KRW로 환산해 표시). KRW는 소수점 없이, 그 외는 2자리.
    """
    if value is None:
        return "-"
    code = currency or currency_code_for(market_type)
    if code == "KRW":
        return f"{code} {value:,.0f}"
    return f"{code} {value:,.2f}"


def format_qty(value: float, market_type: str) -> str:
    """수량을 마켓에 맞는 단위와 함께 표기한다.

    주식(domestic/overseas)은 정수 '주', 코인(crypto)은 소수 '개'.
    코인 수량은 지수표기 없이 불필요한 0을 정리해 출력한다 (예: 0.00010696개).
    """
    if market_type == "crypto":
        s = f"{value:.8f}".rstrip("0").rstrip(".")
        return f"{s or '0'}개"
    return f"{int(value)}주"
