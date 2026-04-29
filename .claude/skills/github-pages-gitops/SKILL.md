---
name: github-pages-gitops
description: >
  GitHub Pages + GitHub API 커밋 기반 설정 변경 패턴을 구현한다.
  MagicSplit의 docs/ 웹 UI에 새 필드를 추가하거나, config-editor 페이지를 처음
  구성하거나, config.json의 새 파라미터와 HTML 폼을 연동하거나, GitHub API
  409 Conflict / SHA 불일치 오류를 디버깅할 때 반드시 이 스킬을 사용한다.
  docs/ 폴더의 JavaScript가 config.json을 읽고 쓰는 모든 작업의 기준이다.
  Make sure to use this skill whenever the user mentions config editor, docs/ UI,
  GitHub Pages settings, commit-based config, or config.json web form.
---

# GitHub Pages GitOps 패턴

## 1. 패턴 개요

GitHub Pages는 정적 파일 호스팅이다. 서버가 없으므로 설정값을 저장하려면
**GitHub Contents API**를 통해 파일을 직접 커밋해야 한다.

핵심 원칙:
- **GET** → 파일 내용 + `sha` 읽기 (페이지 로드 시)
- **PUT** → 변경된 내용 + 현재 `sha` 전송 (저장 시 커밋 생성)
- **SHA**: PUT 요청마다 파일의 현재 SHA가 필수다. 없으면 GitHub이 409 Conflict를 반환한다. 저장 성공 후 반드시 응답에서 새 SHA로 교체한다.
- **Base64**: GitHub API는 파일 내용을 Base64로 주고받는다. UTF-8 한글이 포함된 경우 `btoa/atob` 외에 `encodeURIComponent/decodeURIComponent` 래핑이 필요하다.
- **PAT**: 쓰기 작업은 `contents: write` 권한이 있는 GitHub Personal Access Token이 필요하다. 브라우저 `localStorage`에 저장해 재입력을 줄인다.

---

## 2. 아키텍처 흐름

```
[브라우저 GitHub Pages]
  │
  ├─ 페이지 로드
  │   GET api.github.com/repos/{owner}/{repo}/contents/config.json?t={timestamp}
  │   ← { sha, content: "<base64>" }
  │   → currentSha = sha (메모리 보관)
  │   → JSON.parse(decode(content)) → 폼 필드 채우기
  │
  ├─ 사용자가 폼 수정 후 저장
  │   PUT api.github.com/repos/{owner}/{repo}/contents/config.json
  │   → body: { message, content: encode(JSON), sha: currentSha }
  │   ← { content: { sha: "<new_sha>" } }
  │   → currentSha = new_sha (다음 저장을 위해 갱신)
  │
[GitHub 저장소]
  └─ GitHub Actions cron (trading-bot-overseas.yml, trading-bot-domestic.yml)
      → actions/checkout (최신 config.json 포함)
      → python src/main.py → config.json 읽기 → 봇 실행
```

---

## 3. JavaScript 핵심 템플릿

### Template A — 페이지 로드 (config.json 읽기)

```javascript
const OWNER = 'junghyun99';
const REPO  = 'magicsplit';
const FILE  = 'config.json';

let currentSha = '';

function getToken() {
    return document.getElementById('gh_token').value.trim();
}

function setStatus(msg, type = 'ok') {
    const el = document.getElementById('status');
    if (el) { el.textContent = msg; el.style.color = type === 'error' ? 'red' : 'green'; }
}

window.onload = async () => {
    // 저장된 토큰 복원
    const saved = localStorage.getItem('gh_token');
    if (saved) document.getElementById('gh_token').value = saved;

    // config.json 로드 (캐시 방지용 타임스탬프)
    const url = `https://api.github.com/repos/${OWNER}/${REPO}/contents/${FILE}?t=${Date.now()}`;
    try {
        const res  = await fetch(url, {
            headers: { Authorization: `Bearer ${getToken()}` }
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        currentSha = data.sha;  // 필수: 다음 PUT에 사용

        // Base64 디코딩 (UTF-8 한글 지원)
        const cfg = JSON.parse(decodeURIComponent(escape(atob(data.content))));

        // --- 폼 필드에 값 반영 (호출 코드에서 구현) ---
        populateForm(cfg);
    } catch (e) {
        setStatus('설정 불러오기 실패: ' + e.message, 'error');
    }
};
```

### Template B — 저장 (config.json 커밋)

```javascript
async function saveConfig() {
    const token = getToken();
    if (!token) { alert('GitHub 토큰(PAT)을 입력하세요'); return; }
    localStorage.setItem('gh_token', token);

    // 폼에서 설정 객체 조립 (호출 코드에서 구현)
    const newCfg = buildConfigFromForm();

    // Base64 인코딩 (UTF-8 한글 지원)
    const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(newCfg, null, 2))));

    const url = `https://api.github.com/repos/${OWNER}/${REPO}/contents/${FILE}`;
    try {
        const res = await fetch(url, {
            method: 'PUT',
            headers: {
                Authorization: `Bearer ${token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: '🤖 UI에서 설정 업데이트',
                content: encoded,
                sha: currentSha   // 현재 SHA 필수
            })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        // 성공 후 SHA 갱신 (다음 저장 충돌 방지)
        const data = await res.json();
        currentSha = data.content.sha;

        setStatus('저장 완료 (커밋됨)');
    } catch (e) {
        setStatus('저장 실패: ' + e.message, 'error');
    }
}
```

---

## 4. config.json 스키마

MagicSplit의 `config.json` 구조 (`src/strategy_config.py` → `StockRule` 기준):

```json
{
  "stocks": [
    {
      "ticker": "AAPL",
      "exchange": "NAS",
      "buy_threshold_pct": -5.0,
      "sell_threshold_pct": 10.0,
      "buy_amount": 500,
      "max_lots": 10,
      "enabled": true
    }
  ],
  "global": {
    "check_interval_minutes": 60,
    "notification_enabled": true
  }
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `ticker` | string | 종목 심볼 (예: AAPL, 005930) |
| `exchange` | string | 거래소 코드 (NAS, NYS, KRX 등) — `StrategyConfig._load()`가 내부 등록에 사용하므로 UI에 노출하지 않더라도 **반드시 라운드트립 유지** |
| `buy_threshold_pct` | float | 매수 기준 하락률 (음수, 예: -5.0 = 5% 하락 시 매수) |
| `sell_threshold_pct` | float | 매도 기준 상승률 (양수, 예: 10.0 = 10% 상승 시 매도) |
| `buy_amount` | int | 1회 매수 금액 (원/달러) |
| `max_lots` | int | 최대 분할 매수 횟수 |
| `enabled` | bool | 해당 종목 활성화 여부 |
| `global.check_interval_minutes` | int | 봇 실행 주기 (분) |
| `global.notification_enabled` | bool | 알림 발송 여부 |

---

## 5. 작업별 플레이북

### Playbook 1: 기존 UI에 새 config 필드 추가

1. 새 필드가 `stocks[]` 항목 필드인지 `global` 필드인지 결정
2. `docs/index.html` (또는 config-editor.html)에 `<input>` / `<select>` 태그 추가
3. **Template A** `populateForm(cfg)` 안에서 새 필드 값을 폼에 설정
   - stocks 필드: `cfg.stocks.forEach((s, i) => { row.querySelector('.new-field').value = s.newField ?? 기본값; })`
   - global 필드: `document.getElementById('new_field').value = cfg.global.newField ?? 기본값;`
4. **Template B** `buildConfigFromForm()` 안에서 새 필드를 포함해 반환
5. `src/strategy_config.py`의 `StockRule` 또는 global 파싱 코드와 필드명 일치 확인

### Playbook 2: config-editor 페이지 최초 구성

1. `docs/config-editor.html` 생성 (기존 `docs/index.html`은 읽기 전용 대시보드이므로 수정 금지)
2. `<input type="password" id="gh_token">` + localStorage 저장 로직 포함
3. Template A로 페이지 로드 시 자동으로 config.json 읽기
4. 각 설정 항목별 `<input>` 폼 구성 (스키마 섹션 참조)
5. 저장 버튼 `onclick="saveConfig()"` — Template B 사용
6. `docs/config-editor.js` 로 JavaScript 분리 권장
7. 검증: PAT 입력 후 저장 클릭 → 저장소에 새 커밋 생성 확인

### Playbook 3: GitHub API 오류 디버깅

| 상태 코드 | 원인 | 해결 |
|-----------|------|------|
| **409 Conflict** | `currentSha`가 오래됨 | 페이지 재로드 후 다시 저장. 또는 저장 성공 후 `currentSha` 갱신 코드 누락 확인 |
| **422 Unprocessable** | `content` 필드가 올바른 Base64가 아님 | `btoa(unescape(encodeURIComponent(...)))` 인코딩 경로 확인 |
| **401 / 403** | PAT 없음·만료·권한 부족 | `contents: write` 권한 포함된 PAT 재발급 후 입력 |
| **data.content undefined** | FILE 경로 또는 OWNER/REPO 상수 오류 | 상수값과 실제 저장소 경로 일치 확인 |
| 저장 성공인데 봇이 새 값 미사용 | GitHub Actions가 이전 checkout 사용 | `trading-bot-domestic.yml` 또는 `trading-bot-overseas.yml`의 cron 대기 (최대 1시간). 또는 GitHub UI에서 `workflow_dispatch` 수동 트리거 |
| 캐시된 이전 값 로드 | 브라우저 캐시 | GET URL에 `?t=${Date.now()}` 포함 확인 |

### Playbook 4: stocks 배열을 동적 행(row)으로 렌더링

stocks는 단일 값이 아닌 배열이므로 템플릿 행 방식을 사용한다.

**로드 시:**
```javascript
function populateForm(cfg) {
    const container = document.getElementById('stocks-container');
    container.innerHTML = '';
    cfg.stocks.forEach(s => container.appendChild(createStockRow(s)));
}

function createStockRow(s) {
    const row = document.createElement('div');
    row.className = 'stock-row';
    row.innerHTML = `
        <input class="ticker"   value="${s.ticker}">
        <input class="exchange" value="${s.exchange}" type="hidden">  <!-- 필수: 숨김 유지 -->
        <input class="buy_pct"  value="${s.buy_threshold_pct}" type="number">
        <input class="sell_pct" value="${s.sell_threshold_pct}" type="number">
        <input class="amount"   value="${s.buy_amount}" type="number">
        <input class="max_lots" value="${s.max_lots}" type="number">
        <input class="enabled"  type="checkbox" ${s.enabled ? 'checked' : ''}>
    `;
    return row;
}
```

**저장 시:**
```javascript
function buildConfigFromForm() {
    const stocks = Array.from(document.querySelectorAll('.stock-row')).map(row => ({
        ticker:             row.querySelector('.ticker').value,
        exchange:           row.querySelector('.exchange').value,  // 숨김이어도 반드시 포함
        buy_threshold_pct:  parseFloat(row.querySelector('.buy_pct').value),
        sell_threshold_pct: parseFloat(row.querySelector('.sell_pct').value),
        buy_amount:         parseInt(row.querySelector('.amount').value),
        max_lots:           parseInt(row.querySelector('.max_lots').value),
        enabled:            row.querySelector('.enabled').checked
    }));
    return {
        stocks,
        global: {
            check_interval_minutes: parseInt(document.getElementById('interval').value),
            notification_enabled:   document.getElementById('notify').checked
        }
    };
}
```
