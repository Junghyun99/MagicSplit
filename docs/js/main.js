// MagicSplit Dashboard - main.js
(function () {
    'use strict';

    const VALID_MODES = ['domestic', 'overseas', 'backtest'];
    const DEFAULT_MODE = 'domestic';

    let currentMode = DEFAULT_MODE;
    let currentView = 'positions';
    let historyLoaded = false;
    let isRefreshing = false;
    let lastRefreshTime = null;
    let refreshTimer = null;

    function resolveMode() {
        const params = new URLSearchParams(window.location.search);
        const requested = (params.get('mode') || '').toLowerCase();
        return VALID_MODES.includes(requested) ? requested : DEFAULT_MODE;
    }

    function setOfflineBadge(show) {
        const badge = document.getElementById('offline-badge');
        if (badge) badge.style.display = show ? '' : 'none';
    }

    async function loadStatus(mode) {
        const url = `data/${mode}/status.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            setOfflineBadge(false);
            return data;
        } catch (e) {
            console.error(`Failed to load status (${mode}):`, e);
            setOfflineBadge(true);
            return null;
        }
    }

    async function loadHistory(mode) {
        const url = `data/${mode}/history.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            console.error(`Failed to load history (${mode}):`, e);
            return null;
        }
    }

    function hideHeatmap() {
        const section = document.getElementById('level-heatmap-section');
        if (section) section.style.display = 'none';
    }

    async function renderHeatmapForMode(mode) {
        if (mode !== 'backtest' || !window.MagicSplitCharts) {
            hideHeatmap();
            return;
        }
        const history = await loadHistory(mode);
        if (!history) {
            hideHeatmap();
            return;
        }
        window.MagicSplitCharts.renderLevelHeatmap(history, mode);
    }

    function getRelativeTime(timestamp) {
        if (!timestamp) return '';
        const seconds = Math.floor((Date.now() - timestamp) / 1000);
        if (seconds < 10) return '방금 전';
        if (seconds < 60) return `${seconds}초 전`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}분 전`;
        return `${Math.floor(minutes / 60)}시간 전`;
    }

    function updateRefreshAge() {
        const ageEl = document.getElementById('refresh-age');
        if (ageEl) ageEl.textContent = getRelativeTime(lastRefreshTime);
    }

    async function doRefresh() {
        if (isRefreshing || document.visibilityState === 'hidden') return;
        isRefreshing = true;
        try {
            const data = await loadStatus(currentMode);
            if (data) {
                renderStatus(data, currentMode);
                lastRefreshTime = Date.now();
                updateRefreshAge();
            }
        } finally {
            isRefreshing = false;
        }
    }

    function setAutoRefresh(intervalMs) {
        clearInterval(refreshTimer);
        refreshTimer = null;
        if (intervalMs > 0) {
            refreshTimer = setInterval(doRefresh, intervalMs);
        }
    }

    function formatCurrency(value, mode) {
        const isDomestic = mode === 'domestic';
        return new Intl.NumberFormat(isDomestic ? 'ko-KR' : 'en-US', {
            style: 'currency',
            currency: isDomestic ? 'KRW' : 'USD',
            minimumFractionDigits: isDomestic ? 0 : 2,
            maximumFractionDigits: isDomestic ? 0 : 2,
        }).format(Number(value));
    }

    function applyModeUI(mode) {
        const badge = document.getElementById('mode-badge');
        if (badge) {
            badge.textContent = mode.toUpperCase();
            badge.dataset.mode = mode;
        }
        document.querySelectorAll('.mode-link').forEach((link) => {
            link.classList.toggle('active', link.dataset.mode === mode);
        });
    }

    function classifyReason(reason) {
        if (!reason) return 'info';
        const lower = reason.toLowerCase();
        if (lower.includes('에러') || lower.includes('오류') || lower.includes('error')) return 'danger';
        if (reason.includes('매도')) return 'success';
        if (reason.includes('매수')) return 'primary';
        return 'info';
    }

    function renderReasonBanner(data) {
        const banner = document.getElementById('reason-banner');
        if (!banner) return;
        const reason = data && data.reason;
        if (!reason) {
            banner.style.display = 'none';
            return;
        }
        const type = classifyReason(reason);
        const icons = { info: 'ℹ️', primary: '🔵', success: '✅', danger: '🔴' };
        banner.className = `reason-banner ${type}`;
        banner.textContent = '';

        const iconSpan = document.createElement('span');
        iconSpan.textContent = icons[type];
        banner.appendChild(iconSpan);

        const textSpan = document.createElement('span');
        textSpan.textContent = '최근 실행: ' + reason;
        banner.appendChild(textSpan);

        if (data.last_run_date) {
            const dateSpan = document.createElement('span');
            dateSpan.className = 'reason-date';
            dateSpan.textContent = '(' + data.last_run_date + ')';
            banner.appendChild(dateSpan);
        }

        banner.style.display = '';
    }

    function renderStatus(data, mode) {
        const loading = document.getElementById('loading');
        const container = document.getElementById('positions-container');
        container.innerHTML = '';

        if (!data) {
            loading.textContent = `No ${mode} data available.`;
            loading.style.display = '';
            renderReasonBanner(null);
            return;
        }

        loading.style.display = 'none';

        document.getElementById('last-updated').textContent =
            'Updated: ' + (data.last_updated || '-');
        document.getElementById('total-value').textContent =
            formatCurrency(data.portfolio?.total_value || 0, mode);

        renderReasonBanner(data);

        const positions = data.positions || {};
        if (Object.keys(positions).length === 0) {
            const emptyCard = document.createElement('div');
            emptyCard.className = 'card';
            emptyCard.textContent = `No ${mode} positions yet.`;
            container.appendChild(emptyCard);
            return;
        }

        let totalRealizedPnl = 0;
        let totalUnrealizedPnl = 0;
        for (const info of Object.values(positions)) {
            if (info.realized_pnl != null) totalRealizedPnl += Number(info.realized_pnl);
            if (info.unrealized_pnl != null) totalUnrealizedPnl += Number(info.unrealized_pnl);
        }

        const portRSign = totalRealizedPnl >= 0 ? '+' : '';
        const portRClass = totalRealizedPnl >= 0 ? 'pct-positive' : 'pct-negative';
        const portUSign = totalUnrealizedPnl >= 0 ? '+' : '';
        const portUClass = totalUnrealizedPnl >= 0 ? 'pct-positive' : 'pct-negative';
        const portfolioCard = document.createElement('div');
        portfolioCard.className = 'card portfolio-summary';
        portfolioCard.innerHTML = `
            <div class="portfolio-summary-title">Portfolio</div>
            <div class="portfolio-summary-row">
                <span class="summary-label">총 실현손익:</span>
                <span class="${portRClass}">${portRSign}${formatCurrency(totalRealizedPnl, mode)}</span>
                <span class="summary-sep">|</span>
                <span class="summary-label">총 평가손익:</span>
                <span class="${portUClass}">${portUSign}${formatCurrency(totalUnrealizedPnl, mode)}</span>
            </div>
        `;
        container.appendChild(portfolioCard);

        for (const [ticker, info] of Object.entries(positions)) {
            const card = document.createElement('div');
            card.className = 'card';

            const lots = info.lots || [];
            const maxLevel = lots.length > 0 ? Math.max(...lots.map((l) => l.level || 0)) : null;
            const levelAttr = maxLevel !== null ? Math.min(maxLevel, 5) : 0;
            const levelBadge = maxLevel !== null
                ? `<span class="level-badge" data-level="${levelAttr}">Lv${maxLevel}</span>`
                : '';

            let lotsHtml = '';
            for (const lot of lots) {
                const pctClass = lot.pct_change >= 0 ? 'pct-positive' : 'pct-negative';
                const pctStr = (lot.pct_change >= 0 ? '+' : '') + lot.pct_change.toFixed(1) + '%';
                const lvLabel = lot.level != null ? `<span class="lot-level">Lv${lot.level}</span>` : '<span class="lot-level"></span>';
                lotsHtml += `
                    <li class="lot-item">
                        ${lvLabel}
                        <span class="lot-detail">${lot.buy_date} | ${lot.quantity}shares @$${lot.buy_price.toFixed(2)}</span>
                        <span class="${pctClass}">${pctStr}</span>
                    </li>`;
            }

            const hasPnl = info.unrealized_pnl != null;
            const pnlClass = hasPnl && info.unrealized_pnl >= 0 ? 'pct-positive' : 'pct-negative';
            const pnlSign = hasPnl && info.unrealized_pnl >= 0 ? '+' : '';
            const summaryHtml = hasPnl ? `
                <div class="card-summary">
                    <span class="summary-label">평가:</span>
                    <span>${formatCurrency(info.current_value, mode)}</span>
                    <span class="summary-muted">(투자 ${formatCurrency(info.total_invested, mode)})</span>
                    <span class="summary-sep">|</span>
                    <span class="summary-label">손익:</span>
                    <span class="${pnlClass}">${pnlSign}${formatCurrency(info.unrealized_pnl, mode)} (${pnlSign}${Number(info.unrealized_pnl_pct).toFixed(2)}%)</span>
                </div>` : '';

            let realizedHtml = '';
            if (info.realized_pnl != null) {
                const rPnl = Number(info.realized_pnl);
                const rClass = rPnl >= 0 ? 'pct-positive' : 'pct-negative';
                const rSign = rPnl >= 0 ? '+' : '';
                let totalPnlHtml = '';
                if (info.total_pnl != null) {
                    const tPnl = Number(info.total_pnl);
                    const tClass = tPnl >= 0 ? 'pct-positive' : 'pct-negative';
                    const tSign = tPnl >= 0 ? '+' : '';
                    totalPnlHtml = `
                        <span class="summary-sep">|</span>
                        <span class="summary-label">총손익:</span>
                        <span class="${tClass}">${tSign}${formatCurrency(tPnl, mode)}</span>`;
                }
                realizedHtml = `
                    <div class="card-realized-pnl">
                        <span class="summary-label">실현손익:</span>
                        <span class="${rClass}">${rSign}${formatCurrency(rPnl, mode)}</span>
                        ${totalPnlHtml}
                    </div>`;
            }

            card.innerHTML = `
                <div class="card-header">
                    <span class="ticker">${ticker} ${levelBadge}</span>
                    <span class="price">${info.total_qty} shares | ${info.lot_count} lots</span>
                </div>
                ${summaryHtml}
                ${realizedHtml}
                <ul class="lot-list">${lotsHtml}</ul>
            `;
            container.appendChild(card);
        }
    }

    function showView(view) {
        currentView = view;
        const posEls = ['positions-container', 'level-heatmap-section', 'reason-banner'];
        const histEls = ['history-section'];

        const isPositions = view === 'positions';
        posEls.forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.style.display = isPositions ? '' : 'none';
        });
        histEls.forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.style.display = isPositions ? 'none' : '';
        });

        document.querySelectorAll('.view-link').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.view === view);
        });
    }

    async function loadHistoryView(mode) {
        if (!window.MagicSplitHistory) return;
        if (!historyLoaded) {
            const data = await loadHistory(mode);
            window.MagicSplitHistory.renderHistory(data || [], mode, formatCurrency);
            if (window.MagicSplitCharts && window.MagicSplitCharts.renderEquityCurve) {
                window.MagicSplitCharts.renderEquityCurve(data || [], mode);
            }
            historyLoaded = true;
        }
    }

    function initViewSwitch() {
        document.querySelectorAll('.view-link').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const view = btn.dataset.view;
                showView(view);
                if (view === 'history') {
                    await loadHistoryView(currentMode);
                }
            });
        });
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

        setInterval(updateRefreshAge, 10000);
    }

    async function init() {
        currentMode = resolveMode();
        historyLoaded = false;
        applyModeUI(currentMode);
        const data = await loadStatus(currentMode);
        renderStatus(data, currentMode);
        if (data) {
            lastRefreshTime = Date.now();
            updateRefreshAge();
        }
        initRefreshControls();
        initViewSwitch();
        await renderHeatmapForMode(currentMode);
    }

    init();
})();
