# MagicSplit - 분할매수/익절 자동매매 봇

## 프로젝트 개요
종목별 매수가 대비 %에 따라 분할 매수(물타기) 또는 매도(익절)를 자동 실행하는 Python 트레이딩 봇.
차수(Level) 시스템으로 매수/매도를 계단식으로 관리하며, 트레일링 스톱과 동적 재매수 기준으로 변동성에 대응한다.

## 기술 스택
- Python 3.10
- pandas, requests, python-dotenv, PyYAML
- yfinance, pyarrow (백테스트 시세 캐시)
- pytest + pytest-cov (테스트)

## 주요 명령어
운영성 작업(봇 실행/백테스트/수동매매/시세 다운로드)은 GitHub Actions 워크플로우로 실행한다 (CI/CD 섹션 참조).
로컬에서 자주 쓰는 명령어:
- 의존성 설치: `pip install -r requirements.txt`
- 테스트: `pytest` (Windows 터미널: `$env:PYTHONUTF8=1; pytest`)
- 커버리지 포함 테스트: `pytest --cov=src tests/` (80% 이상 준수 필수)
- 봇 1회 실행: `python -m src.main` (`CONFIG_JSON_PATH`로 국내/해외 선택)
- 포지션 정합: `python scripts/reconcile_positions.py`

## 프로젝트 구조
```
src/
├── main.py              # MagicSplitBot 진입점 (단일 계좌, CONFIG_JSON_PATH로 국내/해외 선택)
├── config.py            # 티커-거래소 매핑, 인프라 설정, KIS 인증
├── strategy_config.py   # config_*.json + presets.json 로더 -> StockRule 리스트
├── core/
│   ├── engine/          # base (MagicSplitEngine), registry
│   ├── logic/           # split_evaluator (분할 매수/매도, 트레일링 스톱, 동적 재매수)
│   ├── interfaces.py    # 추상 인터페이스 정의
│   └── models.py        # 도메인 모델 (PositionLot, StockRule, SplitSignal 등)
├── infra/
│   ├── broker/          # KIS domestic/overseas/mock 브로커
│   ├── data.py          # YFinanceLoader (선택적)
│   ├── repo.py          # JsonRepository (positions.json, status.json, history.json)
│   └── notifier.py      # SlackNotifier, TelegramNotifier
├── backtest/            # runner, cache (parquet), fetcher (yfinance), components
├── utils/               # TradeLogger
scripts/
├── manual_trade.py      # Actions/CLI에서 수동 매수·매도 주문
└── reconcile_positions.py  # 실계좌 잔고와 positions.json 정합
tests/                   # 테스트 (80% 커버리지 요구)
docs/                    # 웹 대시보드(GitHub Pages) + config-editor + data 저장
config_domestic.json     # 국내 종목 매매 규칙
config_overseas.json     # 해외 종목 매매 규칙
presets.json             # 차수별 배열 공유 프리셋 (선택)
```

## 아키텍처 규칙
- Clean Architecture 패턴: core(도메인) -> infra(인프라) 방향으로 의존
- core/interfaces.py에 정의된 추상 인터페이스를 통해 의존성 주입
- 엔진 레지스트리 패턴: `@register_engine` 데코레이터로 엔진 등록
- 단일 계좌: .env의 KIS 인증으로 국내/해외 브로커 각각 생성
- 국내/해외 독립 운용: `CONFIG_JSON_PATH`로 어느 마켓을 돌릴지 선택, 별도 브로커·저장소·엔진 인스턴스로 완전 분리
- 종목별 순차 실행: 평가 -> 주문 -> 포지션 반영 -> 다음 종목

## 핵심 알고리즘 (MagicSplit)
- 종목별 `config_domestic.json` / `config_overseas.json`에 정의된 매매 규칙(StockRule)에 따라 동작
- 차수(Level) 시스템: 각 매수 건(PositionLot)에 level(1~max_lots)을 부여하여 추적
- **마지막 차수만 평가**: 가장 높은 level의 매수가 대비 현재가 %로 판단
  - 상승 M% 이상 -> 마지막 차수 매도 (차수 감소, 예: Lv3->Lv2가 마지막)
  - 하락 N% 이하 -> 다음 차수 매수 (차수 증가, 예: Lv3->Lv4 추가)
- **트레일링 스톱** (`trailing_drop_pct` 설정 시):
  - 매도 임계치(`sell_threshold_pct`) 도달 시 활성화 -> `trailing_highest_price` 추적 시작
  - 이후 고점 갱신을 따라가며, 고점 대비 `trailing_drop_pct`% 하락하면 매도
  - 최소 익절을 보장하면서 추가 상승분도 가져가는 구조
- **동적 재매수 기준 (기준가 상향)**:
  - 직전 매도가(last_sell_price)가 마지막 차수 매수가보다 높으면 매도가를 매수 기준으로 사용
  - 트레일링 스톱으로 평소보다 높게 매도된 뒤, 원래 그리드까지 기다리지 않고 매도가 대비 하락 시 즉시 재매수
- **재진입 가드** (`reentry_guard_pct`):
  - 전량 청산 후 직전 매도가 대비 X% 하락해야 1차수 재진입 허용 (음수, 예: -0.1)
  - None이면 가드 비활성 (다음 사이클 즉시 재진입 가능)
- **차수별 배열 + presets.json**:
  - `buy_threshold_pcts` / `sell_threshold_pcts` / `buy_amounts` / `trailing_drop_pcts` 배열로 차수마다 다른 임계치/금액 지정 가능
  - 배열 길이가 차수보다 짧으면 마지막 값으로 clamp
  - `presets.json`에 공통 설정을 정의하고 종목에서 `"preset": "이름"`으로 참조 (종목 필드가 preset을 override)
- **투입 비율 상한** (`max_exposure_pct`):
  - 계좌 총 자산 대비 종목별 최대 투입 비중을 제한 (예: 20.0 = 20%까지만 투입)
  - 매수 전 `(현재 보유 평가액 + 매수 예정 금액) / 총 자산`이 상한을 넘으면 매수 차단
  - **글로벌/개별 계층**: `global.max_exposure_pct`를 기본값으로, 종목별 설정이 있으면 오버라이드
  - None이면 비중 제한 없음 (하위 호환)
- **한 사이클에 한 종목당 매도 OR 매수 중 하나만** 실행 (매도 우선)
- 종목별 순차 실행: 한 종목 평가->주문->반영 후 다음 종목
- 여러 종목 간 매도 신호를 먼저 실행한 후 매수 진행 (자금 부족 방지)
- 차수 흐름 예시: 1,2,3,4,3,4,5,6,5,4,3,2,3,4,5 (오르락내리락)

## 환경변수 (.env)
- `KIS_APP_KEY` - KIS API 앱 키
- `KIS_APP_SECRET` - KIS API 앱 시크릿
- `KIS_ACC_NO` - KIS 계좌번호
- `IS_LIVE` - 실거래 여부 ("true" / "false", 기본값: "false")
- `SLACK_WEBHOOK_URL` - Slack 알림
- `CONFIG_JSON_PATH` - 매매 규칙 파일 경로 (기본값: `config_overseas.json`. 국내는 `config_domestic.json` 지정)
- `PRESETS_JSON_PATH` - 프리셋 파일 경로 (선택, 기본은 config 파일과 같은 디렉토리의 `presets.json`)
- `PYTHONUTF8` - Windows 환경에서 한글 로그 인코딩 오류 방지용 (필수: `1` 설정)

## 로컬 환경 설정
1. `.env.example` 파일을 `.env`로 복사
2. `.env`에 실제 KIS API 키 및 `PYTHONUTF8=1` 설정 확인
3. VS Code 사용자: `.vscode/settings.json`에 `terminal.integrated.env.windows` 설정 권장 (자동 적용됨)
4. 터미널별 UTF-8 모드 수동 활성화 (명령어 실행 전):
   - **PowerShell**: `$env:PYTHONUTF8=1; python -m pytest`
   - **CMD**: `set PYTHONUTF8=1 && python -m pytest`


## CI/CD
GitHub Actions 워크플로우:
- `python-test.yml` - main 브랜치 Push/PR 시 단위 테스트 (80% 커버리지 필수, 실패 시 Slack 알림)
- `trading-bot-overseas.yml` - 해외 매매 봇 (현재 스케줄 비활성, manual dispatch). `CONFIG_JSON_PATH=config_overseas.json`
- `trading-bot-domestic.yml` - 국내 매매 봇 (현재 스케줄 비활성, manual dispatch). `CONFIG_JSON_PATH=config_domestic.json`
- `manual-trade.yml` - Actions UI에서 수동 매수/매도 주문 (market_type, ticker, action, quantity 입력)
- `run-backtest.yml` - 백테스트 실행 (시작/종료일, 초기 자본, 마켓 타입 선택). 결과를 `docs/data/backtest/`에 커밋
- `download-market-data.yml` - yfinance로 종목 시세를 받아 `src/backtest/cache/`에 parquet 캐시로 커밋

## 워크플로우 규칙
- 코드 수정 요청 시 작업 브랜치를 생성하여 커밋하고 PR 생성까지 완료한다
- PR 생성 전 로컬 테스트(pytest)를 실행하여 통과 및 커버리지(80% 이상)를 확인한다
- CI/빌드 에러 발생 시 로그를 분석하여 원인을 파악하고 수정한다
- PR 리뷰가 있으면 내용을 검토하여 반영하고, 미반영 시에는 그 이유를 명확히 설명한다

## 주의사항
- .env 파일은 절대 커밋하지 않을 것
- 실거래 여부는 .env의 `IS_LIVE` 필드로 설정
- 매도 주문을 먼저 실행한 후 매수 진행 (자금 부족 방지)
- **인코딩 호환성**: 로그, 주석, 문자열에 특수 유니코드 문자(예: `→`)를 사용하지 말고 표준 ASCII 기호(예: `->`)를 사용할 것
- 코드 편집할때는 반드시 파일을 먼저 읽어라
- `docs/js/*.js` 또는 `docs/css/*.css` 를 수정할 때는 반드시 `docs/index.html`의 해당 파일 `?v=` 파라미터도 함께 올릴 것 (브라우저 캐시 무효화)
  - 형식: `날짜-순번` (예: `20260425-1`, 같은 날 재수정 시 `20260425-2`로 증가)
