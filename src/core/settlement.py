# src/core/settlement.py
"""기간(월간) 결산 계산 — snapshots.json 기반 순수 로직.

브로커/파일 I/O에 의존하지 않고, 일별 자산 스냅샷 리스트만 받아
기초/기말 자산, 순입금액, 기간손익(금액), 수익률(TWR)을 계산한다.

결산 항등식:
    기간손익 = 기말자산 - 기초자산 - 순입금액합계

수익률(TWR)은 docs/js/views/charts-view.js 와 동일한 시간가중수익률 공식을 사용해
입출금 효과를 제거한다.
"""
from dataclasses import dataclass, asdict
from typing import List, Optional


@dataclass
class SettlementResult:
    start_date: str            # 조회 시작일 (YYYY-MM-DD)
    end_date: str              # 조회 종료일 (YYYY-MM-DD)
    base_date: Optional[str]   # 기초자산 기준 스냅샷 날짜
    last_date: Optional[str]   # 기말자산 기준 스냅샷 날짜
    start_asset: float         # 기초자산
    end_asset: float           # 기말자산
    net_deposit: float         # 순입금액 합계
    profit: float              # 기간손익(금액) = 기말 - 기초 - 순입금
    twr_pct: Optional[float]   # 수익률(TWR, %). 계산 불가 시 None
    snapshot_count: int        # 기간 내 스냅샷 개수

    def to_dict(self) -> dict:
        return asdict(self)


def compute_settlement(snapshots: List[dict], start: str, end: str) -> SettlementResult:
    """일별 스냅샷 리스트에서 [start, end] 기간(양끝 포함) 결산을 계산한다.

    Args:
        snapshots: repo.load_snapshots() 결과. 각 항목은 date/portfolio_value/
                   cash_balance/net_deposit 을 가진다.
        start, end: 'YYYY-MM-DD' 문자열. start <= end.

    Returns:
        SettlementResult. 기간 내 스냅샷이 없으면 모든 금액 0, twr_pct None.
    """
    if start > end:
        raise ValueError(f"start({start}) must be <= end({end})")

    # 날짜 오름차순 정렬 (저장 순서를 신뢰하지 않고 방어적으로 정렬)
    snaps = sorted(
        (s for s in snapshots if s.get("date")),
        key=lambda s: s["date"],
    )

    in_range = [s for s in snaps if start <= s["date"][:10] <= end]
    if not in_range:
        return SettlementResult(
            start_date=start, end_date=end, base_date=None, last_date=None,
            start_asset=0.0, end_asset=0.0, net_deposit=0.0, profit=0.0,
            twr_pct=None, snapshot_count=0,
        )

    # 기초자산: start 직전 마지막 스냅샷. 없으면 기간 첫 스냅샷을 기초로 사용.
    prior = [s for s in snaps if s["date"][:10] < start]
    if prior:
        base = prior[-1]
        # 기간 내 모든 순입금이 base 이후 발생분
        contrib = in_range
        twr_seq = [base] + in_range
    else:
        base = in_range[0]
        # 첫 스냅샷 값에는 그날까지의 입금이 이미 반영 -> 그 이후 분만 합산
        contrib = in_range[1:]
        twr_seq = in_range

    start_asset = float(base["portfolio_value"])
    end_asset = float(in_range[-1]["portfolio_value"])
    net_deposit = round(sum(float(s.get("net_deposit") or 0.0) for s in contrib), 2)
    profit = round(end_asset - start_asset - net_deposit, 2)
    twr_pct = _twr_pct(twr_seq)

    return SettlementResult(
        start_date=start, end_date=end,
        base_date=base["date"][:10], last_date=in_range[-1]["date"][:10],
        start_asset=round(start_asset, 2), end_asset=round(end_asset, 2),
        net_deposit=net_deposit, profit=profit,
        twr_pct=twr_pct, snapshot_count=len(in_range),
    )


def _twr_pct(seq: List[dict]) -> Optional[float]:
    """시간가중수익률(%). charts-view.js 와 동일 공식.

    각 하위기간 수익률 = V_end / (V_start + CF) - 1, CF는 해당 스냅샷의 net_deposit
    (기초에 유입되었다고 가정). 시퀀스가 2개 미만이면 None.
    """
    if len(seq) < 2:
        return None
    twr = 1.0
    for i in range(1, len(seq)):
        start_val = _finite(seq[i - 1].get("portfolio_value"))
        end_val = _finite(seq[i].get("portfolio_value"))
        # 기준/종료 자산이 없거나(시세조회 실패로 null) 0 이하이면 왜곡되므로 스킵.
        # 특히 end_val이 0이면 곱셈이 전체 TWR을 0(-100%)으로 붕괴시키므로 반드시 가드.
        if start_val is None or end_val is None or start_val <= 0 or end_val <= 0:
            continue
        cf = float(seq[i].get("net_deposit") or 0.0)
        denom = start_val + cf
        if denom <= 0:
            # 대규모 출금 등으로 분모가 0 이하가 되면 수익률이 왜곡되므로 스킵
            continue
        twr *= end_val / denom
    return round((twr - 1) * 100, 4)


def _finite(value) -> Optional[float]:
    """유한한 float면 반환, None/NaN/inf면 None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN or inf
        return None
    return f
