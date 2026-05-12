# tests/test_utils_logger.py
import logging
import os
import pytest
from src.utils.logger import TradeLogger


@pytest.fixture(autouse=True)
def reset_logger():
    """각 테스트 전후 싱글톤 로거 핸들러를 초기화한다."""
    logger = logging.getLogger("MagicSplit")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)
    yield
    logger = logging.getLogger("MagicSplit")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


class TestTradeLogger:
    def test_creates_log_dir(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        logger = TradeLogger(log_dir=log_dir)
        assert os.path.isdir(log_dir)

    def test_creates_log_file(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        logger = TradeLogger(log_dir=log_dir)
        logger.info("test message")
        assert os.path.exists(logger.log_file)

    def test_info(self, tmp_path):
        logger = TradeLogger(log_dir=str(tmp_path))
        logger.info("info message")
        with open(logger.log_file, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "info message" in content

    def test_warning(self, tmp_path):
        logger = TradeLogger(log_dir=str(tmp_path))
        logger.warning("warn message")
        with open(logger.log_file, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "warn message" in content

    def test_error(self, tmp_path):
        logger = TradeLogger(log_dir=str(tmp_path))
        logger.error("error message")
        with open(logger.log_file, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "error message" in content

    def test_run_number_suffix(self, tmp_path):
        logger = TradeLogger(log_dir=str(tmp_path), run_number="42")
        assert "_42" in logger.log_file

    def test_captured_logs_all(self, tmp_path):
        logger = TradeLogger(log_dir=str(tmp_path))
        logger.info("common message")
        logger.set_ticker_context("NVDA")
        logger.info("nvda message")
        logger.set_ticker_context(None)
        logger.info("end message")
        
        logs = logger.get_captured_logs()
        assert len(logs) == 3
        assert "common message" in logs[0]
        assert "nvda message" in logs[1]
        assert "end message" in logs[2]

    def test_captured_logs_by_ticker(self, tmp_path):
        logger = TradeLogger(log_dir=str(tmp_path))
        logger.info("common")
        logger.set_ticker_context("NVDA")
        logger.info("nvda message 1")
        logger.set_ticker_context("AAPL")
        logger.info("aapl message")
        logger.set_ticker_context("NVDA")
        logger.info("nvda message 2")
        
        nvda_logs = logger.get_captured_logs("NVDA")
        assert len(nvda_logs) == 2
        assert "nvda message 1" in nvda_logs[0]
        assert "nvda message 2" in nvda_logs[1]
        
        aapl_logs = logger.get_captured_logs("AAPL")
        assert len(aapl_logs) == 1
        assert "aapl message" in aapl_logs[0]

    def test_clear_captured_logs(self, tmp_path):
        logger = TradeLogger(log_dir=str(tmp_path))
        logger.info("message")
        assert len(logger.get_captured_logs()) == 1
        logger.clear_captured_logs()
        assert len(logger.get_captured_logs()) == 0
