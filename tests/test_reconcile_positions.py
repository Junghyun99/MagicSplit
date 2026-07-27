# tests/test_reconcile_positions.py
"""수량 보정 CLI(scripts/reconcile_positions.py)의 순수 헬퍼 테스트.

브로커/입력이 필요한 대화형 경로는 제외하고, 수량 계산·표기 로직만 검증한다.
"""
import pytest

from scripts.reconcile_positions import (
    _apply_split_ratio, _fmt_diff, _fmt_qty, _round_qty, _shrink_to,
)
from src.core.logic.position_reconciler import QuantityMismatch
from src.core.models import PositionLot


def _lot(ticker, qty, level, buy_price=100.0, lot_id=None, high=None):
    return PositionLot(
        lot_id=lot_id or f"lot_{ticker}_{level:03d}",
        ticker=ticker, buy_price=buy_price, quantity=qty,
        buy_date="2026-07-17", level=level, trailing_highest_price=high,
    )


class TestQtyFormatting:
    def test_crypto_shows_full_precision_without_exponent(self):
        assert _fmt_qty(0.06851895, "crypto") == "0.06851895"
        assert _fmt_qty(0.00000001, "crypto") == "0.00000001"

    def test_crypto_trims_trailing_zeros(self):
        assert _fmt_qty(1.5, "crypto") == "1.5"
        assert _fmt_qty(0.0, "crypto") == "0"

    def test_stock_shows_integer(self):
        assert _fmt_qty(12.0, "domestic") == "12"

    def test_diff_formats_float_without_crashing(self):
        """수량은 float 이므로 ':+d' 포맷은 ValueError 를 낸다 - 회귀 방지."""
        m = QuantityMismatch("KRW-BTC", 0.06851895, 0.13703789, 1, [1])
        assert _fmt_diff(m.diff, "crypto") == "-0.06851894"

    def test_diff_keeps_plus_sign(self):
        assert _fmt_diff(3.0, "domestic") == "+3"


class TestRoundQty:
    def test_crypto_rounds_to_8_decimals(self):
        assert _round_qty(0.068518949999999, "crypto") == 0.06851895

    def test_stock_rounds_to_integer(self):
        assert _round_qty(4.9999999, "domestic") == 5.0


class TestShrinkTo:
    def test_shrinks_from_highest_level(self):
        lots = [_lot("A", 5, 1, lot_id="a1"), _lot("A", 5, 2, lot_id="a2")]

        out = _shrink_to(lots, "A", 5, "domestic")

        assert [l.lot_id for l in out] == ["a1"]

    def test_partial_shrink_keeps_lot_metadata(self):
        lots = [_lot("A", 5, 1, lot_id="a1", high=130.0)]

        out = _shrink_to(lots, "A", 3, "domestic")

        assert len(out) == 1
        assert out[0].quantity == 3
        assert out[0].buy_price == 100.0
        assert out[0].trailing_highest_price == 130.0

    def test_crypto_partial_shrink_has_no_float_residue(self):
        lots = [_lot("KRW-ETH", 1.54440048, 1)]

        out = _shrink_to(lots, "KRW-ETH", 0.77220024, "crypto")

        assert out[0].quantity == 0.77220024

    def test_crypto_target_matching_sum_within_tolerance_is_noop(self):
        """부동소수 합산 오차(1e-15)로 target 이 미세하게 커도 예외가 아니다."""
        lots = [_lot("KRW-USDT", 6.77506775, 1, lot_id="u1"),
                _lot("KRW-USDT", 6.84462696, 2, lot_id="u2")]

        out = _shrink_to(lots, "KRW-USDT", 13.61969471, "crypto")

        assert sorted(l.lot_id for l in out) == ["u1", "u2"]

    def test_target_above_holdings_raises(self):
        lots = [_lot("A", 5, 1)]
        with pytest.raises(ValueError, match="pad"):
            _shrink_to(lots, "A", 9, "domestic")

    def test_other_tickers_are_preserved(self):
        lots = [_lot("A", 5, 1, lot_id="a1"), _lot("B", 7, 1, lot_id="b1")]

        out = _shrink_to(lots, "A", 0, "domestic")

        assert [l.lot_id for l in out] == ["b1"]


class TestApplySplitRatio:
    def test_stock_split_floors_quantity(self):
        lots = [_lot("A", 5, 1, buy_price=100.0)]

        out = _apply_split_ratio(lots, "A", 1, 2, None, "domestic")

        assert out[0].quantity == 10
        assert out[0].buy_price == 50.0

    def test_crypto_keeps_fractional_quantity(self):
        """코인에 내림(//)을 쓰면 소수 수량이 0이 되어 lot 이 사라진다 - 회귀 방지."""
        lots = [_lot("KRW-BTC", 0.13703789, 1, buy_price=114690236.0)]

        out = _apply_split_ratio(lots, "KRW-BTC", 1, 2, None, "crypto")

        assert len(out) == 1
        assert out[0].quantity == 0.27407578

    def test_last_sell_price_is_scaled(self):
        lots = [_lot("A", 5, 1)]
        last_sell = {"A": 120.0}

        _apply_split_ratio(lots, "A", 1, 2, last_sell, "domestic")

        assert last_sell["A"] == 60.0

    def test_trailing_high_is_scaled(self):
        lots = [_lot("A", 5, 1, high=130.0)]

        out = _apply_split_ratio(lots, "A", 1, 2, None, "domestic")

        assert out[0].trailing_highest_price == 65.0

    def test_non_positive_ratio_raises(self):
        with pytest.raises(ValueError):
            _apply_split_ratio([_lot("A", 5, 1)], "A", 0, 2, None, "domestic")
