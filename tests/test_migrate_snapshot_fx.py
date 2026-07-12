# tests/test_migrate_snapshot_fx.py
"""migrate_snapshot_fx의 순수 로직(환율 forward-fill/적용) 테스트.

네트워크 조회(fetch_usdkrw_rates)는 외부 의존이라 여기서 다루지 않는다.
"""
from scripts.migrate_snapshot_fx import resolve_rate, apply_rates


RATES = {
    "2026-05-08": 1454.79,
    "2026-05-11": 1480.00,
    "2026-05-12": 1492.99,
    "2026-05-13": 1489.84,
}
SORTED = sorted(RATES.keys())


class TestResolveRate:
    def test_exact_date(self):
        assert resolve_rate("2026-05-12", SORTED, RATES) == 1492.99

    def test_weekend_forward_fills_from_prior_trading_day(self):
        """거래일이 아닌 날짜(주말)는 직전 거래일 환율을 사용한다"""
        # 2026-05-09(토), 05-10(일) -> 직전 거래일 05-08
        assert resolve_rate("2026-05-10", SORTED, RATES) == 1454.79

    def test_after_last_uses_last(self):
        assert resolve_rate("2026-06-01", SORTED, RATES) == 1489.84

    def test_before_first_returns_none(self):
        assert resolve_rate("2026-01-01", SORTED, RATES) is None


class TestApplyRates:
    def test_fills_only_missing_by_default(self):
        snaps = [
            {"date": "2026-05-08", "portfolio_value": 100.0},               # 없음 -> 채움
            {"date": "2026-05-12", "portfolio_value": 200.0, "exchange_rate": 1300.0},  # 있음 -> 유지
        ]
        updated, missing = apply_rates(snaps, RATES)
        assert updated == 1
        assert missing == []
        assert snaps[0]["exchange_rate"] == 1454.79
        assert snaps[1]["exchange_rate"] == 1300.0  # 기존 값 보존

    def test_overwrite_refetches_all(self):
        snaps = [{"date": "2026-05-12", "portfolio_value": 200.0, "exchange_rate": 1300.0}]
        updated, missing = apply_rates(snaps, RATES, overwrite=True)
        assert updated == 1
        assert snaps[0]["exchange_rate"] == 1492.99

    def test_missing_dates_reported_and_left_unset(self):
        snaps = [{"date": "2026-01-01", "portfolio_value": 50.0}]  # 조회 구간 이전
        updated, missing = apply_rates(snaps, RATES)
        assert updated == 0
        assert missing == ["2026-01-01"]
        assert "exchange_rate" not in snaps[0]

    def test_forward_fill_applied_on_weekend_snapshot(self):
        snaps = [{"date": "2026-05-10", "portfolio_value": 50.0}]
        updated, _ = apply_rates(snaps, RATES)
        assert updated == 1
        assert snaps[0]["exchange_rate"] == 1454.79
