# src/strategy_config.py
"""config.json에서 종목별 매매 규칙(StockRule)을 로드한다.

config.json 구조 (단일값 단순 형태):
{
    "stocks": [
        {
            "ticker": "AAPL",
            "exchange": "NAS",
            "market_type": "overseas",
            "buy_threshold_pct": -5.0,
            "sell_threshold_pct": 10.0,
            "buy_amount": 500,
            "max_lots": 10,
            "enabled": true
        }
    ],
    "global": { "check_interval_minutes": 60 }
}

차수별 배열 및 프리셋(공유 파일) 사용 예:
- `presets.json` (repo 루트, 국내/해외 config와 동일 디렉토리에 위치):
  {
      "large_cap_us": {
          "buy_threshold_pcts": [-3, -5, -7, -10],
          "sell_threshold_pcts": [5, 7, 10, 15],
          "buy_amounts": [1000, 1500, 2000, 3000],
          "max_lots": 10
      }
  }
- `config_overseas.json`의 stock 항목:
  { "ticker": "AAPL", "exchange": "NAS", "preset": "large_cap_us",
    "sell_threshold_pcts": [7, 10, 15, 25] }  # 종목 필드가 preset을 override

경로 해석 순서: 생성자 인자 -> 환경변수 `PRESETS_JSON_PATH` -> config 파일 디렉토리/`presets.json`.
프리셋 파일이 없고 어떤 종목도 `preset` 키를 쓰지 않으면 무시된다.
"""
import json
import os
from typing import Dict, List, Optional, Set

from src.core.models import StockRule


class StrategyConfig:
    """config.json 로더.

    config.json을 읽어 종목별 StockRule 리스트를 생성한다.
    """

    def __init__(
        self,
        config_path: str = "config.json",
        presets_path: Optional[str] = None,
    ):
        self.config_path = config_path
        self.presets_path = self._resolve_presets_path(config_path, presets_path)
        self.presets: Dict[str, dict] = {}
        self.rules: List[StockRule] = []
        self.market_types: Set[str] = set()
        self.global_config: dict = {}
        self._load()

    def get_rules_by_market(self, market_type: str) -> List[StockRule]:
        """지정된 market_type에 해당하는 규칙만 반환한다."""
        return [r for r in self.rules if r.market_type == market_type]

    def get_exchange_map(self) -> Dict[str, str]:
        """티커->거래소 단축 코드 맵을 반환한다 (exchange 미지정 종목 제외)."""
        return {r.ticker: r.exchange for r in self.rules if r.exchange}

    @staticmethod
    def _resolve_presets_path(config_path: str, explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        env = os.environ.get("PRESETS_JSON_PATH")
        if env:
            return env
        base_dir = os.path.dirname(config_path) or "."
        return os.path.join(base_dir, "presets.json")

    def _load_presets(self) -> Dict[str, dict]:
        if not os.path.exists(self.presets_path):
            return {}
        with open(self.presets_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(
                f"{self.presets_path}의 최상위는 프리셋 이름->설정 객체여야 합니다."
            )
        return data

    @staticmethod
    def _merge_preset(raw: dict, presets: Dict[str, dict]) -> dict:
        """stock 설정에 preset을 병합한다. stock 필드가 preset을 덮어쓴다."""
        if "preset" not in raw:
            return raw
        name = raw["preset"]
        if name not in presets:
            raise KeyError(
                f"Unknown preset '{name}' (ticker={raw.get('ticker')}). "
                f"presets.json에 정의되어 있어야 합니다."
            )
        merged = {**presets[name], **raw}
        merged.pop("preset", None)
        return merged

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

        # 글로벌 max_exposure_pct (종목별 설정이 없으면 이 값을 상속)
        global_max_exposure = self.global_config.get("max_exposure_pct")

        needs_presets = any("preset" in s for s in raw_stocks)
        if needs_presets and not os.path.exists(self.presets_path):
            raise FileNotFoundError(
                f"프리셋을 참조하는 종목이 있지만 presets 파일을 찾을 수 없습니다: "
                f"{self.presets_path}"
            )
        self.presets = self._load_presets()
        # 파일은 있으나 비어 있거나 참조한 프리셋이 없는 경우는
        # 아래 _merge_preset에서 KeyError로 구체적으로 실패한다.

        for idx, raw in enumerate(raw_stocks):
            merged = self._merge_preset(raw, self.presets)

            ticker = merged.get("ticker")
            if not ticker:
                raise ValueError(f"{self.config_path}[{idx}]: 'ticker' 필드가 필요합니다.")

            exchange = merged.get("exchange") or ""

            market_type = merged.get("market_type", "overseas")
            if market_type not in ("overseas", "domestic"):
                raise ValueError(
                    f"{self.config_path}[{idx}]: market_type은 "
                    f"'overseas' 또는 'domestic'이어야 합니다. got '{market_type}'"
                )

            reentry_raw = merged.get("reentry_guard_pct")
            reentry_guard_pct = (
                float(reentry_raw) if reentry_raw is not None else None
            )

            buy_pcts = merged.get("buy_threshold_pcts")
            sell_pcts = merged.get("sell_threshold_pcts")
            buy_amounts = merged.get("buy_amounts")

            buy_pct = merged.get("buy_threshold_pct")
            sell_pct = merged.get("sell_threshold_pct")
            buy_amount = merged.get("buy_amount")
            trailing_drop = merged.get("trailing_drop_pct")
            trailing_drops = merged.get("trailing_drop_pcts")

            # 배열/단일값 모두 미제공이면 레거시 기본값(-5/10/500) 적용 -> 하위 호환
            if buy_pct is None and not buy_pcts:
                buy_pct = -5.0
            if sell_pct is None and not sell_pcts:
                sell_pct = 10.0
            if buy_amount is None and not buy_amounts:
                buy_amount = 500

            # max_exposure_pct: 개별 설정 > 글로벌 설정 > None(비활성)
            max_exposure_raw = merged.get("max_exposure_pct", global_max_exposure)
            max_exposure_pct = (
                float(max_exposure_raw) if max_exposure_raw is not None else None
            )

            rule = StockRule(
                ticker=ticker,
                buy_threshold_pct=float(buy_pct) if buy_pct is not None else None,
                sell_threshold_pct=float(sell_pct) if sell_pct is not None else None,
                buy_amount=float(buy_amount) if buy_amount is not None else None,
                max_lots=int(merged.get("max_lots", 10)),
                market_type=market_type,
                enabled=bool(merged.get("enabled", True)),
                exchange=exchange,
                reentry_guard_pct=reentry_guard_pct,
                buy_threshold_pcts=[float(x) for x in buy_pcts] if buy_pcts else None,
                sell_threshold_pcts=[float(x) for x in sell_pcts] if sell_pcts else None,
                buy_amounts=[float(x) for x in buy_amounts] if buy_amounts else None,
                trailing_drop_pct=float(trailing_drop) if trailing_drop is not None else None,
                trailing_drop_pcts=[float(x) for x in trailing_drops] if trailing_drops else None,
                max_exposure_pct=max_exposure_pct,
            )
            self.rules.append(rule)
            self.market_types.add(market_type)

