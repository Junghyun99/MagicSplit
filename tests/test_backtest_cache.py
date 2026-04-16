# tests/test_backtest_cache.py
import pandas as pd
import pytest
from unittest.mock import patch
from src.backtest.cache import BacktestDataCache


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """임시 캐시 디렉토리"""
    return tmp_path / "cache"


@pytest.fixture
def cache(tmp_cache_dir):
    """테스트용 BacktestDataCache 인스턴스"""
    return BacktestDataCache(cache_dir=tmp_cache_dir)


def _make_yf_response(tickers, days=5):
    """yfinance 응답을 시뮬레이션하는 DataFrame 생성"""
    dates = pd.bdate_range("2024-01-02", periods=days)
    if len(tickers) == 1:
        # 단일 티커: SingleIndex
        data = {
            "Open": range(100, 100 + days),
            "High": range(101, 101 + days),
            "Low": range(99, 99 + days),
            "Close": range(100, 100 + days),
            "Volume": [1000] * days,
        }
        return pd.DataFrame(data, index=dates)
    else:
        # 멀티 티커: MultiIndex
        cols = pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close", "Volume"], tickers]
        )
        data = {}
        for field in ["Open", "High", "Low", "Close"]:
            for t in tickers:
                data[(field, t)] = range(100, 100 + days)
        for t in tickers:
            data[("Volume", t)] = [1000] * days
        df = pd.DataFrame(data, index=dates)
        df.columns = cols
        return df


class TestBacktestDataCache:
    def test_get_data_single_ticker(self, cache, tmp_cache_dir):
        """단일 티커 다운로드 및 캐시 저장"""
        mock_df = _make_yf_response(["AAPL"], days=5)

        with patch("src.backtest.cache.yf.download", return_value=mock_df):
            result = cache.get_data(["AAPL"], "2024-01-01", "2024-01-10")

        assert isinstance(result, pd.DataFrame)
        assert "AAPL" in result.columns
        assert len(result) == 5
        assert (tmp_cache_dir / "close.parquet").exists()

    def test_get_data_multi_ticker(self, cache, tmp_cache_dir):
        """멀티 티커 다운로드 및 캐시 저장"""
        mock_df = _make_yf_response(["AAPL", "MSFT"], days=5)

        with patch("src.backtest.cache.yf.download", return_value=mock_df):
            result = cache.get_data(["AAPL", "MSFT"], "2024-01-01", "2024-01-10")

        assert "AAPL" in result.columns
        assert "MSFT" in result.columns
        assert len(result) == 5

    def test_get_data_ffill_nan(self, cache):
        """NaN 값이 ffill로 처리되는지 확인"""
        dates = pd.bdate_range("2024-01-02", periods=3)
        cols = pd.MultiIndex.from_product([["Close"], ["AAPL"]])
        data = {("Close", "AAPL"): [100.0, float("nan"), 102.0]}
        mock_df = pd.DataFrame(data, index=dates)
        mock_df.columns = cols

        # Open, High 등을 추가하여 yfinance 응답처럼 만듦
        for field in ["Open", "High", "Low", "Volume"]:
            mock_df[(field, "AAPL")] = 100.0
        mock_df.columns = pd.MultiIndex.from_tuples(mock_df.columns)

        with patch("src.backtest.cache.yf.download", return_value=mock_df):
            result = cache.get_data(["AAPL"], "2024-01-01", "2024-01-10")

        # NaN이 ffill로 100.0이 되어야 함
        assert result["AAPL"].iloc[1] == 100.0

    def test_get_data_raises_on_empty(self, cache):
        """빈 데이터 다운로드 시 ValueError 발생"""
        with patch("src.backtest.cache.yf.download", return_value=pd.DataFrame()):
            with pytest.raises(ValueError, match="종가 다운로드 실패"):
                cache.get_data(["AAPL"], "2024-01-01", "2024-01-10")

    def test_clear(self, cache, tmp_cache_dir):
        """캐시 삭제"""
        tmp_cache_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = tmp_cache_dir / "close.parquet"
        parquet_path.write_text("dummy")

        cache.clear()
        assert not parquet_path.exists()

    def test_clear_no_file(self, cache):
        """파일이 없을 때 clear가 오류 없이 동작"""
        cache.clear()  # 예외 없이 완료

    def test_get_data_deletes_existing_cache(self, cache, tmp_cache_dir):
        """get_data 호출 시 기존 캐시가 삭제되는지 확인"""
        tmp_cache_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = tmp_cache_dir / "close.parquet"
        parquet_path.write_text("old data")

        mock_df = _make_yf_response(["AAPL"], days=3)
        with patch("src.backtest.cache.yf.download", return_value=mock_df):
            cache.get_data(["AAPL"], "2024-01-01", "2024-01-05")

        # 파일이 새로 생성됨 (parquet 형식)
        assert parquet_path.exists()
        content = parquet_path.read_bytes()
        assert content != b"old data"
