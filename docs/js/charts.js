// MagicSplit Dashboard - charts.js
// Level heatmap (per-ticker × time axis) rendered as a CSS grid.
(function () {
    'use strict';

    const LEVEL_CAP = 5;

    function monthKey(dateStr) {
        return typeof dateStr === 'string' ? dateStr.slice(0, 7) : '';
    }

    function nextMonth(key) {
        const [y, m] = key.split('-').map(Number);
        const d = new Date(Date.UTC(y, m - 1, 1));
        d.setUTCMonth(d.getUTCMonth() + 1);
        const ny = d.getUTCFullYear();
        const nm = String(d.getUTCMonth() + 1).padStart(2, '0');
        return `${ny}-${nm}`;
    }

    function enumerateMonths(startKey, endKey) {
        const months = [];
        let cur = startKey;
        while (cur <= endKey) {
            months.push(cur);
            cur = nextMonth(cur);
        }
        return months;
    }

    // Reconstruct per-month max active level per ticker from history.json.
    // Walks executions in chronological order, maintaining a Set of open
    // levels per ticker (BUY adds, SELL removes). For each month bucket,
    // records the max level observed while open, plus a trade counter.
    // Months with no trades inherit the previous month's open-level max
    // (position still held).
    function buildLevelBuckets(history) {
        if (!Array.isArray(history) || history.length === 0) {
            return { months: [], tickers: [], grid: {}, trades: {} };
        }

        const execs = [];
        for (const tx of history) {
            if (!tx || !Array.isArray(tx.executions)) continue;
            for (const ex of tx.executions) {
                if (!ex || !ex.ticker || !ex.date || ex.level == null) continue;
                if (ex.status && ex.status !== 'FILLED') continue;
                execs.push(ex);
            }
        }
        if (execs.length === 0) {
            return { months: [], tickers: [], grid: {}, trades: {} };
        }

        execs.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));

        // Single pass: group executions by ticker/month and count trades per
        // ticker/month. This replaces an earlier O(T*E) per-ticker re-scan.
        const execsByTicker = {};   // ticker -> {month -> [execs]}
        const trades = {};          // ticker -> {month -> count}
        const tickersSeen = new Set();

        for (const ex of execs) {
            const t = ex.ticker;
            const mk = monthKey(ex.date);
            tickersSeen.add(t);
            if (!execsByTicker[t]) execsByTicker[t] = {};
            if (!execsByTicker[t][mk]) execsByTicker[t][mk] = [];
            execsByTicker[t][mk].push(ex);
            if (!trades[t]) trades[t] = {};
            trades[t][mk] = (trades[t][mk] || 0) + 1;
        }

        const firstMonth = monthKey(execs[0].date);
        const lastMonth = monthKey(execs[execs.length - 1].date);
        const months = enumerateMonths(firstMonth, lastMonth);

        // Roll-forward: months without trades inherit prior month's open-level
        // max. For each ticker, replay its executions month-by-month tracking
        // the set of open levels.
        const grid = {};
        for (const t of tickersSeen) {
            grid[t] = {};
            const open = new Set();
            const byMonth = execsByTicker[t] || {};
            for (const m of months) {
                let monthMax = open.size > 0 ? Math.max(...open) : 0;
                const list = byMonth[m] || [];
                for (const ex of list) {
                    if (ex.action === 'BUY') open.add(ex.level);
                    else if (ex.action === 'SELL') open.delete(ex.level);
                    const cur = open.size > 0 ? Math.max(...open) : 0;
                    if (cur > monthMax) monthMax = cur;
                }
                grid[t][m] = monthMax;
            }
        }

        const tickers = Array.from(tickersSeen).sort();
        return { months, tickers, grid, trades };
    }

    function buildAxisRow(months) {
        const row = document.createElement('div');
        row.className = 'heatmap-row';

        const corner = document.createElement('div');
        corner.className = 'heatmap-label heatmap-axis';
        corner.textContent = '';
        row.appendChild(corner);

        for (const m of months) {
            const cell = document.createElement('div');
            cell.className = 'heatmap-axis';
            cell.textContent = m.slice(2);  // "25-01"
            cell.title = m;
            row.appendChild(cell);
        }
        return row;
    }

    function buildTickerRow(ticker, months, grid, trades) {
        const row = document.createElement('div');
        row.className = 'heatmap-row';

        const label = document.createElement('div');
        label.className = 'heatmap-label';
        label.textContent = ticker;
        row.appendChild(label);

        for (const m of months) {
            const cell = document.createElement('div');
            cell.className = 'heatmap-cell';
            const level = (grid[ticker] && grid[ticker][m]) || 0;
            const count = (trades[ticker] && trades[ticker][m]) || 0;
            const clamped = Math.min(level, LEVEL_CAP);
            cell.dataset.level = String(clamped);
            cell.dataset.ticker = ticker;
            cell.dataset.month = m;
            cell.dataset.rawLevel = String(level);
            const levelLabel = level > 0 ? `Lv${level}` : '보유 없음';
            const tradeLabel = count > 0 ? ` (거래 ${count}건)` : '';
            cell.title = `${ticker} | ${m} | ${levelLabel}${tradeLabel}`;
            if (level > 0) cell.textContent = String(level);
            row.appendChild(cell);
        }
        return row;
    }

    function renderLevelHeatmap(historyData, mode) {
        const container = document.getElementById('level-heatmap');
        const section = document.getElementById('level-heatmap-section');
        if (!container || !section) return;

        container.textContent = '';

        const data = buildLevelBuckets(historyData);
        if (data.months.length === 0 || data.tickers.length === 0) {
            section.style.display = 'none';
            return;
        }

        const columnCount = data.months.length + 1;  // +1 for ticker label
        container.style.gridTemplateColumns = `minmax(64px, auto) repeat(${data.months.length}, minmax(28px, 1fr))`;

        container.appendChild(buildAxisRow(data.months));
        for (const t of data.tickers) {
            container.appendChild(buildTickerRow(t, data.months, data.grid, data.trades));
        }

        // Single delegated click listener on the container; survives re-renders
        // because only cells (children) are replaced, not the container itself.
        if (!container.dataset.listenersBound) {
            container.addEventListener('click', onCellClick);
            container.dataset.listenersBound = '1';
        }

        section.style.display = '';
        section.dataset.mode = mode || '';
        container.dataset.columns = String(columnCount);
    }

    function onCellClick(e) {
        const cell = e.target.closest('.heatmap-cell');
        if (!cell) return;
        const level = parseInt(cell.dataset.rawLevel || '0', 10);
        if (level === 0) return;
        document.dispatchEvent(new CustomEvent('heatmap-select', {
            detail: {
                ticker: cell.dataset.ticker,
                month: cell.dataset.month,
                level,
            },
        }));
    }

    window.MagicSplitCharts = {
        renderLevelHeatmap,
        _buildLevelBuckets: buildLevelBuckets,  // exposed for manual debugging
    };
})();
