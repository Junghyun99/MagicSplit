# tests/test_settlement.py
import pytest
from src.core.settlement import compute_settlement


def _snap(date, value, cash=0.0, net_deposit=0.0):
    return {
        "date": date,
        "portfolio_value": value,
        "cash_balance": cash,
        "stock_value": value - cash,
        "net_deposit": net_deposit,
    }


class TestComputeSettlement:
    def test_empty_range_returns_zero(self):
        snaps = [_snap("2026-03-15", 1000.0)]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.snapshot_count == 0
        assert r.start_asset == 0.0 and r.end_asset == 0.0
        assert r.profit == 0.0 and r.twr_pct is None

    def test_start_end_asset_and_profit_pure_gain(self):
        """입금 없이 시세만 상승 -> 손익 = 기말 - 기초"""
        snaps = [
            _snap("2026-04-01", 1000.0, net_deposit=0.0),
            _snap("2026-04-15", 1100.0, net_deposit=0.0),
            _snap("2026-04-28", 1200.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-28")
        # 기간 직전 스냅샷이 없으므로 첫 스냅샷이 기초
        assert r.start_asset == 1000.0
        assert r.end_asset == 1200.0
        assert r.net_deposit == 0.0
        assert r.profit == 200.0

    def test_uses_prior_snapshot_as_base(self):
        """start 직전 스냅샷이 기초자산이 된다"""
        snaps = [
            _snap("2026-03-31", 1000.0),
            _snap("2026-04-10", 1300.0, net_deposit=0.0),
            _snap("2026-04-28", 1500.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.base_date == "2026-03-31"
        assert r.start_asset == 1000.0
        assert r.end_asset == 1500.0
        assert r.profit == 500.0

    def test_net_deposit_excluded_from_profit(self):
        """기간 중 입금분은 손익에서 제외된다 (기초 직전 스냅샷 존재)"""
        snaps = [
            _snap("2026-03-31", 1000.0),
            # 500 입금 후 시세로 +100 -> 자산 1600
            _snap("2026-04-10", 1600.0, net_deposit=500.0),
            _snap("2026-04-28", 1650.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.net_deposit == 500.0
        # profit = 1650 - 1000 - 500 = 150
        assert r.profit == 150.0

    def test_first_snapshot_net_deposit_excluded_when_no_prior(self):
        """기초 직전 스냅샷이 없으면 첫 스냅샷 net_deposit은 합산 제외"""
        snaps = [
            _snap("2026-04-01", 1000.0, net_deposit=1000.0),  # 초기 원금
            _snap("2026-04-20", 1200.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.start_asset == 1000.0
        assert r.net_deposit == 0.0
        assert r.profit == 200.0

    def test_twr_excludes_deposit_effect(self):
        """TWR은 입금 효과를 제거한다.

        기초 1000 -> 입금 1000 후 2000 (수익 0%) -> 2200 (수익 +10%)
        TWR = (2000/(1000+1000)-1=0) 이후 (2200/2000-1=0.1) -> +10%
        """
        snaps = [
            _snap("2026-03-31", 1000.0),
            _snap("2026-04-10", 2000.0, net_deposit=1000.0),
            _snap("2026-04-28", 2200.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.twr_pct == pytest.approx(10.0)

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError):
            compute_settlement([], "2026-04-30", "2026-04-01")

    def test_boundary_inclusive(self):
        """start/end 양끝 날짜 포함"""
        snaps = [
            _snap("2026-04-01", 1000.0),
            _snap("2026-04-30", 1100.0),
            _snap("2026-05-01", 9999.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.snapshot_count == 2
        assert r.last_date == "2026-04-30"

    def test_twr_skips_nonpositive_denominator(self):
        """대규모 출금으로 분모가 0 이하가 되는 구간은 스킵되어 왜곡/오류가 없다."""
        snaps = [
            _snap("2026-03-31", 1000.0),
            # 2000 출금(net_deposit=-2000) -> denom = 1000 + (-2000) < 0 -> 스킵
            _snap("2026-04-10", 500.0, net_deposit=-2000.0),
            _snap("2026-04-28", 600.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        # 예외 없이 숫자를 반환해야 한다
        assert r.twr_pct is not None
        # 두 번째 구간(600/500-1=+20%)만 반영
        assert r.twr_pct == pytest.approx(20.0)

    def test_twr_skips_nonpositive_start_value(self):
        """기준 자산이 0 이하인 구간도 스킵된다 (예외 없이 처리)."""
        snaps = [
            _snap("2026-04-01", 0.0, net_deposit=0.0),
            _snap("2026-04-15", 100.0, net_deposit=100.0),
            _snap("2026-04-28", 150.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-28")
        assert r.twr_pct is not None

    def test_snapshots_sorted_defensively(self):
        """저장 순서가 뒤섞여 있어도 날짜순으로 정렬해 계산한다"""
        snaps = [
            _snap("2026-04-28", 1200.0),
            _snap("2026-04-01", 1000.0),
            _snap("2026-04-15", 1100.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-28")
        assert r.start_asset == 1000.0
        assert r.end_asset == 1200.0
