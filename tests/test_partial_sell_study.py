import pytest

from src.backtest.partial_sell_study import (
    build_consumer_study_config,
    parse_period,
    render_markdown,
    summarize_history,
)


def test_build_consumer_study_config_keeps_only_group_and_overrides_candidate():
    config = {
        "universe_groups": {"consumer_staples": ["A", "B"]},
        "stocks": [
            {"ticker": "A", "preset": "consumer_staples_ks"},
            {"ticker": "B", "preset": "consumer_staples_ks"},
            {"ticker": "C", "preset": "large_cap_ks"},
        ],
    }

    study, tickers = build_consumer_study_config(config, 60)

    assert tickers == ["A", "B"]
    assert [stock["ticker"] for stock in study["stocks"]] == ["A", "B"]
    assert [stock["trendbreak_partial_sell_pct"] for stock in study["stocks"]] == [60, 60]
    assert "trendbreak_partial_sell_pct" not in config["stocks"][0]


def test_parse_period_rejects_ambiguous_input():
    assert parse_period("holdout:2023-01-02:2026-08-13").label == "holdout"
    try:
        parse_period("2023-01-02:2026-08-13")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid period must fail")


def test_summarize_history_reconstructs_closed_and_aligned_cycles():
    history = [
        {"date": "2024-01-02", "portfolio_value": 1000, "reason": "", "executions": [
            {"ticker": "A", "action": "BUY", "status": "FILLED", "quantity": 2, "price": 100, "fee": 1},
        ]},
        {"date": "2024-01-03", "portfolio_value": 900, "reason": "장·단기 하락 정렬 전량 청산 A", "executions": [
            {"ticker": "A", "action": "SELL", "status": "FILLED", "quantity": 2, "price": 90, "fee": 1},
        ]},
    ]

    summary = summarize_history(history, 1000)

    assert summary["closed_cycles"] == 1
    assert summary["aligned_downtrend_cycles"] == 1
    assert summary["closed_cycle_pnl"] == -22
    assert summary["max_drawdown_pct"] == pytest.approx(-10)


def test_render_markdown_contains_candidate_and_final_value():
    text = render_markdown([{
        "period": "validation", "partial_sell_pct": 70, "final_value": 1010,
        "final_return_pct": 1.0, "max_drawdown_pct": -2.0, "win_rate_pct": 50.0,
        "payoff_ratio": 1.5, "aligned_downtrend_pnl": -10,
    }])

    assert "70%" in text
    assert "1,010" in text
