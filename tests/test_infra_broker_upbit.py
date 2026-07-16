import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode
from unittest.mock import patch, MagicMock

import pytest

from src.infra.broker.upbit import (
    UpbitBroker, UpbitLiveBroker, UpbitPaperBroker,
    encode_jwt_hs256, _fmt_num, MIN_ORDER_KRW,
)
from src.core.models import Order, OrderAction, ExecutionStatus
from src.config import DEFAULT_HTTP_TIMEOUT


def _decode_jwt(token: str, secret: str) -> dict:
    """의존성 없이 HS256 JWT를 검증·디코드한다 (테스트용)."""
    header_seg, payload_seg, sig_seg = token.split(".")
    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    got = base64.urlsafe_b64decode(sig_seg + "=" * (-len(sig_seg) % 4))
    assert hmac.compare_digest(expected, got), "JWT 서명 불일치"
    padded = payload_seg + "=" * (-len(payload_seg) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _resp(json_value):
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.status_code = 200
    r.json.return_value = json_value
    return r


@pytest.fixture(autouse=True)
def _no_sleep():
    """폴링/스로틀 sleep 제거로 테스트 가속."""
    with patch("src.infra.broker.upbit.time.sleep", return_value=None):
        yield


def _broker(cls=UpbitBroker):
    return cls("ACCKEY", "SECRET", MagicMock())


class TestFmtNum:
    def test_no_scientific_notation(self):
        assert _fmt_num(0.00066666) == "0.00066666"

    def test_trims_trailing_zeros(self):
        assert _fmt_num(99999.0) == "99999"
        assert _fmt_num(0.1) == "0.1"


class TestJwtAuth:
    def test_encode_jwt_is_valid_hs256(self):
        token = encode_jwt_hs256({"access_key": "A"}, "SECRET")
        payload = _decode_jwt(token, "SECRET")  # 서명 검증 포함
        assert payload["access_key"] == "A"
        assert token.split(".")[0]  # header 세그먼트 존재

    def test_headers_without_params_have_no_query_hash(self):
        headers = _broker()._jwt_headers()
        token = headers["Authorization"].split(" ")[1]
        payload = _decode_jwt(token, "SECRET")
        assert payload["access_key"] == "ACCKEY"
        assert "nonce" in payload
        assert "query_hash" not in payload

    def test_headers_with_params_include_sha512_query_hash(self):
        params = {"market": "KRW-BTC", "side": "bid", "ord_type": "price", "price": "100000"}
        headers = _broker()._jwt_headers(params)
        payload = _decode_jwt(headers["Authorization"].split(" ")[1], "SECRET")
        expected = hashlib.sha512(urlencode(params).encode()).hexdigest()
        assert payload["query_hash"] == expected
        assert payload["query_hash_alg"] == "SHA512"


class TestFetchCurrentPrices:
    @patch("src.infra.broker.upbit._pkg.requests.get")
    def test_maps_trade_price(self, mock_get):
        mock_get.return_value = _resp([
            {"market": "KRW-BTC", "trade_price": 150000000.0},
            {"market": "KRW-ETH", "trade_price": 5000000.0},
        ])
        prices = _broker().fetch_current_prices(["KRW-BTC", "KRW-ETH"])
        assert prices == {"KRW-BTC": 150000000.0, "KRW-ETH": 5000000.0}
        # 단건 호출로 다건 조회 + timeout 전달
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["markets"] == "KRW-BTC,KRW-ETH"
        assert kwargs["timeout"] == DEFAULT_HTTP_TIMEOUT

    def test_empty_tickers_returns_empty(self):
        assert _broker().fetch_current_prices([]) == {}

    @patch("src.infra.broker.upbit._pkg.requests.get")
    def test_missing_market_defaults_to_zero(self, mock_get):
        mock_get.return_value = _resp([{"market": "KRW-BTC", "trade_price": 100.0}])
        prices = _broker().fetch_current_prices(["KRW-BTC", "KRW-XRP"])
        assert prices["KRW-XRP"] == 0.0


class TestGetPortfolio:
    @patch("src.infra.broker.upbit._pkg.requests.get")
    def test_maps_krw_to_cash_and_coins_to_holdings(self, mock_get):
        accounts = _resp([
            {"currency": "KRW", "balance": "1000000.0", "locked": "0"},
            {"currency": "BTC", "balance": "0.0005", "locked": "0.0001"},
        ])
        ticker = _resp([{"market": "KRW-BTC", "trade_price": 150000000.0}])
        mock_get.side_effect = [accounts, ticker]

        pf = _broker().get_portfolio()
        assert pf.total_cash == 1000000.0
        # balance + locked
        assert pf.holdings["KRW-BTC"] == pytest.approx(0.0006)
        assert pf.current_prices["KRW-BTC"] == 150000000.0

    @patch("src.infra.broker.upbit._pkg.requests.get")
    def test_no_holdings_skips_ticker_call(self, mock_get):
        mock_get.return_value = _resp([{"currency": "KRW", "balance": "500.0", "locked": "0"}])
        pf = _broker().get_portfolio()
        assert pf.total_cash == 500.0
        assert pf.holdings == {}
        assert mock_get.call_count == 1  # accounts만 호출

    @patch("src.infra.broker.upbit._pkg.requests.get")
    def test_error_response_raises(self, mock_get):
        mock_get.return_value = _resp({"error": {"message": "invalid key", "name": "invalid_access_key"}})
        with pytest.raises(RuntimeError, match="portfolio fetch failed"):
            _broker().get_portfolio()


class TestExecuteOrders:
    def _fill_detail(self, volume, price):
        return _resp({
            "uuid": "u-1", "state": "done",
            "executed_volume": str(volume),
            "paid_fee": "50.0",
            "trades": [{"price": str(price), "volume": str(volume), "funds": str(volume * price)}],
        })

    @patch("src.infra.broker.upbit._pkg.requests.get")
    @patch("src.infra.broker.upbit._pkg.requests.post")
    def test_buy_order_fills_fractional(self, mock_post, mock_get):
        mock_post.return_value = _resp({"uuid": "u-1", "state": "wait"})
        mock_get.return_value = self._fill_detail(0.00066666, 150000000.0)

        order = Order("KRW-BTC", OrderAction.BUY, 0.00066666, 150000000.0, qty_precision=8)
        execs = _broker().execute_orders([order])

        assert len(execs) == 1
        assert execs[0].status == ExecutionStatus.FILLED
        assert execs[0].quantity == pytest.approx(0.00066666)
        assert execs[0].price == pytest.approx(150000000.0)
        assert execs[0].fee == 50.0
        # 시장가 매수: side=bid, ord_type=price, price=KRW총액
        _, kwargs = mock_post.call_args
        assert kwargs["params"]["side"] == "bid"
        assert kwargs["params"]["ord_type"] == "price"

    @patch("src.infra.broker.upbit._pkg.requests.get")
    @patch("src.infra.broker.upbit._pkg.requests.post")
    def test_sell_order(self, mock_post, mock_get):
        mock_post.return_value = _resp({"uuid": "u-2", "state": "wait"})
        mock_get.return_value = self._fill_detail(0.0005, 170000000.0)

        order = Order("KRW-BTC", OrderAction.SELL, 0.0005, 170000000.0, qty_precision=8)
        execs = _broker().execute_orders([order])

        assert execs[0].status == ExecutionStatus.FILLED
        _, kwargs = mock_post.call_args
        assert kwargs["params"]["side"] == "ask"
        assert kwargs["params"]["ord_type"] == "market"
        assert kwargs["params"]["volume"] == "0.0005"

    @patch("src.infra.broker.upbit._pkg.requests.get")
    @patch("src.infra.broker.upbit._pkg.requests.post")
    def test_sell_executed_before_buy(self, mock_post, mock_get):
        mock_post.return_value = _resp({"uuid": "u", "state": "wait"})
        mock_get.return_value = self._fill_detail(0.1, 100000.0)

        # 두 주문 모두 최소금액(5000 KRW) 이상: 0.1 * 100000 = 10000 KRW
        buy = Order("KRW-XRP", OrderAction.BUY, 0.1, 100000.0, qty_precision=8)
        sell = Order("KRW-BTC", OrderAction.SELL, 0.1, 100000.0, qty_precision=8)
        _broker().execute_orders([buy, sell])

        sides = [c.kwargs["params"]["side"] for c in mock_post.call_args_list]
        assert sides == ["ask", "bid"]  # 매도 먼저

    @patch("src.infra.broker.upbit._pkg.requests.post")
    def test_below_min_order_rejected_without_post(self, mock_post):
        # 0.00001 * 100000 = 1 KRW < 5000 -> 주문 전송 없이 거부
        order = Order("KRW-BTC", OrderAction.BUY, 0.00001, 100000.0, qty_precision=8)
        execs = _broker().execute_orders([order])
        assert execs[0].status == ExecutionStatus.REJECTED
        mock_post.assert_not_called()

    @patch("src.infra.broker.upbit._pkg.requests.get")
    @patch("src.infra.broker.upbit._pkg.requests.post")
    def test_order_error_response_returns_none(self, mock_post, mock_get):
        mock_post.return_value = _resp({"error": {"message": "insufficient funds", "name": "insufficient_funds_bid"}})
        order = Order("KRW-BTC", OrderAction.BUY, 0.001, 150000000.0, qty_precision=8)
        execs = _broker().execute_orders([order])
        assert execs == []  # 실패 주문은 실행 결과에서 제외
        mock_get.assert_not_called()

    @patch("src.infra.broker.upbit._pkg.requests.get")
    @patch("src.infra.broker.upbit._pkg.requests.post")
    def test_not_filled_returns_rejected(self, mock_post, mock_get):
        mock_post.return_value = _resp({"uuid": "u-3", "state": "wait"})
        mock_get.return_value = _resp({"uuid": "u-3", "state": "cancel", "executed_volume": "0"})
        order = Order("KRW-BTC", OrderAction.BUY, 0.001, 150000000.0, qty_precision=8)
        execs = _broker().execute_orders([order])
        assert execs[0].status == ExecutionStatus.REJECTED


class TestRequestAndPolling:
    def test_request_retries_on_429(self):
        b = _broker()
        r429 = MagicMock(status_code=429)
        r200 = _resp({"ok": True})
        # _pkg.requests.get 는 실제 함수(비-Mock) -> _request 가 self.session.get 사용
        b.session.get = MagicMock(side_effect=[r429, r200])
        res = b._request("GET", "http://x")
        assert res is r200
        assert b.session.get.call_count == 2

    @patch("src.infra.broker.upbit._pkg.requests.get")
    def test_poll_waits_until_done(self, mock_get):
        wait = _resp({"state": "wait"})
        done = _resp({"state": "done", "executed_volume": "0.001", "trades": []})
        mock_get.side_effect = [wait, done]
        detail = _broker()._poll_order("u-x")
        assert detail["state"] == "done"
        assert mock_get.call_count == 2


class TestUpbitPaperBroker:
    @patch("src.infra.broker.upbit._pkg.requests.post")
    def test_simulates_without_real_order(self, mock_post):
        order = Order("KRW-BTC", OrderAction.BUY, 0.001, 150000000.0, qty_precision=8)
        execs = _broker(UpbitPaperBroker).execute_orders([order])
        assert execs[0].status == ExecutionStatus.FILLED
        assert execs[0].quantity == pytest.approx(0.001)
        assert execs[0].reason == "paper-simulated"
        mock_post.assert_not_called()  # 실제 주문 전송 없음

    @patch("src.infra.broker.upbit._pkg.requests.post")
    def test_paper_respects_min_order(self, mock_post):
        order = Order("KRW-BTC", OrderAction.BUY, 0.00001, 100000.0, qty_precision=8)
        execs = _broker(UpbitPaperBroker).execute_orders([order])
        assert execs[0].status == ExecutionStatus.REJECTED


class TestCreateBrokerCrypto:
    def test_create_broker_returns_upbit_for_crypto(self):
        from src.main import _create_broker
        live = _create_broker("crypto", True, "", "", "", MagicMock(),
                              upbit_access_key="A", upbit_secret_key="S")
        paper = _create_broker("crypto", False, "", "", "", MagicMock(),
                               upbit_access_key="A", upbit_secret_key="S")
        assert isinstance(live, UpbitLiveBroker)
        assert isinstance(paper, UpbitPaperBroker)
