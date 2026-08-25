"""Run and compare hybrid and trend-only backtests under identical inputs."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest.runner import run_backtest


BUY_FEE_RATE = 0.0025


def _reason_line(record: dict, ticker: str) -> str:
    marker = f"({ticker})"
    return next(
        (line.rstrip(",") for line in record.get("reason", "").splitlines()
         if marker in line),
        "",
    )


def _is_liquidation(reason: str) -> bool:
    return any(token in reason for token in (
        "추세 이탈", "채널 하단 이탈", "단기 채널 하락 전환",
        "추종 데드라인", "장·단기 하락 정렬",
        "상승·단기 횡보 전환",
    ))


def _adjusted_realized(execution: dict) -> float:
    """Stored PnL already includes sell fees; also deduct the allocated buy fee."""
    pnl = float(execution.get("realized_pnl") or 0.0)
    buy_cost = (
        float(execution.get("buy_price") or 0.0)
        * float(execution.get("quantity") or 0.0)
    )
    return pnl - buy_cost * BUY_FEE_RATE


def _event_stats(history: Iterable[dict]) -> dict:
    event_pnl: Dict[tuple, float] = defaultdict(float)
    exit_context_event_pnl: Dict[tuple, float] = defaultdict(float)
    ticker_pnl: Dict[str, float] = defaultdict(float)
    entry_pnl: Dict[str, float] = defaultdict(float)
    entry_buys: Dict[str, int] = defaultdict(int)
    buys = sells = 0

    for record in history:
        for execution in record.get("executions", []):
            action = execution.get("action")
            if action == "BUY":
                buys += 1
                entry_buys[_entry_kind(execution)] += 1
                continue
            if action != "SELL":
                continue
            sells += 1
            ticker = execution.get("ticker", "")
            reason = _reason_line(record, ticker)
            kind = "liquidation" if _is_liquidation(reason) else "sideways"
            pnl = _adjusted_realized(execution)
            event_pnl[(record.get("date", ""), ticker, reason, kind)] += pnl
            if kind == "liquidation":
                context = _exit_context(execution)
                exit_context_event_pnl[
                    (record.get("date", ""), ticker, reason, context)
                ] += pnl
            ticker_pnl[ticker] += pnl

            entry_pnl[_entry_kind(execution)] += pnl

    liquidation_values = [
        pnl for (*_, kind), pnl in event_pnl.items() if kind == "liquidation"
    ]
    sideways_values = [
        pnl for (*_, kind), pnl in event_pnl.items() if kind == "sideways"
    ]
    exit_contexts = {}
    for (*_, context), pnl in exit_context_event_pnl.items():
        stats = exit_contexts.setdefault(context, {
            "events": 0, "loss_events": 0, "pnl": 0.0,
            "gain": 0.0, "loss": 0.0,
        })
        stats["events"] += 1
        stats["loss_events"] += int(pnl < 0)
        stats["pnl"] += pnl
        stats["gain"] += max(pnl, 0.0)
        stats["loss"] += min(pnl, 0.0)
    return {
        "buy_executions": buys,
        "sell_executions": sells,
        "realized_pnl": sum(event_pnl.values()),
        "sideways_pnl": sum(sideways_values),
        "liquidation_gain": sum(v for v in liquidation_values if v > 0),
        "liquidation_loss": sum(v for v in liquidation_values if v < 0),
        "loss_liquidation_events": sum(v < 0 for v in liquidation_values),
        "entry_pnl": dict(entry_pnl),
        "entry_buys": dict(entry_buys),
        "exit_contexts": exit_contexts,
        "worst_tickers": sorted(ticker_pnl.items(), key=lambda item: item[1])[:10],
    }


def _entry_kind(execution: dict) -> str:
    long_regime = execution.get("entry_long_regime")
    short_regime = execution.get("entry_short_regime")
    if long_regime == "uptrend" and short_regime == "uptrend":
        return "aligned_uptrend"
    if long_regime is None or short_regime is None:
        return "unknown"
    return "non_aligned"


def _exit_context(execution: dict) -> tuple:
    return (
        execution.get("exit_trigger") or "legacy_unknown",
        execution.get("exit_long_regime") or "unknown",
        execution.get("exit_short_regime") or "unknown",
    )


def _exit_context_label(context: tuple) -> str:
    trigger, long_regime, short_regime = context
    trigger_label = {
        "channel_lower_break": "채널 하단 이탈",
        "channel_downtrend_transition": "단기 채널 하락 전환",
        "trend_break": "추세 이탈",
        "uptrend_sideways_transition": "상승→횡보 선제청산",
        "legacy_unknown": "과거 호환/불명",
    }.get(trigger, trigger)
    regime_label = {
        "uptrend": "상승",
        "sideways": "횡보",
        "downtrend": "하락",
        "unknown": "불명",
    }
    return (
        f"{trigger_label} · 장기 {regime_label.get(long_regime, long_regime)}"
        f"/단기 {regime_label.get(short_regime, short_regime)}"
    )


def analyze_output(output_dir: Path, initial_cash: float) -> dict:
    snapshots = json.loads((output_dir / "snapshots.json").read_text(encoding="utf-8"))
    history = json.loads((output_dir / "history.json").read_text(encoding="utf-8"))
    status = json.loads((output_dir / "status.json").read_text(encoding="utf-8"))
    if not snapshots:
        raise ValueError(f"No snapshots found in {output_dir}")

    deposits = sum(float(row.get("net_deposit") or 0.0) for row in snapshots)
    final_value = float(snapshots[-1]["portfolio_value"])
    first_day = date.fromisoformat(snapshots[0]["date"])
    last_day = date.fromisoformat(snapshots[-1]["date"])
    years = max((last_day - first_day).days / 365.2425, 1 / 365.2425)
    cagr = (
        (final_value / initial_cash) ** (1 / years) - 1
        if initial_cash > 0 else math.nan
    )

    peak = float(snapshots[0]["portfolio_value"])
    max_drawdown = 0.0
    exposures = []
    for row in snapshots:
        value = float(row["portfolio_value"])
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
        exposures.append(float(row.get("stock_value") or 0.0) / value if value else 0.0)

    open_pnl = 0.0
    for position in status.get("positions", {}).values():
        for lot in position.get("lots", []):
            quantity = float(lot["quantity"])
            buy_price = float(lot["buy_price"])
            current_price = float(lot["current_price"])
            open_pnl += (current_price - buy_price) * quantity
            open_pnl -= buy_price * quantity * BUY_FEE_RATE

    result = {
        "first_date": snapshots[0]["date"],
        "last_date": snapshots[-1]["date"],
        "net_deposit": deposits,
        "final_value": final_value,
        "total_pnl": final_value - initial_cash,
        "total_return": final_value / initial_cash - 1,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "average_exposure": sum(exposures) / len(exposures),
        "max_exposure": max(exposures),
        "open_pnl": open_pnl,
    }
    result.update(_event_stats(history))
    return result


def _fmt_money(value: float) -> str:
    return f"{value:,.0f}원"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _verdict(baseline: dict, trend: dict) -> str:
    cagr_better = trend["cagr"] > baseline["cagr"]
    drawdown_better = trend["max_drawdown"] >= baseline["max_drawdown"]
    liquidation_better = abs(trend["liquidation_loss"]) <= abs(baseline["liquidation_loss"])
    if cagr_better and drawdown_better and liquidation_better:
        return "추세 전용이 CAGR, MDD, 손실청산액을 모두 개선했습니다."
    return (
        "추세 전용이 세 기준(CAGR 개선, MDD 비악화, 손실청산액 감소)을 "
        "동시에 충족하지 못했습니다."
    )


def render_report(baseline: dict, trend: dict, args: argparse.Namespace) -> str:
    fields = [
        ("최종 자산", "final_value", _fmt_money),
        ("총손익", "total_pnl", _fmt_money),
        ("총수익률", "total_return", _fmt_pct),
        ("CAGR", "cagr", _fmt_pct),
        ("MDD", "max_drawdown", _fmt_pct),
        ("평균 투자 비중", "average_exposure", _fmt_pct),
        ("최대 투자 비중", "max_exposure", _fmt_pct),
        ("실현손익", "realized_pnl", _fmt_money),
        ("미실현손익", "open_pnl", _fmt_money),
        ("횡보 매매 손익", "sideways_pnl", _fmt_money),
        ("추세이탈 청산 이익", "liquidation_gain", _fmt_money),
        ("추세이탈 청산 손실", "liquidation_loss", _fmt_money),
        ("손실청산 이벤트", "loss_liquidation_events", lambda v: f"{v:,}건"),
        ("매수 체결", "buy_executions", lambda v: f"{v:,}건"),
        ("매도 lot 체결", "sell_executions", lambda v: f"{v:,}건"),
    ]
    lines = [
        "# 추세 전용 모드 A/B 백테스트",
        "",
        f"- 설정: `{args.config}`",
        f"- 기간: {args.start} ~ {args.end} (실제 마지막 거래일 {trend['last_date']})",
        f"- 초기자금: {_fmt_money(args.initial_cash)}",
        "- 비용: 기존 백테스트 수수료·슬리피지 적용, 실현손익에 매수 수수료 추가 보정",
        "",
        "## 결과",
        "",
        "| 지표 | 혼합 전략 | 추세 전용 | 차이 |",
        "|---|---:|---:|---:|",
    ]
    for label, key, formatter in fields:
        delta = trend[key] - baseline[key]
        lines.append(
            f"| {label} | {formatter(baseline[key])} | {formatter(trend[key])} | {formatter(delta)} |"
        )

    lines.extend([
        "",
        "## 진입 레짐별 실현손익",
        "",
        "| 진입 레짐 | 혼합 전략 | 추세 전용 |",
        "|---|---:|---:|",
    ])
    labels = {
        "aligned_uptrend": "장·단기 상승 정렬",
        "non_aligned": "비정렬(횡보 포함)",
        "unknown": "과거 호환/불명",
    }
    for key in ("aligned_uptrend", "non_aligned", "unknown"):
        lines.append(
            f"| {labels[key]} | {_fmt_money(baseline['entry_pnl'].get(key, 0.0))} "
            f"| {_fmt_money(trend['entry_pnl'].get(key, 0.0))} |"
        )

    lines.extend([
        "",
        "## 청산 당시 장·단기 레짐별 손익",
        "",
        "| 청산 원인·레짐 | 혼합 손익(손실 이벤트/전체) | 추세 전용 손익(손실 이벤트/전체) |",
        "|---|---:|---:|",
    ])
    contexts = sorted(
        set(baseline["exit_contexts"]) | set(trend["exit_contexts"]),
        key=lambda context: _exit_context_label(context),
    )
    for context in contexts:
        base = baseline["exit_contexts"].get(context, {})
        only = trend["exit_contexts"].get(context, {})
        lines.append(
            f"| {_exit_context_label(context)} "
            f"| {_fmt_money(base.get('pnl', 0.0))} "
            f"({base.get('loss_events', 0):,}/{base.get('events', 0):,}) "
            f"| {_fmt_money(only.get('pnl', 0.0))} "
            f"({only.get('loss_events', 0):,}/{only.get('events', 0):,}) |"
        )

    lines.extend([
        "",
        "## 판정",
        "",
        _verdict(baseline, trend),
        "",
        "판정 기준은 추세 전용의 CAGR 개선, MDD 비악화, 손실청산액 감소를 모두 충족하는지입니다.",
        "추세 전용의 비정렬·불명 진입 체결은 0건으로 확인했습니다.",
        "",
        "## 종목별 최악 실현손익",
        "",
        "| 순위 | 혼합 전략 | 추세 전용 |",
        "|---:|---|---|",
    ])
    for index in range(10):
        base_item = baseline["worst_tickers"][index] if index < len(baseline["worst_tickers"]) else ("-", 0)
        trend_item = trend["worst_tickers"][index] if index < len(trend["worst_tickers"]) else ("-", 0)
        lines.append(
            f"| {index + 1} | {base_item[0]} {_fmt_money(base_item[1])} "
            f"| {trend_item[0]} {_fmt_money(trend_item[1])} |"
        )
    return "\n".join(lines) + "\n"


def _write_variant(source: dict, enabled: bool, path: Path) -> None:
    variant = json.loads(json.dumps(source))
    variant.setdefault("global", {})["trend_only_enabled"] = enabled
    # 프리셋이나 종목별 재정의가 있어도 순수한 전체 OFF/ON A/B가 되도록
    # 임시 설정에서는 모든 종목에 플래그를 명시한다.
    for stock in variant.get("stocks", []):
        stock["trend_only_enabled"] = enabled
    path.write_text(json.dumps(variant, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_test_domestic.json")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-08-13")
    parser.add_argument("--initial-cash", type=float, default=1_000_000_000)
    parser.add_argument("--report", type=Path, default=Path("docs/backtest-trend-only-comparison.md"))
    args = parser.parse_args()

    source = json.loads(Path(args.config).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="magicsplit_ab_") as temp_name:
        root = Path(temp_name)
        presets_path = Path(args.config).resolve().parent / "presets.json"
        if presets_path.exists():
            shutil.copyfile(presets_path, root / "presets.json")
        baseline_config = root / "baseline.json"
        trend_config = root / "trend_only.json"
        _write_variant(source, False, baseline_config)
        _write_variant(source, True, trend_config)

        run_backtest(
            str(baseline_config), args.start, args.end, args.initial_cash,
            "domestic", str(root / "baseline"), "ab_baseline",
            quiet=True, buffered_output=True,
        )
        run_backtest(
            str(trend_config), args.start, args.end, args.initial_cash,
            "domestic", str(root / "trend_only"), "ab_trend_only",
            quiet=True, buffered_output=True,
        )
        baseline = analyze_output(root / "baseline", args.initial_cash)
        trend = analyze_output(root / "trend_only", args.initial_cash)
        invalid_trend_buys = (
            trend["entry_buys"].get("non_aligned", 0)
            + trend["entry_buys"].get("unknown", 0)
        )
        if invalid_trend_buys:
            raise AssertionError(
                f"추세 전용에서 비정렬/불명 진입 {invalid_trend_buys}건 발견"
            )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(baseline, trend, args), encoding="utf-8")
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
