from scripts.compare_backtest_modes import _event_stats, _is_liquidation


def test_liquidation_reason_recognizes_legacy_and_split_channel_reasons():
    assert _is_liquidation("추세 이탈 분할 청산")
    assert _is_liquidation("단기 채널 하단 이탈 통합 전량 청산")
    assert _is_liquidation("상승 채널 하단 이탈 통합 전량 청산")
    assert _is_liquidation("횡보 채널 하단 이탈 분할 청산")
    assert _is_liquidation("단기 채널 하락 전환 분할 청산")
    assert _is_liquidation("장기 상승·단기 횡보 전환 2일 확정 선제 50% 청산")
    assert not _is_liquidation("일반 매직스플릿 익절")


def test_event_stats_groups_liquidation_by_exit_regimes():
    history = [{
        "date": "2026-01-02",
        "reason": "테스트(AAPL):SELL(상승 채널 하단 이탈 전량 청산)",
        "executions": [{
            "ticker": "AAPL", "action": "SELL", "quantity": 2,
            "buy_price": 100.0, "realized_pnl": -20.0,
            "exit_trigger": "channel_lower_break",
            "exit_long_regime": "uptrend",
            "exit_short_regime": "uptrend",
        }],
    }]

    stats = _event_stats(history)
    context = stats["exit_contexts"][("channel_lower_break", "uptrend", "uptrend")]
    assert context["events"] == 1
    assert context["loss_events"] == 1
    assert context["pnl"] < -20.0  # 매수 수수료 보정 포함
