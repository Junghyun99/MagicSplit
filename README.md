# 📈 GitHub Serverless Auto-Trading Bot
**오직 GitHub 생태계만 활용하여 유지비용(서버비) 0원으로 구동되는 주식 자동매매 시스템**

이 프로젝트는 별도의 백엔드 서버나 데이터베이스 없이 **GitHub Pages(UI)**, **GitHub API(데이터 통신)**, **GitHub Actions(매매 봇)**만을 결합하여 만든 서버리스(Serverless) 주식 자동매매 프로그램입니다. 모바일 브라우저에서도 설정 변경이 가능하도록 설계되었습니다.

---

## 🌟 핵심 컨셉 (Core Architecture)

본 시스템은 정적 웹페이지의 한계를 **GitHub REST API**를 통해 극복합니다. 

1. **Frontend (GitHub Pages):** 사용자가 웹 UI에서 알고리즘 설정값을 변경합니다.
2. **Bridge (GitHub API):** 브라우저의 JavaScript가 GitHub API를 호출하여 저장소 내의 `config.json` 파일을 직접 수정(Commit)합니다.
3. **Backend (GitHub Actions):** 매 1시간마다 크론(Cron) 스케줄러가 실행되어, 최신 `config.json`을 읽고 증권사 API와 연동하여 자동 매매를 수행합니다.

---

## 🔄 상세 데이터 흐름 (System Flow)

### Flow 1: 사용자의 알고리즘 설정 변경 (UI -> GitHub Repo)
사용자가 스마트폰이나 PC 브라우저로 GitHub Pages(UI)에 접속할 때 발생합니다.

1. **데이터 로드 (GET):** UI 접속 시 JS가 GitHub API를 통해 `config.json`의 현재 설정값(예: 이평선 기간, 매수 금액 등)을 읽어와 화면에 표시합니다. *(이때 브라우저 캐시를 무시하기 위해 타임스탬프를 쿼리로 추가합니다.)*
2. **토큰 검증:** 브라우저의 `LocalStorage`에 저장된 사용자 본인의 **GitHub Personal Access Token (PAT)**을 불러옵니다.
3. **설정 수정 및 저장:** 사용자가 모바일 화면에서 설정값을 변경하고 [저장] 버튼을 누릅니다.
4. **API 호출 (PUT):** JS가 변경된 설정값을 Base64로 인코딩한 후, PAT를 헤더에 담아 GitHub API로 전송합니다.
5. **Commit 발생:** 백그라운드에서 `config.json` 파일이 업데이트되며 새로운 커밋이 레포지토리에 기록됩니다.

### Flow 2: 자동 매매 스크립트 실행 (GitHub Repo -> 증권사 API)
GitHub 서버 내부에서 스케줄에 따라 백그라운드로 실행됩니다.

1. **Actions 트리거:** `.github/workflows/trading.yml`에 설정된 크론(Cron) 시간(예: 매시 정각)에 GitHub Actions 컨테이너가 부팅됩니다.
2. **환경 세팅:** Python/Node.js 환경이 구성되고 필요한 라이브러리를 설치합니다.
3. **설정값 로드:** 파이썬 매매 스크립트가 실행되며, 가장 최신 상태의 `config.json` 파일을 읽어 알고리즘 변수로 메모리에 적재합니다.
4. **증권사 API 통신:** 
   * GitHub Secrets에 안전하게 보관된 증권사 API Key를 환경변수(ENV)로 불러옵니다.
   * 증권사 서버에 접속해 현재가, 잔고 등의 시장 데이터를 가져옵니다.
5. **알고리즘 판별 및 매매:** `config.json`의 설정값과 현재 시장 데이터를 비교하여 조건 부합 시 매수/매도 주문을 전송합니다.
6. **결과 알림:** 매매 체결 결과 및 에러 로그를 텔레그램(Telegram) 또는 디스코드 봇 API를 통해 사용자의 스마트폰으로 전송합니다.

---

## 📂 디렉토리 구조 (Directory Structure)

```text
📦 stock-trading-bot
 ┣ 📂 .github
 ┃ ┗ 📂 workflows
 ┃ ┃ ┗ 📜 trading-bot.yml    # GitHub Actions 크론 스케줄 및 실행 스크립트
 ┣ 📂 docs                   # GitHub Pages 호스팅을 위한 정적 UI 폴더 (HTML/CSS/JS)
 ┃ ┣ 📜 index.html           # 모바일 친화적 설정 UI (Bootstrap/Tailwind 적용)
 ┃ ┗ 📜 app.js               # GitHub API와 통신하는 자바스크립트 로직
 ┣ 📂 src                    # 실제 매매 알고리즘이 구현된 소스 코드
 ┃ ┗ 📜 main.py              # 자동 매매 파이썬 메인 스크립트
 ┣ 📜 config.json            # 봇과 UI를 연결하는 핵심 설정 파일 (자동 커밋됨)
 ┗ 📜 README.md
```

---

## ⚠️ 보안 및 주의사항 (Security Warning)

본 프로젝트는 금융 자산을 다루므로 보안이 가장 중요합니다.

1. **GitHub Token (PAT) 보안:**
   * UI 화면에서 입력하는 GitHub Token은 **절대 소스코드에 하드코딩하지 마세요.**
   * 토큰은 오직 사용자의 기기 브라우저(`LocalStorage`)에만 저장되어야 합니다.
2. **증권사 API Key 보안:**
   * 증권사 App Key, Secret Key 등은 절대 `config.json`이나 소스코드에 텍스트로 적지 마세요.
   * 반드시 레포지토리의 `Settings > Secrets and variables > Actions > Secrets`에 등록한 후, Actions에서 `${{ secrets.API_KEY }}` 형태로 불러와 사용해야 합니다.
3. **Actions 시간 지연:**
   * GitHub Actions의 Cron은 서버 부하에 따라 정각에서 5~15분 정도 지연 실행될 수 있습니다. 초단타(스캘핑) 매매에는 적합하지 않으며, 스윙/적립식 매매에 권장합니다.

---

*📱 **Tip:** 스마트폰 브라우저 메뉴에서 "홈 화면에 추가"를 선택하면, 일반 주식 앱처럼 바탕화면 아이콘을 통해 전체화면으로 UI를 띄워 편리하게 사용할 수 있습니다.*
