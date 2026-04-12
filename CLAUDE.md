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
├── main.py              # MagicSplitBot 진입점 (단일 계좌, 국내/해외 독립 운용)
├── config.py            # 티커-거래소 매핑, 인프라 설정, KIS 인증
├── strategy_config.py   # config.json 로더 → StockRule 리스트
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
config.json              # 종목별 매매 규칙 (GitHub Pages UI에서 수정)
```

## 아키텍처 규칙
- Clean Architecture 패턴: core(도메인) → infra(인프라) 방향으로 의존
- core/interfaces.py에 정의된 추상 인터페이스를 통해 의존성 주입
- 엔진 레지스트리 패턴: `@register_engine` 데코레이터로 엔진 등록
- 단일 계좌: .env의 KIS 인증으로 국내/해외 브로커 각각 생성
- 국내/해외 독립 운용: 별도 브로커, 저장소, 엔진 인스턴스로 완전 분리
- 종목별 순차 실행: 평가 → 주문 → 포지션 반영 → 다음 종목

## 핵심 알고리즘 (MagicSplit)
- 종목별 `config.json`에 정의된 매매 규칙(StockRule)에 따라 동작
- 각 매수 건(PositionLot)을 개별 추적 (분할별 개별 관리)
- lot의 매수가 대비 현재가 % 변동으로 매수/매도 판단:
  - 하락 N% 이하 → 추가 매수 (max_lots 미만일 때)
  - 상승 M% 이상 → 매도 (익절)
- 종목별 순차 실행: 한 종목 평가→주문→반영 후 다음 종목
- 매도 주문을 먼저 실행한 후 매수 진행 (자금 부족 방지)

## 환경변수 (.env)
- `KIS_APP_KEY` - KIS API 앱 키
- `KIS_APP_SECRET` - KIS API 앱 시크릿
- `KIS_ACC_NO` - KIS 계좌번호
- `IS_LIVE` - 실거래 여부 ("true" / "false", 기본값: "false")
- `SLACK_WEBHOOK_URL` - Slack 알림
- `CONFIG_JSON_PATH` - config.json 경로 (기본값: "config.json")

## CI/CD
GitHub Actions 워크플로우:
- `python-test.yml` - main 브랜치 Push/PR 시 단위 테스트 (80% 커버리지 필수)
- `trading-bot.yml` - 크론 스케줄: 매매 봇 자동 실행

## 주의사항
- .env 파일은 절대 커밋하지 않을 것
- 실거래 여부는 .env의 `IS_LIVE` 필드로 설정
- 매도 주문을 먼저 실행한 후 매수 진행 (자금 부족 방지)
- 코드 편집할때는 반드시 파일을 먼저 읽어라
