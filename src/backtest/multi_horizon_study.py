"""Helpers for a controlled legacy-channel versus three-layer regime study."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


VALID_MODES = ("off", "on")


def build_multi_horizon_study_config(
    config: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    """Return an A/B config whose only strategy difference is the 3-layer flag.

    Both arms deliberately retain ``regime_enabled=true`` and
    ``regime_algo=channel``.  Therefore ``off`` means the existing 63-bar
    channel strategy, rather than disabling the channel regime altogether.
    """
    study = deepcopy(config)
    global_config = study.setdefault("global", {})
    if not global_config.get("regime_enabled") or global_config.get("regime_algo") != "channel":
        raise ValueError("A/B 스터디는 global.regime_enabled=true 및 regime_algo=channel이 필요합니다.")

    global_config["multi_horizon_regime_enabled"] = enabled
    # Explicitly override per-stock values as well, so every enabled stock is
    # in the same arm even when a future test config adds a stock override.
    for stock in study.get("stocks", []):
        stock["multi_horizon_regime_enabled"] = enabled
    return study


def summarize_policy_activity(history: list[dict[str, Any]]) -> dict[str, int]:
    """Count policy-visible differences from daily decisions and executions."""
    long_lock_blocks = 0
    reentry_gate_blocks = 0
    aligned_liquidations = 0
    buys = 0
    sells = 0

    for day in history:
        reason = str(day.get("reason", ""))
        long_lock_blocks += reason.count("장기 하락 확정 - 신규·추가 매수 차단")
        reentry_gate_blocks += reason.count("전량 청산 후 재진입 대기")
        aligned_liquidations += reason.count("장·단기 하락 정렬 전량 청산")
        for execution in day.get("executions", []):
            if execution.get("status") not in {"FILLED", "PARTIAL"}:
                continue
            if execution.get("action") == "BUY":
                buys += 1
            elif execution.get("action") == "SELL":
                sells += 1

    return {
        "buy_executions": buys,
        "sell_executions": sells,
        "long_downtrend_lock_blocks": long_lock_blocks,
        "reentry_gate_blocks": reentry_gate_blocks,
        "aligned_downtrend_liquidations": aligned_liquidations,
    }


def render_markdown(rows: list[dict[str, Any]]) -> str:
    """Render the committed summary for the two otherwise-identical arms."""
    lines = [
        "# 장·단기 추세 3층 레이어 ON/OFF 스터디",
        "",
        "두 군은 63봉 `channel` 레짐, 종목·기간·초기자본·나머지 설정을 동일하게 유지합니다.",
        "차이는 `multi_horizon_regime_enabled` 하나뿐입니다. OFF는 기존 63봉 채널 방식입니다.",
        "",
        "| 구간 | 3층 | 최종 평가액 | 수익률 | MDD | 승률 | 손익비 | 매수/매도 | 장기하락 차단 | 정렬 청산 | 재진입 대기 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        def number(key: str) -> str:
            value = row.get(key)
            return "-" if value is None else f"{value:.2f}"

        lines.append(
            "| {period} | {mode} | {value:,.0f} | {ret}% | {mdd}% | {win}% | {payoff} | {buys}/{sells} | {lock} | {aligned} | {gate} |".format(
                period=row["period"],
                mode="ON" if row["multi_horizon_regime_enabled"] else "OFF",
                value=row["final_value"],
                ret=number("final_return_pct"),
                mdd=number("max_drawdown_pct"),
                win=number("win_rate_pct"),
                payoff=number("payoff_ratio"),
                buys=row["buy_executions"],
                sells=row["sell_executions"],
                lock=row["long_downtrend_lock_blocks"],
                aligned=row["aligned_downtrend_liquidations"],
                gate=row["reentry_gate_blocks"],
            )
        )
    lines.extend([
        "",
        "장기하락 차단·정렬 청산·재진입 대기는 일별 의사결정 로그의 발생 횟수입니다.",
        "최종 평가액은 백테스트 종료 시점 값이며 MDD는 일별 스냅샷 기준입니다.",
    ])
    return "\n".join(lines) + "\n"
