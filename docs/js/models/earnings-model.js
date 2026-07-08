// docs/js/models/earnings-model.js
window.EarningsModel = (function () {
    'use strict';

    // { "2026-05": { realized_pnl, sell_count, tickers: { ticker: { pnl, count, alias } } } }
    let monthlyData = {};
    let sortedYears = [];
    let currentUnrealized = { total: 0, byTicker: {} };
    let exchangeRate = null; // 마지막 저장 시점 기준환율 (KRW/USD), 없으면 null

    // { ticker: { sell_count, win_count, alias, trades: [...], monthly: { "YYYY-MM": { pnl, count } } } }
    let tickerSellStats = {};
    let _rawStatusData = null;

    // { "YYYY-MM": { level: { total_pnl, sell_count, win_count, rates: [] } } }
    let levelMonthlyData = {};

    function setHistoryData(histData) {
        monthlyData = {};
        tickerSellStats = {};
        levelMonthlyData = {};
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

                // -- global monthly buckets --
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

                // -- per-ticker sell stats --
                if (!tickerSellStats[ticker]) {
                    tickerSellStats[ticker] = { sell_count: 0, win_count: 0, alias: ex.alias || '', trades: [], monthly: {} };
                }
                const ts = tickerSellStats[ticker];
                ts.sell_count += 1;
                if (Number(ex.realized_pnl) > 0) ts.win_count += 1;
                if (!ts.alias && ex.alias) ts.alias = ex.alias;

                const buyPrice = ex.buy_price != null ? Number(ex.buy_price) : null;
                const qty = ex.quantity || 0;
                const profitRate = (buyPrice && buyPrice > 0 && qty > 0)
                    ? (Number(ex.realized_pnl) / (buyPrice * qty)) * 100 : null;
                ts.trades.push({
                    date: dateStr,
                    level: ex.level != null ? ex.level : '',
                    qty,
                    sell_price: ex.price || 0,
                    buy_price: buyPrice,
                    fee: ex.fee || 0,
                    realized_pnl: Number(ex.realized_pnl),
                    profit_rate: profitRate
                });

                if (!ts.monthly[yearMonth]) ts.monthly[yearMonth] = { pnl: 0, count: 0 };
                ts.monthly[yearMonth].pnl += Number(ex.realized_pnl);
                ts.monthly[yearMonth].count += 1;

                // -- level monthly buckets --
                const level = ex.level != null ? ex.level : null;
                if (level != null) {
                    if (!levelMonthlyData[yearMonth]) levelMonthlyData[yearMonth] = {};
                    if (!levelMonthlyData[yearMonth][level]) {
                        levelMonthlyData[yearMonth][level] = { total_pnl: 0, sell_count: 0, win_count: 0, rates: [] };
                    }
                    const lb = levelMonthlyData[yearMonth][level];
                    lb.total_pnl += Number(ex.realized_pnl);
                    lb.sell_count += 1;
                    if (Number(ex.realized_pnl) > 0) lb.win_count += 1;
                    if (profitRate != null) lb.rates.push(profitRate);
                }
            }
        }

        // sort trades newest-first within each ticker
        for (const ts of Object.values(tickerSellStats)) {
            ts.trades.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
        }

        const yearSet = new Set();
        for (const key of Object.keys(monthlyData)) yearSet.add(key.slice(0, 4));
        sortedYears = Array.from(yearSet).sort();
    }

    function setStatusData(statusData) {
        _rawStatusData = statusData || null;
        currentUnrealized = { total: 0, byTicker: {} };
        const parsedRate = (statusData && statusData.exchange_rate != null) ? Number(statusData.exchange_rate) : null;
        exchangeRate = (parsedRate !== null && !isNaN(parsedRate)) ? parsedRate : null;
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
                if (!tickerMap[ticker].alias && td.alias) tickerMap[ticker].alias = td.alias;
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
            if (!year || key.slice(0, 4) === year) set.add(key.slice(5, 7));
        }
        return set;
    }

    function getCurrentUnrealized() {
        return currentUnrealized;
    }

    function getExchangeRate() {
        return exchangeRate;
    }

    // Returns array of per-ticker comprehensive P&L (all tickers ever traded or held)
    function getTickerSummaries() {
        const statusData = _rawStatusData || {};
        const realizedByTicker = statusData.realized_pnl_by_ticker || {};
        const positions = statusData.positions || {};

        // Collect all known tickers: union of positions + realized_pnl_by_ticker + history
        const allTickers = new Set([
            ...Object.keys(positions),
            ...Object.keys(realizedByTicker),
            ...Object.keys(tickerSellStats)
        ]);

        const result = [];
        for (const ticker of allTickers) {
            const posInfo = positions[ticker];
            const ts = tickerSellStats[ticker] || { sell_count: 0, win_count: 0, alias: '' };

            // Prefer positions.alias, then tickerSellStats.alias
            const alias = (posInfo && posInfo.alias) || ts.alias || '';

            // realized_pnl: positions > realized_pnl_by_ticker > history trades sum (backtest/incomplete data fallback)
            const realized_pnl = posInfo
                ? Number(posInfo.realized_pnl || 0)
                : (realizedByTicker[ticker] != null
                    ? Number(realizedByTicker[ticker])
                    : (tickerSellStats[ticker]
                        ? tickerSellStats[ticker].trades.reduce((sum, t) => sum + t.realized_pnl, 0)
                        : 0));

            const unrealized_pnl = posInfo ? Number(posInfo.unrealized_pnl || 0) : 0;
            const total_pnl = realized_pnl + unrealized_pnl;
            const status = posInfo ? '보유중' : '청산완료';
            const win_rate = ts.sell_count > 0 ? (ts.win_count / ts.sell_count) * 100 : null;

            result.push({ ticker, alias, status, realized_pnl, unrealized_pnl, total_pnl, sell_count: ts.sell_count, win_count: ts.win_count, win_rate });
        }

        return result.sort((a, b) => b.total_pnl - a.total_pnl);
    }

    // Returns level-aggregated stats filtered by year/month
    // Returns array sorted by level asc: [{ level, total_pnl, sell_count, win_count, win_rate, avg_profit_rate }]
    function getLevelStats(year, month) {
        const map = {};
        for (const [ym, levels] of Object.entries(levelMonthlyData)) {
            const keyYear = ym.slice(0, 4);
            const keyMonth = ym.slice(5, 7);
            if (year && keyYear !== year) continue;
            if (month && keyMonth !== month) continue;
            for (const [lv, lb] of Object.entries(levels)) {
                const lvNum = Number(lv);
                if (!map[lvNum]) map[lvNum] = { total_pnl: 0, sell_count: 0, win_count: 0, rates: [] };
                map[lvNum].total_pnl += lb.total_pnl;
                map[lvNum].sell_count += lb.sell_count;
                map[lvNum].win_count += lb.win_count;
                map[lvNum].rates = map[lvNum].rates.concat(lb.rates);
            }
        }
        return Object.entries(map)
            .map(([lv, d]) => ({
                level: Number(lv),
                total_pnl: d.total_pnl,
                sell_count: d.sell_count,
                win_count: d.win_count,
                win_rate: d.sell_count > 0 ? (d.win_count / d.sell_count) * 100 : null,
                avg_profit_rate: d.rates.length > 0 ? d.rates.reduce((s, r) => s + r, 0) / d.rates.length : null
            }))
            .sort((a, b) => a.level - b.level);
    }

    // Returns sell trade history + monthly breakdown + current lots for a single ticker
    function getTickerDetail(ticker) {
        const ts = tickerSellStats[ticker] || { trades: [], monthly: {}, alias: '' };
        const posInfo = (_rawStatusData && _rawStatusData.positions && _rawStatusData.positions[ticker]) || null;

        const monthly = Object.entries(ts.monthly)
            .map(([ym, d]) => ({ yearMonth: ym, pnl: d.pnl, count: d.count }))
            .sort((a, b) => a.yearMonth.localeCompare(b.yearMonth));

        const lots = posInfo ? (posInfo.lots || []) : [];
        const alias = (posInfo && posInfo.alias) || ts.alias || ticker;

        return { alias, trades: ts.trades, monthly, lots };
    }

    return {
        setHistoryData,
        setStatusData,
        getAvailableYears,
        getYearMonthly,
        getPeriodSummary,
        getMonthsWithData,
        getCurrentUnrealized,
        getExchangeRate,
        getTickerSummaries,
        getTickerDetail,
        getLevelStats
    };
})();
