import sys
from unittest.mock import MagicMock

# Mock out dependencies that are not available in the environment
if 'requests' not in sys.modules:
    sys.modules['requests'] = MagicMock()
if 'dotenv' not in sys.modules:
    sys.modules['dotenv'] = MagicMock()
if 'yfinance' not in sys.modules:
    sys.modules['yfinance'] = MagicMock()
if 'yaml' not in sys.modules:
    sys.modules['yaml'] = MagicMock()
if 'pandas' not in sys.modules:
    sys.modules['pandas'] = MagicMock()
