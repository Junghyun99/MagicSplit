import pytest
from src.infra.broker.kis_domestic import _to_yf_ticker

@pytest.mark.parametrize("code, expected", [
    ("069500", "069500.KS"),
    ("069500.KS", "069500.KS"),
    ("069500.KQ", "069500.KQ"),
    ("069500.ABC", "069500.ABC"),
    ("", ".KS"),
])
def test_to_yf_ticker(code, expected):
    """Test KIS code to yfinance ticker conversion."""
    assert _to_yf_ticker(code) == expected
