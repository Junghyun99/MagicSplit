# tests/test_backtest_runner.py
import json
import os
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch

from src.backtest.runner import run_backtest, _validate_tickers


@pytest.fixture
def backtest_config(tmp_path):
    """백테스트용 config.json 생성"""
    config = {
        "stocks": [
            {
                "ticker": "AAPL",
                "exchange": "NAS",
                "market_type": "overseas",
                "buy_threshold_pct": -5.0,
                "sell_threshold_pct": 10.0,
                "buy_amount": 500,
                "max_lots": 10,
                "enabled": True,
            }
        ],
        "global": {
            "check_interval_minutes": 60,
            "notification_enabled": False,
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    return str(config_path)


@pytest.fixture
def multi_stock_config(tmp_path):
    """2종목 백테스트 config.json"""
    config = {
        "stocks": [
            {
                "ticker": "AAPL",
                "exchange": "NAS",
                "market_type": "overseas",
                "buy_threshold_pct": -5.0,
                "sell_threshold_pct": 10.0,
                "buy_amount": 300,
                "max_lots": 5,
                "enabled": True,
            },
            {
                "ticker": "MSFT",
                "exchange": "NAS",
                "market_type": "overseas",
                "buy_threshold_pct": -5.0,
                "sell_threshold_pct": 10.0,
                "buy_amount": 300,
                "max_lots": 5,
                "enabled": True,
            },
        ],
        "global": {},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    return str(config_path)


def _make_close_df(tickers, days=20, start_price=100.0, price_step=-1.0):
    """가격이 점진적으로 변하는 종가 DataFrame 생성.

    기본값: 100 -> 99 -> 98 -> ... (하락 추세, 추가 매수 유도)
    """
    dates = pd.bdate_range("2024-01-02", periods=days)
    data = {}
    for t in tickers:
        data[t] = [start_price + i * price_step for i in range(days)]
    return pd.DataFrame(data, index=dates)


class TestValidateTickers:
    def test_no_missing(self):
        """모든 티커가 존재하면 빈 리스트 반환"""
        df = pd.DataFrame({"AAPL": [100], "MSFT": [200]})

        class FakeLogger:
            def warning(self, msg): pass

        assert _validate_tickers(df, ["AAPL", "MSFT"], FakeLogger()) == []

    def test_missing_tickers(self):
        """누락 티커가 있으면 리스트로 반환"""
        df = pd.DataFrame({"AAPL": [100]})

        class FakeLogger:
            def warning(self, msg): pass

        result = _validate_tickers(df, ["AAPL", "GOOG"], FakeLogger())
        assert result == ["GOOG"]


class TestRunBacktest:
    def test_basic_backtest_flow(self, backtest_config, tmp_path):
        """기본 백테스트 흐름: 데이터 다운 -> 시뮬레이션 -> 결과 파일 생성"""
        close_df = _make_close_df(["AAPL"], days=10, start_price=100.0, price_step=-2.0)
        output_dir = str(tmp_path / "output")

        with patch("src.backtest.runner.download_historical_data", return_value=close_df):
            result = run_backtest(
                config_path=backtest_config,
                start_date="2024-01-02",
                end_date="2024-01-15",
                initial_cash=10000.0,
                output_dir=output_dir,
            )

        assert result is not None
        assert result.date is not None
        # 결과 파일 존재 확인
        assert os.path.exists(os.path.join(output_dir, "positions.json"))
        assert os.path.exists(os.path.join(output_dir, "history.json"))
        assert os.path.exists(os.path.join(output_dir, "status.json"))

    def test_initial_buy_occurs(self, backtest_config, tmp_path):
        """첫 거래일에 Lv1 초기 매수가 발생"""
        close_df = _make_close_df(["AAPL"], days=5, start_price=100.0, price_step=0.0)
        output_dir = str(tmp_path / "output")

        with patch("src.backtest.runner.download_historical_data", return_value=close_df):
            result = run_backtest(
                config_path=backtest_config,
                start_date="2024-01-02",
                end_date="2024-01-08",
                initial_cash=10000.0,
                output_dir=output_dir,
            )

        # history.json에 매수 내역이 있어야 함
        with open(os.path.join(output_dir, "history.json"), encoding='utf-8') as f:
            history = json.load(f)
        assert len(history) > 0
        first_exec = history[0]["executions"][0]
        assert first_exec["action"] == "BUY"
        assert first_exec["ticker"] == "AAPL"

    def test_no_rules_returns_none(self, tmp_path):
        """활성화된 종목이 없으면 None 반환"""
        config = {
            "stocks": [
                {
                    "ticker": "AAPL",
                    "exchange": "NAS",
                    "market_type": "domestic",  # overseas 요청 시 매칭 안됨
                    "buy_threshold_pct": -5.0,
                    "sell_threshold_pct": 10.0,
                    "buy_amount": 500,
                    "max_lots": 10,
                    "enabled": True,
                }
            ],
            "global": {},
        }
        config_path = str(tmp_path / "config.json")
        with open(config_path, "w", encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False)

        result = run_backtest(
            config_path=config_path,
            start_date="2024-01-01",
            end_date="2024-01-31",
            market_type="overseas",
            output_dir=str(tmp_path / "output"),
        )
        assert result is None

    def test_multi_stock_backtest(self, multi_stock_config, tmp_path):
        """2종목 백테스트가 정상 실행"""
        close_df = _make_close_df(
            ["AAPL", "MSFT"], days=10, start_price=100.0, price_step=-1.0,
        )
        output_dir = str(tmp_path / "output")

        with patch("src.backtest.runner.download_historical_data", return_value=close_df):
            result = run_backtest(
                config_path=multi_stock_config,
                start_date="2024-01-02",
                end_date="2024-01-15",
                initial_cash=10000.0,
                output_dir=output_dir,
            )

        assert result is not None
        # status.json에 포트폴리오 정보 존재
        with open(os.path.join(output_dir, "status.json"), encoding='utf-8') as f:
            status = json.load(f)
        assert "portfolio" in status

    def test_missing_ticker_data_returns_none(self, backtest_config, tmp_path):
        """필요한 티커 데이터가 없으면 None 반환"""
        # AAPL이 필요한데 MSFT만 있는 데이터
        close_df = _make_close_df(["MSFT"], days=5)
        output_dir = str(tmp_path / "output")

        with patch("src.backtest.runner.download_historical_data", return_value=close_df):
            result = run_backtest(
                config_path=backtest_config,
                start_date="2024-01-02",
                end_date="2024-01-08",
                output_dir=output_dir,
            )
        assert result is None
