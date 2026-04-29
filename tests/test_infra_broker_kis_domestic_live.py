"""KIS 국내 브로커 라이브(실거래 서버) 통합 테스트.

수동 실행 전용. CI 환경에서 KIS_APP_KEY/SECRET/ACC_NO 시크릿이 설정된
'KIS Domestic Broker Live Test' 워크플로우(.github/workflows/kis-domestic-broker-test.yml)
에서 호출된다. 로컬에서 환경변수만 세팅하면 동일하게 실행 가능.

환경변수:
  KIS_APP_KEY / KIS_APP_SECRET / KIS_ACC_NO  - 필수 (실거래 키)
  RUN_ORDER_TESTS=true                       - S6~S8 (실주문 시나리오) 활성화
  TEST_TICKER=069500                         - 주문 테스트 종목 (기본: KODEX 200 ETF)

readonly tier(S1~S5): 자금 위험 0. RUN_ORDER_TESTS 미설정이어도 실행.
full tier(S6~S8)    : 실제 주문 발생. 지정가 매수 후 즉시 취소(미체결) +
                      타임아웃 시나리오. 한국장 운영 시간(09:00~15:30 KST) 권장.
"""
import logging
import os
import time
from typing import List

import pytest

from src.core.models import Order, OrderAction, ExecutionStatus, Portfolio
from src.infra.broker.kis_domestic import KisDomesticLiveBroker, _to_kis_code
from src.infra.broker.kis_token_cache import (
    KIS_TOKEN_CACHE_PATH,
    load_token_from_cache,
)


# --- 모듈 가드: 자격증명 없으면 전체 skip ---
_REQUIRED = ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACC_NO")
pytestmark = pytest.mark.skipif(
    not all(os.getenv(k) for k in _REQUIRED),
    reason=f"{_REQUIRED} 환경변수 미설정 — 라이브 테스트 skip",
)

# --- 상수 ---
DEFAULT_TEST_TICKER = "069500"           # KODEX 200 ETF (~35,000원, 매우 유동성 높음)
READONLY_PRICE_TICKERS = ["005930.KS", "069500.KS"]  # 삼성전자 + KODEX 200
LIMIT_DROP_PCT = 0.95                    # 현재가 대비 -5% 지정가 (체결 안 되도록)
ORDER_TIMEOUT_SHORT = 5                  # S8 타임아웃 시나리오용


def _normalize_ticker(raw: str) -> str:
    """입력이 6자리 코드든 yfinance 티커든 .KS suffix 형태로 통일."""
    if "." in raw:
        return raw
    return f"{raw.zfill(6)}.KS"


def _floor_to_tick(price: int) -> int:
    """KOSPI/KOSDAQ 호가 단위로 floor. ETF는 5원 단위지만 더 큰 단위로 floor 해도 호가 만족."""
    if price < 2000:
        tick = 1
    elif price < 5000:
        tick = 5
    elif price < 20000:
        tick = 10
    elif price < 50000:
        tick = 50
    elif price < 200000:
        tick = 100
    elif price < 500000:
        tick = 500
    else:
        tick = 1000
    return (price // tick) * tick


def _requires_order_tests():
    return pytest.mark.skipif(
        os.getenv("RUN_ORDER_TESTS", "false").lower() != "true",
        reason="RUN_ORDER_TESTS=true 필요 (실제 주문 발생 시나리오)",
    )


@pytest.fixture(scope="session")
def logger():
    log = logging.getLogger("kis_live_test")
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        log.addHandler(h)
    return log


@pytest.fixture(scope="session")
def test_ticker() -> str:
    return _normalize_ticker(os.getenv("TEST_TICKER", DEFAULT_TEST_TICKER))


@pytest.fixture(scope="session")
def broker(logger, test_ticker):
    """KisDomesticLiveBroker 세션 fixture. 실거래 서버 사용."""
    app_key = os.environ["KIS_APP_KEY"]
    app_secret = os.environ["KIS_APP_SECRET"]
    acc_no = os.environ["KIS_ACC_NO"]
    return KisDomesticLiveBroker(
        app_key=app_key,
        app_secret=app_secret,
        acc_no=acc_no,
        logger=logger,
        known_tickers=[test_ticker, "005930.KS", "069500.KS"],
    )


@pytest.fixture(scope="session", autouse=True)
def _cleanup_pending(broker, logger):
    """세션 종료 시 본 테스트가 남긴 미체결을 일괄 취소 (CI 중단·예외 대비)."""
    yield
    try:
        pending = broker._get_pending_order_ids()
    except Exception as e:
        logger.warning(f"[cleanup] pending 조회 실패: {e}")
        return
    if not pending:
        return
    logger.warning(f"[cleanup] 잔여 미체결 {len(pending)}건 — 취소 시도")
    for odno in list(pending):
        try:
            broker._cancel_order(odno, _normalize_ticker(DEFAULT_TEST_TICKER), 1)
        except Exception as e:
            logger.warning(f"[cleanup] cancel ODNO={odno} 실패: {e}")


# =====================================================================
# Readonly Tier (S1~S5) — 항상 실행
# =====================================================================

class TestReadonly:
    """자금 위험 없는 조회 API 5종 검증."""

    def test_s1_auth_and_token_cache(self, broker):
        """S1. 인증 + 토큰 캐시.

        broker 생성 시점에 _auth() 가 이미 호출됨. access_token 비어 있지 않고
        토큰 캐시 파일이 정상적인 expires_at 으로 기록되었는지 검증.
        """
        assert broker.access_token, "access_token 미수신"
        assert broker.token_expires_at is not None, "token_expires_at 미설정"

        # 캐시 파일 존재 + 해당 app_key 항목 유효
        assert os.path.exists(KIS_TOKEN_CACHE_PATH), \
            f"토큰 캐시 파일 없음: {KIS_TOKEN_CACHE_PATH}"
        cached = load_token_from_cache(broker.app_key, broker.logger)
        assert cached is not None, "load_token_from_cache 가 None 반환 (캐시 만료/누락)"
        assert cached["access_token"] == broker.access_token, "캐시된 토큰 불일치"

        # 두 번째 _ensure_token 호출은 캐시 hit (재발급 없음)
        before = broker.access_token
        broker._ensure_token()
        assert broker.access_token == before, "유효 토큰인데 재발급 발생"

    def test_s2_fetch_current_prices(self, broker):
        """S2. 현재가 조회 (FHKST01010100). 다종목 + KOSPI/KOSDAQ 혼합."""
        prices = broker.fetch_current_prices(READONLY_PRICE_TICKERS)
        assert set(prices.keys()) == set(READONLY_PRICE_TICKERS), \
            f"응답 키 불일치: {prices.keys()}"
        for ticker, price in prices.items():
            assert price > 0, f"{ticker} 가격이 0 이하: {price} (장외시간 가능 — 전일종가 fallback 동작 확인 필요)"
            print(f"  {ticker}: {price:,.0f}")

    def test_s3_fetch_asking_price(self, broker, test_ticker):
        """S3. 호가 조회 (FHKST01010200). 스프레드 검증 동시 수행."""
        bid, ask = broker._fetch_asking_price(test_ticker)
        print(f"  {test_ticker} bid={bid} ask={ask}")
        assert bid > 0, f"bid <= 0: {bid} (장외시간 또는 종목 휴장 가능)"
        assert ask > 0, f"ask <= 0: {ask}"
        assert bid <= ask, f"bid({bid}) > ask({ask}) — 데이터 비정상"
        assert broker._check_spread(bid, ask), \
            f"_check_spread 실패: bid={bid} ask={ask} spread > {broker.SPREAD_THRESHOLD_PCT}%"

    def test_s4_get_portfolio(self, broker):
        """S4. 잔고 조회 (TTTC8434R)."""
        pf = broker.get_portfolio()
        assert isinstance(pf, Portfolio)
        assert pf.total_cash >= 0, f"total_cash 음수: {pf.total_cash}"
        assert isinstance(pf.holdings, dict)
        assert isinstance(pf.current_prices, dict)
        print(f"  total_cash={pf.total_cash:,.0f} holdings={len(pf.holdings)}건")
        for ticker, qty in pf.holdings.items():
            assert qty > 0, f"{ticker} 수량이 0 이하: {qty}"
            print(f"    {ticker}: qty={qty} price={pf.current_prices.get(ticker, 0):,.0f}")

    def test_s5_pending_order_inquiry(self, broker):
        """S5. 미체결 조회 (TTTC0084R) — set/int 반환만 검증 (목록은 가변)."""
        ids = broker._get_pending_order_ids()
        assert isinstance(ids, set), f"set 아님: {type(ids)}"
        count = broker._get_pending_orders_count()
        assert isinstance(count, int) and count >= 0
        assert count == len(ids), f"_get_pending_orders_count({count}) != len(ids)({len(ids)})"
        print(f"  pending count={count} ids={ids}")


# =====================================================================
# Full Tier (S6~S8) — 실주문 발생, RUN_ORDER_TESTS=true 필요
# =====================================================================

@_requires_order_tests()
class TestFull:
    """지정가 매수→취소→타임아웃 시나리오. 자금 이동 없음(체결 회피)."""

    def _make_unfillable_buy_price(self, broker, ticker: str) -> int:
        """현재가의 -5%를 호가 단위 floor 한 정수가. 체결되지 않을 매수가."""
        prices = broker.fetch_current_prices([ticker])
        cur = prices.get(ticker, 0)
        assert cur > 0, f"현재가 조회 실패: {ticker}={cur}"
        raw = int(cur * LIMIT_DROP_PCT)
        return _floor_to_tick(raw)

    def _send_raw_buy(self, broker, ticker: str, qty: int, price: int) -> str:
        """_send_order_and_wait 의 폴링 없이 주문만 전송. ODNO 반환.

        S6/S7 에서는 체결 폴링이 불필요하고 즉시 취소가 목적이라
        broker 내부 메서드를 흉내 내어 raw POST 만 수행한다.
        """
        import src.infra.broker as _pkg
        from src.config import DEFAULT_HTTP_TIMEOUT

        url = f"{broker.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {
            "CANO": broker.cano,
            "ACNT_PRDT_CD": broker.acnt_prdt_cd,
            "PDNO": _to_kis_code(ticker),
            "ORD_DVSN": "00",                # 지정가
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        headers = broker._get_header(broker.BUY_TR_ID, data)
        res = _pkg.requests.post(url, headers=headers, json=data, timeout=DEFAULT_HTTP_TIMEOUT)
        res.raise_for_status()
        body = res.json()
        assert body["rt_cd"] == "0", f"주문 실패: {body.get('msg1')} ({body})"
        odno = body.get("output", {}).get("ODNO", "")
        assert odno, f"ODNO 미수신: {body}"
        return odno

    def test_s6_limit_buy_then_cancel(self, broker, test_ticker):
        """S6. 지정가 매수 → 미체결 등록 확인 → 취소 → 미체결 해제 확인."""
        price = self._make_unfillable_buy_price(broker, test_ticker)
        print(f"  주문가(현재가 -5%, tick floor): {price:,}")

        odno = self._send_raw_buy(broker, test_ticker, qty=1, price=price)
        print(f"  ODNO={odno}")

        try:
            # KIS 서버 반영 약간 대기
            time.sleep(1.5)
            pending = broker._get_pending_order_ids()
            assert odno in pending, \
                f"발주한 ODNO={odno} 가 미체결 목록에 없음: {pending}"
            assert broker._get_pending_orders_count() >= 1
            print(f"  미체결 등록 확인 (전체 {len(pending)}건)")
        finally:
            cancelled = broker._cancel_order(odno, test_ticker, 1)
            assert cancelled, f"_cancel_order 실패: ODNO={odno}"

        # 취소 반영 대기 후 검증
        deadline = time.time() + 10
        while time.time() < deadline:
            if odno not in broker._get_pending_order_ids():
                break
            time.sleep(1)
        assert odno not in broker._get_pending_order_ids(), \
            f"취소 후에도 ODNO={odno} 가 미체결에 남음"
        print(f"  취소 후 미체결 해제 확인")

    def test_s7_query_fill_details_for_cancelled(self, broker, test_ticker):
        """S7. 체결조회 (TTTC0081R) — 취소된 ODNO로 호출해도 파싱 깨지지 않아야 함."""
        price = self._make_unfillable_buy_price(broker, test_ticker)
        odno = self._send_raw_buy(broker, test_ticker, qty=1, price=price)
        print(f"  ODNO={odno}")

        try:
            time.sleep(1.5)
            broker._cancel_order(odno, test_ticker, 1)
            time.sleep(2.0)  # 체결조회 응답에 취소건이 반영될 시간

            fill_price, fill_qty, fill_fee = broker._query_fill_details(odno, test_ticker)
            print(f"  fill_price={fill_price} fill_qty={fill_qty} fill_fee={fill_fee}")
            # 체결 안 된 주문 — 모든 값 0 또는 매칭 row 없음으로 (0,0,0) 반환
            assert fill_qty == 0, f"체결 안 된 주문인데 fill_qty={fill_qty}"
            assert fill_price >= 0
            assert fill_fee >= 0
        finally:
            # 혹시 cancel 실패했을 가능성 대비
            if odno in broker._get_pending_order_ids():
                broker._cancel_order(odno, test_ticker, 1)

    def test_s8_send_order_timeout(self, broker, test_ticker):
        """S8. 타임아웃 시나리오 — _send_order_and_wait + resolve_timeout_outcome E2E.

        체결 안 될 지정가로 5초 timeout 발주 -> 자동으로 취소·재폴링 경로 진입 ->
        TradeExecution(REJECTED|PARTIAL|ORDERED|FILLED) 반환.
        """
        prices = broker.fetch_current_prices([test_ticker])
        cur = prices[test_ticker]
        unfillable = _floor_to_tick(int(cur * LIMIT_DROP_PCT))
        print(f"  현재가={cur:,.0f} 주문가={unfillable:,}")

        order = Order(
            ticker=test_ticker,
            action=OrderAction.BUY,
            quantity=1,
            price=float(unfillable),
        )
        execution = broker._send_order_and_wait(order, timeout=ORDER_TIMEOUT_SHORT)
        print(f"  execution: {execution}")

        assert execution is not None, "_send_order_and_wait 가 None 반환 (예외/네트워크 실패)"
        assert execution.status in {
            ExecutionStatus.REJECTED,
            ExecutionStatus.PARTIAL,
            ExecutionStatus.ORDERED,
            ExecutionStatus.FILLED,
        }, f"예상 외 status: {execution.status}"

        # 가장 흔한 결과는 REJECTED (취소 성공 + fill 0). reason 에 ODNO 포함.
        assert "ODNO=" in (execution.reason or ""), \
            f"reason 에 ODNO 누락: {execution.reason}"

        if execution.status == ExecutionStatus.REJECTED:
            assert execution.quantity == 0, \
                f"REJECTED 인데 quantity={execution.quantity}"
        elif execution.status == ExecutionStatus.FILLED:
            assert execution.quantity == order.quantity
            # 의도치 않은 체결 — 주의 로그
            print(f"  WARNING: 의도치 않게 체결됨. quantity={execution.quantity}")

        # teardown — 혹시라도 ORDERED 로 잔존하면 ODNO 추출해 수동 취소
        if execution.status == ExecutionStatus.ORDERED:
            for token in (execution.reason or "").split():
                if token.startswith("ODNO="):
                    odno = token.split("=", 1)[1]
                    print(f"  ORDERED — 잔존 ODNO={odno} 수동 취소")
                    broker._cancel_order(odno, test_ticker, order.quantity)
                    break
