import pytest
from unittest.mock import patch, MagicMock
from src.infra.broker.kis_domestic import KisDomesticPaperBroker
from src.infra.broker.kis_domestic import _to_kis_code, _to_yf_ticker
from src.config import DEFAULT_HTTP_TIMEOUT

class TestKisDomesticBroker:
    @patch("src.infra.broker.kis_domestic._pkg.requests.get")
    def test_fetch_current_prices_timeout(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'rt_cd': '0',
            'output': {'stck_prpr': '100'}
        }
        mock_get.return_value = mock_response

        logger = MagicMock()
        # Mock auth to avoid real API call
        with patch.object(KisDomesticPaperBroker, "_auth", return_value="fake_token"):
            broker = KisDomesticPaperBroker("key", "secret", "acc", logger)
            broker.token_expires_at = None # ensure _auth not called again unnecessarily if not needed
            prices = broker.fetch_current_prices(["069500.KS"])

            assert prices["069500.KS"] == 100.0
            args, kwargs = mock_get.call_args
            assert kwargs["timeout"] == DEFAULT_HTTP_TIMEOUT


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
    assert _to_yf_ticker("005930") == "005930.KS"
    assert _to_yf_ticker("005930.KS") == "005930.KS"
    assert _to_yf_ticker("058470.KQ") == "058470.KQ"


def test_get_portfolio_kosdaq_ticker():
    """KOSDAQ 종목(058470.KQ)이 known_tickers로 전달되면 잔고 조회 시 .KS 대신 .KQ를 반환한다."""
    from datetime import datetime, timedelta
    logger = MagicMock()

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "rt_cd": "0",
        "output1": [
            {"pdno": "058470", "hldg_qty": "10", "prpr": "200000"},
        ],
        "output2": [{"dnca_tot_amt": "1000000", "cma_evlu_amt": "0"}],
    }

    with patch.object(KisDomesticPaperBroker, "_auth", return_value="fake_token"), \
         patch("src.infra.broker.kis_domestic._pkg.requests.get", return_value=mock_resp):
        broker = KisDomesticPaperBroker(
            "key", "secret", "12345678AB", logger,
            known_tickers=["005930.KS", "058470.KQ"]
        )
        broker.token_expires_at = datetime.now() + timedelta(hours=1)
        pf = broker.get_portfolio()

    assert "058470.KQ" in pf.holdings
    assert "058470.KS" not in pf.holdings
    assert pf.holdings["058470.KQ"] == 10


def test_paper_broker_uses_mock_pending_tr_id():
    assert KisDomesticPaperBroker.PENDING_TR_ID == "VTTC0084R"


class TestKisDomesticOutcomeToExecution:
    """타임아웃 outcome -> TradeExecution 변환 검증."""

    def _make_broker(self):
        from datetime import datetime, timedelta
        logger = MagicMock()
        with patch.object(KisDomesticPaperBroker, "_auth", return_value="t"):
            broker = KisDomesticPaperBroker("k", "s", "12345678AB", logger)
            broker.token_expires_at = datetime.now() + timedelta(hours=1)
        return broker

    def test_partial_outcome(self):
        from src.infra.broker.kis_order_helpers import TimeoutOutcome
        from src.core.models import Order, OrderAction, ExecutionStatus
        broker = self._make_broker()
        outcome = TimeoutOutcome(
            classification="PARTIAL", fill_qty=3, fill_price=100.0,
            fill_fee=0.5, cancel_ok=True, still_pending=False,
            detail="partial_after_cancel",
        )
        order = Order(ticker="005930.KS", action=OrderAction.BUY, quantity=5, price=100.0)
        exe = broker._outcome_to_execution(outcome, order, "ODNO123", 99.0)
        assert exe.status == ExecutionStatus.PARTIAL
        assert exe.quantity == 3
        assert exe.price == 100.0
        assert exe.fee == 0.5
        assert "partial_after_cancel(3/5)" in exe.reason
        assert "ODNO=ODNO123" in exe.reason

    def test_rejected_outcome_zeroes_qty(self):
        from src.infra.broker.kis_order_helpers import TimeoutOutcome
        from src.core.models import Order, OrderAction, ExecutionStatus
        broker = self._make_broker()
        outcome = TimeoutOutcome(
            classification="REJECTED", fill_qty=0, fill_price=0.0,
            fill_fee=0.0, cancel_ok=True, still_pending=False,
            detail="cancelled_no_fill",
        )
        order = Order(ticker="005930.KS", action=OrderAction.SELL, quantity=5, price=100.0)
        exe = broker._outcome_to_execution(outcome, order, "ODNO9", 99.5)
        assert exe.status == ExecutionStatus.REJECTED
        assert exe.quantity == 0
        assert exe.price == 99.5  # fallback price

    def test_ordered_with_partial_fill_reason(self):
        from src.infra.broker.kis_order_helpers import TimeoutOutcome
        from src.core.models import Order, OrderAction, ExecutionStatus
        broker = self._make_broker()
        outcome = TimeoutOutcome(
            classification="ORDERED", fill_qty=2, fill_price=100.0,
            fill_fee=0.3, cancel_ok=False, still_pending=True,
            detail="partial_fill_pending",
        )
        order = Order(ticker="005930.KS", action=OrderAction.SELL, quantity=5, price=100.0)
        exe = broker._outcome_to_execution(outcome, order, "ODNO5", 99.0)
        assert exe.status == ExecutionStatus.ORDERED
        assert "PARTIAL_FILL=2" in exe.reason
        assert "manual_check_required" in exe.reason
        assert "cancel_unconfirmed" in exe.reason

    def test_filled_race_outcome(self):
        from src.infra.broker.kis_order_helpers import TimeoutOutcome
        from src.core.models import Order, OrderAction, ExecutionStatus
        broker = self._make_broker()
        outcome = TimeoutOutcome(
            classification="FILLED", fill_qty=5, fill_price=101.0,
            fill_fee=1.0, cancel_ok=True, still_pending=True,
            detail="race_full_fill",
        )
        order = Order(ticker="005930.KS", action=OrderAction.BUY, quantity=5, price=100.0)
        exe = broker._outcome_to_execution(outcome, order, "ODNO7", 100.0)
        assert exe.status == ExecutionStatus.FILLED
        assert exe.quantity == 5
        assert exe.price == 101.0
        assert "race_full_fill" in exe.reason


class TestKisDomesticQueryFillDetailsMultiRow:
    @patch("src.infra.broker.kis_domestic._pkg.requests.get")
    def test_multi_row_warning(self, mock_get):
        from datetime import datetime, timedelta
        logger = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "rt_cd": "0",
            "output1": [
                {"odno": "X", "avg_prvs": "100", "tot_ccld_qty": "3",
                 "tot_ccld_amt": "300"},
                {"odno": "X", "avg_prvs": "100", "tot_ccld_qty": "5",
                 "tot_ccld_amt": "500"},
            ]
        }
        mock_get.return_value = mock_resp

        with patch.object(KisDomesticPaperBroker, "_auth", return_value="t"):
            broker = KisDomesticPaperBroker("k", "s", "12345678AB", logger)
            broker.token_expires_at = datetime.now() + timedelta(hours=1)
            price, qty, fee = broker._query_fill_details("X", "005930.KS")

        # 첫 매칭 row 사용 -> qty=3
        assert qty == 3
        assert price == 100.0
        # warning 호출 확인
        warns = [c.args[0] for c in logger.warning.call_args_list]
        assert any("returned 2 rows" in w for w in warns)
