# Config Editor - GitHub 연동 방식 이식 가이드

이 문서는 `docs/config-editor.html`(및 `docs/manual-trade.html`)이 **서버 없이**
브라우저 + GitHub Personal Access Token(PAT)만으로 저장소를 읽고/쓰고/GitHub Actions를
실행하는 방식을 정리한 것이다. 다른 저장소에 그대로 이식할 수 있도록 핵심 패턴과
실제 코드 예시, 전체 플로우를 담았다.

---

## 1. 한눈에 보는 구조

GitHub Pages는 정적 호스팅이라 백엔드 서버가 없다. 그래서 "설정 저장" 같은 쓰기 작업을
모두 **클라이언트(브라우저)가 GitHub REST API를 직접 호출**해서 처리한다. 인증 수단은
사용자가 입력하는 PAT 하나뿐이고, 이 토큰은 `localStorage`에만 보관한다.

이 저장소에는 토큰 하나로 동작하는 두 가지 패턴이 공존한다.

| 패턴 | 사용처 | 동작 | GitHub API |
|------|--------|------|------------|
| **A. 직접 커밋** | config-editor | 브라우저가 파일을 GET -> 수정 -> PUT 으로 직접 커밋 | `GET/PUT /contents/{path}` |
| **B. 액션 트리거** | manual-trade | 브라우저가 워크플로우를 실행시키고, **Action 잡이 결과를 커밋** | `POST /actions/workflows/{file}/dispatches` |

> 질문에서 말한 "토큰값만 있으면 GitHub Action을 수행시키고, Action이 내용을 저장소에
> 커밋하는 방식"은 **패턴 B**에 해당한다. config-editor 자체는 패턴 A(직접 커밋)를 쓰지만,
> 두 패턴 모두 같은 `GitHubAPI` 클라이언트(`docs/js/services/github-api.js`)를 공유하므로
> 함께 이식하는 것을 권장한다.

---

## 2. 공통 토대: GitHubAPI 클라이언트

두 패턴이 공유하는 단일 클래스다. PAT를 받아 헤더를 만들고, 파일 읽기/쓰기/워크플로우
실행 메서드를 제공한다. (출처: `docs/js/services/github-api.js`)

```javascript
class GitHubAPI {
    constructor(token, owner, repo) {
        this.token = token;
        this.owner = owner;
        this.repo = repo;
        this.baseUrl = `https://api.github.com/repos/${owner}/${repo}`;
    }

    get headers() {
        return {
            'Authorization': `Bearer ${this.token}`,
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        };
    }

    // [패턴 A] 파일 읽기: 내용 + sha 반환 (sha는 쓰기에 필수)
    async getFile(path) {
        const res = await fetch(`${this.baseUrl}/contents/${path}`, { headers: this.headers });
        if (!res.ok) {
            let errMsg = res.statusText;
            try { const e = await res.json(); if (e.message) errMsg = e.message; } catch (e) {}
            throw new Error(`[${res.status}] ${errMsg}`);
        }
        const data = await res.json();
        // Base64 디코드 (UTF-8 한글 지원)
        const content = decodeURIComponent(escape(atob(data.content.replace(/\n/g, ''))));
        return { content, sha: data.sha };
    }

    // [패턴 A] 파일 쓰기: 새 내용 + 현재 sha 로 커밋 생성
    async updateFile(path, content, message, sha) {
        const encoded = btoa(unescape(encodeURIComponent(content)));  // Base64 인코드 (UTF-8)
        const res = await fetch(`${this.baseUrl}/contents/${path}`, {
            method: 'PUT',
            headers: this.headers,
            body: JSON.stringify({ message, content: encoded, sha })
        });
        if (res.status === 409) {
            throw new Error('Conflict: 파일이 그 사이 변경됨. 다시 불러온 뒤 진행하세요.');
        }
        if (!res.ok) {
            const e = await res.json();
            throw new Error(`Failed to update file: ${e.message}`);
        }
        return await res.json();  // { content: { sha: "<new_sha>" }, commit: {...} }
    }

    // [패턴 B] 워크플로우 실행 (workflow_dispatch)
    async triggerWorkflow(workflowFileName, inputs) {
        const res = await fetch(`${this.baseUrl}/actions/workflows/${workflowFileName}/dispatches`, {
            method: 'POST',
            headers: this.headers,
            body: JSON.stringify({ ref: 'main', inputs })
        });
        if (!res.ok) {
            const e = await res.json();
            throw new Error(`워크플로우 실행 실패: ${e.message}`);
        }
        return true;
    }

    // [패턴 B] 방금 실행한 워크플로우의 최신 run 조회 (로그 링크용)
    async getLatestWorkflowRun(workflowFileName) {
        const res = await fetch(`${this.baseUrl}/actions/workflows/${workflowFileName}/runs?per_page=1`, {
            headers: this.headers
        });
        if (!res.ok) return null;
        const data = await res.json();
        return data.workflow_runs && data.workflow_runs.length > 0 ? data.workflow_runs[0] : null;
    }
}
window.GitHubAPI = GitHubAPI;
```

### 핵심 주의점

- **sha 필수**: `updateFile`(PUT)에는 그 파일의 현재 `sha`가 반드시 들어가야 한다.
  없거나 오래된 값이면 GitHub가 **409 Conflict**를 던진다. PUT 성공 응답의
  `content.sha`로 즉시 교체해 다음 저장에 대비한다.
- **Base64 + UTF-8**: GitHub Contents API는 파일 내용을 Base64로 주고받는다. 한글 등
  멀티바이트 문자가 있으면 `btoa`/`atob`만으로는 깨진다. 반드시
  `btoa(unescape(encodeURIComponent(...)))` / `decodeURIComponent(escape(atob(...)))`
  래핑을 쓴다.
- **PAT 스코프**: 두 패턴 모두 클래식 PAT 기준 `repo` 스코프(파인그레인드면
  `Contents: Read/Write` + `Actions: Read/Write`)가 필요하다.

---

## 3. 패턴 A - 직접 커밋 (config-editor)

브라우저가 설정 파일을 직접 읽어 폼에 채우고, 사용자가 수정한 뒤 저장하면 브라우저가
곧바로 커밋을 만든다. **GitHub Action을 거치지 않는다.**

### 플로우

```
[브라우저 / GitHub Pages]                       [GitHub 저장소]
  |
  | 1) 사용자가 PAT/Owner/Repo/Path 입력 후 "불러오기"
  |---- GET /repos/{owner}/{repo}/contents/{path} -------->|
  |<--- { sha, content: "<base64>" } ----------------------|
  |     currentSha = sha (메모리에 보관)
  |     JSON.parse(decode(content)) -> 폼 렌더
  |
  | 2) 사용자가 폼 수정
  |
  | 3) "저장 (Commit)" 클릭
  |---- PUT /repos/{owner}/{repo}/contents/{path} -------->|  새 커밋 생성
  |     body: { message, content: encode(json), sha }      |  (브라우저가 직접 author)
  |<--- { content: { sha: "<new_sha>" } } -----------------|
  |     currentSha = new_sha (다음 저장 충돌 방지)
  |
  | 4) (선택) 재로드해서 최신 sha 동기화
  |
                                              [별도 cron 워크플로우가
                                               다음 실행 때 checkout 으로
                                               바뀐 파일을 자동 반영]
```

### 컨트롤러 코드 (발췌)

출처: `docs/js/controllers/config-controller.js`

```javascript
// 1) 불러오기 - PAT/Owner/Repo/Path 저장 후 GET
document.getElementById('load-config-btn').addEventListener('click', async () => {
    const token = tokenInput.value.trim();
    const owner = ownerInput.value.trim();
    const repo  = repoInput.value.trim();
    const path  = pathInput.value.trim();
    if (!token || !owner || !repo || !path) { /* 경고 */ return; }

    localStorage.setItem('githubToken', token);   // 토큰은 localStorage 에만 저장
    localStorage.setItem('githubOwner', owner);
    localStorage.setItem('githubRepo', repo);
    localStorage.setItem('githubConfigPath', path);

    githubApi = new GitHubAPI(token, owner, repo);
    await loadConfig(path);
});

async function loadConfig(path) {
    const { content, sha } = await githubApi.getFile(path);
    ConfigModel.setConfigData(path, content, sha);   // sha를 모델에 저장
    // ... 폼 렌더 ...
}

// 3) 저장 - 모델의 sha 와 함께 PUT
async function saveConfigToGithub() {
    if (!githubApi || !ConfigModel.getSha() || !ConfigModel.getConfig()) return;
    const contentStr = ConfigModel.getSaveContent();
    const msg = `chore(config): update rules via web editor`;

    await githubApi.updateFile(ConfigModel.getPath(), contentStr, msg, ConfigModel.getSha());

    // 성공 후 재로드 -> 최신 sha 동기화 (다음 저장 409 방지)
    setTimeout(() => loadConfig(ConfigModel.getPath()), 1500);
}
```

> 핵심: `getFile`로 받은 `sha`를 모델(`ConfigModel`)에 보관했다가 `updateFile`에 그대로
> 넘긴다. 저장 직후 재로드해 `sha`를 새로 받아오는 것이 연속 저장 시 충돌을 막는 가장
> 안전한 패턴이다.

---

## 4. 패턴 B - 액션 트리거 + 액션이 커밋 (manual-trade)

질문에서 말한 방식이다. 브라우저는 **데이터를 직접 쓰지 않는다.** 대신 토큰으로
`workflow_dispatch`를 호출해 GitHub Action을 실행시키고, **그 Action 잡이 실제 작업
(예: 매매 실행)을 수행한 뒤 결과 파일을 저장소에 커밋**한다.

이 방식은 다음과 같은 경우에 쓴다.
- 브라우저에 둘 수 없는 비밀값(예: 증권사 API 키)이 서버 측에서만 필요할 때
- 브라우저에서 못 하는 무거운/장시간 작업(Python 실행, 외부 API 호출)이 필요할 때
- 결과물(데이터/로그)을 저장소에 커밋해야 할 때

### 플로우

```
[브라우저 / GitHub Pages]                         [GitHub 저장소 / Actions]
  |
  | 1) 사용자가 액션 버튼 클릭 (예: 매수)
  |---- POST /actions/workflows/manual-trade.yml/dispatches ---->|
  |     body: { ref: "main", inputs: { ticker, action, ... } }   |
  |<--- 204 No Content (실행 접수) ------------------------------|
  |                                                              v
  |                                              [Action 잡 실행 시작]
  |                                              - actions/checkout
  |                                              - Python 등 작업 수행
  |                                                (secrets 로 외부 API 호출)
  |                                              - 결과 파일 생성/수정
  |                                              - git-auto-commit-action 으로
  |                                                저장소에 자동 커밋 + push
  |                                                          |
  | 2) (선택) 최신 run 조회 -> 로그 링크 표시                     |
  |---- GET /actions/workflows/manual-trade.yml/runs?per_page=1 >|
  |<--- { workflow_runs: [{ html_url, ... }] } -----------------|
  |     "실행 로그 보기" 링크 렌더
```

### 클라이언트 코드 (발췌)

출처: `docs/js/manual-trade.js`

```javascript
async function executeTrade() {
    const inputs = {
        market_type: activeOrderParams.marketType,  // workflow_dispatch inputs 와 1:1 매칭
        ticker:      activeOrderParams.ticker,
        action:      activeOrderParams.action,
    };
    // 선택 입력은 조건부로만 추가
    if (isBuy && enteredAmount > 0) inputs.amount = String(enteredAmount);

    // 1) 토큰으로 워크플로우 실행
    await githubApi.triggerWorkflow('manual-trade.yml', inputs);
    showFeedback('매매 요청 성공! 1~2분 후 반영됩니다.', 'success');

    // 2) 잠시 후 최신 run 을 찾아 로그 링크 노출
    setTimeout(async () => {
        const run = await githubApi.getLatestWorkflowRun('manual-trade.yml');
        if (run) { /* run.html_url 로 링크 생성 */ }
    }, 3000);
}
```

### 워크플로우 코드 (발췌)

출처: `.github/workflows/manual-trade.yml`. 핵심은 (1) `workflow_dispatch.inputs`가
클라이언트의 `inputs`와 정확히 일치, (2) `permissions: contents: write`, (3) 비밀값은
저장소 Secrets에서 주입, (4) 마지막 단계에서 결과를 **자동 커밋**.

```yaml
name: Manual Trade via Actions

on:
  workflow_dispatch:
    inputs:                       # 클라이언트 triggerWorkflow(inputs) 와 1:1 매칭
      market_type:
        type: choice
        options: [domestic, overseas]
        required: true
      ticker:
        type: string
        required: true
      action:
        type: choice
        options: [buy, sell, sell_all]
        required: true
      amount:
        type: string
        required: false
        default: ''

permissions:
  contents: write                 # Action 이 저장소에 커밋하려면 필수

jobs:
  trade:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v3
        with: { python-version: "3.10" }

      - run: pip install -r requirements.txt

      - name: Execute work
        env:
          # 비밀값은 브라우저가 아니라 저장소 Secrets 에서만 주입한다
          KIS_APP_KEY: ${{ secrets.KIS_APP_KEY }}
          KIS_APP_SECRET: ${{ secrets.KIS_APP_SECRET }}
          # 셸 인젝션 방지: 입력값은 반드시 env 경유로 전달 (run 안에서 직접 보간 금지)
          TICKER: ${{ github.event.inputs.ticker }}
          ORDER_ACTION: ${{ github.event.inputs.action }}
        run: |
          python scripts/manual_trade.py --ticker "$TICKER" --action "$ORDER_ACTION"

      # 결과 파일을 저장소에 자동 커밋 + push
      - name: Commit results
        uses: stefanzweifel/git-auto-commit-action@b863ae1933cb653a53c021fe36dbb774e1fb9403 # v5.2.0
        with:
          commit_message: "Manual trade update: ${{ github.event.inputs.ticker }} ${{ github.event.inputs.action }} [skip ci]"
          file_pattern: "docs/data/${{ github.event.inputs.market_type }}/*.json"
          commit_user_name: "github-actions[bot]"
          commit_user_email: "github-actions[bot]@users.noreply.github.com"
```

> 클라이언트의 `inputs` 키와 워크플로우의 `on.workflow_dispatch.inputs` 키는 **이름이
> 정확히 같아야** 한다. 하나라도 빠지거나 이름이 다르면 422 오류가 난다.

---

## 5. 어떤 패턴을 쓸까

| 상황 | 권장 패턴 |
|------|-----------|
| 단순 설정/텍스트 파일을 사람이 편집해 커밋 | **A. 직접 커밋** (즉시 반영, Action 불필요) |
| 비밀키/외부 API/무거운 연산이 필요 | **B. 액션 트리거** (Secrets는 서버 측에만) |
| 브라우저에 노출되면 안 되는 자격증명이 작업에 필요 | **B. 액션 트리거** (필수) |
| 결과물을 봇 계정으로 커밋하고 싶음 | **B. 액션 트리거** |

핵심 차이: **패턴 A의 커밋 author는 PAT 소유자(사람)**, **패턴 B의 커밋 author는
`github-actions[bot]`**이다. 또한 패턴 A는 PAT에 파일 쓰기 권한만 있으면 되지만,
패턴 B는 **PAT에 Actions 실행 권한**이 추가로 필요하다.

---

## 6. 새 저장소로 이식하기 (체크리스트)

### 공통

1. `docs/js/services/github-api.js`의 `GitHubAPI` 클래스를 그대로 복사한다.
2. HTML에 인증 입력 폼을 추가한다(아래 예시). 토큰은 `type="password"`로 받고
   `localStorage`에 저장한다.

```html
<div class="form-group">
  <label>Personal Access Token (PAT)</label>
  <input type="password" id="github-token" placeholder="ghp_...">
  <small>repo 스코프 권한 필요. 브라우저 로컬 스토리지에만 저장됩니다.</small>
</div>
<input type="text" id="github-owner" placeholder="Owner">
<input type="text" id="github-repo"  placeholder="Repo">
```

3. PAT를 발급한다.
   - 클래식: `repo` 스코프 (패턴 B도 쓰면 `workflow` 포함)
   - 파인그레인드: 대상 저장소 + `Contents: Read and write`
     (패턴 B는 `Actions: Read and write` 추가)
4. 캐시 무효화: GET URL이 캐시될 수 있으면 `?t=${Date.now()}`를 붙인다.
   (이 저장소는 PUT 후 재로드로 sha를 동기화한다.)

### 패턴 A를 이식한다면

5. 편집 대상 파일 경로를 `getFile(path)`로 읽어 폼에 채운다. **sha를 반드시 보관**한다.
6. 저장 시 보관한 sha와 함께 `updateFile(path, content, msg, sha)`를 호출하고,
   응답의 새 sha로 교체(또는 재로드)한다.

### 패턴 B를 이식한다면

5. 대상 저장소에 `workflow_dispatch` 트리거를 가진 워크플로우(`.github/workflows/xxx.yml`)를
   만든다. `inputs` 키를 클라이언트와 일치시키고 `permissions: contents: write`를 준다.
6. 작업에 필요한 비밀값은 **저장소 Settings > Secrets**에 등록한다(브라우저에 절대 두지 않는다).
7. 결과를 커밋하려면 마지막에 `git-auto-commit-action`(또는 `git add/commit/push`) 단계를 둔다.
8. 클라이언트에서 `triggerWorkflow('xxx.yml', inputs)`를 호출한다. 입력은 env 경유로만
   `run`에 전달해 셸 인젝션을 막는다.

---

## 7. 보안 및 트러블슈팅

### 보안 주의

- **토큰은 localStorage에만**. 코드/저장소에 하드코딩하지 않는다. 공용 PC에서는 사용 후 삭제.
- **비밀키는 브라우저에 두지 않는다**. 외부 서비스 자격증명이 필요한 작업은 패턴 B로 옮겨
  GitHub Secrets에서만 주입한다.
- **GitHub Pages는 공개**일 수 있으므로 페이지 HTML/JS에 민감정보를 넣지 않는다.
- 워크플로우 `run`에서 사용자 입력을 직접 보간하지 말고 `env:`로 받아 `"$VAR"`로 쓴다.

### 오류 표

| 증상 | 원인 | 해결 |
|------|------|------|
| **409 Conflict** (PUT) | sha가 오래됨/누락 | 다시 불러온 뒤 저장. 성공 응답의 새 sha로 교체하는 로직 확인 |
| **422 Unprocessable** | Base64 인코딩 오류 또는 inputs 키 불일치 | `btoa(unescape(encodeURIComponent(...)))` 경로 / 워크플로우 inputs 이름 일치 확인 |
| **401 / 403** | PAT 없음·만료·권한 부족 | 패턴 A는 Contents 쓰기, 패턴 B는 Actions 권한까지 포함해 재발급 |
| **404** (workflow) | 워크플로우 파일명/ref 오류, 기본 브랜치 아님 | 파일명과 `ref`(보통 `main`) 확인. 워크플로우가 기본 브랜치에 있어야 dispatch 가능 |
| `data.content` undefined | path 또는 owner/repo 오타 | 실제 저장소 경로와 대조 |
| 저장은 됐는데 반영 안 됨 (패턴 A) | cron 워크플로우가 아직 실행 전 | 다음 스케줄 대기 또는 수동 dispatch |
| 한글 깨짐 | Base64 UTF-8 래핑 누락 | `encodeURIComponent`/`decodeURIComponent` 래핑 확인 |

---

## 8. 참고 파일 (이 저장소)

| 파일 | 역할 |
|------|------|
| `docs/js/services/github-api.js` | 공통 GitHubAPI 클라이언트 (두 패턴 공유) |
| `docs/config-editor.html` | 패턴 A UI (인증 폼 + 설정 편집기) |
| `docs/js/controllers/config-controller.js` | 패턴 A 흐름 제어 (불러오기/저장) |
| `docs/js/models/config-model.js` | sha/원본/수정본 상태 보관, diff 생성 |
| `docs/manual-trade.html` | 패턴 B UI |
| `docs/js/manual-trade.js` | 패턴 B 흐름 제어 (triggerWorkflow) |
| `.github/workflows/manual-trade.yml` | 패턴 B 워크플로우 (dispatch + 자동 커밋) |
