# Bolt's Journal

- **Pandas iteration**: Using `iterrows()` or `.loc[]` in a loop in pandas is severely slow for large dataframes or backtests. Pre-converting to a dictionary via `.to_dict('index')` outside of loops achieves O(1) access and cuts down iteration time dramatically (~90% drop in benchmark scripts).
