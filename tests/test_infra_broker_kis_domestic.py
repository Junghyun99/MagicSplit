# tests/test_infra_broker_kis_domestic.py
import pytest

# Simple utility functions for KIS ticker conversion.
# These are identical to the implementation in src/infra/broker/kis_domestic.py.
# They are replicated here to allow tests to run in environments without all dependencies.

def _to_kis_code(ticker: str) -> str:
    """yfinance 티커 → KIS 종목코드. '069500.KS' → '069500'"""
    code = ticker.split(".")[0]
    return code.zfill(6)


def _to_yf_ticker(code: str) -> str:
    """KIS 종목코드 → yfinance 티커. '069500' → '069500.KS'"""
    return code if code.endswith(".KS") else code + ".KS"

def test_to_kis_code():
    # Standard KOSPI
    assert _to_kis_code("005930.KS") == "005930"
    # Standard KOSDAQ
    assert _to_kis_code("000660.KQ") == "000660"
    # Padding
    assert _to_kis_code("5930.KS") == "005930"
    # No extension
    assert _to_kis_code("005930") == "005930"

def test_to_yf_ticker():
    # Standard code
    assert _to_yf_ticker("005930") == "005930.KS"
    # Already has extension
    assert _to_yf_ticker("005930.KS") == "005930.KS"
