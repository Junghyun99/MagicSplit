"""업비트(Upbit) 브로커 라이브(실거래 서버) 통합 테스트.

수동 실행 전용. CI 환경에서 UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY 시크릿이 설정된
'Upbit Broker Live Test' 워크플로우(.github/workflows/upbit-broker-test.yml)
에서 호출된다. 로컬에서 환경변수만 세팅하면 동일하게 실행 가능.

환경변수:
  UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY  - 필수 (실계좌 키)
  RUN_ORDER_TESTS=true                 - U5~U6 (실주문 시나리오) 활성화
  TEST_MARKET=KRW-BTC                  - 주문 테스트 마켓 (기본: KRW-BTC)

readonly tier(U1~U4): 자금 위험 0. RUN_ORDER_TESTS 미설정이어도 실행.
full tier(U5~U6)    : 실제 주문 발생. 시장가는 즉시 체결되므로 체결되지 않는
                      지정가 매수를 넣고 즉시 취소한다(자금 이동 없음, 잠금만).

주의: 프로덕션 브로커(UpbitBroker.execute_orders)는 시장가 주문을 쓴다.
full tier 는 자금 이동을 피하려고 raw 지정가+취소 경로로 인증/주문/취소 왕복만
검증한다(브로커의 실제 인증 코드 _jwt_headers/_request 를 그대로 사용).
"""
import logging
import os
import time

import pytest

from src.core.models import Portfolio
from src.config import DEFAULT_HTTP_TIMEOUT
from src.infra.broker.upbit import UpbitLiveBroker


# --- 모듈 가드: 자격증명 없으면 전체 skip ---
_REQUIRED = ("UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY")
pytestmark = pytest.mark.skipif(
    not all(os.getenv(k) for k in _REQUIRED),
    reason=f"{_REQUIRED} 환경변수 미설정 — 라이브 테스트 skip",
)

DEFAULT_TEST_MARKET = "KRW-BTC"
READONLY_PRICE_MARKETS = ["KRW-BTC", "KRW-ETH"]
LIMIT_DROP_PCT = 0.5   # 현재가 대비 -50% 지정가 (체결 안 되도록)


def _floor_price_tick(price: float) -> float:
    """업비트 KRW 마켓 호가 단위로 price 를 floor 한다.

    잘못된 호가로 주문 시 API가 거부하므로 유효 tick 으로 내림한다.
    """
    ranges = [
        (2_000_000, 1000),
        (1_000_000, 500),
        (500_000, 100),
        (100_000, 50),
        (10_000, 10),
        (1_000, 5),
        (100, 1),
        (10, 0.1),
        (1, 0.01),
        (0.1, 0.001),
        (0, 0.0001),
    ]
    for lower, tick in ranges:
        if price >= lower:
            # 부동소수 오차 방어: int 변환 전 8자리 반올림 (예: 0.29/0.01=28.9999996 -> 28 오류)
            return int(round(price / tick, 8)) * tick
    return price


def _requires_order_tests():
    return pytest.mark.skipif(
        os.getenv("RUN_ORDER_TESTS", "false").lower() != "true",
        reason="RUN_ORDER_TESTS=true 필요 (실제 주문 발생 시나리오)",
    )


@pytest.fixture(scope="session")
def logger():
    log = logging.getLogger("upbit_live_test")
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        log.addHandler(h)
    return log


@pytest.fixture(scope="session")
def test_market() -> str:
    return os.getenv("TEST_MARKET", DEFAULT_TEST_MARKET)


@pytest.fixture(scope="session")
def broker(logger):
    """UpbitLiveBroker 세션 fixture. 실거래 서버 사용."""
    return UpbitLiveBroker(
        access_key=os.environ["UPBIT_ACCESS_KEY"],
        secret_key=os.environ["UPBIT_SECRET_KEY"],
        logger=logger,
    )


# --- raw API 헬퍼 (브로커의 실제 인증 경로 _jwt_headers/_request 를 사용) ---

def _open_order_uuids(broker, market: str) -> set:
    """열린(미체결) 주문 uuid 집합을 조회한다 (GET /v1/orders/open)."""
    url = f"{broker.BASE_URL}/v1/orders/open"
    params = {"market": market}
    res = broker._request("GET", url, params=params,
                          headers=broker._jwt_headers(params),
                          timeout=DEFAULT_HTTP_TIMEOUT)
    res.raise_for_status()
    data = res.json()
    if not isinstance(data, list):
        return set()
    return {o.get("uuid") for o in data if isinstance(o, dict) and o.get("uuid")}


def _place_unfillable_limit_buy(broker, market: str) -> tuple:
    """체결되지 않을 지정가 매수를 넣고 (uuid, price, volume) 반환.

    현재가 -50% 가격, 최소주문금액(5000 KRW) 이상을 만족하는 최소 수량.
    """
    prices = broker.fetch_current_prices([market])
    cur = prices.get(market, 0)
    assert cur > 0, f"현재가 조회 실패: {market}={cur}"
    price = _floor_price_tick(cur * LIMIT_DROP_PCT)
    assert price > 0
    # 최소주문 5000 KRW 초과하도록 여유 있게 volume 산정 (8자리)
    from src.infra.broker.upbit import MIN_ORDER_KRW, _fmt_num
    volume = round((MIN_ORDER_KRW * 1.2) / price, 8)
    params = {
        "market": market,
        "side": "bid",
        "ord_type": "limit",
        # 소수 호가 단위(1~100원대 tick 0.01/0.1) 소실 방지 -> 전 구간 _fmt_num 사용
        "price": _fmt_num(price),
        "volume": _fmt_num(volume),
    }
    url = f"{broker.BASE_URL}/v1/orders"
    res = broker._request("POST", url, params=params,
                          headers=broker._jwt_headers(params),
                          timeout=DEFAULT_HTTP_TIMEOUT)
    res.raise_for_status()
    body = res.json()
    assert isinstance(body, dict) and not body.get("error"), f"주문 실패: {body}"
    uuid = body.get("uuid")
    assert uuid, f"uuid 미수신: {body}"
    return uuid, price, volume


def _cancel(broker, uuid: str) -> dict:
    """지정가 주문 취소 (DELETE /v1/order)."""
    url = f"{broker.BASE_URL}/v1/order"
    params = {"uuid": uuid}
    res = broker._request("DELETE", url, params=params,
                          headers=broker._jwt_headers(params),
                          timeout=DEFAULT_HTTP_TIMEOUT)
    res.raise_for_status()
    return res.json()


@pytest.fixture(scope="session", autouse=True)
def _cleanup_open_orders(broker, logger, test_market):
    """세션 종료 시 본 테스트가 남긴 미체결을 일괄 취소 (CI 중단·예외 대비)."""
    yield
    if os.getenv("RUN_ORDER_TESTS", "false").lower() != "true":
        return
    try:
        open_uuids = _open_order_uuids(broker, test_market)
    except Exception as e:
        logger.warning(f"[cleanup] 미체결 조회 실패: {e}")
        return
    for uuid in open_uuids:
        try:
            _cancel(broker, uuid)
            logger.warning(f"[cleanup] 미체결 취소: {uuid}")
        except Exception as e:
            logger.warning(f"[cleanup] cancel {uuid} 실패: {e}")


# =====================================================================
# Readonly Tier (U1~U4) — 항상 실행, 자금 위험 0
# =====================================================================

class TestReadonly:
    def test_u1_auth_via_get_portfolio(self, broker):
        """U1. JWT 인증 — 인증이 필요한 계좌 조회가 성공하면 서명이 유효한 것."""
        pf = broker.get_portfolio()
        assert isinstance(pf, Portfolio)
        assert pf.total_cash >= 0, f"total_cash 음수: {pf.total_cash}"
        print(f"  total_cash(KRW)={pf.total_cash:,.0f} holdings={len(pf.holdings)}건")

    def test_u2_fetch_current_prices(self, broker):
        """U2. 현재가 조회 (무인증 시세 API, 다건)."""
        prices = broker.fetch_current_prices(READONLY_PRICE_MARKETS)
        assert set(prices.keys()) == set(READONLY_PRICE_MARKETS)
        for market, price in prices.items():
            assert price > 0, f"{market} 가격이 0 이하: {price}"
            print(f"  {market}: {price:,.0f}")

    def test_u3_get_portfolio_shape(self, broker):
        """U3. 잔고 구조 검증 — holdings/current_prices 타입·수량."""
        pf = broker.get_portfolio()
        assert isinstance(pf.holdings, dict)
        assert isinstance(pf.current_prices, dict)
        for market, qty in pf.holdings.items():
            assert market.startswith("KRW-"), f"보유 마켓 코드 형식 이상: {market}"
            assert qty > 0, f"{market} 수량이 0 이하: {qty}"
            print(f"    {market}: qty={qty} price={pf.current_prices.get(market, 0):,.0f}")

    def test_u4_order_chance_signed_get(self, broker, test_market):
        """U4. 주문가능정보 (GET /v1/orders/chance) — 파라미터 있는 인증 요청.

        query_hash(SHA512) 서명 경로를 실서버로 검증하는 가장 중요한 readonly 테스트.
        """
        url = f"{broker.BASE_URL}/v1/orders/chance"
        params = {"market": test_market}
        res = broker._request("GET", url, params=params,
                              headers=broker._jwt_headers(params),
                              timeout=DEFAULT_HTTP_TIMEOUT)
        res.raise_for_status()
        data = res.json()
        assert isinstance(data, dict) and not data.get("error"), \
            f"orders/chance 실패(서명/권한 확인): {data}"
        assert data.get("market", {}).get("id") == test_market, f"마켓 불일치: {data.get('market')}"
        # 최소주문금액 등 제약 정보 존재
        min_total = data["market"].get("bid", {}).get("min_total")
        print(f"  {test_market} bid_fee={data.get('bid_fee')} min_total={min_total}")


# =====================================================================
# Full Tier (U5~U6) — 실주문 발생, RUN_ORDER_TESTS=true 필요
# =====================================================================

@_requires_order_tests()
class TestFull:
    """체결되지 않는 지정가 매수 -> 미체결 확인 -> 취소. 자금 이동 없음(잠금만)."""

    def test_u5_limit_buy_then_cancel(self, broker, test_market):
        """U5. 지정가 매수 -> 미체결 등록 확인 -> 취소 -> 미체결 해제 확인."""
        uuid, price, volume = _place_unfillable_limit_buy(broker, test_market)
        print(f"  주문 uuid={uuid} price={price:,.0f} volume={volume}")

        try:
            time.sleep(1.0)  # 서버 반영 대기
            open_uuids = _open_order_uuids(broker, test_market)
            assert uuid in open_uuids, f"발주 uuid={uuid} 가 미체결 목록에 없음: {open_uuids}"
            print(f"  미체결 등록 확인 (전체 {len(open_uuids)}건)")
        finally:
            result = _cancel(broker, uuid)
            assert isinstance(result, dict) and not result.get("error"), \
                f"취소 실패: {result}"

        # 취소 반영 대기 후 검증
        deadline = time.time() + 10
        while time.time() < deadline:
            if uuid not in _open_order_uuids(broker, test_market):
                break
            time.sleep(1)
        assert uuid not in _open_order_uuids(broker, test_market), \
            f"취소 후에도 uuid={uuid} 가 미체결에 남음"
        print("  취소 후 미체결 해제 확인")

    def test_u6_cancelled_order_state(self, broker, test_market):
        """U6. 취소된 주문 조회 — _poll_order 가 취소 상태를 정상 파싱하는지."""
        uuid, _, _ = _place_unfillable_limit_buy(broker, test_market)
        time.sleep(1.0)
        _cancel(broker, uuid)
        time.sleep(1.5)

        detail = broker._poll_order(uuid)
        state = detail.get("state") if isinstance(detail, dict) else detail
        print(f"  취소된 주문 상태: {state}")
        assert isinstance(detail, dict)
        # 취소된 주문은 체결 수량 0
        executed = float(detail.get("executed_volume", 0) or 0)
        assert executed == 0, f"체결 안 된 취소 주문인데 executed_volume={executed}"
