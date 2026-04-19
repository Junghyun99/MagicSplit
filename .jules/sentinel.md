# Sentinel's Journal

## Brokerage API Interactions
*   Many `requests.get` and `requests.post` calls to the KIS broker APIs lacked `timeout` parameters.
*   Added `timeout=5` (or similar) to all API requests in `src/infra/broker/kis_base.py`, `src/infra/broker/kis_http.py`, `src/infra/broker/kis_overseas.py`, and `src/infra/broker/kis_domestic.py`. This ensures the application doesn't hang indefinitely during API calls.
