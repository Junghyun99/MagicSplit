# src/utils/currency.py
"""시장(market_type)에 따라 금액 문자열을 통화 단위(KRW/USD)에 맞춰 포매팅한다.

로그/알림용 ASCII 표기를 사용한다 (CLAUDE.md 인코딩 규칙).
- domestic -> "KRW 1,234,567"
- overseas -> "USD 1,234.56"
"""
from typing import Optional


def currency_code_for(market_type: str) -> str:
    """market_type -> 통화 코드 (KRW/USD)."""
    return "KRW" if market_type == "domestic" else "USD"


def format_money(value: Optional[float], market_type: str) -> str:
    """금액을 통화 단위와 함께 ASCII 문자열로 포매팅한다.

    domestic은 소수점 없이, overseas는 소수점 2자리로 출력한다.
    None은 "-"로 표시한다.
    """
    if value is None:
        return "-"
    code = currency_code_for(market_type)
    if market_type == "domestic":
        return f"{code} {value:,.0f}"
    return f"{code} {value:,.2f}"
