"""Run a reproducible 50/60/70% consumer partial-sell study.

Example:
    python scripts/run_consumer_partial_sell_study.py --initial-cash 1000000000
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# ``python scripts/...`` 실행에서도 프로젝트 패키지를 찾게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.partial_sell_study import (
    DEFAULT_CANDIDATES,
    DEFAULT_PERIODS,
    build_consumer_study_config,
    parse_period,
    render_markdown,
    summarize_history,
)
from src.backtest.runner import run_backtest


def main() -> int:
    parser = argparse.ArgumentParser(description="소비재 부분청산 후보 비교")
    parser.add_argument("--config", type=Path, default=Path("config_test_domestic.json"))
    parser.add_argument("--initial-cash", type=float, default=1_000_000_000)
    parser.add_argument("--candidates", default="50,60,70", help="쉼표로 구분한 0~100 후보")
    parser.add_argument(
        "--period", action="append", default=None,
        help="label:YYYY-MM-DD:YYYY-MM-DD. 여러 번 지정할 수 있음",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("docs/data/backtest-studies/consumer-partial-sell"),
    )
    parser.add_argument("--keep-charts", action="store_true", help="후보별 차트 JSON을 보관")
    args = parser.parse_args()

    candidates = tuple(float(value.strip()) for value in args.candidates.split(",") if value.strip())
    if not candidates:
        parser.error("적어도 하나의 후보가 필요합니다.")
    if any(candidate not in DEFAULT_CANDIDATES for candidate in candidates):
        parser.error("후보는 사전 등록된 50, 60, 70만 허용합니다. 사후 탐색을 제한하기 위함입니다.")
    periods = tuple(parse_period(value) for value in args.period) if args.period else DEFAULT_PERIODS

    source_config = json.loads(args.config.read_text(encoding="utf-8"))
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    config_dir = output_dir / "_configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    presets_source = args.config.parent / "presets.json"
    if not presets_source.exists():
        raise FileNotFoundError(f"설정과 같은 경로에 presets.json이 필요합니다: {presets_source}")
    shutil.copy2(presets_source, config_dir / "presets.json")
    rows = []

    for period in periods:
        for candidate in candidates:
            config, tickers = build_consumer_study_config(source_config, candidate)
            scenario_dir = output_dir / period.label / f"partial_{candidate:g}"
            scenario_dir.mkdir(parents=True, exist_ok=True)
            # run_backtest clears output_dir before each scenario; keep the
            # generated config outside that directory for auditability.
            config_path = config_dir / f"{period.label}_partial_{candidate:g}.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = run_backtest(
                config_path=str(config_path), start_date=period.start, end_date=period.end,
                initial_cash=args.initial_cash, market_type="domestic", output_dir=str(scenario_dir),
                run_number=f"partial_study_{period.label}_{candidate:g}",
            )
            if result is None:
                raise RuntimeError(f"{period.label} / {candidate:g}% 백테스트가 결과 없이 종료되었습니다.")

            history_path = scenario_dir / "history.json"
            history = json.loads(history_path.read_text(encoding="utf-8"))
            row = {
                "period": period.label,
                "start": period.start,
                "end": period.end,
                "partial_sell_pct": candidate,
                "tickers": tickers,
                **summarize_history(history, args.initial_cash),
                "final_value": result.final_portfolio.total_value,
                "final_return_pct": (result.final_portfolio.total_value / args.initial_cash - 1.0) * 100,
            }
            rows.append(row)
            if not args.keep_charts:
                shutil.rmtree(scenario_dir / "charts", ignore_errors=True)

    payload = {"candidates": candidates, "periods": [period.__dict__ for period in periods], "results": rows}
    (output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "summary.md").write_text(render_markdown(rows), encoding="utf-8")
    print((output_dir / "summary.md").as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
