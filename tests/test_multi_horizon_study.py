import pytest

from src.backtest.multi_horizon_study import (
    build_multi_horizon_study_config,
    render_markdown,
    summarize_policy_activity,
)


def test_config_changes_only_multi_horizon_flag_and_copies_per_stock():
    source = {
        "global": {"regime_enabled": True, "regime_algo": "channel", "other": 1},
        "stocks": [{"ticker": "A", "buy_amount": 1}, {"ticker": "B", "buy_amount": 2}],
    }

    study = build_multi_horizon_study_config(source, True)

    assert study["global"]["multi_horizon_regime_enabled"] is True
    assert [stock["multi_horizon_regime_enabled"] for stock in study["stocks"]] == [True, True]
    assert "multi_horizon_regime_enabled" not in source["stocks"][0]
    assert study["global"]["other"] == 1


def test_config_requires_legacy_channel_to_remain_enabled():
    with pytest.raises(ValueError):
        build_multi_horizon_study_config({"global": {"regime_enabled": False}}, False)


def test_policy_activity_counts_only_filled_orders_and_named_policies():
    history = [{
        "reason": "장기 하락 확정 - 신규·추가 매수 차단\n전량 청산 후 재진입 대기\n장·단기 하락 정렬 전량 청산",
        "executions": [
            {"action": "BUY", "status": "FILLED"},
            {"action": "SELL", "status": "PARTIAL"},
            {"action": "BUY", "status": "REJECTED"},
        ],
    }]

    assert summarize_policy_activity(history) == {
        "buy_executions": 1,
        "sell_executions": 1,
        "long_downtrend_lock_blocks": 1,
        "reentry_gate_blocks": 1,
        "aligned_downtrend_liquidations": 1,
    }


def test_markdown_labels_on_and_off_modes():
    text = render_markdown([
        {"period": "validation", "multi_horizon_regime_enabled": False, "final_value": 1000,
         "final_return_pct": 0.0, "max_drawdown_pct": -1.0, "win_rate_pct": 50.0,
         "payoff_ratio": 1.0, "buy_executions": 2, "sell_executions": 3,
         "long_downtrend_lock_blocks": 0, "aligned_downtrend_liquidations": 0, "reentry_gate_blocks": 0},
        {"period": "validation", "multi_horizon_regime_enabled": True, "final_value": 1001,
         "final_return_pct": 0.1, "max_drawdown_pct": -0.9, "win_rate_pct": 51.0,
         "payoff_ratio": 1.1, "buy_executions": 1, "sell_executions": 2,
         "long_downtrend_lock_blocks": 4, "aligned_downtrend_liquidations": 1, "reentry_gate_blocks": 2},
    ])

    assert "| validation | OFF |" in text
    assert "| validation | ON |" in text
