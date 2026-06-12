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
DEFAULT_HTTP_TIMEOUT = _parse_http_timeout(os.getenv("KIS_HTTP_TIMEOUT", "10"))


class Config:
    def __init__(self):
        # KIS 단일 계좌 인증
        self.KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
        self.KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
        self.KIS_ACC_NO = os.getenv("KIS_ACC_NO", "")
        self.IS_LIVE = os.getenv("IS_LIVE", "false").lower() == "true"

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

