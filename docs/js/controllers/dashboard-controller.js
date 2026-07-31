// docs/js/controllers/dashboard-controller.js
window.DashboardController = (function () {
    'use strict';

    let isRefreshing = false;
    let refreshTimer = null;
    let lastRefreshTime = null;

    // Charts 탭은 lazy loading이다. 탭을 열 때만 차트 JSON을 fetch하므로
    // 초기 로딩과 다른 탭 사용에는 영향을 주지 않는다.
    let activeChartTicker = null;

    async function init() {
        const urlParams = new URLSearchParams(window.location.search);
        const modeParam = urlParams.get('mode') || '';
        DashboardModel.setMode(modeParam);

        DashboardView.applyModeUI(DashboardModel.getMode());

        // Enforce initial view visibility before first data load
        const initialViewBtn = document.querySelector('.view-link.active');
        const initialView = initialViewBtn ? initialViewBtn.dataset.view : 'risk';
        DashboardView.showView(initialView);

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

            // setStatusData 호출 후에 결정해야 backtest 모드의 market_type이 반영된다.
            const currencyMode = DashboardModel.getCurrencyMode();

            if (data) {
                lastRefreshTime = Date.now();
                DashboardView.updateRefreshAge(lastRefreshTime);
                DashboardView.setOfflineBadge(false);

                const summary = DashboardModel.getPortfolioSummary();
                DashboardView.renderStatus(data, currencyMode, summary);
                
                const reasonType = DashboardModel.classifyReason(data.reason);
                DashboardView.renderReasonBanner(data.reason, data.last_run_date, reasonType);
            } else {
                DashboardView.setOfflineBadge(true);
                DashboardView.showLoading(mode);
            }

            const histData = await DataRepository.loadHistory(mode);
            DashboardModel.setHistoryData(histData);
            HistoryModel.setHistoryData(histData || []);
            EarningsModel.setHistoryData(histData || []);
            EarningsModel.setStatusData(data);
            
            const buckets = DashboardModel.buildLevelBuckets();
            ChartsView.renderLevelHeatmap(buckets, mode, onHeatmapSelect);
            // Re-apply view visibility so heatmap doesn't bleed into non-positions views
            const activeViewBtn = document.querySelector('.view-link.active');
            const currentView = activeViewBtn ? activeViewBtn.dataset.view : 'risk';
            DashboardView.showView(currentView);
            
            const decData = await DataRepository.loadDecisions(mode);
            DecisionModel.setDecisions(decData);

            if (document.querySelector('.view-link[data-view="history"]').classList.contains('active')) {
                renderHistoryView();
            } else if (document.querySelector('.view-link[data-view="decisions"]').classList.contains('active')) {
                DecisionView.renderDecisions(DecisionModel.getDecisions());
            } else if (document.querySelector('.view-link[data-view="risk"]').classList.contains('active')) {
                window.RiskController.renderRisk();
            } else if (document.querySelector('.view-link[data-view="earnings"]').classList.contains('active')) {
                renderEarningsView();
            } else if (document.querySelector('.view-link[data-view="charts"]').classList.contains('active')) {
                // 자동 새로고침 중이라면 이미 그려진 차트도 최신 데이터로 다시 그린다.
                renderChartsView(activeChartTicker);
            }

        } finally {
            isRefreshing = false;
        }
    }

    function renderEarningsView() {
        EarningsView.render(DashboardModel.getCurrencyMode());
    }

    /** 차트 탭: 종목 목록을 세우고 선택된 종목의 차트를 로드한다. */
    async function renderChartsView(forceTicker) {
        const data = DashboardModel.getStatusData();
        const tickers = (data && data.enabled_tickers) ? data.enabled_tickers.slice() : [];
        const aliasMap = buildAliasMap(data);

        if (!tickers.length) {
            RegimeChartView.renderTickerList([], null, aliasMap, () => {});
            RegimeChartView.renderPlaceholder('활성화된 종목이 없습니다.');
            return;
        }

        const ticker = forceTicker
            || (tickers.includes(activeChartTicker) ? activeChartTicker : tickers[0]);
        activeChartTicker = ticker;

        RegimeChartView.renderTickerList(tickers, ticker, aliasMap, (picked) => {
            renderChartsView(picked);
        });

        const chart = await DataRepository.loadChartSeries(DashboardModel.getMode(), ticker);
        // 로딩 중 사용자가 다른 종목을 눌렀으면 늦게 도착한 응답은 버린다.
        if (activeChartTicker !== ticker) return;

        if (!chart) {
            RegimeChartView.renderPlaceholder(
                '이 종목의 차트 데이터가 아직 없습니다. 다음 매매 사이클 후 생성됩니다.'
            );
            return;
        }

        const currencyMode = DashboardModel.getCurrencyMode();
        RegimeChartView.renderChart(
            chart, aliasMap, (v) => DashboardView.formatCurrency(v, currencyMode)
        );
    }

    /**
     * 티커 -> 한글명 맵을 만든다.
     *
     * alias_by_ticker가 활성 종목 전체를 담은 정본이다. positions/holdings는
     * 보유 중인 종목만 있어 미보유 종목의 이름이 빠진다. 봇이 아직 새 형식으로
     * status.json을 쓰기 전에도 이름이 보이도록 기존 두 곳을 폴백으로 남긴다.
     */
    function buildAliasMap(data) {
        const map = {};
        if (!data) return map;
        const positions = data.positions || {};
        Object.keys(positions).forEach((t) => {
            if (positions[t] && positions[t].alias) map[t] = positions[t].alias;
        });
        const holdings = (data.portfolio && data.portfolio.holdings) || [];
        holdings.forEach((h) => {
            if (h && h.ticker && h.alias) map[h.ticker] = h.alias;
        });
        Object.assign(map, data.alias_by_ticker || {});
        return map;
    }

    function renderHistoryView() {
        const currencyMode = DashboardModel.getCurrencyMode();
        const hasData = HistoryModel.getTotalCount() > 0;

        if (hasData) {
            const pts = HistoryModel.buildEquityPoints(DashboardModel.getHistoryData());
            ChartsView.renderEquityCurve(pts, currencyMode, DashboardView.formatCurrency);
        } else {
            // Hide equity curve if no data
            const equitySection = document.getElementById('equity-curve-section');
            if (equitySection) equitySection.style.display = 'none';
        }

        HistoryModel.resetPagination();
        const firstPage = HistoryModel.getNextPage();
        HistoryView.renderPage(firstPage, true, HistoryModel.hasMore(), DashboardView.formatCurrency, currencyMode);
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
                HistoryView.renderPage(page, false, HistoryModel.hasMore(), DashboardView.formatCurrency, DashboardModel.getCurrencyMode());
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
                        HistoryView.renderPage(firstPage, true, HistoryModel.hasMore(), DashboardView.formatCurrency, DashboardModel.getCurrencyMode());
                    });
                    
                    renderHistoryView();
                } else if (view === 'decisions') {
                    DecisionView.renderDecisions(DecisionModel.getDecisions());
                } else if (view === 'risk') {
                    window.RiskController.renderRisk();
                } else if (view === 'earnings') {
                    renderEarningsView();
                } else if (view === 'charts') {
                    renderChartsView();
                }

                // 차트는 캔버스를 붙들고 있으므로 탭을 벗어나면 인스턴스를 놓아준다.
                if (view !== 'charts') {
                    RegimeChartView.destroyChart();
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
            HistoryView.renderPage(firstPage, true, HistoryModel.hasMore(), DashboardView.formatCurrency, DashboardModel.getCurrencyMode());
        });
        
        const tickerFilter = document.getElementById('history-ticker-filter');
        if (tickerFilter) tickerFilter.value = ticker;
        HistoryModel.setFilter('ticker', ticker);
        const firstPage = HistoryModel.getNextPage();
        HistoryView.renderPage(firstPage, true, HistoryModel.hasMore(), DashboardView.formatCurrency, DashboardModel.getCurrencyMode());
        renderHistoryView();
    }

    return { init };
})();
