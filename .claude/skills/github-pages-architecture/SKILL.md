---
name: github-pages-architecture
description: >
  GitHub Pages 정적 웹 UI의 3계층 아키텍처(Presentation/Application/Infrastructure)
  설계 원칙. docs/ 디렉토리 구조 설계, 탭 추가, 모듈 분리, Lazy loading, 차트 인스턴스
  관리, 데이터 흐름 정의 시 반드시 이 스킬을 사용한다.
  Use this skill when designing docs/ directory structure, adding tabs, splitting
  modules, implementing lazy loading, managing chart instances, or defining data flow
  in a GitHub Pages static web UI.
---

# GitHub Pages 3계층 아키텍처 가이드

## 1. When to use / When NOT to use

### 사용하는 경우
- docs/ 디렉토리 구조를 새로 설계하거나 리팩터링할 때
- 새 탭/페이지를 추가할 때
- JavaScript 모듈을 분리하거나 책임을 재배치할 때
- 데이터 로딩 흐름(fetch → 렌더)을 설계할 때
- 차트 인스턴스 관리 전략을 결정할 때
- Lazy loading / 탭 라우팅을 구현할 때

### 사용하지 않는 경우
- GitHub API로 config.json을 읽고 쓰는 작업 → **github-pages-gitops** skill 사용
- Python 백엔드 로직, GitHub Actions 워크플로 수정
- 데이터 파일(JSON) 스키마 변경만 하는 경우

---

## 2. 3계층 아키텍처 원칙

```
┌─────────────────────────────────────────────┐
│  Presentation (표현 계층)                     │
│  index.html · css/style.css                  │
│  HTML 구조, CSS 스타일, CDN 라이브러리 로드     │
├─────────────────────────────────────────────┤
│  Application (응용 계층)                      │
│  js/main.js · js/ui.js · js/charts.js        │
│  오케스트레이션, DOM 업데이트, 차트 렌더링       │
├─────────────────────────────────────────────┤
│  Infrastructure (인프라 계층)                  │
│  js/utils.js · data/*.json                   │
│  순수 계산, 데이터 파일, 외부 API 없음           │
└─────────────────────────────────────────────┘
```

### 의존 방향 (단방향)

```
Presentation → Application → Infrastructure
     ✗ 역방향 의존 금지
```

- **Infrastructure(utils.js)**: 다른 모듈을 import하지 않는다. DOM에 접근하지 않는다.
- **Application(ui.js, charts.js)**: utils.js만 import한다. index.html의 DOM 구조에 의존한다.
- **Presentation(index.html)**: main.js를 `<script type="module">`로 로드한다. CSS만 직접 관리한다.
- **main.js**: 오케스트레이션 전용. utils/ui/charts를 모두 import하고 실행 순서를 제어한다.

---

## 3. 권장 디렉토리 구조

```
docs/
├── index.html              # 진입점 (모든 탭 컨테이너)
├── css/
│   └── style.css           # 커스텀 스타일 (Bootstrap 보완)
├── js/
│   ├── main.js             # 오케스트레이션: fetch → 렌더 호출
│   ├── utils.js            # 순수 함수: 계산, 포맷, 필터
│   ├── ui.js               # DOM 업데이트: 카드, 테이블, 배지
│   └── charts.js           # 차트 래핑: 인스턴스 생성/파괴/리사이즈
├── data/
│   ├── summary.json        # 요약 데이터
│   ├── status.json         # 상태 데이터
│   └── history.json        # 이력 데이터
└── plans/                  # (선택) 문서/계획 파일
```

### 파일 역할표

| 파일 | 계층 | 책임 | import 허용 |
|------|------|------|------------|
| `index.html` | Presentation | HTML 구조, CDN 로드 | 없음 (script 태그만) |
| `style.css` | Presentation | 레이아웃, 색상, 반응형 | 없음 |
| `main.js` | Application | 진입점, fetch, 탭 이벤트, 모드 분기 | utils, ui, charts |
| `ui.js` | Application | DOM 업데이트 (카드, 테이블, 배지) | utils |
| `charts.js` | Application | 차트 인스턴스 생성/파괴/리사이즈 | utils |
| `utils.js` | Infrastructure | 순수 계산, 포맷팅, 필터링 | 없음 (의존성 0) |
| `data/*.json` | Infrastructure | 정적/생성 데이터 | N/A |

---

## 4. 모듈 분리 기준

### utils.js — 순수 함수 모듈

```javascript
// 의존성: 없음
// DOM 접근: 금지
// 외부 라이브러리: 금지

export function computeReturns(data) {
    // Array, Math, Date만 사용하는 순수 계산
    return data.map(d => ({ date: d.date, return: d.close / d.open - 1 }));
}

export function computeSummary(data) {
    // 요약 통계 계산 로직
    return { total: 1000000, daily: 5.2 };
}

export function formatCurrency(value) {
    return new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'KRW' }).format(value);
}

export function filterByDateRange(data, startDate, endDate) {
    return data.filter(d => d.date >= startDate && d.date <= endDate);
}
```

**원칙**: Node.js, 웹 워커 등 어떤 환경에서도 동작해야 한다. `document`, `window`, `Chart` 등 브라우저/라이브러리 전역 객체를 참조하면 안 된다.

### ui.js — DOM 업데이트 모듈

```javascript
import { formatCurrency, computeReturns } from './utils.js';

// DOM 업데이트만 담당
export function renderSummaryCards(data) {
    const returns = computeReturns(data);
    document.getElementById('total-return').textContent = formatCurrency(returns.total);
    document.getElementById('daily-return').textContent = returns.daily.toFixed(2) + '%';
}

export function renderDataTable(rows, containerId) {
    const container = document.getElementById(containerId);
    container.innerHTML = rows.map(r => `<tr><td>${r.date}</td><td>${r.value}</td></tr>`).join('');
}
```

**원칙**: 비즈니스 로직(계산, 필터링)은 utils.js에 위임한다. ui.js는 "어디에 무엇을 표시할지"만 결정한다.

### charts.js — 차트 래핑 모듈

```javascript
import { computeReturns } from './utils.js';

// 모듈 스코프 인스턴스 (섹션 7 참조)
let returnChart = null;

export function renderReturnChart(data) {
    if (returnChart) returnChart.destroy();
    const canvas = document.getElementById('returnChart');
    const returns = computeReturns(data);
    returnChart = new Chart(canvas, {
        type: 'line',
        data: { labels: returns.map(r => r.date), datasets: [{ data: returns.map(r => r.return) }] }
    });
}

export function resizeAllCharts() {
    [returnChart].forEach(c => { if (c) c.resize(); });
}
```

**원칙**: Chart.js(또는 사용 중인 차트 라이브러리) 의존성을 이 파일 안에 격리한다. 다른 모듈은 차트 라이브러리를 직접 import하지 않는다.

### main.js — 오케스트레이션 모듈

```javascript
import { renderSummaryCards, renderDataTable } from './ui.js';
import { renderReturnChart, resizeAllCharts } from './charts.js';
import { filterByDateRange } from './utils.js';

// 진입점: 모든 모듈을 조율
document.addEventListener('DOMContentLoaded', async () => {
    const data = await loadData();
    renderSummaryCards(data.summary);
    setupTabEvents(data);
});
```

**원칙**: main.js는 "무엇을 언제 호출할지"만 결정한다. 직접 DOM을 조작하거나 차트를 생성하지 않는다.

---

## 5. 데이터 로딩 패턴

### Promise.all 병렬 fetch

```javascript
async function loadData() {
    const basePath = 'data/';
    const cacheBust = `v=${Date.now()}`;

    const [summaryRes, statusRes, historyRes] = await Promise.all([
        fetch(`${basePath}summary.json?${cacheBust}`),
        fetch(`${basePath}status.json?${cacheBust}`),
        fetch(`${basePath}history.json?${cacheBust}`)
    ]);

    return {
        summary: await summaryRes.json(),
        status:  await statusRes.json(),
        history: await historyRes.json()
    };
}
```

### 캐시 무효화

- GitHub Pages는 CDN 캐시를 사용한다. 데이터 파일이 자주 갱신되면 `?v=${Date.now()}`를 붙여 매 로드마다 최신 데이터를 가져온다.
- JS/CSS 모듈은 import 경로에 버전 쿼리를 붙인다: `import { fn } from './utils.js?v=3'`. 코드 변경 시 버전 번호를 올려 캐시를 갱신한다.

### 모듈 간 데이터 공유 전략

| 방식 | 사용 시점 | 예시 |
|------|----------|------|
| **함수 파라미터** (권장) | 렌더 함수에 데이터를 직접 전달할 때 | `renderSummaryCards(summaryData)` |
| **ES6 export** | 상수나 유틸 함수를 공유할 때 | `export const ENGINE_COLORS = {...}` |
| **window 글로벌** | 디버깅 편의용, DevTools 콘솔 접근 | `window.__summary = summaryData` |

**원칙**: 프로덕션 데이터 흐름은 함수 파라미터 또는 ES6 export를 사용한다. window 글로벌은 디버깅 보조 수단으로만 사용한다.

---

## 6. 탭/페이지 확장 패턴

### Lazy Loading 플래그

각 탭에 대해 `<tab>Rendered` boolean 플래그를 두어, 최초 클릭 시에만 렌더링하고 이후 클릭은 무시한다.

```javascript
let overviewRendered = false;
let performanceRendered = false;
let allocationRendered = false;

function renderOverviewTab(data) {
    if (overviewRendered) return;  // 이미 렌더링됨 → 스킵
    renderSummaryCards(data.summary);
    renderStatusBadges(data.status);
    overviewRendered = true;
}

function renderPerformanceTab(data) {
    if (performanceRendered) return;
    renderReturnChart(data.summary);
    renderDrawdownChart(data.summary);
    performanceRendered = true;
}
```

**이점**: 페이지 초기 로드 시 Overview 탭만 렌더링되므로 빠르다. 차트가 많은 탭은 사용자가 실제로 클릭할 때 렌더링된다.

### Bootstrap 탭 이벤트 기반 라우팅

```javascript
function setupTabEvents(data) {
    document.querySelectorAll('[data-bs-toggle="tab"]').forEach(tab => {
        tab.addEventListener('shown.bs.tab', (e) => {
            const target = e.target.getAttribute('data-bs-target');
            switch (target) {
                case '#overview':    renderOverviewTab(data); break;
                case '#performance': renderPerformanceTab(data); break;
                case '#allocation':  renderAllocationTab(data); break;
            }
        });
    });
    // 최초 탭은 즉시 렌더링
    renderOverviewTab(data);
}
```

### URL 모드 분기

하나의 HTML 파일에서 여러 모드를 지원할 때 URL 쿼리 파라미터를 사용한다.

```javascript
const urlParams = new URLSearchParams(window.location.search);
const mode = urlParams.get('mode') || 'live';

if (mode === 'live') {
    await loadLiveMode();
} else if (mode === 'backtest') {
    await loadBacktestMode();
}
```

- `index.html` → 기본(live) 모드
- `index.html?mode=backtest` → 백테스트 모드
- 각 모드는 별도의 데이터 경로와 렌더 로직을 갖는다.

---

## 7. 차트 인스턴스 관리

### 모듈 스코프 변수 + .destroy() 패턴

```javascript
// charts.js 상단 — 모듈 스코프에 인스턴스 보관
let returnChart = null;
let drawdownChart = null;
let allocationChart = null;

export function renderReturnChart(data) {
    if (returnChart) returnChart.destroy();  // 기존 인스턴스 파괴
    const canvas = document.getElementById('returnChart');
    returnChart = new Chart(canvas, { /* config */ });
}
```

**왜 .destroy()가 필수인가**:
- Chart.js는 캔버스에 이벤트 리스너와 내부 상태를 바인딩한다.
- destroy() 없이 new Chart()를 반복하면 메모리 누수와 렌더링 충돌이 발생한다.

### resizeAllCharts() 중앙 관리

```javascript
export function resizeAllCharts() {
    [returnChart, drawdownChart, allocationChart].forEach(chart => {
        if (chart) chart.resize();
    });
}

// main.js에서 window resize 이벤트에 연결
window.addEventListener('resize', () => resizeAllCharts());
```

**새 차트 추가 시**: 모듈 스코프 변수 선언 → render 함수 작성 → `resizeAllCharts()` 배열에 추가.

---

## 8. CSS 구성 원칙

### Bootstrap CDN + 최소 커스텀

```html
<!-- index.html -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="css/style.css">
```

- Bootstrap의 그리드, 카드, 탭, 배지 등을 기본으로 사용한다.
- `style.css`는 Bootstrap이 제공하지 않는 프로젝트 고유 스타일만 정의한다.

### 상태별 색상 클래스

```css
/* 상태 표시용 색상 클래스 */
.status-positive { color: #28a745; }  /* 수익, 정상 */
.status-negative { color: #dc3545; }  /* 손실, 에러 */
.status-neutral  { color: #6c757d; }  /* 보합, 대기 */
.status-warning  { color: #ffc107; }  /* 주의 */
```

### 반응형 디자인

```css
/* 모바일 우선: 기본 스타일은 모바일, 큰 화면에 확장 */
.dashboard-grid { display: grid; grid-template-columns: 1fr; gap: 1rem; }

@media (min-width: 768px) {
    .dashboard-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (min-width: 1200px) {
    .dashboard-grid { grid-template-columns: repeat(4, 1fr); }
}
```

---

## 9. 안티패턴

| 안티패턴 | 이유 | 올바른 방법 |
|----------|------|------------|
| utils.js에서 `document.getElementById()` 사용 | 테스트 불가, 환경 의존 | DOM 조작은 ui.js에서만 |
| ui.js에서 복잡한 계산 로직 작성 | 책임 혼재, 재사용 불가 | 계산은 utils.js에 함수로 분리 |
| 차트를 매번 `new Chart()` (destroy 없이) | 메모리 누수, 이벤트 중복 | 모듈 스코프 변수 + .destroy() 후 재생성 |
| charts.js 밖에서 `new Chart()` 직접 호출 | 차트 라이브러리 의존성 분산 | charts.js에 래핑 함수 추가 |
| main.js에서 직접 innerHTML 조작 | 오케스트레이션 역할 초과 | ui.js의 render 함수 호출로 위임 |
| fetch마다 별도 await (순차 실행) | 불필요한 네트워크 대기 | Promise.all로 병렬 fetch |
| 탭 전환 시 매번 전체 재렌더링 | 성능 저하, 차트 깜빡임 | Lazy loading 플래그로 최초 1회만 렌더 |
| JS 파일에 하드코딩된 토큰/시크릿 | 보안 위험 | → github-pages-gitops skill 참조 (localStorage + PAT) |

---

## 10. 확장 체크리스트

### 새 탭 추가 시

1. `index.html`: `<li class="nav-item">` + `<div class="tab-pane">` 추가
2. `main.js`: `setupTabEvents()`에 새 case 추가, `<tab>Rendered` 플래그 선언
3. `ui.js` 또는 `charts.js`: 해당 탭의 render 함수 추가
4. `style.css`: 탭 고유 스타일이 필요하면 추가

### 새 차트 추가 시

1. `index.html`: `<canvas id="newChart">` 추가
2. `charts.js`: 모듈 스코프 변수 선언 + `renderNewChart()` 함수 추가 + `resizeAllCharts()` 배열에 추가
3. `utils.js`: 차트 데이터 계산 함수가 필요하면 추가
4. `main.js`: 적절한 탭의 render 함수에서 `renderNewChart()` 호출

### 새 데이터 필드 추가 시

1. `data/*.json`: 백엔드(GitHub Actions 등)가 생성하는 JSON에 필드 추가
2. `utils.js`: 새 필드를 활용하는 계산 함수 추가
3. `ui.js`: 카드/테이블에 새 필드 표시하는 render 로직 추가
4. `charts.js`: 차트에 새 데이터 시리즈가 필요하면 추가

---

## 11. 참조 스킬

| 작업 | 참조 스킬 |
|------|----------|
| config.json 읽기/쓰기 (GitHub API 커밋) | **github-pages-gitops** |
| GitHub API 409 Conflict / SHA 오류 디버깅 | **github-pages-gitops** |
| PAT 관리, Base64 인코딩/디코딩 | **github-pages-gitops** |
| docs/ 디렉토리 구조, 모듈 분리, 탭 확장 | **이 스킬 (github-pages-architecture)** |
