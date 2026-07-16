# src/infra/broker/upbit.py
"""업비트(Upbit) 코인 브로커.

업비트 REST API(https://api.upbit.com)를 사용해 KRW 마켓 코인을 매매한다.
KIS 브로커와 달리 요청마다 JWT 서명을 만들며(토큰 발급/캐시 불필요),
수량은 소수점을 그대로 사용한다.

인증(JWT):
- 파라미터 없음: payload = {access_key, nonce}
- 파라미터 있음: query_hash = SHA512(쿼리스트링), query_hash_alg = "SHA512" 추가
- jwt.encode(payload, secret, "HS256") -> "Authorization: Bearer <token>"

테스트 호환을 위해 KIS와 동일하게 src.infra.broker.requests(=_pkg.requests)를 사용한다.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid as _uuid
from datetime import datetime
from typing import Dict, List, Optional, Set
from urllib.parse import urlencode

import src.infra.broker as _pkg  # test patch 타깃: src.infra.broker.requests
from src.config import DEFAULT_HTTP_TIMEOUT
from src.core.interfaces import IBrokerAdapter
from src.core.models import (
    Portfolio, Order, TradeExecution, OrderAction, ExecutionStatus,
)

# 업비트 KRW 마켓 최소 주문 금액(원)
MIN_ORDER_KRW = 5000.0
# 업비트 KRW 마켓 기본 수수료율(0.05%). 페이퍼 시뮬레이션 및 fallback 용.
DEFAULT_FEE_RATE = 0.0005


def _fmt_num(value: float) -> str:
    """지수표기(1e-05) 없이 최대 8자리 소수 문자열로 포맷한다.

    예: 0.00066666 -> "0.00066666", 99999.0 -> "99999".
    """
    s = f"{value:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _b64url(data: bytes) -> str:
    """패딩 없는 base64url 인코딩 (JWT 세그먼트용)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_jwt_hs256(payload: dict, secret: str) -> str:
    """HS256(HMAC-SHA256) JWT를 생성한다.

    업비트는 HS256만 요구하므로 외부 의존성(PyJWT/cryptography) 없이
    표준 라이브러리(hmac/hashlib/base64)만으로 서명한다.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    segments = [
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    segments.append(_b64url(signature))
    return ".".join(segments)


class UpbitBroker(IBrokerAdapter):
    """업비트 실거래 브로커 (KRW 마켓 시장가 주문)."""

    BASE_URL = "https://api.upbit.com"

    # 레이트리밋 방어용 기본값 (주문 API ~8건/초, 시세 ~30건/초).
    MIN_REQUEST_INTERVAL = 0.1
    RATE_LIMIT_RETRIES = 3
    RATE_LIMIT_BACKOFF = 0.5
    # 주문 체결 폴링
    POLL_ATTEMPTS = 10
    POLL_INTERVAL = 0.5

    def __init__(self, access_key: str, secret_key: str, logger):
        # 페이퍼 브로커도 실계좌(잔고/시세)를 읽으므로 키가 필수 -> 구동 시점에 즉시 검증
        if not access_key or not secret_key:
            raise ValueError(
                "[Upbit] API Access Key와 Secret Key가 필요합니다. "
                "UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 환경변수를 확인하세요."
            )
        self.access_key = access_key
        self.secret_key = secret_key
        self.logger = logger
        self.session = _pkg.requests.Session()
        self._last_request_ts = 0.0

    # ------------------------------------------------------------------ #
    # 인증 / HTTP
    # ------------------------------------------------------------------ #
    def _jwt_headers(self, params: Optional[dict] = None) -> dict:
        """요청용 JWT Authorization 헤더를 생성한다."""
        payload = {
            "access_key": self.access_key,
            "nonce": str(_uuid.uuid4()),
        }
        if params:
            query = urlencode(params)
            payload["query_hash"] = hashlib.sha512(query.encode()).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        token = encode_jwt_hs256(payload, self.secret_key)
        return {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, url: str, **kwargs):
        """테스트 Mock 과 실제 Session 을 전환하여 호출하는 헬퍼.

        실제 호출은 최소 간격을 강제하고 HTTP 429 응답은 지수 백오프로 재시도한다.
        """
        from unittest.mock import MagicMock
        target_fn = getattr(_pkg.requests, method.lower())

        # 테스트 환경: src.infra.broker.requests.get/post 가 Mock 이면 그대로 호출
        if isinstance(target_fn, MagicMock) or hasattr(target_fn, "assert_called"):
            return target_fn(url, **kwargs)

        session_fn = getattr(self.session, method.lower())
        res = None
        for attempt in range(self.RATE_LIMIT_RETRIES + 1):
            self._throttle()
            res = session_fn(url, **kwargs)
            if getattr(res, "status_code", None) != 429:
                return res
            if attempt < self.RATE_LIMIT_RETRIES:
                backoff = self.RATE_LIMIT_BACKOFF * (2 ** attempt)
                self.logger.warning(
                    f"[Upbit] 429 Too Many Requests — {backoff:.1f}s 대기 후 재시도 "
                    f"({attempt + 1}/{self.RATE_LIMIT_RETRIES})"
                )
                time.sleep(backoff)
        return res

    def _throttle(self) -> None:
        if self.MIN_REQUEST_INTERVAL <= 0:
            return
        wait = self.MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    # ------------------------------------------------------------------ #
    # 조회
    # ------------------------------------------------------------------ #
    def fetch_current_prices(self, tickers: List[str]) -> Dict[str, float]:
        """현재가 조회 (무인증 시세 API, 1회 호출로 다건 조회)."""
        prices: Dict[str, float] = {t: 0.0 for t in tickers}
        if not tickers:
            return prices
        url = f"{self.BASE_URL}/v1/ticker"
        try:
            res = self._request(
                "GET", url,
                params={"markets": ",".join(tickers)},
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            res.raise_for_status()
            data = res.json()
            if isinstance(data, dict) and data.get("error"):
                self.logger.warning(f"[Upbit] Price fetch error: {data['error']}")
                return prices
            if not isinstance(data, list):
                self.logger.warning(f"[Upbit] Unexpected ticker response: {data}")
                return prices
            for item in data:
                if not isinstance(item, dict):
                    continue
                market = item.get("market")
                if market:
                    prices[market] = float(item.get("trade_price", 0) or 0)
        except Exception as e:
            self.logger.error(f"[Upbit] Price fetch error {tickers}: {e}")
        return prices

    def _active_markets(self) -> Optional[Set[str]]:
        """거래 가능한 전체 마켓 코드 집합을 조회한다 (무인증 GET /v1/market/all).

        보유 코인이 상폐/미상장인지 판별하는 데 쓴다. 실패 시 None 을 반환하고,
        호출측은 필터링을 건너뛴다(정상 코인을 상폐로 오분류하지 않기 위함).
        """
        url = f"{self.BASE_URL}/v1/market/all"
        try:
            res = self._request("GET", url, timeout=DEFAULT_HTTP_TIMEOUT)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            self.logger.error(f"[Upbit] market/all fetch error: {e}")
            return None
        if not isinstance(data, list):
            self.logger.warning(f"[Upbit] Unexpected market/all response: {data}")
            return None
        return {m.get("market") for m in data if isinstance(m, dict) and m.get("market")}

    def get_portfolio(self) -> Portfolio:
        """전체 계좌 조회 -> Portfolio.

        KRW 통화의 balance 를 현금으로, 그 외 통화(balance+locked)를 보유수량으로 매핑한다.
        거래 가능 마켓 목록에 없는 보유분(상폐/KRW미상장)은 holdings 에서 제외한다.
        보유 코인의 현재가는 시세 API로 채운다.
        """
        url = f"{self.BASE_URL}/v1/accounts"
        headers = self._jwt_headers()
        try:
            res = self._request("GET", url, headers=headers, timeout=DEFAULT_HTTP_TIMEOUT)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            raise RuntimeError(f"[Upbit] Error getting portfolio: {e}") from e

        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"[Upbit] portfolio fetch failed: {data['error']}")
        if not isinstance(data, list):
            raise RuntimeError(f"[Upbit] unexpected accounts response: {data}")

        total_cash = 0.0
        holdings: Dict[str, float] = {}
        for acc in data:
            currency = acc.get("currency")
            balance = float(acc.get("balance", 0) or 0)
            locked = float(acc.get("locked", 0) or 0)
            if currency == "KRW":
                total_cash = balance
            else:
                qty = balance + locked
                if qty > 0:
                    holdings[f"KRW-{currency}"] = qty

        # 상폐/KRW미상장 보유분 제외 — 거래 가능 마켓에 없는 코인은 holdings 에서 뺀다.
        # (제외하지 않으면 /v1/ticker 가 잘못된 마켓 하나로 배치 전체를 에러 처리해
        #  정상 코인 현재가까지 0 이 되는 문제가 있음)
        if holdings:
            active = self._active_markets()
            # 빈 집합(비정상 응답)은 필터를 건너뛴다 — 정상 보유를 통째로 지우지 않도록.
            if active:
                delisted = sorted(m for m in holdings if m not in active)
                if delisted:
                    self.logger.warning(
                        f"[Upbit] 상폐/KRW미상장 보유 제외 ({len(delisted)}건): {delisted}"
                    )
                    holdings = {m: q for m, q in holdings.items() if m in active}

        prices = self.fetch_current_prices(list(holdings.keys())) if holdings else {}
        return Portfolio(total_cash=total_cash, holdings=holdings, current_prices=prices)

    # ------------------------------------------------------------------ #
    # 주문
    # ------------------------------------------------------------------ #
    def execute_orders(self, orders: List[Order]) -> List[TradeExecution]:
        """주문 실행 — 매도 우선(자금 확보 후 매수)."""
        executions: List[TradeExecution] = []
        sell_orders = [o for o in orders if o.action == OrderAction.SELL]
        buy_orders = [o for o in orders if o.action == OrderAction.BUY]

        if sell_orders:
            self.logger.info(f"[Upbit] Processing {len(sell_orders)} SELL orders...")
            for order in sell_orders:
                res = self._send_order_and_wait(order)
                if res:
                    executions.append(res)
                time.sleep(self.MIN_REQUEST_INTERVAL)

        if buy_orders:
            self.logger.info(f"[Upbit] Processing {len(buy_orders)} BUY orders...")
            for order in buy_orders:
                res = self._send_order_and_wait(order)
                if res:
                    executions.append(res)
                time.sleep(self.MIN_REQUEST_INTERVAL)

        return executions

    def _reject(self, order: Order, reason: str) -> TradeExecution:
        self.logger.warning(f"[Upbit] REJECTED {order.action} {order.ticker}: {reason}")
        return TradeExecution(
            ticker=order.ticker, action=order.action, quantity=0,
            price=order.price, fee=0.0,
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status=ExecutionStatus.REJECTED, reason=reason,
        )

    def _build_order_params(self, order: Order) -> tuple:
        """주문 파라미터와 최소금액 위반 사유를 반환한다. (params, reject_reason)."""
        if order.action == OrderAction.BUY:
            krw_total = order.quantity * order.price
            if krw_total < MIN_ORDER_KRW:
                return None, f"최소 주문 금액 미달 ({krw_total:.0f} < {MIN_ORDER_KRW:.0f} KRW)"
            # KRW는 정수 통화 -> 시장가 매수 금액은 정수로 전달 (소수점 시 API 검증 오류)
            params = {
                "market": order.ticker,
                "side": "bid",
                "ord_type": "price",
                "price": str(int(round(krw_total))),
            }
            return params, None
        # 매도 (시장가)
        est_value = order.quantity * order.price
        if est_value < MIN_ORDER_KRW:
            return None, f"최소 주문 금액 미달 ({est_value:.0f} < {MIN_ORDER_KRW:.0f} KRW)"
        params = {
            "market": order.ticker,
            "side": "ask",
            "ord_type": "market",
            "volume": _fmt_num(order.quantity),
        }
        return params, None

    def _send_order_and_wait(self, order: Order) -> Optional[TradeExecution]:
        """단일 주문 전송 후 체결 조회."""
        params, reject_reason = self._build_order_params(order)
        if reject_reason:
            return self._reject(order, reject_reason)

        url = f"{self.BASE_URL}/v1/orders"
        headers = self._jwt_headers(params)
        try:
            res = self._request("POST", url, params=params, headers=headers,
                                timeout=DEFAULT_HTTP_TIMEOUT)
            res.raise_for_status()
            resp = res.json()
        except Exception as e:
            self.logger.error(f"[Upbit] Order error {order.action} {order.ticker}: {e}")
            return None

        if isinstance(resp, dict) and resp.get("error"):
            self.logger.error(f"[Upbit] Order failed {order.ticker}: {resp['error']}")
            return None
        order_uuid = resp.get("uuid") if isinstance(resp, dict) else None
        if not order_uuid:
            self.logger.error(f"[Upbit] Order response missing uuid: {resp}")
            return None

        self.logger.info(
            f"[Upbit] Order sent: {order.action} {order.ticker} "
            f"qty={order.quantity} (uuid={order_uuid})"
        )
        detail = self._poll_order(order_uuid)
        executed_vol, avg_price, fee = self._extract_fill(detail or resp, order)

        if executed_vol > 0:
            self.logger.info(
                f"[Upbit] Order FILLED: {order.ticker} uuid={order_uuid} "
                f"qty={executed_vol} @ {avg_price} fee={fee}"
            )
            return TradeExecution(
                ticker=order.ticker, action=order.action, quantity=executed_vol,
                price=avg_price, fee=fee,
                date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                status=ExecutionStatus.FILLED, reason=f"uuid={order_uuid}",
            )

        # 체결 수량 0. 거래소가 종료 상태(done/cancel)로 확정했으면 안전하게 REJECTED,
        # 확정 못 한(폴링 실패/여전히 wait) 경우는 ORDERED 로 남겨 수동 확인을 유도한다.
        # (주문은 수락됐으나 체결 여부를 못 밝힌 상태를 REJECTED 로 처리하면
        #  실제 체결 시 로컬 포지션과 거래소 잔고가 어긋날 수 있음)
        terminal = isinstance(detail, dict) and detail.get("state") in ("done", "cancel")
        if terminal:
            self.logger.warning(
                f"[Upbit] Order NOT filled (terminal): {order.ticker} uuid={order_uuid}"
            )
            return TradeExecution(
                ticker=order.ticker, action=order.action, quantity=0,
                price=order.price, fee=0.0,
                date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                status=ExecutionStatus.REJECTED, reason=f"uuid={order_uuid} not filled",
            )
        self.logger.error(
            f"[Upbit] Order UNCONFIRMED — 수동 확인 필요: {order.ticker} uuid={order_uuid}"
        )
        return TradeExecution(
            ticker=order.ticker, action=order.action, quantity=0,
            price=order.price, fee=0.0,
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status=ExecutionStatus.ORDERED, reason=f"uuid={order_uuid} unconfirmed",
        )

    def _poll_order(self, order_uuid: str) -> Optional[dict]:
        """개별 주문 조회를 반복해 체결 완료(done/cancel)를 기다린다.

        일시적 네트워크 오류로 한 번 조회에 실패해도 폴링을 중단하지 않고
        다음 시도로 넘어간다 (체결 여부 미확정으로 오판하는 것을 방지).
        """
        url = f"{self.BASE_URL}/v1/order"
        params = {"uuid": order_uuid}
        detail = None
        for _ in range(self.POLL_ATTEMPTS):
            headers = self._jwt_headers(params)
            try:
                res = self._request("GET", url, params=params, headers=headers,
                                    timeout=DEFAULT_HTTP_TIMEOUT)
                res.raise_for_status()
                detail = res.json()
            except Exception as e:
                self.logger.warning(f"[Upbit] Order query error uuid={order_uuid}: {e}")
                time.sleep(self.POLL_INTERVAL)
                continue
            if isinstance(detail, dict) and detail.get("state") in ("done", "cancel"):
                return detail
            time.sleep(self.POLL_INTERVAL)
        return detail

    @staticmethod
    def _extract_fill(detail: Optional[dict], order: Order) -> tuple:
        """주문 상세에서 (체결수량, 평균단가, 수수료)를 추출한다."""
        if not isinstance(detail, dict):
            return 0.0, order.price, 0.0
        executed_vol = float(detail.get("executed_volume", 0) or 0)
        paid_fee = float(detail.get("paid_fee", 0) or 0)
        trades = detail.get("trades") or []
        total_funds = sum(float(t.get("funds", 0) or 0) for t in trades)
        total_vol = sum(float(t.get("volume", 0) or 0) for t in trades)
        if total_vol > 0:
            avg_price = total_funds / total_vol
        else:
            avg_price = order.price
        return executed_vol, avg_price, paid_fee


class UpbitLiveBroker(UpbitBroker):
    """업비트 실거래 브로커."""


class UpbitPaperBroker(UpbitBroker):
    """업비트 페이퍼(모의) 브로커.

    업비트에는 모의투자 서버가 없으므로, 잔고/시세는 실계좌를 그대로 읽되(읽기 전용)
    주문은 실제로 내지 않고 현재가 기준으로 체결을 시뮬레이션한다.
    """

    def _send_order_and_wait(self, order: Order) -> Optional[TradeExecution]:
        _, reject_reason = self._build_order_params(order)
        if reject_reason:
            return self._reject(order, reject_reason)

        exec_price = order.price
        amount = order.quantity * exec_price
        fee = amount * DEFAULT_FEE_RATE
        self.logger.info(
            f"[UpbitPaper] SIMULATED {order.action} {order.ticker} "
            f"qty={order.quantity} @ {exec_price} fee={fee:.2f}"
        )
        return TradeExecution(
            ticker=order.ticker, action=order.action, quantity=order.quantity,
            price=exec_price, fee=fee,
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status=ExecutionStatus.FILLED, reason="paper-simulated",
        )
