# src/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# 단축 코드 -> 주문/잔고/미체결 API용 전체 코드 변환
EXCHANGE_CODE_SHORT_TO_FULL: dict[str, str] = {
    'NAS': 'NASD',
    'NYS': 'NYSE',
    'AMS': 'AMEX',
}

def _parse_http_timeout(raw, default: float = 10.0) -> float:
    """KIS_HTTP_TIMEOUT 환경변수 값을 검증한다. 숫자가 아니거나 0 이하면 기본값으로 폴백."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# KIS REST 호출 타임아웃 (초). 미설정 시 무한 대기 방지를 위해 항상 적용된다.
DEFAULT_HTTP_TIMEOUT = _parse_http_timeout(os.getenv("KIS_HTTP_TIMEOUT", "20"))


def _parse_positive_float(raw, default: float) -> float:
    """양수 float 환경변수를 검증한다. 숫자가 아니거나 0 이하(간격은 0 이상)면 기본값."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _parse_positive_int(raw, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


# KIS 초당 호출수 상한: 모든 REST 호출 사이 최소 간격(초). 실전 유량제한(~20건/초)
# 아래로 유지하기 위한 중앙 스로틀. 0이면 간격 제어 비활성화.
KIS_MIN_REQUEST_INTERVAL = _parse_positive_float(
    os.getenv("KIS_MIN_REQUEST_INTERVAL", "0.06"), 0.06
)
# 초당 거래건수 초과(EGW00201)/HTTP 429 응답 시 재시도 횟수와 백오프 기준(초).
KIS_RATE_LIMIT_RETRIES = _parse_positive_int(os.getenv("KIS_RATE_LIMIT_RETRIES", "3"), 3)
KIS_RATE_LIMIT_BACKOFF = _parse_positive_float(
    os.getenv("KIS_RATE_LIMIT_BACKOFF", "0.5"), 0.5
)


class Config:
    def __init__(self):
        # KIS 단일 계좌 인증
        self.KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
        self.KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
        self.KIS_ACC_NO = os.getenv("KIS_ACC_NO", "")
        self.IS_LIVE = os.getenv("IS_LIVE", "false").lower() == "true"

        # 업비트(코인) 인증 — access/secret 키 쌍 (계좌번호 개념 없음)
        self.UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
        self.UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "")

        # 알림
        self.SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
        self.SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
        self.SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

        # 종목별 매매 규칙 설정 파일 경로 (국내/해외 분리: config_domestic.json | config_overseas.json)
        self.CONFIG_JSON_PATH = os.getenv("CONFIG_JSON_PATH", "config_overseas.json")

        # 데이터 경로
        self.DATA_PATH = "docs/data"
        self.LOG_PATH = "logs"

        # 저장소 크기 제한
        self.MAX_HISTORY_RECORDS = 100000

