import time
from src.backtest.runner import run_backtest
from src.strategy_config import StrategyConfig

start_time = time.time()
result = run_backtest(
    config_path="config_overseas.json",
    start_date="2022-01-01",
    end_date="2023-12-31",
    initial_cash=10000.0,
    market_type="overseas",
    output_dir="docs/data/backtest_bench"
)
end_time = time.time()
print(f"Backtest took {end_time - start_time:.2f} seconds")
