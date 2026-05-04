# tests/test_infra_broker.py
import pytest
from unittest.mock import MagicMock, patch
from src.infra.broker.mock import MockBroker
from src.infra.broker.kis_base import KisBrokerCommon
from src.infra.broker.kis_overseas import KisOverseasBrokerBase
from src.core.models import Order, OrderAction, ExecutionStatus


class TestMockBroker:
    def test_initial_portfolio(self):
        broker = MockBroker(initial_cash=10000.0)
        pf = broker.get_portfolio()
        assert pf.total_cash == 10000.0
        assert pf.holdings == {}

    def test_fetch_current_prices(self):
        broker = MockBroker(prices={"AAPL": 150.0, "MSFT": 300.0})
        prices = broker.fetch_current_prices(["AAPL", "MSFT"])
        assert prices["AAPL"] == 150.0
        assert prices["MSFT"] == 300.0

    def test_fetch_unknown_ticker_price(self):
        broker = MockBroker()
        prices = broker.fetch_current_prices(["UNKNOWN"])
        assert prices["UNKNOWN"] == 100.0  # 기본값

    def test_buy_order(self):
        broker = MockBroker(initial_cash=10000.0, prices={"AAPL": 100.0})
        orders = [Order("AAPL", OrderAction.BUY, 5, 100.0)]
        executions = broker.execute_orders(orders)

        assert len(executions) == 1
        assert executions[0].action == OrderAction.BUY
        assert executions[0].status == ExecutionStatus.FILLED
        assert executions[0].quantity == 5

        pf = broker.get_portfolio()
        assert pf.holdings["AAPL"] == 5
        assert pf.total_cash < 10000.0

    def test_sell_order(self):
        broker = MockBroker(
            initial_cash=5000.0,
            holdings={"AAPL": 10},
            prices={"AAPL": 100.0},
        )
        orders = [Order("AAPL", OrderAction.SELL, 5, 100.0)]
        executions = broker.execute_orders(orders)

        assert len(executions) == 1
        assert executions[0].action == OrderAction.SELL
        assert executions[0].quantity == 5

        pf = broker.get_portfolio()
        assert pf.holdings["AAPL"] == 5
        assert pf.total_cash > 5000.0

    def test_sell_before_buy(self):
        """매도가 매수보다 먼저 실행됨"""
        broker = MockBroker(
            initial_cash=1000.0,
            holdings={"AAPL": 10},
            prices={"AAPL": 100.0},
        )
        orders = [
            Order("AAPL", OrderAction.BUY, 2, 100.0),
            Order("AAPL", OrderAction.SELL, 5, 100.0),
        ]
        executions = broker.execute_orders(orders)

        # 매도가 먼저 실행되어야 함
        assert executions[0].action == OrderAction.SELL
        assert executions[1].action == OrderAction.BUY

    def test_sell_more_than_holdings(self):
        """보유량보다 많이 매도 시도 -> 보유량만큼만 체결"""
        broker = MockBroker(holdings={"AAPL": 3}, prices={"AAPL": 100.0})
        orders = [Order("AAPL", OrderAction.SELL, 10, 100.0)]
        executions = broker.execute_orders(orders)

        assert executions[0].quantity == 3  # 보유량만큼만

    def test_sell_with_zero_holdings_returns_rejected(self):
        """보유량 0인 종목 매도 시도 -> REJECTED 반환, 상태 변경 없음"""
        initial_cash = 5000.0
        broker = MockBroker(initial_cash=initial_cash, holdings={"AAPL": 0}, prices={"AAPL": 100.0})
        orders = [Order("AAPL", OrderAction.SELL, 10, 100.0)]
        executions = broker.execute_orders(orders)

        assert len(executions) == 1
        assert executions[0].status == ExecutionStatus.REJECTED
        assert executions[0].quantity == 0
        assert broker.cash == initial_cash  # 현금 변동 없음
        assert broker.holdings.get("AAPL", 0) == 0  # 보유량 변동 없음

    def test_sell_unowned_ticker_returns_rejected(self):
        """보유하지 않은 종목 매도 시도 -> REJECTED 반환"""
        broker = MockBroker(holdings={}, prices={"AAPL": 100.0})
        orders = [Order("AAPL", OrderAction.SELL, 5, 100.0)]
        executions = broker.execute_orders(orders)

        assert executions[0].status == ExecutionStatus.REJECTED

    def test_buy_insufficient_cash(self):
        """자금 부족 시 가능한 만큼만 매수"""
        broker = MockBroker(initial_cash=200.0, prices={"AAPL": 100.0})
        orders = [Order("AAPL", OrderAction.BUY, 10, 100.0)]
        executions = broker.execute_orders(orders)

        # 200 * 0.98 / (100 * 1.01) = 1.94 -> 1주만 매수 가능
        assert executions[0].quantity <= 2

    def test_multiple_orders(self):
        """여러 주문 동시 처리"""
        broker = MockBroker(
            initial_cash=20000.0,
            prices={"AAPL": 100.0, "MSFT": 200.0},
        )
        orders = [
            Order("AAPL", OrderAction.BUY, 5, 100.0),
            Order("MSFT", OrderAction.BUY, 3, 200.0),
        ]
        executions = broker.execute_orders(orders)

        assert len(executions) == 2
        pf = broker.get_portfolio()
        assert pf.holdings["AAPL"] == 5
        assert pf.holdings["MSFT"] == 3


class TestCheckSpread:
    @pytest.fixture
    def broker(self):
        with patch.object(KisBrokerCommon, "_auth", return_value="fake_token"):
            b = KisBrokerCommon.__new__(KisBrokerCommon)
            b.SPREAD_THRESHOLD_PCT = 0.5
            return b

    def test_ask_zero_returns_false(self, broker):
        assert broker._check_spread(100.0, 0.0) is False

    def test_bid_zero_returns_false(self, broker):
        assert broker._check_spread(0.0, 100.0) is False

    def test_both_zero_returns_false(self, broker):
        assert broker._check_spread(0.0, 0.0) is False

    def test_negative_bid_returns_false(self, broker):
        assert broker._check_spread(-1.0, 100.0) is False

    def test_normal_spread_within_threshold_returns_true(self, broker):
        # spread = (100.2 - 100.0) / 100.1 * 100 ≈ 0.2% < 0.5%
        assert broker._check_spread(100.0, 100.2) is True

    def test_spread_exceeds_threshold_returns_false(self, broker):
        # spread = (101.0 - 99.0) / 100.0 * 100 = 2.0% > 0.5%
        assert broker._check_spread(99.0, 101.0) is False

    def test_spread_equal_threshold_returns_true(self, broker):
        # spread = (100.5 - 99.5) / 100.0 * 100 = 1.0% — threshold 맞춰 커스텀
        b = broker
        b.SPREAD_THRESHOLD_PCT = 1.0
        assert b._check_spread(99.5, 100.5) is True

    def test_inverted_spread_returns_false(self, broker):
        assert broker._check_spread(100.0, 90.0) is False


class TestKisOverseasGetPortfolio:
    @pytest.fixture
    def broker(self):
        from datetime import datetime, timedelta
        b = KisOverseasBrokerBase.__new__(KisOverseasBrokerBase)
        b.logger = MagicMock()
        b.base_url = "https://fake"
        b.cano = "12345678"
        b.acnt_prdt_cd = "01"
        b.PORTFOLIO_TR_ID = "TTTS3012R"
        b.app_key = "fake_key"
        b.app_secret = "fake_secret"
        b.access_token = "fake_token"
        b.token_expires_at = datetime.now() + timedelta(hours=1)
        b.MARGIN_TR_ID = "TTTC2101R"
        return b

    @patch("src.infra.broker.kis_overseas._pkg.requests.get")
    def test_all_exchanges_fail_raises_runtime_error(self, mock_get, broker):
        """모든 거래소 조회 실패 시 RuntimeError 발생"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rt_cd": "1", "msg1": "error"}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="모든 거래소"):
            broker.get_portfolio()

    @patch("src.infra.broker.kis_overseas._pkg.requests.get")
    def test_all_exchanges_raise_exception_raises_runtime_error(self, mock_get, broker):
        """모든 거래소에서 예외 발생 시 RuntimeError 발생"""
        mock_get.side_effect = Exception("network error")

        with pytest.raises(RuntimeError, match="모든 거래소"):
            broker.get_portfolio()

    @patch("src.infra.broker.kis_overseas._pkg.requests.get")
    def test_first_exchange_fails_second_succeeds(self, mock_get, broker):
        """첫 번째 거래소 실패 -> 두 번째 성공 시 total_cash 정상 반환"""
        fail_resp = MagicMock()
        fail_resp.json.return_value = {"rt_cd": "1", "msg1": "error"}
        fail_resp.raise_for_status.return_value = None

        success_resp = MagicMock()
        success_resp.raise_for_status.return_value = None
        success_resp.json.return_value = {
            "rt_cd": "0",
            "output1": [],
            "output2": {"ovrs_ord_psbl_amt": "5000.00"},
        }

        # 1: margin API (success), 2: exchange 1 (fail), 3: exchange 2 (success), 4: exchange 3 (success)
        margin_resp = MagicMock()
        margin_resp.raise_for_status.return_value = None
        margin_resp.json.return_value = {
            "rt_cd": "0",
            "output": [{"natn_name": "미국", "frcr_gnrl_ord_psbl_amt": "5000.00"}]
        }

        mock_get.side_effect = [margin_resp, fail_resp, success_resp, success_resp]

        pf = broker.get_portfolio()
        assert pf.total_cash == 5000.0


class TestKisOverseasSendOrderRemoved:
    def test_send_order_does_not_exist(self):
        """_send_order는 dead code로 제거됨 — _send_order_and_wait만 존재해야 함."""
        assert not hasattr(KisOverseasBrokerBase, '_send_order')
        assert hasattr(KisOverseasBrokerBase, '_send_order_and_wait')


class TestKisOverseasQueryFillDetails:
    """해외 _query_fill_details 다중 row 합산 검증."""

    def _make_broker(self):
        from datetime import datetime, timedelta
        b = KisOverseasBrokerBase.__new__(KisOverseasBrokerBase)
        b.logger = MagicMock()
        b.base_url = "https://fake"
        b.cano = "12345678"
        b.acnt_prdt_cd = "01"
        b.FILL_TR_ID = "VTTS3035R"
        b.app_key = "k"; b.app_secret = "s"; b.access_token = "t"
        b.token_expires_at = datetime.now() + timedelta(hours=1)
        return b

    @patch("src.infra.broker.kis_overseas._pkg.requests.get")
    def test_multi_row_sum(self, mock_get):
        broker = self._make_broker()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "rt_cd": "0",
            "output": [
                {"odno": "X", "ft_ccld_qty": "2", "ft_ccld_unpr3": "100.0",
                 "ovrs_stck_ccld_fee": "0.10"},
                {"odno": "X", "ft_ccld_qty": "3", "ft_ccld_unpr3": "110.0",
                 "ovrs_stck_ccld_fee": "0.15"},
                # 다른 ODNO 무시되어야 함
                {"odno": "Y", "ft_ccld_qty": "10", "ft_ccld_unpr3": "999.0",
                 "ovrs_stck_ccld_fee": "5.0"},
            ]
        }
        mock_get.return_value = mock_resp

        price, qty, fee = broker._query_fill_details("X", "AAPL", "NASD")
        # 가중평균: (2*100 + 3*110)/5 = 530/5 = 106
        assert qty == 5
        assert price == pytest.approx(106.0)
        assert fee == pytest.approx(0.25)

    @patch("src.infra.broker.kis_overseas._pkg.requests.get")
    def test_single_row_regression(self, mock_get):
        """단일 row 응답 시 합산 결과 == 단일 row 값 (회귀 방지)."""
        broker = self._make_broker()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "rt_cd": "0",
            "output": [
                {"odno": "X", "ft_ccld_qty": "5", "ft_ccld_unpr3": "100.0",
                 "ovrs_stck_ccld_fee": "0.5"},
            ]
        }
        mock_get.return_value = mock_resp
        price, qty, fee = broker._query_fill_details("X", "AAPL", "NASD")
        assert qty == 5
        assert price == 100.0
        assert fee == 0.5

    @patch("src.infra.broker.kis_overseas._pkg.requests.get")
    def test_no_match_returns_zero(self, mock_get):
        broker = self._make_broker()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "rt_cd": "0",
            "output": [
                {"odno": "Y", "ft_ccld_qty": "5", "ft_ccld_unpr3": "100.0",
                 "ovrs_stck_ccld_fee": "0.5"},
            ]
        }
        mock_get.return_value = mock_resp
        price, qty, fee = broker._query_fill_details("X", "AAPL", "NASD")
        assert (price, qty, fee) == (0.0, 0, 0.0)


class TestKisOverseasOutcomeToExecution:
    def _make_broker(self):
        from datetime import datetime, timedelta
        b = KisOverseasBrokerBase.__new__(KisOverseasBrokerBase)
        b.logger = MagicMock()
        b.base_url = "https://fake"
        b.cano = "12345678"; b.acnt_prdt_cd = "01"
        b.app_key = "k"; b.app_secret = "s"; b.access_token = "t"
        b.token_expires_at = datetime.now() + __import__("datetime").timedelta(hours=1)
        return b

    def test_partial_outcome(self):
        from src.infra.broker.kis_order_helpers import TimeoutOutcome
        from src.core.models import Order, OrderAction, ExecutionStatus
        broker = self._make_broker()
        outcome = TimeoutOutcome(
            classification="PARTIAL", fill_qty=2, fill_price=100.5,
            fill_fee=0.1, cancel_ok=True, still_pending=False,
            detail="partial_after_cancel",
        )
        order = Order(ticker="AAPL", action=OrderAction.SELL, quantity=5, price=100.0)
        exe = broker._outcome_to_execution(outcome, order, "ODNO_O", 99.0)
        assert exe.status == ExecutionStatus.PARTIAL
        assert exe.quantity == 2
        assert exe.price == 100.5
        assert "partial_after_cancel(2/5)" in exe.reason
