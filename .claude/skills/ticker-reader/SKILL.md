---
name: ticker-reader
description: Use when the user needs to look up Korean or overseas stock ticker metadata (exchange, alias/Korean name, asset_type, currency) via the ticker_reader.py helper and bundled tickers.db SQLite file copied from the ticker-map repo into a project's utils/ directory. Trigger on ticker lookups (e.g. "005930", "AAPL"), questions about exchange codes (KS/KQ/NAS/NYS/AMS), questions about how to import or set up ticker_reader.py / tickers.db in another repo, or any reference to the tickers SQLite schema (ticker, exchange, alias, asset_type, currency).
---

# ticker-reader

A read-only helper for looking up Korean and overseas stock ticker metadata. The module `ticker_reader.py` ships alongside a self-contained SQLite file `tickers.db` (~16,000 rows). Together they expose three stateless functions that return ticker info as plain dicts. The module depends only on the Python standard library (`os`, `sqlite3`, `typing`) — no third-party packages, no DB server, no migrations.

## When to use this skill

Invoke whenever the user (or downstream code) needs to:

- Resolve a ticker code to its Korean name or English symbol (`get_alias`).
- Determine which exchange a ticker belongs to (`get_exchange`).
- Fetch the full metadata record for a ticker (`get_ticker_info`).
- Set up `ticker_reader.py` + `tickers.db` in a brand-new repo by copying the `utils/` directory.
- Answer questions about the `tickers` table schema or the meaning of exchange / asset_type / currency codes.

## Setup: copying into another repo

Two files are required, and they MUST live in the same directory:

1. `ticker_reader.py` — the query module.
2. `tickers.db` — the SQLite database.

The recommended layout mirrors the source repo so `DEFAULT_DB_PATH` resolution works without arguments:

```
<target_repo>/
└── src/
    └── utils/
        ├── ticker_reader.py
        └── tickers.db
```

Copy command (run from the **source** ticker-map repo root):

```bash
mkdir -p <target_repo>/src/utils
cp src/utils/ticker_reader.py src/utils/tickers.db <target_repo>/src/utils/
```

Any directory works as long as both files sit together — `DEFAULT_DB_PATH` is computed at import time as `<dir of ticker_reader.py>/tickers.db`. If the layout differs, pass `db_path=` explicitly to every function call.

## Public API

Import:

```python
from src.utils.ticker_reader import (
    get_ticker_info,
    get_alias,
    get_exchange,
    DEFAULT_DB_PATH,
)
```

| Symbol | Signature | Returns |
|---|---|---|
| `get_ticker_info` | `get_ticker_info(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict]` | `dict` with keys `ticker, exchange, alias, asset_type, currency`, or `None` |
| `get_alias` | `get_alias(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[str]` | The `alias` field, or `None` |
| `get_exchange` | `get_exchange(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[str]` | The `exchange` field, or `None` |
| `DEFAULT_DB_PATH` | `str` | Absolute path resolving to `<module_dir>/tickers.db` |

**No-throw contract.** All three functions return `None` (never raise) when the DB file is missing, the ticker is not found, or any `sqlite3.Error` is raised internally. Always check for `None` before subscripting the result.

## Schema reference: `tickers` table

```sql
CREATE TABLE tickers (
    ticker     TEXT PRIMARY KEY,
    exchange   TEXT NOT NULL,
    alias      TEXT,
    asset_type TEXT NOT NULL,
    currency   TEXT NOT NULL
);
```

| Column | Type | Nullable | Allowed values / examples |
|---|---|---|---|
| `ticker` | TEXT | NO (PK) | `005930` (KR 6-digit), `AAPL` (overseas symbol) |
| `exchange` | TEXT | NO | `KS` (KOSPI), `KQ` (KOSDAQ), `NAS` (NASDAQ), `NYS` (NYSE), `AMS` (AMEX) |
| `alias` | TEXT | YES | Korean name for KR tickers (e.g. `삼성전자`); ticker symbol repeated for overseas |
| `asset_type` | TEXT | NO | `Stock`, `ETF` (the source pipeline can also produce `ETN`, currently absent from the DB) |
| `currency` | TEXT | NO | `KRW`, `USD` |

Approximate distribution at time of writing (~16,000 rows total):

| Exchange | Rows | Currency |
|---|---|---|
| KS | ~2,000 | KRW |
| KQ | ~1,800 | KRW |
| NAS | ~5,100 | USD |
| NYS | ~2,800 | USD |
| AMS | ~4,200 | USD |

## Usage examples

### Full record lookup

```python
from src.utils.ticker_reader import get_ticker_info

get_ticker_info("005930")
# {'ticker': '005930', 'exchange': 'KS', 'alias': '삼성전자',
#  'asset_type': 'Stock', 'currency': 'KRW'}

get_ticker_info("AAPL")
# {'ticker': 'AAPL', 'exchange': 'NAS', 'alias': 'AAPL',
#  'asset_type': 'Stock', 'currency': 'USD'}

get_ticker_info("NOT_A_TICKER")
# None
```

### Single-field helpers

```python
from src.utils.ticker_reader import get_alias, get_exchange

get_alias("005930")     # '삼성전자'
get_exchange("AAPL")    # 'NAS'
get_alias("???")        # None — always check before use
```

### Defensive pattern

```python
info = get_ticker_info(user_input)
if info is None:
    raise ValueError(f"Unknown ticker: {user_input!r}")
display_name = info["alias"]
```

### Custom DB path (non-standard layout)

```python
from src.utils.ticker_reader import get_ticker_info

info = get_ticker_info("005930", db_path="/data/cache/tickers.db")
```

## Common pitfalls

- **DB co-location.** `tickers.db` must sit next to `ticker_reader.py`, otherwise pass `db_path=` on every call. A missing DB silently returns `None` rather than raising.
- **None-guard required.** Every function returns `None` on any failure path. Subscripting without a check (`get_ticker_info(t)["alias"]`) will raise `TypeError` for unknown tickers.
- **Read-only.** This module is strictly for reads. It opens a normal `sqlite3.connect()` (no special flags) but performs only `SELECT`. Do not extend it to write — the source-of-truth pipeline lives in the original ticker-map repo.
- **Korean alias encoding.** `alias` values for KR exchanges are UTF-8 Korean strings. Ensure the consuming environment handles UTF-8 output (most modern Python setups do by default).
