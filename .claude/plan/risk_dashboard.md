# 리스크 대시보드 구현 계획 (Risk Dashboard Plan)

본 문서는 MagicSplit의 계좌 안정성을 모니터링하고 위험 신호를 사전에 고지하기 위한 리스크 대시보드 구현 계획을 담고 있습니다.

## 1. 개요
MagicSplit의 분할 매수 전략은 하락장에서의 대응력에 의존하므로, 현금 고갈이나 특정 종목 쏠림 등의 리스크를 시각화하여 사용자가 사전에 인지하고 대응할 수 있도록 합니다.

## 2. 주요 리스크 지표 및 역할 분담

| 카테고리 | 지표명 | 계산 주체 | 데이터 소스 및 로직 |
| :--- | :--- | :---: | :--- |
| **유동성** | **현금 비중 (Cash Ratio)** | **프론트** | `현금 / 총자산` |
| | **차기 매수 소요 자금** | **프론트** | 보유 종목들의 `다음 차수 매수금액` 총합 |
| | **잠재적 최대 노출액** | **백엔드** | 모든 규칙의 `1~Max차수 매수금액` 총합 |
| **집중도** | **종목별 비중** | **프론트** | `종목 평가액 / 총자산` |
| | **섹터별 비중** | **프론트** | `섹터별 합산액 / 총자산` (섹터 정보는 백엔드 제공) |
| **상태** | **레벨 분포 (Level Dist)** | **프론트** | `Level N인 종목 수 / 총 종목 수` |
| | **장기 정체 종목** | **백엔드** | `마지막 거래일`과 `현재일` 차이 분석 |
| **신뢰도** | **잔고 동기화 상태** | **백엔드** | `abs(증권사 수량 - 로컬 수량)` |
| | **가격 이상 알림** | **백엔드** | `abs(현재가 - 직전가) / 직전가` 등의 이격 분석 |

## 3. 지표별 상세 계산 로직

### 3.1 유동성 (Liquidity)
*   **현금 비중:** `(Portfolio.total_cash / Portfolio.total_value) * 100`
    *   *경고 기준:* 10% 미만(주의), 5% 미만(위험)
*   **차기 매수 소요 자금:** 
    *   `for pos in positions: if pos.level < rule.max_lots: total += rule.buy_amount_at(pos.level + 1)`
    *   *의미:* 현재 보유한 모든 종목이 한 단계 더 떨어졌을 때 당장 필요한 현금.
*   **잠재적 최대 노출액:**
    *   `for rule in rules: total += sum(rule.buy_amounts[1...max_lots])`
    *   *의미:* 이론적으로 계좌 내 모든 종목이 바닥(Max Level)까지 갔을 때 필요한 총자본.

### 3.2 집중도 (Concentration)
*   **종목별 비중:** `(Ticker_Value / Total_Value) * 100`
    *   *경고 기준:* 단일 종목 15~20% 초과 시.
*   **섹터별 비중:** `(sum(Value_in_Sector) / Total_Value) * 100`
    *   *경고 기준:* 특정 섹터 30~40% 초과 시.
    *   *백엔드 역할:* `tickers.db`에서 `Industry/Sector` 컬럼 정보를 추출하여 종목별 매핑 테이블 제공.

### 3.3 상태 및 신뢰도 (Health & Reliability)
*   **고차수 집중도:** `(Count(Level >= 8) / Total_Ticker_Count) * 100`
    *   *의미:* 계좌 내 종목들이 얼마나 하락장에 깊게 물려있는지 지표화.
*   **장기 정체 기간:** `Today - max(Last_Trade_Date)`
    *   *의미:* 30일 이상 거래가 없는 종목은 전략 수정이나 종목 교체 검토 대상으로 분류.
*   **동기화 오차:** `abs(Broker_Qty - Local_Qty)`
    *   *의미:* 0이 아닐 경우 수동 매매 또는 주문 실패로 인한 데이터 불일치 발생을 즉시 고지.

## 4. 코딩 관점의 구현 예시

### 4.1 프론트엔드 (JavaScript) - `docs/js/controllers/risk-controller.js`
프론트엔드는 백엔드에서 받아온 `portfolio.json`과 `positions.json`을 사용하여 실시간으로 계산합니다.

```javascript
// 리스크 계산 핵심 로직 예시
function calculateRiskMetrics(portfolio, positions, rules) {
    const totalStockValue = positions.reduce((sum, p) => sum + (p.quantity * p.current_price), 0);
    const totalValue = portfolio.total_cash + totalStockValue;

    // 1. 현금 비중
    const cashRatio = (portfolio.total_cash / totalValue) * 100;

    // 2. 차기 매수 소요 자금 (Next Level Needs)
    let nextLevelNeeds = 0;
    positions.forEach(pos => {
        const rule = rules.find(r => r.ticker === pos.ticker);
        if (rule && pos.level < rule.max_lots) {
            // rule.buy_amount_at()은 유틸리티 함수로 가정
            nextLevelNeeds += getBuyAmountAt(rule, pos.level + 1);
        }
    });

    // 3. 종목별 비중 상세
    const tickerConcentration = positions.map(pos => ({
        ticker: pos.ticker,
        weight: ((pos.quantity * pos.current_price) / totalValue) * 100,
        isWarning: ((pos.quantity * pos.current_price) / totalValue) * 100 > 20
    }));

    // 4. 섹터별 비중 (백엔드에서 준 sectorMap 활용)
    const sectorWeights = {};
    positions.forEach(pos => {
        const sector = sectorMap[pos.ticker] || "Unknown";
        const val = pos.quantity * pos.current_price;
        sectorWeights[sector] = (sectorWeights[sector] || 0) + val;
    });

    // 5. 레벨 분포 (Level Distribution)
    const levelDist = { high: 0, mid: 0, low: 0 };
    positions.forEach(pos => {
        if (pos.level >= 8) levelDist.high++;
        else if (pos.level >= 4) levelDist.mid++;
        else levelDist.low++;
    });

    return { cashRatio, nextLevelNeeds, tickerConcentration, sectorWeights, levelDist, totalValue };
}
```
```

### 4.2 백엔드 (Python) - `src/core/logic/risk_analyzer.py`
백엔드는 모든 규칙을 전수 조사하거나 DB를 조회해야 하는 무거운 작업을 수행합니다.

```python
class RiskAnalyzer:
    def get_max_potential_exposure(self, rules: List[StockRule]) -> float:
        """계좌의 이론적 최대 투입 가능 금액 계산"""
        total = 0
        for rule in rules:
            for level in range(1, rule.max_lots + 1):
                total += rule.buy_amount_at(level)
        return total

    def check_sync_status(self, broker_positions: Dict[str, int], local_positions: List[PositionLot]):
        """증권사 실제 잔고와 로컬 DB 수량 비교"""
        errors = []
        # 로컬 수량 합계 계산
        local_sums = {}
        for p in local_positions:
            local_sums[p.ticker] = local_sums.get(p.ticker, 0) + p.quantity
            
        # 비교 로직
        all_tickers = set(broker_positions.keys()) | set(local_sums.keys())
        for t in all_tickers:
            b_qty = broker_positions.get(t, 0)
            l_qty = local_sums.get(t, 0)
            if b_qty != l_qty:
                errors.append({"ticker": t, "broker": b_qty, "local": l_qty, "diff": b_qty - l_qty})
        return errors

    def get_stale_positions(self, history: List[TradeExecution], days=30) -> List[str]:
        """최근 N일간 거래가 없는 정체 종목 추출"""
        cutoff = datetime.now() - timedelta(days=days)
        # 로직: 각 종목별 마지막 거래일 확인 후 cutoff보다 이전이면 포함
        ...
        return stale_tickers

    def detect_price_anomaly(self, ticker: str, current_price: float, last_price: float) -> bool:
        """가격 급변동(액면분할/병합 등) 의심 징후 포착"""
        if last_price <= 0: return False
        change_pct = abs(current_price - last_price) / last_price * 100
        return change_pct > 30.0  # 30% 이상 급변 시 경고
```

### 4.3 데이터베이스 확장 (SQL)
`tickers.db`에 섹터 정보를 추가하기 위한 스키마 변경 예시입니다.

```sql
-- Industry/Sector 정보 추가
ALTER TABLE tickers ADD COLUMN industry TEXT;
ALTER TABLE tickers ADD COLUMN sector TEXT;

-- 데이터 업데이트 예시
UPDATE tickers SET sector = 'Technology', industry = 'Semiconductors' WHERE ticker = '005930';
```

## 3. 구현 단계별 로드맵

### Phase 1: 프론트엔드 기초 시각화 (기존 데이터 활용)
- [ ] 대시보드 내 'Risk' 탭 또는 섹션 추가
- [ ] 현재 `portfolio.json`과 `positions.json` 기반 현금/종목 비중 차트 구현
- [ ] 차수별 분포도(Histogram) 구현

### Phase 2: 백엔드 데이터 보강
- [ ] `tickers.db`에 섹터(Industry) 정보 컬럼 추가 및 데이터 수집 스크립트 작성
- [ ] `MagicSplitEngine` 종료 시 리스크 요약 데이터(`risk_summary`) 생성 로직 추가
- [ ] `dashboard_data.json` 구조 확장 (섹터 정보 및 시뮬레이션 데이터 포함)

### Phase 3: 알림 및 리포트 고도화
- [ ] 지표별 위험 임계치(Threshold) 설정 기능
- [ ] 임계치 초과 시 대시보드 상단 경고 배너 및 알림 발생
- [ ] "리스크 점수" 시스템 도입 (계좌 건강 상태 수치화)

## 4. 데이터 구조 예시 (risk_summary)
```json
{
  "max_potential_exposure": 50000.0,
  "sector_map": {
    "AAPL": "Technology",
    "005930": "Electronics"
  },
  "stale_positions": ["TSLA"],
  "sync_error": false
}
```

---
**마지막 업데이트:** 2026-05-06
