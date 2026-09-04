# tests/test_core_models.py
import pytest
from src.core.models import (
    StockRule, PositionLot, Portfolio, Order, OrderAction,
    TradeExecution, ExecutionStatus, SplitSignal, TradeSignal, DayResult,
)


class TestStockRule:
    def test_post_liquidation_early_probe_defaults_and_validation(self):
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        assert rule.post_liquidation_early_probe_enabled is False
        assert rule.post_liquidation_early_probe_pct == 20
        assert rule.post_liquidation_early_probe_confirm_bars == 2
        assert rule.post_liquidation_early_probe_max_ema_atr == 1
        assert rule.post_liquidation_early_probe_stop_atr_multiplier == 2

        with pytest.raises(ValueError, match="단계형 반등"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                post_liquidation_early_probe_enabled=True,
            )

    def test_creation(self):
        rule = StockRule(
            ticker="AAPL",
            buy_threshold_pct=-5.0,
            sell_threshold_pct=10.0,
            buy_amount=500,
            max_lots=10,
        )
        assert rule.ticker == "AAPL"
        assert rule.buy_threshold_pct == -5.0
        assert rule.sell_threshold_pct == 10.0
        assert rule.buy_amount == 500
        assert rule.max_lots == 10
        assert rule.enabled is True

    def test_disabled_rule(self):
        rule = StockRule("TSLA", -3.0, 8.0, 300, 5, enabled=False)
        assert rule.enabled is False

    def test_accessor_uses_scalar_when_no_array(self):
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        assert rule.buy_threshold_at(1) == -5.0
        assert rule.buy_threshold_at(5) == -5.0
        assert rule.sell_threshold_at(3) == 10.0
        assert rule.buy_amount_at(2) == 500

    def test_accessor_uses_array_when_present(self):
        rule = StockRule(
            "AAPL",
            buy_threshold_pct=-5.0,      # 배열이 있으면 무시되어야 함
            sell_threshold_pct=10.0,
            buy_amount=500,
            max_lots=10,
            buy_threshold_pcts=[-3.0, -5.0, -7.0, -10.0],
            sell_threshold_pcts=[5.0, 7.0, 10.0, 15.0],
            buy_amounts=[100.0, 200.0, 300.0, 400.0],
        )
        assert rule.buy_threshold_at(1) == -3.0
        assert rule.buy_threshold_at(3) == -7.0
        assert rule.sell_threshold_at(2) == 7.0
        assert rule.buy_amount_at(4) == 400.0

    def test_accessor_clamps_to_last_when_level_exceeds_array(self):
        rule = StockRule(
            "AAPL",
            buy_amount=500,
            max_lots=10,
            buy_threshold_pcts=[-5.0],
            sell_threshold_pcts=[10.0],
            buy_amounts=[100.0, 200.0],
        )
        assert rule.buy_threshold_at(4) == -5.0
        assert rule.sell_threshold_at(9) == 10.0
        assert rule.buy_amount_at(7) == 200.0

    def test_missing_buy_threshold_raises(self):
        with pytest.raises(ValueError, match="buy_threshold"):
            StockRule("AAPL", sell_threshold_pct=10.0, buy_amount=500)

    def test_missing_sell_threshold_raises(self):
        with pytest.raises(ValueError, match="sell_threshold"):
            StockRule("AAPL", buy_threshold_pct=-5.0, buy_amount=500)

    def test_missing_buy_amount_raises(self):
        with pytest.raises(ValueError, match="buy_amount"):
            StockRule("AAPL", buy_threshold_pct=-5.0, sell_threshold_pct=10.0)

    def test_empty_array_rejected(self):
        with pytest.raises(ValueError, match="buy_threshold_pcts"):
            StockRule(
                "AAPL",
                sell_threshold_pct=10.0,
                buy_amount=500,
                buy_threshold_pcts=[],
            )


class TestQuantizeQty:
    """수량 정밀도 정규화: 주식(KIS)=정수, 코인(업비트)=소수."""

    def _stock(self, market_type="overseas", **kw):
        return StockRule("AAPL", -5.0, 10.0, 500, market_type=market_type, **kw)

    def _crypto(self, **kw):
        return StockRule("KRW-BTC", -5.0, 10.0, 100000, market_type="crypto", **kw)

    def test_stock_floors_to_integer(self):
        rule = self._stock()
        result = rule.quantize_qty(3.99)
        assert result == 3
        assert isinstance(result, int)

    def test_domestic_floors_to_integer(self):
        rule = self._stock(market_type="domestic")
        assert rule.quantize_qty(7.5) == 7
        assert isinstance(rule.quantize_qty(7.5), int)

    def test_crypto_keeps_fraction(self):
        rule = self._crypto()
        # buy_amount 100000 / price 150000000 = 0.000666... -> 8자리 내림
        qty = rule.quantize_qty(100000 / 150000000)
        assert qty == pytest.approx(0.00066666, abs=1e-9)
        assert isinstance(qty, float)

    def test_crypto_default_precision_is_8(self):
        rule = self._crypto()
        assert rule.quantize_qty(0.123456789) == pytest.approx(0.12345678, abs=1e-12)

    def test_explicit_precision_overrides_market_default(self):
        # 코인이지만 소수 3자리로 제한
        rule = self._crypto(qty_precision=3)
        assert rule.quantize_qty(0.123456) == pytest.approx(0.123, abs=1e-12)

    def test_precision_zero_on_crypto_forces_integer(self):
        rule = self._crypto(qty_precision=0)
        result = rule.quantize_qty(2.9)
        assert result == 2
        assert isinstance(result, int)

    def test_round_up_for_partial_sell(self):
        rule = self._crypto()
        # 부분매도: 올림 -> dust 방지
        assert rule.quantize_qty(0.000000011, round_up=True) == pytest.approx(0.00000002, abs=1e-12)

    def test_stock_round_up_returns_integer(self):
        rule = self._stock()
        result = rule.quantize_qty(3.01, round_up=True)
        assert result == 4
        assert isinstance(result, int)

    def test_negative_precision_treated_as_integer(self):
        rule = self._crypto(qty_precision=-2)
        assert rule.quantize_qty(5.9) == 5

    def test_floor_not_broken_by_float_error(self):
        # 0.29 * 100 = 28.999999999999996 (FP 오차) -> 방어 없으면 floor가 0.28로 잘못 계산
        rule = self._crypto(qty_precision=2)
        assert rule.quantize_qty(0.29) == pytest.approx(0.29, abs=1e-12)

    def test_ceil_not_broken_by_float_error(self):
        # 0.07 * 100 = 7.000000000000001 (FP 오차) -> 방어 없으면 ceil이 0.08로 잘못 올림
        rule = self._crypto(qty_precision=2)
        assert rule.quantize_qty(0.07, round_up=True) == pytest.approx(0.07, abs=1e-12)


class TestPositionLot:
    def test_creation(self):
        lot = PositionLot(
            lot_id="lot_001",
            ticker="AAPL",
            buy_price=150.0,
            quantity=3,
            buy_date="2026-04-01",
            level=1,
        )
        assert lot.lot_id == "lot_001"
        assert lot.ticker == "AAPL"
        assert lot.buy_price == 150.0
        assert lot.quantity == 3
        assert lot.buy_date == "2026-04-01"
        assert lot.level == 1

    def test_default_level(self):
        """level 미지정 시 기본값 0 (레거시 호환)"""
        lot = PositionLot("lot_001", "AAPL", 150.0, 3, "2026-04-01")
        assert lot.level == 0


class TestPortfolio:
    def test_total_value_cash_only(self):
        pf = Portfolio(total_cash=10000.0, holdings={}, current_prices={})
        assert pf.total_value == 10000.0

    def test_total_value_with_holdings(self):
        pf = Portfolio(
            total_cash=5000.0,
            holdings={"AAPL": 10, "MSFT": 5},
            current_prices={"AAPL": 150.0, "MSFT": 300.0},
        )
        # 5000 + (10 * 150) + (5 * 300) = 5000 + 1500 + 1500 = 8000
        assert pf.total_value == 8000.0

    def test_total_value_missing_price(self):
        """가격이 없는 종목은 0으로 계산"""
        pf = Portfolio(
            total_cash=1000.0,
            holdings={"UNKNOWN": 100},
            current_prices={},
        )
        assert pf.total_value == 1000.0


class TestOrder:
    def test_buy_order(self):
        order = Order("AAPL", OrderAction.BUY, 10, 150.0)
        assert order.ticker == "AAPL"
        assert order.action == OrderAction.BUY
        assert order.quantity == 10
        assert order.price == 150.0

    def test_sell_order(self):
        order = Order("MSFT", OrderAction.SELL, 5, 300.0)
        assert order.action == OrderAction.SELL


class TestTradeSignal:
    def test_has_orders_true(self):
        signal = TradeSignal(
            orders=[Order("AAPL", OrderAction.BUY, 1, 100.0)],
            reason="test",
        )
        assert signal.has_orders is True

    def test_has_orders_false(self):
        signal = TradeSignal(orders=[], reason="no signal")
        assert signal.has_orders is False


class TestSplitSignal:
    def test_buy_signal(self):
        sig = SplitSignal(
            ticker="AAPL",
            lot_id=None,
            action=OrderAction.BUY,
            quantity=5,
            price=95.0,
            reason="초기 매수 Lv1",
            pct_change=0.0,
            level=1,
        )
        assert sig.lot_id is None
        assert sig.action == OrderAction.BUY
        assert sig.level == 1

    def test_sell_signal(self):
        sig = SplitSignal(
            ticker="AAPL",
            lot_id="lot_001",
            action=OrderAction.SELL,
            quantity=5,
            price=110.0,
            reason="Lv1 +10.0% -> 익절",
            pct_change=10.0,
            level=1,
        )
        assert sig.lot_id == "lot_001"
        assert sig.action == OrderAction.SELL
        assert sig.level == 1

    def test_default_level(self):
        """level 미지정 시 기본값 0"""
        sig = SplitSignal("AAPL", None, OrderAction.BUY, 5, 100.0, "test", 0.0)
        assert sig.level == 0


class TestOrderAction:
    def test_str(self):
        assert str(OrderAction.BUY) == "BUY"
        assert str(OrderAction.SELL) == "SELL"


class TestExecutionStatus:
    def test_str(self):
        assert str(ExecutionStatus.FILLED) == "FILLED"
        assert str(ExecutionStatus.REJECTED) == "REJECTED"


class TestStockRuleRegime:
    def test_regime_defaults_off(self):
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        assert rule.regime_enabled is False
        assert rule.regime_adx_trend == 25.0
        assert rule.regime_adx_range == 20.0
        assert rule.regime_min_bars == 200
        assert rule.uptrend_max_adds == 3
        assert rule.trendbreak_use_sma50 is True
        assert rule.uptrend_add_amount is None
        assert rule.uptrend_add_amounts is None

    def test_uptrend_add_amount_fallback_to_buy_amount(self):
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        assert rule.uptrend_add_amount_at(1) == 500
        assert rule.uptrend_add_amount_at(3) == 500

    def test_uptrend_add_amount_scalar(self):
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10, uptrend_add_amount=800)
        assert rule.uptrend_add_amount_at(1) == 800
        assert rule.uptrend_add_amount_at(5) == 800

    def test_uptrend_add_amounts_array_clamps(self):
        rule = StockRule(
            "AAPL", -5.0, 10.0, 500, 10,
            uptrend_add_amounts=[1500.0, 1000.0, 600.0],
        )
        assert rule.uptrend_add_amount_at(1) == 1500.0
        assert rule.uptrend_add_amount_at(2) == 1000.0
        assert rule.uptrend_add_amount_at(3) == 600.0
        assert rule.uptrend_add_amount_at(9) == 600.0  # clamp to last

    def test_guard_rejects_inverted_adx_thresholds(self):
        with pytest.raises(ValueError, match="regime_adx_range"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_adx_trend=20.0, regime_adx_range=30.0,
            )

    def test_guard_rejects_negative_pullback_band(self):
        with pytest.raises(ValueError, match="uptrend_pullback_band_pct"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, uptrend_pullback_band_pct=-1.0,
            )

    def test_guard_inactive_when_regime_disabled(self):
        # regime_enabled=False면 비정상 값이어도 통과 (OFF)
        rule = StockRule(
            "AAPL", -5.0, 10.0, 500, 10,
            regime_adx_trend=20.0, regime_adx_range=30.0,
        )
        assert rule.regime_enabled is False

    def test_empty_uptrend_add_amounts_rejected(self):
        with pytest.raises(ValueError, match="uptrend_add_amounts"):
            StockRule("AAPL", -5.0, 10.0, 500, 10, uptrend_add_amounts=[])


class TestStockRuleChannelRegime:
    def test_channel_defaults(self):
        rule = StockRule("AAPL", -5.0, 10.0, 500, 10)
        assert rule.regime_algo == "ma_adx"
        assert rule.channel_lookback == 63
        assert rule.channel_stddev_k == 2.0
        assert rule.channel_slope_band_pct == 8.0
        assert rule.channel_breakdown_tolerance_pct == 0.0
        assert rule.channel_breakdown_atr_multiplier is None
        assert rule.trend_only_enabled is False
        assert rule.shadow_mode_v2_enabled is False
        assert rule.shadow_mode_v3_enabled is False
        assert rule.shadow_mode_v3_1_enabled is False
        assert rule.shadow_mode_v3_2_enabled is False
        assert rule.shadow_mode_v2_routing_enabled is False
        assert rule.pullback_rebound_add_amount_multiplier == 1.0

    def test_shadow_routing_and_pullback_multiplier_validate_dependencies(self):
        with pytest.raises(ValueError, match="shadow_mode_v2_routing_enabled"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_algo="channel",
                multi_horizon_regime_enabled=True,
                shadow_mode_v2_routing_enabled=True,
            )
        with pytest.raises(ValueError, match="pullback_rebound_add_amount_multiplier"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                pullback_rebound_add_amount_multiplier=1.1,
            )

    @pytest.mark.parametrize("regime_enabled,regime_algo", [
        (False, "channel"), (True, "ma_adx"),
    ])
    def test_shadow_v2_requires_channel_regime(self, regime_enabled, regime_algo):
        with pytest.raises(ValueError, match="shadow_mode_v2_enabled"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=regime_enabled, regime_algo=regime_algo,
                shadow_mode_v2_enabled=True,
            )

    def test_shadow_v3_requires_channel_regime(self):
        with pytest.raises(ValueError, match="shadow_mode_v3_enabled"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_algo="ma_adx",
                shadow_mode_v3_enabled=True,
            )

    def test_shadow_v3_1_requires_channel_regime(self):
        with pytest.raises(ValueError, match="shadow_mode_v3_1_enabled"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=False, regime_algo="channel",
                shadow_mode_v3_1_enabled=True,
            )

    def test_shadow_v3_2_requires_channel_regime(self):
        with pytest.raises(ValueError, match="shadow_mode_v3_2_enabled"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=False, regime_algo="channel",
                shadow_mode_v3_2_enabled=True,
            )

    @pytest.mark.parametrize("overrides", [
        {"regime_enabled": False, "regime_algo": "channel", "multi_horizon_regime_enabled": True},
        {"regime_enabled": True, "regime_algo": "ma_adx", "multi_horizon_regime_enabled": True},
        {"regime_enabled": True, "regime_algo": "channel", "multi_horizon_regime_enabled": False},
    ])
    def test_trend_only_requires_channel_multi_horizon_regime(self, overrides):
        with pytest.raises(ValueError, match="trend_only_enabled"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                trend_only_enabled=True, **overrides,
            )

    def test_trend_only_accepts_required_regime_combination(self):
        rule = StockRule(
            "AAPL", -5.0, 10.0, 500, 10,
            regime_enabled=True, regime_algo="channel",
            multi_horizon_regime_enabled=True, trend_only_enabled=True,
        )
        assert rule.trend_only_enabled is True

    def test_transition_partial_sell_defaults_disabled_and_validates_dependencies(self):
        rule = StockRule("AAPL", -5, 10, 100, 10)
        assert rule.uptrend_sideways_transition_partial_sell_pct == 0.0
        assert rule.uptrend_sideways_transition_confirm_bars == 2

        with pytest.raises(ValueError, match="상승→횡보 선제청산"):
            StockRule(
                "AAPL", -5, 10, 100, 10,
                uptrend_sideways_transition_partial_sell_pct=50,
            )
        with pytest.raises(ValueError, match="100 미만"):
            StockRule(
                "AAPL", -5, 10, 100, 10,
                uptrend_sideways_transition_partial_sell_pct=100,
            )
        with pytest.raises(ValueError, match="1 이상의 정수"):
            StockRule(
                "AAPL", -5, 10, 100, 10,
                uptrend_sideways_transition_confirm_bars=0,
            )

    def test_rebound_entry_mode_defaults_and_dependencies(self):
        rule = StockRule("AAPL", -5, 10, 100, 10)
        assert rule.trend_entry_mode == "aligned"
        with pytest.raises(ValueError, match="rebound 진입"):
            StockRule("AAPL", -5, 10, 100, 10, trend_entry_mode="rebound")
        rebound = StockRule(
            "AAPL", -5, 10, 100, 10,
            regime_enabled=True, regime_algo="channel",
            multi_horizon_regime_enabled=True, trend_only_enabled=True,
            trend_entry_mode="rebound",
        )
        assert rebound.rebound_entry_confirm_bars == 2
        assert rebound.pullback_rebound_max_wait_bars == 10
        staged = StockRule(
            "AAPL", -5, 10, 100, 10,
            regime_enabled=True, regime_algo="channel",
            multi_horizon_regime_enabled=True, trend_only_enabled=True,
            trend_entry_mode="staged_rebound",
        )
        assert staged.staged_rebound_probe_pct == 50
        assert staged.staged_rebound_wait_probe_enabled is False
        assert staged.staged_rebound_wait_probe_pct == 25
        assert staged.post_liquidation_recovery_probe_enabled is False
        with pytest.raises(ValueError, match="staged_rebound_probe_pct"):
            StockRule(
                "AAPL", -5, 10, 100, 10,
                regime_enabled=True, regime_algo="channel",
                multi_horizon_regime_enabled=True, trend_only_enabled=True,
                trend_entry_mode="staged_rebound", staged_rebound_probe_pct=100,
            )
        with pytest.raises(ValueError, match="단계형 반등 대기·게이트 탐색"):
            StockRule(
                "AAPL", -5, 10, 100, 10,
                staged_rebound_wait_probe_enabled=True,
            )
        with pytest.raises(ValueError, match="staged_rebound_wait_probe_pct"):
            StockRule(
                "AAPL", -5, 10, 100, 10,
                regime_enabled=True, regime_algo="channel",
                multi_horizon_regime_enabled=True, trend_only_enabled=True,
                trend_entry_mode="staged_rebound", staged_rebound_wait_probe_pct=100,
            )

    def test_channel_algo_accepted(self):
        rule = StockRule(
            "AAPL", -5.0, 10.0, 500, 10,
            regime_enabled=True, regime_algo="channel",
        )
        assert rule.regime_algo == "channel"

    def test_unknown_algo_rejected(self):
        with pytest.raises(ValueError, match="regime_algo"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_algo="slope",
            )

    def test_lookback_too_small_rejected(self):
        with pytest.raises(ValueError, match="channel_lookback"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_algo="channel", channel_lookback=20,
            )

    def test_non_positive_stddev_k_rejected(self):
        with pytest.raises(ValueError, match="channel_stddev_k"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_algo="channel", channel_stddev_k=0.0,
            )

    def test_negative_slope_band_rejected(self):
        with pytest.raises(ValueError, match="channel_slope_band_pct"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_algo="channel",
                channel_slope_band_pct=-1.0,
            )

    def test_breakdown_tolerance_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="channel_breakdown_tolerance_pct"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_algo="channel",
                channel_breakdown_tolerance_pct=100.0,
            )

    @pytest.mark.parametrize("value", [-0.1, float("nan"), float("inf")])
    def test_breakdown_atr_multiplier_must_be_finite_and_nonnegative(self, value):
        with pytest.raises(ValueError, match="channel_breakdown_atr_multiplier"):
            StockRule(
                "AAPL", -5.0, 10.0, 500, 10,
                regime_enabled=True, regime_algo="channel",
                channel_breakdown_atr_multiplier=value,
            )

    def test_breakdown_atr_multiplier_zero_is_valid(self):
        rule = StockRule(
            "AAPL", -5.0, 10.0, 500, 10,
            regime_enabled=True, regime_algo="channel",
            channel_breakdown_atr_multiplier=0.0,
        )
        assert rule.channel_breakdown_atr_multiplier == 0.0

    def test_channel_guards_inactive_when_algo_ma_adx(self):
        # ma_adx 모드에서는 채널 파라미터가 비정상이어도 통과 (미사용)
        rule = StockRule(
            "AAPL", -5.0, 10.0, 500, 10,
            regime_enabled=True, channel_lookback=5, channel_stddev_k=-1.0,
        )
        assert rule.regime_algo == "ma_adx"

    def test_channel_guards_inactive_when_regime_disabled(self):
        rule = StockRule(
            "AAPL", -5.0, 10.0, 500, 10,
            regime_algo="channel", channel_lookback=5,
        )
        assert rule.regime_enabled is False
