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


def convert_snapshots_to_krw(snapshots: List[dict]):
    """USD 스냅샷을 각 시점의 그날 기준환율(exchange_rate)로 KRW 환산한다.

    증권사 계좌평가 방식과 동일하게, 기간말 단일 환율이 아니라 각 스냅샷 시점의
    환율을 적용한다 -> 원화 기간손익에 주가손익과 환차손익이 함께 반영된다.

    exchange_rate가 없거나(과거 스냅샷/조회 실패) 0 이하인 스냅샷은 환산 불가하여
    제외한다. 반환값의 dropped로 제외 건수를 알려 소급 불가 구간을 표시할 수 있다.

    Args:
        snapshots: repo.load_snapshots() 결과 (portfolio_value/net_deposit은 USD).

    Returns:
        (converted, dropped): KRW로 환산된 스냅샷 리스트와 환율 부재로 제외된 건수.
    """
    converted = []
    dropped = 0
    for s in snapshots:
        rate = _finite(s.get("exchange_rate"))
        if rate is None or rate <= 0:
            dropped += 1
            continue
        c = dict(s)
        # 모든 금액 필드를 같은 환율로 환산 -> portfolio_value = cash + stock 항등식 유지
        for key in ("portfolio_value", "cash_balance", "stock_value"):
            if key in s:
                v = _finite(s.get(key))
                c[key] = None if v is None else round(v * rate, 2)
        nd = _finite(s.get("net_deposit"))
        c["net_deposit"] = 0.0 if nd is None else round(nd * rate, 2)
        converted.append(c)
    return converted, dropped


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

    # 기초/기말 스냅샷 선택: portfolio_value가 null(시세조회 실패 등)인 스냅샷을
    # 기초/기말로 쓰면 자산이 0으로 잡혀 손익이 크게 왜곡되므로, 가장 가까운
    # "유효한"(값이 있는) 스냅샷을 선택한다.
    #   기초: start 직전의 마지막 유효 스냅샷. 없으면 기간 내 첫 유효 스냅샷.
    #   기말: 기간 내 마지막 유효 스냅샷.
    # 유효 스냅샷이 하나도 없으면 기존 위치(기간 첫/끝)로 강등하고 금액은 0 처리.
    def _valid(s):
        return _finite(s.get("portfolio_value")) is not None

    prior = [s for s in snaps if s["date"][:10] < start]
    valid_prior = [s for s in prior if _valid(s)]
    valid_in_range = [s for s in in_range if _valid(s)]

    if valid_prior:
        base = valid_prior[-1]
    elif valid_in_range:
        base = valid_in_range[0]
    else:
        base = in_range[0]
    end_snap = valid_in_range[-1] if valid_in_range else in_range[-1]

    # 결산 창은 (base, end_snap]: 항등식(손익 = 기말 - 기초 - 순입금)이 유지되도록
    # base 이후 ~ end_snap까지 발생한 순입금만 합산한다. base가 null 스냅샷을
    # 건너뛰어 기간 밖 과거로 이동했다면 그 사이 순입금도 포함하고, end_snap이
    # 기간 끝보다 앞이라면 그 이후 순입금은 제외한다.
    pos = {id(s): i for i, s in enumerate(snaps)}
    window = snaps[pos[id(base)] + 1: pos[id(end_snap)] + 1]

    start_asset = _finite(base.get("portfolio_value")) or 0.0
    end_asset = _finite(end_snap.get("portfolio_value")) or 0.0
    net_deposit = round(sum(float(s.get("net_deposit") or 0.0) for s in window), 2)
    profit = round(end_asset - start_asset - net_deposit, 2)
    twr_pct = _twr_pct([base] + window)

    return SettlementResult(
        start_date=start, end_date=end,
        base_date=base["date"][:10], last_date=end_snap["date"][:10],
        start_asset=round(start_asset, 2), end_asset=round(end_asset, 2),
        net_deposit=net_deposit, profit=profit,
        twr_pct=twr_pct, snapshot_count=len(in_range),
    )


def _twr_pct(seq: List[dict]) -> Optional[float]:
    """시간가중수익률(%). charts-view.js 와 동일 공식.

    각 하위기간 수익률 = V_end / (V_start + CF) - 1, CF는 해당 스냅샷의 net_deposit
    (기초에 유입되었다고 가정). 시퀀스가 2개 미만이면 None.

    비정상 스냅샷(portfolio_value가 null이거나 0 이하 - 시세조회 실패 등)은 단순히
    건너뛰지 않고 다음 정상 스냅샷까지 하위기간을 병합하며, 그 사이 발생한
    순입금은 병합 구간 분모에 누적 반영한다. 단순 스킵은 비정상 스냅샷 전후의
    수익률 변화를 통째로 누락시켜 TWR을 왜곡하기 때문이다.
    유효한 하위기간이 하나도 없으면 None을 반환한다.
    """
    if len(seq) < 2:
        return None
    twr = 1.0
    last_valid_val = None   # 직전 정상 스냅샷의 자산 (병합 구간의 기준값)
    accumulated_cf = 0.0    # 병합 구간에 누적된 순입금
    has_valid_period = False

    for rec in seq:
        val = _finite(rec.get("portfolio_value"))
        valid = val is not None and val > 0

        if last_valid_val is None:
            # 아직 기준값이 없으면 첫 정상 스냅샷을 기준으로 삼는다
            # (기준 스냅샷의 net_deposit은 자산에 이미 반영되어 있으므로 미사용)
            if valid:
                last_valid_val = val
            continue

        accumulated_cf += float(rec.get("net_deposit") or 0.0)
        if valid:
            denom = last_valid_val + accumulated_cf
            if denom > 0:
                twr *= val / denom
                has_valid_period = True
            # 대규모 출금 등으로 분모가 0 이하이면 해당 병합 구간은 왜곡 방지를
            # 위해 반영하지 않고, 이번 정상값을 새 기준으로 삼는다
            last_valid_val = val
            accumulated_cf = 0.0

    if not has_valid_period:
        return None
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
