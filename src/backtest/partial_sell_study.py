"""Helpers for repeatable trend-break partial-sell parameter studies."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable


DEFAULT_CANDIDATES = (50.0, 60.0, 70.0)


@dataclass(frozen=True)
class StudyPeriod:
    """A labelled, out-of-sample-friendly backtest interval."""

    label: str
    start: str
    end: str


DEFAULT_PERIODS = (
    StudyPeriod("selection", "2016-01-01", "2022-12-30"),
    StudyPeriod("validation", "2023-01-02", "2026-08-13"),
)


def parse_period(value: str) -> StudyPeriod:
    """Parse ``label:YYYY-MM-DD:YYYY-MM-DD`` CLI input."""
    parts = value.split(":")
    if len(parts) != 3 or not all(parts):
        raise ValueError("기간은 label:YYYY-MM-DD:YYYY-MM-DD 형식이어야 합니다.")
    return StudyPeriod(*parts)


def build_consumer_study_config(
    config: dict[str, Any],
    partial_sell_pct: float,
) -> tuple[dict[str, Any], list[str]]:
    """Return a consumer-only config with an explicit candidate override.

    The override is written directly on each stock instead of mutating a
    preset. Therefore a candidate is isolated from both the checked-in preset
    and the next candidate in the same study run.
    """
    if not 0 <= partial_sell_pct <= 100:
        raise ValueError("partial_sell_pct는 0~100 범위여야 합니다.")

    groups = config.get("universe_groups", {})
    tickers = groups.get("consumer_staples")
    if not tickers:
        raise ValueError("config.universe_groups.consumer_staples가 필요합니다.")

    selected = set(tickers)
    stocks = []
    for stock in config.get("stocks", []):
        if stock.get("ticker") not in selected:
            continue
        candidate = deepcopy(stock)
        candidate["trendbreak_partial_sell_pct"] = partial_sell_pct
        stocks.append(candidate)

    missing = selected - {stock["ticker"] for stock in stocks}
    if missing:
        raise ValueError(f"consumer_staples에 stocks 항목이 없는 티커: {sorted(missing)}")

    study_config = deepcopy(config)
    study_config["stocks"] = stocks
    study_config["universe_groups"] = {"consumer_staples": list(tickers)}
    return study_config, list(tickers)


def _completed_cycle_pnls(history: Iterable[dict[str, Any]]) -> tuple[list[float], list[float]]:
    """Reconstruct closed-cycle and aligned-downtrend P/L from history."""
    active: dict[str, dict[str, float]] = {}
    closed: list[float] = []
    aligned: list[float] = []

    for day in sorted(history, key=lambda item: item.get("date", "")):
        reason = str(day.get("reason", ""))
        touched: set[str] = set()
        for execution in day.get("executions", []):
            if execution.get("status") not in {"FILLED", "PARTIAL"}:
                continue
            ticker = execution.get("ticker")
            action = execution.get("action")
            if not ticker or action not in {"BUY", "SELL"}:
                continue
            quantity = float(execution.get("quantity") or 0)
            gross = quantity * float(execution.get("price") or 0)
            fee = float(execution.get("fee") or 0)
            cycle = active.get(ticker)
            if action == "BUY":
                if cycle is None:
                    cycle = {"quantity": 0.0, "cost": 0.0, "revenue": 0.0}
                    active[ticker] = cycle
                cycle["quantity"] += quantity
                cycle["cost"] += gross + fee
            elif cycle is not None:
                cycle["quantity"] -= quantity
                cycle["revenue"] += gross - fee
            touched.add(ticker)

        for ticker in touched:
            cycle = active.get(ticker)
            if cycle is None or cycle["quantity"] > 1e-7:
                continue
            pnl = cycle["revenue"] - cycle["cost"]
            closed.append(pnl)
            if "장·단기 하락 정렬 전량 청산" in reason and ticker in reason:
                aligned.append(pnl)
            del active[ticker]

    return closed, aligned


def summarize_history(history: list[dict[str, Any]], initial_cash: float) -> dict[str, float | int | None]:
    """Return comparable risk and realised-cycle metrics for one run."""
    values = [float(day["portfolio_value"]) for day in history if day.get("portfolio_value") is not None]
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, value / peak - 1.0)

    cycles, aligned = _completed_cycle_pnls(history)
    wins = [pnl for pnl in cycles if pnl > 0]
    losses = [pnl for pnl in cycles if pnl < 0]
    average_win = sum(wins) / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0

    return {
        "snapshot_final_value": values[-1] if values else None,
        "snapshot_return_pct": ((values[-1] / initial_cash - 1.0) * 100) if values else None,
        "max_drawdown_pct": max_drawdown * 100,
        "closed_cycles": len(cycles),
        "win_rate_pct": (len(wins) / len(cycles) * 100) if cycles else 0.0,
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": (average_win / abs(average_loss)) if average_loss else None,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses else None,
        "closed_cycle_pnl": sum(cycles),
        "aligned_downtrend_cycles": len(aligned),
        "aligned_downtrend_pnl": sum(aligned),
    }


def render_markdown(rows: list[dict[str, Any]]) -> str:
    """Render the compact comparison table committed with the study output."""
    lines = [
        "# 소비재 추세 이탈 부분청산 스터디",
        "",
        "같은 소비재 종목·기간·초기자본에서 `trendbreak_partial_sell_pct`만 바꾼 비교입니다.",
        "`selection` 결과로 후보를 고르고, `validation`은 선택에 사용하지 않는 검증 구간입니다.",
        "",
        "| 구간 | 부분청산 | 최종 평가액 | 수익률 | MDD | 승률 | 손익비 | 하락 정렬 손익 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        def money(key: str) -> str:
            value = row.get(key)
            return "-" if value is None else f"{value:,.0f}"

        def number(key: str) -> str:
            value = row.get(key)
            return "-" if value is None else f"{value:.2f}"

        lines.append(
            "| {period} | {candidate:.0f}% | {value} | {ret} | {mdd} | {win} | {payoff} | {aligned} |".format(
                period=row["period"],
                candidate=row["partial_sell_pct"],
                value=money("final_value"),
                ret=number("final_return_pct") + "%",
                mdd=number("max_drawdown_pct") + "%",
                win=number("win_rate_pct") + "%",
                payoff=number("payoff_ratio"),
                aligned=money("aligned_downtrend_pnl"),
            )
        )
    lines.append("")
    lines.append("최종 평가액·수익률은 백테스트 종료 시점 값이며, MDD는 일별 스냅샷 기준입니다.")
    return "\n".join(lines) + "\n"
