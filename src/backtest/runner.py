# src/backtest/runner.py
import shutil
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional

from src.core.models import DayResult
from src.core.engine.base import MagicSplitEngine
from src.infra.repo import JsonRepository
from src.utils.logger import TradeLogger
from src.strategy_config import StrategyConfig
from src.backtest.fetcher import download_historical_data
from src.backtest.components import BacktestBroker


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
        config_path: config.json 경로
        start_date: 시작일 'YYYY-MM-DD'
        end_date: 종료일 'YYYY-MM-DD'
        initial_cash: 초기 자금 (USD 또는 KRW)
        market_type: 'overseas' 또는 'domestic'
        output_dir: 결과 저장 디렉토리
        run_number: 실행 번호 (로그 구분용)

    Returns:
        마지막 거래일의 DayResult, 또는 데이터가 없으면 None
    """
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

    # 2. 데이터 다운로드
    logger.info(f"--- 데이터 다운로드: {tickers} ({start_date} ~ {end_date}) ---")
    close_df = download_historical_data(tickers, start_date, end_date)

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
    engine = MagicSplitEngine(
        broker=broker,
        repo=repo,
        logger=logger,
        stock_rules=rules,
        notifier=None,
        is_live_trading=False,
    )

    # 5. 시뮬레이션 루프
    logger.info(f"--- 백테스트 시작 ({len(sim_days)} 거래일) ---")

    last_result: Optional[DayResult] = None

    # ⚡ [Bolt Optimization]: Vectorized forward-fill for all tickers over the entire period
    # This prevents iterating and checking `pd.isna` on a per-ticker, per-day basis, which is extremely slow.
    close_df_filled = close_df.ffill()
    # Pre-compute records as a dictionary to avoid slow `.loc` access inside the loop
    prices_records = close_df_filled.to_dict(orient='index')

    for today in sim_days:
        # 종가 추출
        try:
            # Drop remaining NaNs (e.g. leading NaNs before the first valid price)
            current_prices = {
                t: p for t, p in prices_records[today].items() if not pd.isna(p)
            }
        except Exception as e:
            logger.warning(f"[{today.date()}] 종가 추출 실패, 건너뜀: {e}")
            continue

        sim_date = today.strftime("%Y-%m-%d")

        broker.set_date(today)
        broker.set_prices(current_prices)

        try:
            last_result = engine.run_one_cycle(sim_date=sim_date)
        except Exception as e:
            logger.error(f"[{sim_date}] 사이클 실행 실패: {e}")

    logger.info("--- 백테스트 완료 ---")
    if last_result:
        pf = last_result.final_portfolio
        logger.info(
            f"최종: Cash=${pf.total_cash:,.0f}, "
            f"Value=${pf.total_value:,.0f}"
        )

    return last_result
