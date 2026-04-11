# src/strategy_config.py
"""config.json에서 종목별 매매 규칙(StockRule)을 로드한다.

config.json 구조:
{
    "stocks": [
        {
            "ticker": "AAPL",
            "exchange": "NAS",
            "buy_threshold_pct": -5.0,
            "sell_threshold_pct": 10.0,
            "buy_amount": 500,
            "max_lots": 10,
            "enabled": true
        }
    ],
    "global": {
        "check_interval_minutes": 60,
        "notification_enabled": true
    }
}
"""
import json
import os
from typing import List

from src.core.models import StockRule
from src.config import TICKER_EXCHANGE_MAP


class StrategyConfig:
    """config.json 로더.

    config.json을 읽어 종목별 StockRule 리스트를 생성하고,
    종목의 거래소 코드를 TICKER_EXCHANGE_MAP에 동적으로 등록한다.
    """

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.rules: List[StockRule] = []
        self.global_config: dict = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(
                f"매매 규칙 설정 파일을 찾을 수 없습니다: {self.config_path}. "
                f"config.json을 생성하세요."
            )

        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.global_config = data.get("global", {})
        raw_stocks = data.get("stocks", [])

        if not raw_stocks:
            raise ValueError(f"{self.config_path}에 'stocks' 항목이 비어 있습니다.")

        for idx, raw in enumerate(raw_stocks):
            ticker = raw.get("ticker")
            if not ticker:
                raise ValueError(f"{self.config_path}[{idx}]: 'ticker' 필드가 필요합니다.")

            # 거래소 코드 동적 등록
            exchange = raw.get("exchange")
            if exchange and ticker not in TICKER_EXCHANGE_MAP:
                TICKER_EXCHANGE_MAP[ticker] = exchange

            rule = StockRule(
                ticker=ticker,
                buy_threshold_pct=float(raw.get("buy_threshold_pct", -5.0)),
                sell_threshold_pct=float(raw.get("sell_threshold_pct", 10.0)),
                buy_amount=float(raw.get("buy_amount", 500)),
                max_lots=int(raw.get("max_lots", 10)),
                enabled=bool(raw.get("enabled", True)),
            )
            self.rules.append(rule)
