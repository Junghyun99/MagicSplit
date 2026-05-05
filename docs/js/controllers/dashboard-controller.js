// docs/js/controllers/dashboard-controller.js
window.DashboardController = (function () {
    'use strict';

    let isRefreshing = false;
    let refreshTimer = null;
    let lastRefreshTime = null;

    async function init() {
        const urlParams = new URLSearchParams(window.location.search);
        const modeParam = urlParams.get('mode') || '';
        DashboardModel.setMode(modeParam);
        
        DashboardView.applyModeUI(DashboardModel.getMode());
        
        await doRefresh();
        
        initRefreshControls();
        initViewSwitch();
    }

    async function doRefresh() {
        if (isRefreshing || document.visibilityState === 'hidden') return;
        isRefreshing = true;
        const mode = DashboardModel.getMode();
        
        try {
            const data = await DataRepository.loadStatus(mode);
            DashboardModel.setStatusData(data);
            
            if (data) {
                lastRefreshTime = Date.now();
                DashboardView.updateRefreshAge(lastRefreshTime);
                DashboardView.setOfflineBadge(false);
                
                const summary = DashboardModel.getPortfolioSummary();
                DashboardView.renderStatus(data, mode, summary);
                
                const reasonType = DashboardModel.classifyReason(data.reason);
                DashboardView.renderReasonBanner(data.reason, data.last_run_date, reasonType);
            } else {
                DashboardView.setOfflineBadge(true);
                DashboardView.showLoading(mode);
            }

            const histData = await DataRepository.loadHistory(mode);
            DashboardModel.setHistoryData(histData);
            HistoryModel.setHistoryData(histData || []);
            
            const buckets = DashboardModel.buildLevelBuckets();
            ChartsView.renderLevelHeatmap(buckets, mode, onHeatmapSelect);
            
            const decData = await DataRepository.loadDecisions(mode);
            DecisionModel.setDecisions(decData);

            if (document.querySelector('.view-link[data-view="history"]').classList.contains('active')) {
                renderHistoryView();
            } else if (document.querySelector('.view-link[data-view="decisions"]').classList.contains('active')) {
                DecisionView.renderDecisions(DecisionModel.getDecisions());
            }

        } finally {
            isRefreshing = false;
        }
    }

    function renderHistoryView() {
        const mode = DashboardModel.getMode();
        const hasData = HistoryModel.getTotalCount() > 0;
        
        if (hasData) {
            const pts = HistoryModel.buildEquityPoints(DashboardModel.getHistoryData());
            ChartsView.renderEquityCurve(pts, mode, DashboardView.formatCurrency);
        } else {
            // Hide equity curve if no data
            const equitySection = document.getElementById('equity-curve-section');
            if (equitySection) equitySection.style.display = 'none';
        }

        HistoryModel.resetPagination();
        const firstPage = HistoryModel.getNextPage();
        HistoryView.renderPage(firstPage, true, HistoryModel.hasMore(), DashboardView.formatCurrency, mode);
    }

    function setAutoRefresh(intervalMs) {
        clearInterval(refreshTimer);
        refreshTimer = null;
        if (intervalMs > 0) {
            refreshTimer = setInterval(doRefresh, intervalMs);
        }
    }

    function initRefreshControls() {
        const refreshBtn = document.getElementById('refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', doRefresh);
        }

        const selectEl = document.getElementById('auto-refresh-select');
        if (selectEl) {
            const saved = parseInt(localStorage.getItem('autoRefreshInterval') || '0', 10);
            const validValues = ['0', '30000', '60000', '300000'];
            selectEl.value = validValues.includes(String(saved)) ? String(saved) : '0';

            selectEl.addEventListener('change', () => {
                const intervalMs = parseInt(selectEl.value, 10);
                localStorage.setItem('autoRefreshInterval', intervalMs);
                setAutoRefresh(intervalMs);
            });

            setAutoRefresh(parseInt(selectEl.value, 10));
        }

        setInterval(() => DashboardView.updateRefreshAge(lastRefreshTime), 10000);
        
        const moreBtn = document.getElementById('load-more-btn');
        if (moreBtn) {
            moreBtn.addEventListener('click', () => {
                const page = HistoryModel.getNextPage();
                HistoryView.renderPage(page, false, HistoryModel.hasMore(), DashboardView.formatCurrency, DashboardModel.getMode());
            });
        }
    }

    function initViewSwitch() {
        document.querySelectorAll('.view-link').forEach((btn) => {
            btn.addEventListener('click', () => {
                const view = btn.dataset.view;
                DashboardView.showView(view);
                if (view === 'history') {
                    const tickers = HistoryModel.getUniqueTickers();
                    HistoryView.renderFilters(tickers, (type, value) => {
                        HistoryModel.setFilter(type, value);
                        const firstPage = HistoryModel.getNextPage();
                        HistoryView.renderPage(firstPage, true, HistoryModel.hasMore(), DashboardView.formatCurrency, DashboardModel.getMode());
                    });
                    
                    renderHistoryView();
                } else if (view === 'decisions') {
                    DecisionView.renderDecisions(DecisionModel.getDecisions());
                }
            });
        });
    }

    function onHeatmapSelect(ticker, month, level) {
        DashboardView.showView('history');
        const tickers = HistoryModel.getUniqueTickers();
        HistoryView.renderFilters(tickers, (type, value) => {
            HistoryModel.setFilter(type, value);
            const firstPage = HistoryModel.getNextPage();
            HistoryView.renderPage(firstPage, true, HistoryModel.hasMore(), DashboardView.formatCurrency, DashboardModel.getMode());
        });
        
        const tickerFilter = document.getElementById('history-ticker-filter');
        if (tickerFilter) tickerFilter.value = ticker;
        HistoryModel.setFilter('ticker', ticker);
        const firstPage = HistoryModel.getNextPage();
        HistoryView.renderPage(firstPage, true, HistoryModel.hasMore(), DashboardView.formatCurrency, DashboardModel.getMode());
        renderHistoryView();
    }

    return { init };
})();
