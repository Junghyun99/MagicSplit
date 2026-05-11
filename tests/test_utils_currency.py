# tests/test_utils_currency.py
import pytest

from src.utils.currency import currency_code_for, format_money


class TestCurrencyCode:
    def test_domestic_returns_krw(self):
        assert currency_code_for("domestic") == "KRW"

    def test_overseas_returns_usd(self):
        assert currency_code_for("overseas") == "USD"

    def test_unknown_defaults_to_usd(self):
        assert currency_code_for("anything-else") == "USD"


class TestFormatMoney:
    def test_domestic_no_decimals_with_thousand_separator(self):
        assert format_money(1234567, "domestic") == "KRW 1,234,567"

    def test_domestic_rounds_to_zero_decimals(self):
        assert format_money(100700.6, "domestic") == "KRW 100,701"

    def test_overseas_two_decimals(self):
        assert format_money(1234.5, "overseas") == "USD 1,234.50"

    def test_overseas_thousand_separator(self):
        assert format_money(1234567.89, "overseas") == "USD 1,234,567.89"

    def test_zero(self):
        assert format_money(0, "domestic") == "KRW 0"
        assert format_money(0, "overseas") == "USD 0.00"

    def test_negative(self):
        assert format_money(-500, "domestic") == "KRW -500"
        assert format_money(-1.25, "overseas") == "USD -1.25"

    def test_none_returns_dash(self):
        assert format_money(None, "domestic") == "-"
        assert format_money(None, "overseas") == "-"

    def test_unknown_market_type_treated_as_overseas(self):
        assert format_money(10, "futures") == "USD 10.00"
