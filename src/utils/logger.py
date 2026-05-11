# src/utils/logger.py
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional, Any
from src.core.interfaces import ILogger


class TradeLogger(ILogger):
    def __init__(self, log_dir: str = "logs", run_number: str | None = None):
        os.makedirs(log_dir, exist_ok=True)
        suffix = f"_{run_number}" if run_number else ""
        self.log_file = os.path.join(
            log_dir, f"{datetime.now().strftime('%Y-%m-%d')}{suffix}.log"
        )

        self.logger = logging.getLogger(f"MagicSplit_{run_number}" if run_number else "MagicSplit")
        self.logger.setLevel(logging.INFO)

        # 캡처용 데이터 저장소
        self.captured_logs: List[Dict[str, Any]] = []
        self.current_ticker: Optional[str] = None

        # 중복 핸들러 방지
        if not self.logger.handlers:
            # 1. 파일 핸들러
            fh = logging.FileHandler(self.log_file, encoding='utf-8')
            fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            self.logger.addHandler(fh)

            # 2. 콘솔 핸들러 (GitHub Actions 로그용)
            ch = logging.StreamHandler()
            ch.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
            self.logger.addHandler(ch)

    def set_ticker_context(self, ticker: Optional[str]) -> None:
        self.current_ticker = ticker

    def get_captured_logs(self, ticker: Optional[str] = None) -> List[str]:
        if ticker:
            # 특정 티커 로그만 필터링
            return [item["msg"] for item in self.captured_logs if item["ticker"] == ticker]
        # 전체 로그 반환
        return [item["msg"] for item in self.captured_logs]

    def clear_captured_logs(self) -> None:
        self.captured_logs = []

    def _capture(self, level: str, msg: Any):
        self.captured_logs.append({
            "ticker": self.current_ticker,
            "level": level,
            "msg": f"{msg}"
        })

    def debug(self, msg: Any):
        self.logger.debug(f"{msg}")
        # Debug 로그는 너무 많을 수 있으므로 캡처에서는 제외 (필요시 추가)

    def info(self, msg: Any):
        self.logger.info(f"{msg}")
        self._capture("INFO", msg)

    def warning(self, msg: Any):
        self.logger.warning(f"{msg}")
        self._capture("WARNING", msg)

    def error(self, msg: Any):
        self.logger.error(f"{msg}")
        self._capture("ERROR", msg)
