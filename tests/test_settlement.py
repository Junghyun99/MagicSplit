# tests/test_settlement.py
import pytest
from src.core.settlement import compute_settlement, convert_snapshots_to_krw


def _snap(date, value, cash=0.0, net_deposit=0.0, exchange_rate=None):
    s = {
        "date": date,
        "portfolio_value": value,
        "cash_balance": cash,
        "stock_value": value - cash,
        "net_deposit": net_deposit,
    }
    if exchange_rate is not None:
        s["exchange_rate"] = exchange_rate
    return s


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

    def test_twr_not_collapsed_by_zero_end_value(self):
        """종료 자산이 0(시세조회 실패 등)인 구간이 전체 TWR을 -100%로 붕괴시키지 않는다."""
        snaps = [
            _snap("2026-04-01", 1000.0),
            _snap("2026-04-10", 0.0, net_deposit=0.0),   # 비정상 0 자산
            _snap("2026-04-20", 1100.0, net_deposit=0.0),
            _snap("2026-04-28", 1200.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-28")
        # 0 구간은 다음 정상 스냅샷까지 병합되어 전체 변화가 반영됨 (1200/1000-1 = +20%)
        assert r.twr_pct == pytest.approx(20.0)

    def test_twr_merges_null_portfolio_value_period(self):
        """portfolio_value가 None(null)인 구간은 병합되어 전체 수익률이 유지된다."""
        snaps = [
            _snap("2026-04-01", 1000.0),
            {"date": "2026-04-10", "portfolio_value": None, "net_deposit": 0.0},
            _snap("2026-04-28", 1100.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-28")
        assert r.twr_pct == pytest.approx(10.0)

    def test_twr_merged_period_accumulates_cash_flow(self):
        """병합된 비정상 구간의 순입금도 분모에 누적 반영된다."""
        snaps = [
            _snap("2026-04-01", 1000.0),
            # 비정상 스냅샷에 1000 입금 -> 다음 정상 구간 분모는 1000+1000
            {"date": "2026-04-10", "portfolio_value": None, "net_deposit": 1000.0},
            _snap("2026-04-28", 2200.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-28")
        assert r.twr_pct == pytest.approx(10.0)  # 2200/(1000+1000)-1

    def test_twr_skips_nonpositive_start_value(self):
        """기준 자산이 0 이하이면 다음 정상 스냅샷이 기준이 된다 (예외 없이 처리)."""
        snaps = [
            _snap("2026-04-01", 0.0, net_deposit=0.0),
            _snap("2026-04-15", 100.0, net_deposit=100.0),
            _snap("2026-04-28", 150.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-28")
        assert r.twr_pct == pytest.approx(50.0)  # 150/100-1

    def test_twr_none_when_no_valid_period(self):
        """유효한 하위기간이 하나도 없으면 0%가 아니라 None을 반환한다."""
        snaps = [
            _snap("2026-04-01", 1000.0),
            {"date": "2026-04-10", "portfolio_value": None, "net_deposit": 0.0},
            {"date": "2026-04-28", "portfolio_value": None, "net_deposit": 0.0},
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-28")
        assert r.twr_pct is None

    def test_null_boundaries_resolve_to_nearest_valid_snapshot(self):
        """기초/기말 스냅샷이 null이면 가장 가까운 유효 스냅샷을 기초/기말로 쓴다."""
        snaps = [
            {"date": "2026-04-01", "portfolio_value": None, "net_deposit": 0.0},
            _snap("2026-04-15", 1000.0, net_deposit=0.0),
            {"date": "2026-04-28", "portfolio_value": None, "net_deposit": 0.0},
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.start_asset == 1000.0
        assert r.end_asset == 1000.0
        assert r.base_date == "2026-04-15"
        assert r.last_date == "2026-04-15"
        assert r.profit == 0.0

    def test_null_end_snapshot_uses_last_valid_and_excludes_later_deposits(self):
        """기말이 null이면 마지막 유효 스냅샷이 기말이 되고, 그 이후 순입금은 제외된다."""
        snaps = [
            _snap("2026-03-31", 1000.0),
            _snap("2026-04-15", 1200.0, net_deposit=100.0),
            # 마지막 날 시세조회 실패 + 입금 500: 기말은 4/15, 500은 합산 제외
            {"date": "2026-04-28", "portfolio_value": None, "net_deposit": 500.0},
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.last_date == "2026-04-15"
        assert r.end_asset == 1200.0
        assert r.net_deposit == 100.0
        assert r.profit == 100.0  # 1200 - 1000 - 100

    def test_null_prior_base_walks_back_and_keeps_identity(self):
        """직전 스냅샷이 null이면 그 이전 유효 스냅샷이 기초가 되고,
        그 사이(기간 밖) 순입금도 합산해 항등식이 유지된다."""
        snaps = [
            _snap("2026-03-28", 1000.0),
            # start 직전 스냅샷이 비정상이지만 200 입금이 기록됨
            {"date": "2026-03-31", "portfolio_value": None, "net_deposit": 200.0},
            _snap("2026-04-15", 1300.0, net_deposit=0.0),
        ]
        r = compute_settlement(snaps, "2026-04-01", "2026-04-30")
        assert r.base_date == "2026-03-28"
        assert r.start_asset == 1000.0
        assert r.net_deposit == 200.0
        assert r.profit == 100.0  # 1300 - 1000 - 200

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


class TestConvertSnapshotsToKrw:
    def test_converts_each_snapshot_at_its_own_rate(self):
        """각 스냅샷의 그날 환율로 환산한다 (기간말 단일 환율 아님)"""
        snaps = [
            _snap("2026-04-01", 1000.0, net_deposit=1000.0, exchange_rate=1300.0),
            _snap("2026-04-28", 1100.0, net_deposit=0.0, exchange_rate=1400.0),
        ]
        conv, dropped = convert_snapshots_to_krw(snaps)
        assert dropped == 0
        assert conv[0]["portfolio_value"] == 1_300_000.0  # 1000 * 1300
        assert conv[0]["net_deposit"] == 1_300_000.0
        assert conv[1]["portfolio_value"] == 1_540_000.0  # 1100 * 1400

    def test_all_monetary_fields_converted_and_invariant_holds(self):
        """cash_balance/stock_value도 같은 환율로 환산 -> value = cash + stock 유지"""
        snaps = [_snap("2026-04-01", 1000.0, cash=300.0, exchange_rate=1300.0)]
        conv, _ = convert_snapshots_to_krw(snaps)
        c = conv[0]
        assert c["cash_balance"] == 390_000.0   # 300 * 1300
        assert c["stock_value"] == 910_000.0    # 700 * 1300
        assert c["portfolio_value"] == c["cash_balance"] + c["stock_value"]

    def test_drops_snapshots_without_rate(self):
        """환율이 없거나 0 이하인 스냅샷은 제외하고 개수를 보고한다"""
        snaps = [
            _snap("2026-04-01", 1000.0, exchange_rate=None),
            _snap("2026-04-10", 1050.0, exchange_rate=0.0),
            _snap("2026-04-28", 1100.0, exchange_rate=1400.0),
        ]
        conv, dropped = convert_snapshots_to_krw(snaps)
        assert dropped == 2
        assert len(conv) == 1
        assert conv[0]["portfolio_value"] == 1_540_000.0

    def test_krw_settlement_includes_fx_gain(self):
        """원화 결산 손익에 환차손익이 포함된다.

        USD 자산은 그대로(1000->1000)여도 환율이 1300->1400으로 오르면
        원화 기간손익은 +100,000 (순수 환차익).
        """
        snaps = [
            _snap("2026-03-31", 1000.0, exchange_rate=1300.0),
            _snap("2026-04-28", 1000.0, net_deposit=0.0, exchange_rate=1400.0),
        ]
        conv, _ = convert_snapshots_to_krw(snaps)
        r = compute_settlement(conv, "2026-04-01", "2026-04-30")
        assert r.start_asset == 1_300_000.0
        assert r.end_asset == 1_400_000.0
        assert r.profit == 100_000.0

    def test_none_portfolio_value_preserved_as_none(self):
        """portfolio_value가 None이면 환산 후에도 None (compute가 스킵)"""
        snaps = [{"date": "2026-04-01", "portfolio_value": None,
                  "net_deposit": 0.0, "exchange_rate": 1300.0}]
        conv, dropped = convert_snapshots_to_krw(snaps)
        assert dropped == 0
        assert conv[0]["portfolio_value"] is None
