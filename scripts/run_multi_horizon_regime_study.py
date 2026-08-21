"""Run a controlled ON/OFF study for the multi-horizon regime layer.

Example:
    python scripts/run_multi_horizon_regime_study.py --initial-cash 1000000000
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.multi_horizon_study import (
    VALID_MODES,
    build_multi_horizon_study_config,
    render_markdown,
    summarize_policy_activity,
)
from src.backtest.partial_sell_study import DEFAULT_PERIODS, parse_period, summarize_history
from src.backtest.runner import run_backtest
from src.strategy_config import StrategyConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="장·단기 추세 3층 레이어 ON/OFF 비교")
    parser.add_argument("--config", type=Path, default=Path("config_test_domestic.json"))
    parser.add_argument("--initial-cash", type=float, default=1_000_000_000)
    parser.add_argument("--modes", default="off,on", help="off,on 중 쉼표로 구분")
    parser.add_argument("--period", action="append", default=None, help="label:YYYY-MM-DD:YYYY-MM-DD")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("docs/data/backtest-studies/multi-horizon-regime"),
    )
    parser.add_argument("--keep-charts", action="store_true", help="시나리오별 차트 JSON을 보관")
    args = parser.parse_args()

    modes = tuple(value.strip().lower() for value in args.modes.split(",") if value.strip())
    if not modes or set(modes) - set(VALID_MODES):
        parser.error("modes는 off,on만 허용합니다.")
    if set(modes) != set(VALID_MODES):
        parser.error("통제 비교를 위해 off,on 두 군을 함께 실행해야 합니다.")
    periods = tuple(parse_period(value) for value in args.period) if args.period else DEFAULT_PERIODS

    source = json.loads(args.config.read_text(encoding="utf-8"))
    output_dir = args.output_dir
    config_dir = output_dir / "_configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    presets_source = args.config.parent / "presets.json"
    if not presets_source.exists():
        raise FileNotFoundError(f"설정과 같은 경로에 presets.json이 필요합니다: {presets_source}")
    shutil.copy2(presets_source, config_dir / "presets.json")
    rows = []

    for period in periods:
        for mode in modes:
            enabled = mode == "on"
            config = build_multi_horizon_study_config(source, enabled)
            config_path = config_dir / f"{period.label}_{mode}.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # Ensure a future config override cannot silently invalidate A/B parity.
            rules = StrategyConfig(str(config_path)).get_rules_by_market("domestic")
            if not rules or any(rule.multi_horizon_regime_enabled != enabled for rule in rules):
                raise RuntimeError(f"{period.label}/{mode}: 3층 레짐 플래그 검증 실패")

            scenario_dir = output_dir / period.label / mode
            result = run_backtest(
                config_path=str(config_path), start_date=period.start, end_date=period.end,
                initial_cash=args.initial_cash, market_type="domestic", output_dir=str(scenario_dir),
                run_number=f"multi_horizon_{period.label}_{mode}",
            )
            if result is None:
                raise RuntimeError(f"{period.label}/{mode} 백테스트가 결과 없이 종료되었습니다.")

            history = json.loads((scenario_dir / "history.json").read_text(encoding="utf-8"))
            rows.append({
                "period": period.label,
                "start": period.start,
                "end": period.end,
                "multi_horizon_regime_enabled": enabled,
                **summarize_history(history, args.initial_cash),
                **summarize_policy_activity(history),
                "final_value": result.final_portfolio.total_value,
                "final_return_pct": (result.final_portfolio.total_value / args.initial_cash - 1.0) * 100,
            })
            if not args.keep_charts:
                shutil.rmtree(scenario_dir / "charts", ignore_errors=True)

    payload = {"modes": modes, "periods": [period.__dict__ for period in periods], "results": rows}
    (output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "summary.md").write_text(render_markdown(rows), encoding="utf-8")
    print((output_dir / "summary.md").as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
