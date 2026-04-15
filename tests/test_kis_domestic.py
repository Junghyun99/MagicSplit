import pytest
from src.infra.broker.kis_domestic import _to_yf_ticker

def test_to_yf_ticker_no_dot():
    """Test standard KIS code gets .KS appended."""
    assert _to_yf_ticker("069500") == "069500.KS"

def test_to_yf_ticker_with_ks():
    """Test code with .KS already present remains unchanged."""
    assert _to_yf_ticker("069500.KS") == "069500.KS"

def test_to_yf_ticker_with_kq():
    """Test code with .KQ or other dot remains unchanged."""
    assert _to_yf_ticker("069500.KQ") == "069500.KQ"
    assert _to_yf_ticker("069500.ABC") == "069500.ABC"

def test_to_yf_ticker_empty():
    """Test empty string gets .KS appended."""
    assert _to_yf_ticker("") == ".KS"
