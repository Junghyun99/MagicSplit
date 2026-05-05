// docs/js/views/dashboard-view.js
window.DashboardView = (function () {
    'use strict';

    const { escapeHtml, formatTickerLabel } = window.FormatUtils;

    function setOfflineBadge(show) {
        const badge = document.getElementById('offline-badge');
        if (badge) badge.style.display = show ? '' : 'none';
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

    function updateRefreshAge(lastRefreshTime) {
        const ageEl = document.getElementById('refresh-age');
        if (ageEl) ageEl.textContent = getRelativeTime(lastRefreshTime);
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

    function renderReasonBanner(reason, lastRunDate, type) {
        const banner = document.getElementById('reason-banner');
        if (!banner) return;
        
        if (!reason) {
            banner.style.display = 'none';
            return;
        }
        
        const icons = { info: 'ℹ️', primary: '🔵', success: '✅', danger: '🔴' };
        banner.className = `reason-banner ${type}`;
        banner.textContent = '';

        const iconSpan = document.createElement('span');
        iconSpan.textContent = icons[type] || icons['info'];
        banner.appendChild(iconSpan);

        const textSpan = document.createElement('span');
        textSpan.textContent = '최근 실행: ' + reason;
        banner.appendChild(textSpan);

        if (lastRunDate) {
            const dateSpan = document.createElement('span');
            dateSpan.className = 'reason-date';
            dateSpan.textContent = '(' + lastRunDate + ')';
            banner.appendChild(dateSpan);
        }

        banner.style.display = '';
    }

    function showLoading(mode) {
        const loading = document.getElementById('loading');
        if (loading) {
            loading.textContent = mode ? `No ${mode} data available.` : 'Loading positions...';
            loading.style.display = '';
        }
        const container = document.getElementById('positions-container');
        if (container) container.innerHTML = '';
        renderReasonBanner(null);
    }

    function hideLoading() {
        const loading = document.getElementById('loading');
        if (loading) loading.style.display = 'none';
    }

    function renderStatus(statusData, mode, portfolioSummary) {
        const container = document.getElementById('positions-container');
        container.innerHTML = '';

        if (!statusData) {
            showLoading(mode);
            return;
        }

        hideLoading();

        document.getElementById('last-updated').textContent =
            'Updated: ' + (statusData.last_updated || '-');
        document.getElementById('total-value').textContent =
            formatCurrency(portfolioSummary.totalValue, mode);

        const positions = statusData.positions || {};
        if (Object.keys(positions).length === 0) {
            const emptyCard = document.createElement('div');
            emptyCard.className = 'card';
            emptyCard.textContent = `No ${mode} positions yet.`;
            container.appendChild(emptyCard);
            return;
        }

        const portRSign = portfolioSummary.totalRealizedPnl >= 0 ? '+' : '';
        const portRClass = portfolioSummary.totalRealizedPnl >= 0 ? 'pct-positive' : 'pct-negative';
        const portUSign = portfolioSummary.totalUnrealizedPnl >= 0 ? '+' : '';
        const portUClass = portfolioSummary.totalUnrealizedPnl >= 0 ? 'pct-positive' : 'pct-negative';
        const portUPctSign = portfolioSummary.unrealizedPct != null && portfolioSummary.unrealizedPct >= 0 ? '+' : '';
        const portUPctClass = portfolioSummary.unrealizedPct != null ? (portfolioSummary.unrealizedPct >= 0 ? 'pct-positive' : 'pct-negative') : '';

        const cashBalanceRows = portfolioSummary.cashBalance != null ? `
            <div class="ps-row">
                <span class="ps-label">예수금</span>
                <span class="ps-value">${formatCurrency(portfolioSummary.cashBalance, mode)}</span>
                <span class="ps-pct">(${portfolioSummary.cashPct.toFixed(1)}%)</span>
            </div>
            <div class="ps-row">
                <span class="ps-label">주식평가</span>
                <span class="ps-value">${formatCurrency(portfolioSummary.stockValue, mode)}</span>
                <span class="ps-pct">(${portfolioSummary.stockPct.toFixed(1)}%)</span>
            </div>` : '';

        const unrealizedPctHtml = portfolioSummary.unrealizedPct != null
            ? `<span class="ps-pct ${portUPctClass}">(${portUPctSign}${portfolioSummary.unrealizedPct.toFixed(2)}%)</span>`
            : '';

        const portfolioCard = document.createElement('div');
        portfolioCard.className = 'card portfolio-summary';
        portfolioCard.innerHTML = `
            <div class="portfolio-summary-title">Portfolio Summary</div>
            <div class="ps-rows">
                <div class="ps-row">
                    <span class="ps-label">총평가액</span>
                    <span class="ps-value">${formatCurrency(portfolioSummary.totalValue, mode)}</span>
                </div>
                ${cashBalanceRows}
                <div class="ps-row">
                    <span class="ps-label">실현손익</span>
                    <span class="ps-value ${portRClass}">${portRSign}${formatCurrency(portfolioSummary.totalRealizedPnl, mode)}</span>
                </div>
                <div class="ps-row">
                    <span class="ps-label">평가손익</span>
                    <span class="ps-value ${portUClass}">${portUSign}${formatCurrency(portfolioSummary.totalUnrealizedPnl, mode)}</span>
                    ${unrealizedPctHtml}
                </div>
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

            const tickerLabel = escapeHtml(formatTickerLabel(ticker, info.alias));
            card.innerHTML = `
                <div class="card-header">
                    <span class="ticker">${tickerLabel} ${levelBadge}</span>
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

    return {
        setOfflineBadge,
        updateRefreshAge,
        formatCurrency,
        applyModeUI,
        renderReasonBanner,
        showLoading,
        hideLoading,
        renderStatus,
        showView
    };
})();
