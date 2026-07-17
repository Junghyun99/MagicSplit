# tests/test_utils_currency.py
import pytest

from src.utils.currency import currency_code_for, format_money, format_qty


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

    def test_currency_override_krw_for_overseas(self):
        """overseas 값을 KRW로 환산 표기 (소수점 없음)"""
        assert format_money(1_540_000.0, "overseas", currency="KRW") == "KRW 1,540,000"

    def test_currency_override_preserves_none(self):
        assert format_money(None, "overseas", currency="KRW") == "-"


class TestFormatQty:
    def test_stock_uses_ju_unit_integer(self):
        assert format_qty(5, "overseas") == "5주"
        assert format_qty(5.0, "domestic") == "5주"
        assert format_qty(5.9, "domestic") == "5주"   # 정수화(주 단위)

    def test_crypto_uses_gae_unit_fractional(self):
        assert format_qty(0.00010696, "crypto") == "0.00010696개"
        assert format_qty(5.0, "crypto") == "5개"       # 뒤 0 정리
        assert format_qty(0.5, "crypto") == "0.5개"

    def test_crypto_no_scientific_notation(self):
        # 아주 작은 수량도 지수표기 없이
        assert "e" not in format_qty(0.00000001, "crypto")
        assert format_qty(0.00000001, "crypto") == "0.00000001개"

    def test_crypto_zero(self):
        assert format_qty(0, "crypto") == "0개"
