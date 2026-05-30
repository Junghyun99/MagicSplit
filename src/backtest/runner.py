# src/backtest/runner.py
import shutil
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.core.models import DayResult
from src.core.engine.base import MagicSplitEngine
from src.infra.repo import JsonRepository
from src.utils.logger import TradeLogger
from src.utils.currency import format_money
from src.strategy_config import StrategyConfig
from src.backtest.fetcher import download_ohlc_data
from src.backtest.components import BacktestBroker, BacktestMarketDataProvider


def _validate_tickers(
    close_df: pd.DataFrame, required: List[str], logger: TradeLogger,
) -> List[str]:
    """close_df에 실제로 수신된 티커와 required를 비교해 누락된 티커를 반환한다."""
    available = set(close_df.columns)
    missing = [t for t in required if t not in available]
    if missing:
        logger.warning(f"데이터 미수신 티커 {missing} — 백테스트를 중단합니다.")
    return missing


def run_backtest(
    config_path: str,
    start_date: str,
    end_date: str,
    initial_cash: float = 10000.0,
    market_type: str = "overseas",
    output_dir: str = "docs/data/backtest",
    run_number: Optional[str] = None,
) -> Optional[DayResult]:
    """MagicSplit 전략 백테스트를 실행한다.

    Args:
        config_path: 설정 파일(config_*.json) 경로
        start_date: 시작일 'YYYY-MM-DD'
        end_date: 종료일 'YYYY-MM-DD'
        initial_cash: 초기 자금 (USD 또는 KRW)
        market_type: 'overseas' 또는 'domestic'
        output_dir: 결과 저장 디렉토리
        run_number: 실행 번호 (로그 구분용)

    Returns:
        마지막 거래일의 DayResult, 또는 데이터가 없으면 None
    """
    if run_number is None:
        run_number = f"{datetime.now().strftime('%H%M%S')}_{market_type}"

    logger = TradeLogger(log_dir="logs/backtest", run_number=run_number)

    # 1. 설정 로드
    strategy = StrategyConfig(config_path=config_path)
    rules = strategy.get_rules_by_market(market_type)
    if not rules:
        logger.warning(f"'{market_type}' 마켓에 해당하는 종목이 없습니다.")
        return None

    tickers = [r.ticker for r in rules if r.enabled]
    if not tickers:
        logger.warning("활성화된 종목이 없습니다.")
        return None

    # 2. 데이터 다운로드 (OHLC; 레짐 지표 + 종가 모두 여기서 파생)
    # 만약 레짐 지표(이동평균/ADX/ATR 등)를 사용한다면, 첫 백테스트 거래일에 충분한 과거 데이터(window_size 봉)가 
    # 확보되도록 데이터 다운로드 시작 시점을 과거로 이동합니다.
    regime_active = any(getattr(r, "regime_enabled", False) for r in rules)
    window_size = max((r.regime_min_bars for r in rules), default=200) + 60
    
    if regime_active:
        start_dt = pd.to_datetime(start_date)
        # 1 거래일 = 약 1.45 영업일. 안전하게 1.6배 곱해 calendar days 산출
        days_to_subtract = int(window_size * 1.6)
        download_start = (start_dt - pd.Timedelta(days=days_to_subtract)).strftime("%Y-%m-%d")
        logger.info(f"레짐 감지 활성화: 과거 데이터 {window_size}봉 확보를 위해 다운로드 시작일을 {start_date}에서 {download_start}로 조정합니다.")
    else:
        download_start = start_date

    logger.info(f"--- 데이터 다운로드: {tickers} ({download_start} ~ {end_date}) ---")
    ohlc_df = download_ohlc_data(tickers, download_start, end_date)
    close_df = ohlc_df["Close"]

    if _validate_tickers(close_df, tickers, logger):
        return None

    # 3. 거래일 산출
    trading_days = close_df.index
    sim_days = [d for d in trading_days
                if start_date <= d.strftime("%Y-%m-%d") <= end_date]

    if not sim_days:
        logger.warning("시뮬레이션 기간에 거래일이 없습니다.")
        return None

    # 4. 컴포넌트 생성
    out_path = Path(output_dir)
    if out_path.exists():
        shutil.rmtree(out_path)

    broker = BacktestBroker(initial_cash=initial_cash, logger=logger)
    repo = JsonRepository(root_path=output_dir)
    # 레짐 지표용 시세 제공자 (브로커와 분리). 레짐 종목이 있을 때만 주입.
    regime_active = any(getattr(r, "regime_enabled", False) for r in rules)
    window_size = max((r.regime_min_bars for r in rules), default=200) + 60
    market_data = (
        BacktestMarketDataProvider(ohlc_df, window_size=window_size)
        if regime_active else None
    )
    engine = MagicSplitEngine(
        broker=broker,
        repo=repo,
        logger=logger,
        stock_rules=rules,
        notifier=None,
        is_live_trading=False,
        market_data=market_data,
    )

    # 5. 시뮬레이션 루프
    logger.info(f"--- 백테스트 시작 ({len(sim_days)} 거래일) ---")

    prev_prices: Dict[str, float] = {}
    last_result: Optional[DayResult] = None

    for today in sim_days:
        logger.info(f"{today.strftime('%Y-%m-%d')} 시뮬시작")
        # 종가 추출
        try:
            row = close_df.loc[today]
            current_prices = row.to_dict()
            # NaN -> 전일 가격으로 대체 (forward-fill)
            current_prices = {
                t: (p if not pd.isna(p) else prev_prices.get(t))
                for t, p in current_prices.items()
                if not pd.isna(p) or prev_prices.get(t) is not None
            }
            prev_prices = current_prices
        except Exception as e:
            logger.warning(f"[{today.date()}] 종가 추출 실패, 건너뜀: {e}")
            continue

        sim_date = today.strftime("%Y-%m-%d")

        broker.set_date(today)
        broker.set_prices(current_prices)

        try:
            # 엔진이 market_data 제공자에서 "오늘 직전까지" 윈도우를 직접 조회한다.
            last_result = engine.run_one_cycle(sim_date=sim_date)
        except Exception as e:
            logger.error(f"[{sim_date}] 사이클 실행 실패: {e}")

    logger.info("--- 백테스트 완료 ---")
    if last_result:
        pf = last_result.final_portfolio
        logger.info(
            f"최종: Cash={format_money(pf.total_cash, market_type)}, "
            f"Value={format_money(pf.total_value, market_type)}"
        )

    return last_result
