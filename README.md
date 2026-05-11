# 📈 MagicSplit: GitHub Serverless Auto-Trading Bot

**GitHub 인프라만 활용하여 서버 유지비 0원으로 구동되는 주식 분할매매/익절 시스템**

MagicSplit은 별도의 서버나 데이터베이스 없이 **GitHub Actions(매매 봇)**, **GitHub Pages(대시보드)**, **GitHub API**를 결합하여 만든 서버리스(Serverless) 주식 자동매매 프로그램입니다. 

---

## 🌟 핵심 컨셉 (Core Philosophy)

MagicSplit은 **"분할 매수"**와 **"계단식 익절"**을 핵심 전략으로 합니다. 주가가 하락할 때 정해진 규칙에 따라 차수(Level)별로 추가 매수하고, 반등 시 각 차수별로 목표 수익률에 도달하면 순차적으로 익절하여 수익을 확정합니다.

1. **차수(Level) 시스템**: 각 매수 건을 독립적인 '차수'로 관리하여 철저한 리스크 관리와 이익 극대화를 동시에 추구합니다.
2. **서버리스(Serverless)**: 24시간 켜져 있는 서버 대신 GitHub Actions의 스케줄러가 정해진 시간에 봇을 깨워 매매를 수행합니다.
3. **모바일 우선(Mobile First)**: 언제 어디서나 스마트폰 브라우저로 대시보드에 접속해 수익 현황을 확인하고 설정값을 변경할 수 있습니다.

---

## ✨ 주요 기능 (Key Features)

### 1. MagicSplit 알고리즘
- **계단식 분할 매수/매도**: 주가 변동에 따라 미리 설정한 % 간격으로 차수를 높여가며 매수하고, 목표 수익률 도달 시 매도합니다.
- **트레일링 스톱 (Trailing Stop)**: 수익이 발생하면 즉시 매도하지 않고 고점을 추적합니다. 고점 대비 특정 % 하락할 때 매도하여 상승 추세를 최대한 누리면서 익절합니다.
- **동적 재매수 (Dynamic Re-entry)**: 트레일링 스톱으로 평소보다 높은 가격에 매도된 경우, 기준가를 상향 조정하여 즉각적인 반등에 대응합니다.
- **재진입 가드 (Re-entry Guard)**: 전량 청산 후 주가가 충분히 하락(X%)하지 않으면 재진입을 제한하여 고점 매수를 방지합니다.

### 2. 강력한 리스크 관리
- **투자 비중 제한 (Max Exposure)**: 전체 자산 대비 특정 종목의 최대 투자 비중을 설정하여 과도한 집중 투자를 방지합니다.
- **국내/해외 통합 지원**: 한국 투자 증권(KIS) API를 통해 국내 주식과 해외 주식을 모두 지원하며, 각각 독립적인 설정으로 운영 가능합니다.

### 3. 직관적인 UI 및 대시보드
- **실시간 현황판**: 현재 보유 종목, 차수 상태, 수익률, 자산 곡선을 한눈에 확인합니다.
- **설정 에디터 (Config Editor)**: 복잡한 JSON 파일 수정 없이 웹 UI에서 종목 추가, 매매 규칙 변경, 프리셋 적용이 가능합니다.
- **차수 히트맵**: 종목별 차수 변화를 시각적으로 보여주어 현재 매매 진행 상황을 직관적으로 파악합니다.

### 4. 전문적인 백테스트 엔진
- **과거 데이터 검증**: 실제 시세 데이터를 기반으로 설정한 알고리즘의 성과를 미리 검증할 수 있습니다.
- **백테스트 리포트**: 상세한 매매 기록과 수익률 차트를 통해 전략의 유효성을 분석합니다.

---

## 🛠 기술 스택 (Tech Stack)

- **Language**: Python 3.10
- **Infrastructure**: GitHub Actions (Runner), GitHub Pages (Hosting)
- **API**: 한국투자증권(KIS) REST API
- **Frontend**: Vanilla JS, CSS (Modern Premium Design), HTML5
- **Data**: JSON (Configuration & Status), Parquet (Market Data Cache)

---

## 🚀 시작하기 (Quick Start)

### 1. 저장소 포크 (Fork)
- 본 저장소 오른쪽 상단의 **Fork** 버튼을 눌러 자신의 계정으로 가져옵니다.

### 2. GitHub Secrets 설정
- `Settings > Secrets and variables > Actions > New repository secret`에 다음 항목을 등록합니다.
  - `KIS_APP_KEY`: 한국투자증권 앱 키
  - `KIS_APP_SECRET`: 한국투자증권 앱 시크릿
  - `KIS_ACC_NO`: 한국투자증권 계좌번호 (8~10자리)
  - `SLACK_WEBHOOK_URL`: (선택) 알림용 Slack Webhook URL

### 3. GitHub Pages 활성화
- `Settings > Pages`에서 Source를 `Deploy from a branch`로 설정하고, Branch를 `main`, Folder를 `/docs`로 선택 후 저장합니다.

### 4. 대시보드 설정
- 생성된 Pages URL에 접속한 후, `Config` 메뉴에서 자신의 **GitHub PAT (Personal Access Token)**를 입력하면 웹에서 설정 수정이 가능해집니다.

---

## 📂 디렉토리 구조 (Directory Structure)

```text
📦 MagicSplit
 ┣ 📂 .github/workflows      # 매매/백테스트/데이터 수집 워크플로우
 ┣ 📂 docs                   # 대시보드 UI 및 상태 데이터 (GitHub Pages)
 ┃ ┣ 📂 data                 # 포지션, 매매 내역, 백테스트 결과 저장
 ┃ ┣ 📜 index.html           # 메인 대시보드
 ┃ ┗ 📜 config-editor.html   # 웹 설정 에디터
 ┣ 📂 src                    # 핵심 소스 코드
 ┃ ┣ 📂 core/logic           # MagicSplit 매매 알고리즘 (핵심)
 ┃ ┣ 📂 infra/broker         # 증권사 API 연동 (KIS)
 ┃ ┗ 📂 backtest             # 백테스트 엔진
 ┣ 📜 config_domestic.json   # 국내 주식 매매 설정
 ┣ 📜 config_overseas.json   # 해외 주식 매매 설정
 ┗ 📜 presets.json           # 공통 매매 규칙 프리셋
```

---

## ⚠️ 보안 및 주의사항

1. **보안 제일**: 증권사 API Key는 절대 코드나 설정 파일에 직접 기재하지 마세요. 반드시 GitHub Secrets를 사용하세요.
2. **GitHub PAT**: 대시보드에서 사용하는 PAT는 브라우저의 `LocalStorage`에만 저장되며 외부로 전송되지 않습니다.
3. **투자 책임**: 본 프로그램은 투자를 보조하는 도구일 뿐입니다. 모든 투자의 결과와 책임은 사용자 본인에게 있습니다. 반드시 소액이나 모의투자(IS_LIVE=false)로 충분히 테스트한 후 실거래에 임하시기 바랍니다.

---

*📱 **Tip**: 스마트폰 브라우저에서 대시보드 URL을 "홈 화면에 추가"하면 실제 앱처럼 편리하게 사용할 수 있습니다.*
