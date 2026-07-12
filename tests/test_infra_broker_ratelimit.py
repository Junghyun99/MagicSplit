# tests/test_infra_broker_ratelimit.py
"""KIS 중앙 레이트리밋(_throttle) + 초당 거래건수 초과 백오프(_request) 테스트."""
from unittest.mock import MagicMock
import pytest

from src.infra.broker.kis_base import KisBrokerCommon


def _make_broker(min_interval=0.0, retries=3, backoff=0.5):
    """__init__/_auth 우회하고 레이트리밋 속성만 세팅한 브로커."""
    b = KisBrokerCommon.__new__(KisBrokerCommon)
    b.logger = MagicMock()
    b._rl_lock = None  # _throttle이 지연 생성
    b._last_request_ts = 0.0
    b._min_request_interval = min_interval
    b._rate_limit_retries = retries
    b._rate_limit_backoff = backoff
    return b


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {"rt_cd": "0"}
    return r


class TestIsRateLimited:
    def test_http_429(self):
        assert KisBrokerCommon._is_rate_limited(_resp(status=429)) is True

    def test_egw00201_code(self):
        r = _resp(body={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."})
        assert KisBrokerCommon._is_rate_limited(r) is True

    def test_message_match(self):
        r = _resp(body={"rt_cd": "1", "msg_cd": "XXX", "msg1": "초당 거래건수 초과"})
        assert KisBrokerCommon._is_rate_limited(r) is True

    def test_normal_ok(self):
        assert KisBrokerCommon._is_rate_limited(_resp(body={"rt_cd": "0"})) is False

    def test_non_json_body(self):
        r = MagicMock()
        r.status_code = 200
        r.json.side_effect = ValueError("not json")
        assert KisBrokerCommon._is_rate_limited(r) is False


class TestThrottle:
    def test_enforces_min_interval(self, monkeypatch):
        """직전 호출 직후면 min_interval 만큼 sleep 한다."""
        b = _make_broker(min_interval=0.05)
        slept = []
        monkeypatch.setattr("src.infra.broker.kis_base.time.sleep", lambda s: slept.append(s))
        # 첫 호출: last_ts=0, monotonic 큼 -> wait<=0 (sleep 안함)
        b._throttle()
        # 두번째 즉시 호출: 방금 갱신된 last_ts 기준 -> sleep 발생
        b._throttle()
        assert any(s > 0 for s in slept)

    def test_disabled_when_zero_interval(self, monkeypatch):
        b = _make_broker(min_interval=0.0)
        slept = []
        monkeypatch.setattr("src.infra.broker.kis_base.time.sleep", lambda s: slept.append(s))
        b._throttle(); b._throttle()
        assert slept == []


class _FakeSession:
    """미리 정의한 응답 시퀀스를 순서대로 반환하는 세션."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self._responses.pop(0)

    post = get


class TestRequestBackoff:
    def test_retries_on_rate_limit_then_succeeds(self, monkeypatch):
        """EGW00201 응답 후 백오프 재시도하여 최종 성공 응답을 반환한다."""
        # 실제 경로 강제: _pkg.requests.get 이 Mock 이 아니어야 함 (patch 안함)
        monkeypatch.setattr("src.infra.broker.kis_base.time.sleep", lambda s: None)
        limited = _resp(body={"msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
        ok = _resp(body={"rt_cd": "0"})
        b = _make_broker(min_interval=0.0, retries=3, backoff=0.1)
        b.session = _FakeSession([limited, limited, ok])
        res = b._request("GET", "http://x")
        assert res is ok
        assert b.session.calls == 3

    def test_returns_last_response_after_exhausting_retries(self, monkeypatch):
        """재시도를 모두 소진하면 마지막(초과) 응답을 그대로 반환한다 (호출측이 처리)."""
        monkeypatch.setattr("src.infra.broker.kis_base.time.sleep", lambda s: None)
        limited = _resp(body={"msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
        b = _make_broker(min_interval=0.0, retries=2, backoff=0.1)
        b.session = _FakeSession([limited, limited, limited])
        res = b._request("GET", "http://x")
        assert KisBrokerCommon._is_rate_limited(res) is True
        assert b.session.calls == 3  # 최초 1 + 재시도 2

    def test_no_retry_on_success(self, monkeypatch):
        monkeypatch.setattr("src.infra.broker.kis_base.time.sleep", lambda s: None)
        ok = _resp(body={"rt_cd": "0"})
        b = _make_broker(min_interval=0.0, retries=3, backoff=0.1)
        b.session = _FakeSession([ok])
        res = b._request("GET", "http://x")
        assert res is ok
        assert b.session.calls == 1
