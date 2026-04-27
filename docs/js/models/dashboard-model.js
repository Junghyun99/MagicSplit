// docs/js/models/dashboard-model.js
window.DashboardModel = (function () {
    'use strict';

    const VALID_MODES = ['domestic', 'overseas', 'backtest'];
    const DEFAULT_MODE = 'domestic';

    let currentMode = DEFAULT_MODE;
    let statusData = null;
    let historyData = null;

    function getValidMode(mode) {
        return VALID_MODES.includes(mode) ? mode : DEFAULT_MODE;
    }

    function setMode(mode) {
        currentMode = getValidMode(mode);
    }

    function getMode() {
        return currentMode;
    }

    function setStatusData(data) {
        statusData = data;
    }

    function getStatusData() {
        return statusData;
    }

    function setHistoryData(data) {
        historyData = data;
    }

    function getHistoryData() {
        return historyData;
    }

    function getPortfolioSummary() {
        if (!statusData) return null;

        const positions = statusData.positions || {};
        let totalRealizedPnl = 0;
        let totalUnrealizedPnl = 0;
        let totalInvested = 0;

        for (const info of Object.values(positions)) {
            if (info.realized_pnl != null) totalRealizedPnl += Number(info.realized_pnl);
            if (info.unrealized_pnl != null) totalUnrealizedPnl += Number(info.unrealized_pnl);
            if (info.total_invested != null) totalInvested += Number(info.total_invested);
        }

        const portfolio = statusData.portfolio || {};
        const totalValue = Number(portfolio.total_value || 0);
        const cashBalance = portfolio.cash_balance != null ? Number(portfolio.cash_balance) : null;
        const stockValue = cashBalance != null ? totalValue - cashBalance : null;
        const cashPct = (totalValue > 0 && cashBalance != null) ? (cashBalance / totalValue) * 100 : 0;
        const stockPct = (totalValue > 0 && cashBalance != null) ? 100 - cashPct : 0;
        const unrealizedPct = totalInvested > 0 ? (totalUnrealizedPnl / totalInvested) * 100 : null;

        return {
            totalValue,
            cashBalance,
            stockValue,
            cashPct,
            stockPct,
            totalRealizedPnl,
            totalUnrealizedPnl,
            unrealizedPct,
            totalInvested
        };
    }

    function classifyReason(reason) {
        if (!reason) return 'info';
        const lower = reason.toLowerCase();
        if (lower.includes('에러') || lower.includes('오류') || lower.includes('error')) return 'danger';
        if (reason.includes('매도')) return 'success';
        if (reason.includes('매수')) return 'primary';
        return 'info';
    }

    // Heatmap Logic
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

    function buildLevelBuckets() {
        if (!Array.isArray(historyData) || historyData.length === 0) {
            return { months: [], tickers: [], grid: {}, trades: {} };
        }

        const execs = [];
        for (const tx of historyData) {
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

        const execsByTicker = {};
        const trades = {};
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

    return {
        getValidMode,
        setMode,
        getMode,
        setStatusData,
        getStatusData,
        setHistoryData,
        getHistoryData,
        getPortfolioSummary,
        classifyReason,
        buildLevelBuckets
    };
})();
