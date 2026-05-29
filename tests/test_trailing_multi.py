# tests/test_trailing_multi.py
"""_evaluate_trailing_multi: 멀티-lot trailing 동시 추적 및 벌크 매도 평가기 테스트."""
import pytest
from src.core.models import StockRule, PositionLot, Portfolio, SplitSignal, OrderAction
from src.core.logic.split_evaluator import SplitEvaluator


def _portfolio(ticker, price):
    return Portfolio(total_cash=1_000_000.0, holdings={},
                     current_prices={ticker: price})


def _lot(level, buy_price, qty=10, trailing_highest=None):
    return PositionLot(
        lot_id=f"lot_{level:03d}", ticker="005930",
        buy_price=buy_price, quantity=qty,
        buy_date="2024-01-01", level=level,
        trailing_highest_price=trailing_highest,
    )


def _rule(**kw):
    base = dict(
        ticker="005930", buy_threshold_pct=-5.0,
        sell_threshold_pct=10.0, buy_amount=500_000,
        trailing_drop_pct=5.0,
    )
    base.update(kw)
    return StockRule(**base)


@pytest.fixture
def ev():
    return SplitEvaluator()


class TestEvaluateTrailingMulti:
    # -- None 반환: last_lot 미활성화 ------------------------------------------

    def test_returns_none_when_last_lot_not_activated(self, ev):
        rule = _rule()
        lots = [_lot(1, 100.0), _lot(2, 95.0)]
        # current_price=102 < buy_price*(1+10%) = 104.5 -> 미활성
        result = ev._evaluate_trailing_multi(rule, lots, 102.0)
        assert result is None

    def test_returns_none_when_price_barely_below_threshold(self, ev):
        rule = _rule(sell_threshold_pct=10.0)
        lots = [_lot(1, 100.0), _lot(2, 100.0)]
        # 9.9% -> 미활성
        result = ev._evaluate_trailing_multi(rule, lots, 109.9)
        assert result is None

    # -- 빈 리스트 반환: 활성화됐지만 미발동 ------------------------------------

    def test_returns_empty_list_when_activated_but_no_fire(self, ev):
        rule = _rule(sell_threshold_pct=10.0, trailing_drop_pct=5.0)
        # trailing_highest=115 설정 -> 이미 활성화, 현재가 112 -> drop=2.6% < 5%
        lots = [_lot(1, 100.0, trailing_highest=115.0)]
        result = ev._evaluate_trailing_multi(rule, lots, 112.0)
        assert result == []

    def test_returns_info_only_for_newly_activated_lot(self, ev):
        rule = _rule(sell_threshold_pct=10.0, trailing_drop_pct=5.0)
        # Lv1: buy=100, price=110 -> 10% 도달 -> 신규 활성화
        lots = [_lot(1, 100.0)]
        result = ev._evaluate_trailing_multi(rule, lots, 110.0)
        # 활성화됐지만 drop=0 -> 미발동 -> info 신호만
        assert result is not None
        assert len(result) == 1
        assert result[0].is_info is True
        assert result[0].quantity == 0
        assert result[0].ticker == "005930"

    # -- 벌크 매도 신호 반환 --------------------------------------------------

    def test_bulk_sell_signal_on_fired_lot(self, ev):
        rule = _rule(sell_threshold_pct=10.0, trailing_drop_pct=5.0)
        # trailing_highest=115, price=109 -> drop=5.2% >= 5% -> 발동
        lots = [_lot(1, 100.0, trailing_highest=115.0)]
        result = ev._evaluate_trailing_multi(rule, lots, 109.0)
        assert result is not None and len(result) == 1
        sig = result[0]
        assert sig.trailing_bulk is True
        assert sig.action == OrderAction.SELL
        assert sig.quantity == 10
        assert sig.lot_id is None

    def test_partial_fire_high_levels_only(self, ev):
        rule = _rule(sell_threshold_pct=10.0, trailing_drop_pct=5.0)
        # Lv3(highest=115, drop=5.2% -> 발동), Lv2(highest=115, drop=5.2% -> 발동),
        # Lv1(buy=100, price=109 -> 9% < 10% -> 미활성 -> 탐색 중단)
        lots = [
            _lot(1, 100.0),                    # 미활성
            _lot(2, 95.0, trailing_highest=115.0),  # 활성, 발동
            _lot(3, 90.0, trailing_highest=115.0),  # 활성, 발동
        ]
        result = ev._evaluate_trailing_multi(rule, lots, 109.0)
        assert result is not None
        bulk = [s for s in result if s.trailing_bulk]
        assert len(bulk) == 1
        assert bulk[0].quantity == 20  # Lv2+Lv3 각 10주
        assert bulk[0].level == 3

    def test_all_lots_fired(self, ev):
        rule = _rule(sell_threshold_pct=10.0, trailing_drop_pct=5.0)
        lots = [
            _lot(1, 80.0, trailing_highest=115.0),
            _lot(2, 90.0, trailing_highest=115.0),
            _lot(3, 100.0, trailing_highest=115.0),
        ]
        result = ev._evaluate_trailing_multi(rule, lots, 109.0)
        assert result is not None
        bulk = [s for s in result if s.trailing_bulk]
        assert len(bulk) == 1
        assert bulk[0].quantity == 30
        assert bulk[0].trailing_bulk is True

    # -- 배열 기반 trailing_drop_pcts -----------------------------------------

    def test_array_drop_per_lot_level(self, ev):
        # Lv3: drop_pcts=[3.0, 4.0, 6.0] -> Lv3=6%, Lv2=4%, Lv1=3%
        rule = _rule(trailing_drop_pct=None, trailing_drop_pcts=[3.0, 4.0, 6.0],
                     sell_threshold_pct=10.0)
        # drop=5.2%: Lv1(3% -> 발동), Lv2(4% -> 발동), Lv3(6% -> 미발동)
        lots = [
            _lot(1, 80.0, trailing_highest=115.0),
            _lot(2, 90.0, trailing_highest=115.0),
            _lot(3, 100.0, trailing_highest=115.0),
        ]
        result = ev._evaluate_trailing_multi(rule, lots, 109.0)
        assert result is not None
        bulk = [s for s in result if s.trailing_bulk]
        assert len(bulk) == 1
        # Lv1+Lv2만 발동 (Lv3은 6% 미충족)
        assert bulk[0].quantity == 20
        assert bulk[0].level == 2

    # -- trailing OFF 종목 경로 불변 ------------------------------------------

    def test_trailing_off_goes_through_evaluate_sell(self, ev):
        # trailing_drop_pct=None -> trailing_drop_at returns None -> evaluate_stock이
        # evaluate_sell 경로를 타야 함 (직접 _evaluate_trailing_multi 호출은 안 됨)
        rule = _rule(trailing_drop_pct=None, sell_threshold_pct=10.0)
        lots = [_lot(1, 100.0)]
        result = ev._evaluate_trailing_multi(rule, lots, 115.0)
        # trailing OFF 종목에서 호출해도 last_lot 미활성화로 None 아님(활성화 조건 충족)
        # 발동은 trailing_drop이 None이면 break -> fired_lots 없음 -> []
        assert result == []

    # -- 조기 종료: 미활성 lot 이하 탐색 안 함 --------------------------------

    def test_early_stop_on_inactive_lot(self, ev):
        rule = _rule(sell_threshold_pct=10.0, trailing_drop_pct=5.0)
        lots = [
            _lot(1, 100.0),                        # 미활성 (9% < 10%)
            _lot(2, 90.0, trailing_highest=115.0),  # 활성, 발동 대상이지만 탐색 순서 때문
            _lot(3, 80.0, trailing_highest=115.0),  # 활성
        ]
        # 고차수(Lv3)부터: Lv3 활성+발동, Lv2 활성+발동, Lv1 미활성 -> break
        result = ev._evaluate_trailing_multi(rule, lots, 109.0)
        bulk = [s for s in result if s.trailing_bulk]
        assert bulk[0].quantity == 20  # Lv3+Lv2만 발동
        # Lv1은 탐색 안 함 -> trailing_highest_price 여전히 None
        assert lots[0].trailing_highest_price is None

    # -- evaluate_stock 경로 분기 검증 ----------------------------------------

    def test_evaluate_stock_uses_trailing_multi_when_trailing_on(self, ev):
        rule = _rule(sell_threshold_pct=10.0, trailing_drop_pct=5.0)
        # trailing 미활성화 상태 -> evaluate_stock이 buy eval로 넘어가야 함 (빈 결과)
        lots = [_lot(1, 100.0)]
        portfolio = _portfolio("005930", 102.0)
        result = ev.evaluate_stock(rule, lots, portfolio)
        # 미활성화 -> _evaluate_trailing_multi returns None -> buy eval 진행
        # buy_threshold_pct=-5%, 현재가 102 -> 매수 조건 미충족
        assert all(not s.trailing_bulk for s in result)

    def test_evaluate_stock_returns_bulk_when_trailing_fires(self, ev):
        rule = _rule(sell_threshold_pct=10.0, trailing_drop_pct=5.0)
        lots = [_lot(1, 100.0, trailing_highest=115.0)]
        portfolio = _portfolio("005930", 109.0)
        # drop=5.2% >= 5% -> 발동
        result = ev.evaluate_stock(rule, lots, portfolio)
        assert any(s.trailing_bulk for s in result)
