# src/config.py
import os
from dotenv import load_dotenv

load_dotenv()

# 티커별 거래소 단축 코드 (현재가 조회 API용)
# config.json의 종목 추가 시 StrategyConfig에서 동적으로 확장됨
TICKER_EXCHANGE_MAP: dict[str, str] = {
    'SPY': 'AMS',
    'QQQ': 'NAS',
    'AAPL': 'NAS',
    'MSFT': 'NAS',
    'GOOGL': 'NAS',
    'AMZN': 'NAS',
    'TSLA': 'NAS',
    'NVDA': 'NAS',
    'META': 'NAS',
}

# 단축 코드 → 주문/잔고/미체결 API용 전체 코드 변환
EXCHANGE_CODE_SHORT_TO_FULL: dict[str, str] = {
    'NAS': 'NASD',
    'NYS': 'NYSE',
    'AMS': 'AMEX',
}


class Config:
    def __init__(self):
        # 멀티 계좌 설정 파일 경로
        self.ACCOUNTS_CONFIG_PATH = os.getenv("ACCOUNTS_CONFIG_PATH", "accounts.yaml")

        # 알림
        self.SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

        # 종목별 매매 규칙 설정 파일 경로
        self.CONFIG_JSON_PATH = os.getenv("CONFIG_JSON_PATH", "config.json")

        # 데이터 경로
        self.DATA_PATH = "docs/data"
        self.LOG_PATH = "logs"

        # 저장소 크기 제한
        self.MAX_HISTORY_RECORDS = 100000
