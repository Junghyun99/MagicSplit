// docs/js/models/earnings-model.js
window.EarningsModel = (function () {
    'use strict';

    // { "2026-05": { realized_pnl, sell_count, tickers: { ticker: { pnl, count, alias } } } }
    let monthlyData = {};
    let sortedYears = [];
    let currentUnrealized = { total: 0, byTicker: {} };

    function setHistoryData(histData) {
        monthlyData = {};
        if (!Array.isArray(histData)) {
            sortedYears = [];
            return;
        }

        for (const tx of histData) {
            const executions = Array.isArray(tx.executions) ? tx.executions : [];
            for (const ex of executions) {
                if ((ex.action || '').toUpperCase() !== 'SELL') continue;
                if (ex.realized_pnl == null) continue;

                const dateStr = ex.date || tx.date || '';
                const yearMonth = dateStr.slice(0, 7); // "YYYY-MM"
                if (yearMonth.length < 7) continue;

                if (!monthlyData[yearMonth]) {
                    monthlyData[yearMonth] = { realized_pnl: 0, sell_count: 0, tickers: {} };
                }
                const bucket = monthlyData[yearMonth];
                bucket.realized_pnl += Number(ex.realized_pnl);
                bucket.sell_count += 1;

                const ticker = ex.ticker || '';
                if (!bucket.tickers[ticker]) {
                    bucket.tickers[ticker] = { pnl: 0, count: 0, alias: ex.alias || '' };
                }
                bucket.tickers[ticker].pnl += Number(ex.realized_pnl);
                bucket.tickers[ticker].count += 1;
                if (!bucket.tickers[ticker].alias && ex.alias) {
                    bucket.tickers[ticker].alias = ex.alias;
                }
            }
        }

        const yearSet = new Set();
        for (const key of Object.keys(monthlyData)) {
            yearSet.add(key.slice(0, 4));
        }
        sortedYears = Array.from(yearSet).sort();
    }

    function setStatusData(statusData) {
        currentUnrealized = { total: 0, byTicker: {} };
        if (!statusData || !statusData.positions) return;

        for (const [ticker, info] of Object.entries(statusData.positions)) {
            if (info.unrealized_pnl == null) continue;
            const pnl = Number(info.unrealized_pnl);
            currentUnrealized.total += pnl;
            currentUnrealized.byTicker[ticker] = {
                unrealized_pnl: pnl,
                alias: info.alias || ''
            };
        }
    }

    function getAvailableYears() {
        return sortedYears;
    }

    // Returns 12-element array for the year (all months, zero if no data)
    function getYearMonthly(year) {
        return Array.from({ length: 12 }, (_, i) => {
            const month = String(i + 1).padStart(2, '0');
            const key = `${year}-${month}`;
            const bucket = monthlyData[key];
            return {
                month,
                realized_pnl: bucket ? bucket.realized_pnl : 0,
                sell_count: bucket ? bucket.sell_count : 0,
                has_data: !!bucket
            };
        });
    }

    // year=null -> all years; month=null -> all months in year
    function getPeriodSummary(year, month) {
        let realized_pnl = 0;
        let sell_count = 0;
        const tickerMap = {};

        for (const [key, bucket] of Object.entries(monthlyData)) {
            const keyYear = key.slice(0, 4);
            const keyMonth = key.slice(5, 7);
            if (year && keyYear !== year) continue;
            if (month && keyMonth !== month) continue;

            realized_pnl += bucket.realized_pnl;
            sell_count += bucket.sell_count;

            for (const [ticker, td] of Object.entries(bucket.tickers)) {
                if (!tickerMap[ticker]) {
                    tickerMap[ticker] = { pnl: 0, count: 0, alias: td.alias };
                }
                tickerMap[ticker].pnl += td.pnl;
                tickerMap[ticker].count += td.count;
                if (!tickerMap[ticker].alias && td.alias) {
                    tickerMap[ticker].alias = td.alias;
                }
            }
        }

        const ticker_breakdown = Object.entries(tickerMap)
            .map(([ticker, td]) => ({ ticker, ...td }))
            .sort((a, b) => b.pnl - a.pnl);

        return { realized_pnl, sell_count, ticker_breakdown };
    }

    function getMonthsWithData(year) {
        const set = new Set();
        for (const key of Object.keys(monthlyData)) {
            if (!year || key.slice(0, 4) === year) {
                set.add(key.slice(5, 7));
            }
        }
        return set;
    }

    function getCurrentUnrealized() {
        return currentUnrealized;
    }

    return {
        setHistoryData,
        setStatusData,
        getAvailableYears,
        getYearMonthly,
        getPeriodSummary,
        getMonthsWithData,
        getCurrentUnrealized
    };
})();
