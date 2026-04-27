// docs/js/models/history-model.js
window.HistoryModel = (function () {
    'use strict';

    let allRows = [];
    let filteredRows = [];
    let visibleCount = 0;
    const PAGE_SIZE = 30;
    let activeFilters = { ticker: '', action: '' };

    function buildRows(historyData) {
        const rows = [];
        if (!Array.isArray(historyData)) return rows;

        for (const tx of historyData) {
            const txDate = tx.date || '';
            const txReason = tx.reason || '';
            const executions = Array.isArray(tx.executions) ? tx.executions : [];
            for (const ex of executions) {
                const action = (ex.action || '').toUpperCase();
                const price = ex.price || 0;
                const buyPrice = ex.buy_price != null ? ex.buy_price : null;
                const realizedPnl = ex.realized_pnl != null ? ex.realized_pnl : null;
                const profitRate = (action === 'SELL' && buyPrice != null && buyPrice > 0 && realizedPnl != null && (ex.quantity || 0) > 0)
                    ? (realizedPnl / (buyPrice * ex.quantity)) * 100
                    : null;
                rows.push({
                    date: ex.date || txDate,
                    ticker: ex.ticker || '',
                    action,
                    level: ex.level != null ? ex.level : '',
                    quantity: ex.quantity || 0,
                    price,
                    fee: ex.fee || 0,
                    amount: (ex.quantity || 0) * price,
                    buyPrice,
                    realizedPnl,
                    profitRate,
                    txReason: txReason,
                });
            }
        }

        rows.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
        return rows;
    }

    function setHistoryData(data) {
        allRows = buildRows(data);
        filteredRows = allRows.slice();
        visibleCount = 0;
        activeFilters = { ticker: '', action: '' };
    }

    function getUniqueTickers() {
        const set = new Set(allRows.map((r) => r.ticker).filter(Boolean));
        return Array.from(set).sort();
    }

    function setFilter(type, value) {
        if (type === 'ticker') activeFilters.ticker = value;
        if (type === 'action') activeFilters.action = value;
        applyFilters();
    }

    function applyFilters() {
        filteredRows = allRows.filter((row) => {
            if (activeFilters.ticker && row.ticker !== activeFilters.ticker) return false;
            if (activeFilters.action && row.action !== activeFilters.action) return false;
            return true;
        });
        visibleCount = 0;
    }

    function getNextPage() {
        const newRows = filteredRows.slice(visibleCount, visibleCount + PAGE_SIZE);
        visibleCount += newRows.length;
        return newRows;
    }

    function hasMore() {
        return visibleCount < filteredRows.length;
    }
    
    function resetPagination() {
        visibleCount = 0;
    }

    function getVisibleCount() {
        return visibleCount;
    }
    
    function getTotalCount() {
        return filteredRows.length;
    }

    function buildEquityPoints(historyData) {
        if (!Array.isArray(historyData)) return [];
        const pts = [];
        for (const tx of historyData) {
            if (tx && tx.date && tx.portfolio_value != null) {
                pts.push({ date: tx.date, value: Number(tx.portfolio_value) });
            }
        }
        pts.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
        return pts;
    }

    return {
        setHistoryData,
        getUniqueTickers,
        setFilter,
        getNextPage,
        hasMore,
        resetPagination,
        getVisibleCount,
        getTotalCount,
        buildEquityPoints
    };
})();
