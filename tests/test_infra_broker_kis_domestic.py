# tests/test_infra_broker_kis_domestic.py
import pytest
from src.infra.broker.kis_domestic import _to_kis_code, _to_yf_ticker, KisDomesticBrokerBase

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

def test_broker_static_methods():
    # Test that KisDomesticBrokerBase still has these as static methods for backward compatibility
    assert KisDomesticBrokerBase._to_kis_code("005930.KS") == "005930"
    assert KisDomesticBrokerBase._to_yf_ticker("005930") == "005930.KS"
