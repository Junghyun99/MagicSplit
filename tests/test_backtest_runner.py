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
    """백테스트용 설정 파일 생성"""
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
            "notification_enabled": False,
        },
    }
    config_path = tmp_path / "config_overseas.json"
    config_path.write_text(json.dumps(config))
    return str(config_path)


@pytest.fixture
def multi_stock_config(tmp_path):
    """2종목 백테스트 설정 파일"""
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
    config_path = tmp_path / "config_overseas.json"
    config_path.write_text(json.dumps(config))
    return str(config_path)


def _make_ohlc_df(tickers, days=20, start_price=100.0, price_step=-1.0, spread=0.5):
    """가격이 점진적으로 변하는 OHLC DataFrame 생성 (컬럼 MultiIndex (field, ticker)).

    기본값: 100 -> 99 -> 98 -> ... (하락 추세, 추가 매수 유도)
    runner는 ohlc_df["Close"]로 종가를 파생한다.
    """
    dates = pd.bdate_range("2024-01-02", periods=days)
    closes = {t: [start_price + i * price_step for i in range(days)] for t in tickers}
    data = {}
    for t in tickers:
        c = closes[t]
        data[("High", t)] = [x + spread for x in c]
        data[("Low", t)] = [x - spread for x in c]
        data[("Close", t)] = c
    df = pd.DataFrame(data, index=dates)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


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
        close_df = _make_ohlc_df(["AAPL"], days=10, start_price=100.0, price_step=-2.0)
        output_dir = str(tmp_path / "output")

        with patch("src.backtest.runner.download_ohlc_data", return_value=close_df):
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
        close_df = _make_ohlc_df(["AAPL"], days=5, start_price=100.0, price_step=0.0)
        output_dir = str(tmp_path / "output")

        with patch("src.backtest.runner.download_ohlc_data", return_value=close_df):
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
        config_path = str(tmp_path / "config_overseas.json")
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
        close_df = _make_ohlc_df(
            ["AAPL", "MSFT"], days=10, start_price=100.0, price_step=-1.0,
        )
        output_dir = str(tmp_path / "output")

        with patch("src.backtest.runner.download_ohlc_data", return_value=close_df):
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
        close_df = _make_ohlc_df(["MSFT"], days=5)
        output_dir = str(tmp_path / "output")

        with patch("src.backtest.runner.download_ohlc_data", return_value=close_df):
            result = run_backtest(
                config_path=backtest_config,
                start_date="2024-01-02",
                end_date="2024-01-08",
                output_dir=output_dir,
            )
        assert result is None


def _make_regime_switch_ohlc(spread=0.3):
    """횡보(210) -> 완만한 상승(150) -> 급락(15)으로 레짐 전환을 합성한다."""
    sideways = [100 + (i % 2) * 0.5 for i in range(210)]
    climb = []
    p = sideways[-1]
    for _ in range(150):
        p *= 1.001
        climb.append(p)
    drop = []
    p = climb[-1]
    for _ in range(15):
        p *= 0.97
        drop.append(p)
    closes = sideways + climb + drop
    dates = pd.bdate_range("2022-01-03", periods=len(closes))
    data = {
        ("High", "AAPL"): [c + spread for c in closes],
        ("Low", "AAPL"): [c - spread for c in closes],
        ("Close", "AAPL"): closes,
    }
    df = pd.DataFrame(data, index=dates)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


def _regime_config(tmp_path, enabled):
    config = {
        "stocks": [{
            "ticker": "AAPL",
            "market_type": "overseas",
            "buy_threshold_pct": -20.0,
            "sell_threshold_pct": 50.0,
            "buy_amount": 300,
            "max_lots": 20,
            "enabled": True,
            "regime_enabled": enabled,
            "regime_min_bars": 200,
            "uptrend_max_adds": 5,
            "uptrend_pullback_band_pct": 4.0,
        }],
        "global": {"notification_enabled": False},
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config_overseas.json"
    path.write_text(json.dumps(config))
    return str(path)


def _max_sells_per_day(output_dir, ticker="AAPL"):
    with open(os.path.join(output_dir, "history.json"), encoding="utf-8") as f:
        history = json.load(f)
    worst = 0
    for rec in history:
        sells = sum(
            1 for e in rec.get("executions", [])
            if e.get("ticker") == ticker and e.get("action") == "SELL"
        )
        worst = max(worst, sells)
    return worst


class TestRegimeIntegration:
    def test_uptrend_accumulates_then_liquidates(self, tmp_path):
        ohlc = _make_regime_switch_ohlc()
        dates = ohlc.index
        start, end = dates[0].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")

        on_dir = str(tmp_path / "on")
        with patch("src.backtest.runner.download_ohlc_data", return_value=ohlc):
            result = run_backtest(
                config_path=_regime_config(tmp_path / "on_cfg", enabled=True),
                start_date=start, end_date=end,
                initial_cash=100000.0, output_dir=on_dir,
            )
        assert result is not None
        # 상승장에서 누적 후 추세 이탈 시 다수 lot을 같은 날 전량 청산
        assert _max_sells_per_day(on_dir) >= 2

    def test_control_regime_off_single_sell_path(self, tmp_path):
        ohlc = _make_regime_switch_ohlc()
        dates = ohlc.index
        start, end = dates[0].strftime("%Y-%m-%d"), dates[-1].strftime("%Y-%m-%d")

        off_dir = str(tmp_path / "off")
        with patch("src.backtest.runner.download_ohlc_data", return_value=ohlc):
            run_backtest(
                config_path=_regime_config(tmp_path / "off_cfg", enabled=False),
                start_date=start, end_date=end,
                initial_cash=100000.0, output_dir=off_dir,
            )
        # 평균회귀 경로는 종목당 하루 최대 1건 매도 (동시 다중 청산 없음)
        assert _max_sells_per_day(off_dir) <= 1
