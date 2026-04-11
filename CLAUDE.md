# MagicSplit - 분할매수/익절 자동매매 봇

## 프로젝트 개요
종목별 매수가 대비 %에 따라 분할 매수(물타기) 또는 매도(익절)를 자동 실행하는 Python 트레이딩 봇.
각 매수 건(lot)을 개별 추적하여 분할별로 독립적인 매매 판단을 수행한다.

## 기술 스택
- Python 3.10
- pandas, requests, python-dotenv, PyYAML
- pytest + pytest-cov (테스트)

## 주요 명령어
- 테스트: `pytest --cov=src --cov-report=term-missing --cov-fail-under=80 tests/`
- 봇 실행: `python src/main.py`
- 의존성 설치: `pip install -r requirements.txt`

## 프로젝트 구조
```
src/
├── main.py              # MagicSplitBot 진입점 (멀티 계정 지원)
├── config.py            # 티커-거래소 매핑, 인프라 설정
├── strategy_config.py   # config.json 로더 → StockRule 리스트
├── account_config.py    # accounts.yaml 로더
├── core/
│   ├── engine/          # base (MagicSplitEngine), registry
│   ├── logic/           # split_evaluator (분할 매수/매도 판단)
│   ├── interfaces.py    # 추상 인터페이스 정의
│   └── models.py        # 도메인 모델 (PositionLot, StockRule, SplitSignal 등)
├── infra/
│   ├── broker/          # KIS domestic/overseas/mock 브로커
│   ├── data.py          # YFinanceLoader (선택적)
│   ├── repo.py          # JsonRepository (positions.json, status.json, history.json)
│   └── notifier.py      # SlackNotifier, TelegramNotifier
├── utils/               # TradeLogger
tests/                   # 테스트 (80% 커버리지 요구)
docs/                    # 웹 대시보드 및 데이터 저장
accounts.yaml            # 멀티 계정 설정 (accounts.yaml.example 참고)
config.json              # 종목별 매매 규칙 (GitHub Pages UI에서 수정)
```

## 아키텍처 규칙
- Clean Architecture 패턴: core(도메인) → infra(인프라) 방향으로 의존
- core/interfaces.py에 정의된 추상 인터페이스를 통해 의존성 주입
- 엔진 레지스트리 패턴: `@register_engine` 데코레이터로 엔진 등록
- 멀티 계정: accounts.yaml → AccountConfig → 계정별 엔진 인스턴스 생성

## 핵심 알고리즘 (MagicSplit)
- 종목별 `config.json`에 정의된 매매 규칙(StockRule)에 따라 동작
- 각 매수 건(PositionLot)을 개별 추적 (분할별 개별 관리)
- lot의 매수가 대비 현재가 % 변동으로 매수/매도 판단:
  - 하락 N% 이하 → 추가 매수 (max_lots 미만일 때)
  - 상승 M% 이상 → 매도 (익절)
- 매도 주문을 먼저 실행한 후 매수 진행 (자금 부족 방지)

## 환경변수 (.env)
- `ACCOUNTS_CONFIG_PATH` - accounts.yaml 경로 (기본값: "accounts.yaml")
- `{PREFIX}_KIS_APP_KEY`, `{PREFIX}_KIS_APP_SECRET`, `{PREFIX}_KIS_ACC_NO` - 계정별 KIS API 인증
- `SLACK_WEBHOOK_URL` - Slack 알림

## 멀티 계정 설정 (accounts.yaml)
각 계정 항목:
- `id` - 계정 식별자
- `market_type` - "domestic" 또는 "overseas"
- `is_live` - 실거래 여부 (true/false)
- `engine` - 사용할 엔진 이름 (레지스트리 키)
- `kis_env_prefix` - 환경변수 prefix (예: "ACC1" → `ACC1_KIS_APP_KEY`)

## CI/CD
GitHub Actions 워크플로우:
- `python-test.yml` - main 브랜치 Push/PR 시 단위 테스트 (80% 커버리지 필수)
- `trading-bot.yml` - 크론 스케줄: 매매 봇 자동 실행

## 주의사항
- .env 파일과 accounts.yaml은 절대 커밋하지 않을 것 (accounts.yaml.example 참고)
- 실거래 여부는 accounts.yaml의 `is_live` 필드로 계정별 설정
- 매도 주문을 먼저 실행한 후 매수 진행 (자금 부족 방지)
- 코드 편집할때는 반드시 파일을 먼저 읽어라
