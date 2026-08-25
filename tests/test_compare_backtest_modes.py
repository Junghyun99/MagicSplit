from scripts.compare_backtest_modes import _is_liquidation


def test_liquidation_reason_recognizes_legacy_and_split_channel_reasons():
    assert _is_liquidation("추세 이탈 분할 청산")
    assert _is_liquidation("단기 채널 하단 이탈 통합 전량 청산")
    assert _is_liquidation("단기 채널 하락 전환 분할 청산")
    assert not _is_liquidation("일반 매직스플릿 익절")
