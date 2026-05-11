# Sentinel's Journal

## Critical Learnings
- **API Call Timeouts**: `requests` module needs strict timeouts (e.g., `timeout=10`) when talking to KIS APIs to prevent bot freezing during market volatility. Unhandled infinite timeouts can lead to frozen scripts that miss trades.
