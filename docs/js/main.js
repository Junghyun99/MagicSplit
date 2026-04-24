// MagicSplit Dashboard - main.js
(function () {
    'use strict';

    const VALID_MODES = ['domestic', 'overseas', 'backtest'];
    const DEFAULT_MODE = 'domestic';

    function resolveMode() {
        const params = new URLSearchParams(window.location.search);
        const requested = (params.get('mode') || '').toLowerCase();
        return VALID_MODES.includes(requested) ? requested : DEFAULT_MODE;
    }

    async function loadStatus(mode) {
        const url = `data/${mode}/status.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            console.error(`Failed to load status (${mode}):`, e);
            return null;
        }
    }

    function formatCurrency(value, mode) {
        if (mode === 'domestic') {
            return '₩' + Number(value).toLocaleString('ko-KR', {
                minimumFractionDigits: 0,
                maximumFractionDigits: 0,
            });
        }
        return '$' + Number(value).toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
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

    function renderStatus(data, mode) {
        const loading = document.getElementById('loading');
        const container = document.getElementById('positions-container');
        container.innerHTML = '';

        if (!data) {
            loading.textContent = `No ${mode} data available.`;
            loading.style.display = '';
            return;
        }

        loading.style.display = 'none';

        document.getElementById('last-updated').textContent =
            'Updated: ' + (data.last_updated || '-');
        document.getElementById('total-value').textContent =
            formatCurrency(data.portfolio?.total_value || 0, mode);

        const positions = data.positions || {};
        if (Object.keys(positions).length === 0) {
            const emptyCard = document.createElement('div');
            emptyCard.className = 'card';
            emptyCard.textContent = `No ${mode} positions yet.`;
            if (data.reason) {
                const reasonNode = document.createElement('span');
                reasonNode.className = 'empty-reason';
                reasonNode.textContent = ` (${data.reason})`;
                emptyCard.appendChild(reasonNode);
            }
            container.appendChild(emptyCard);
            return;
        }

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

            card.innerHTML = `
                <div class="card-header">
                    <span class="ticker">${ticker} ${levelBadge}</span>
                    <span class="price">${info.total_qty} shares | ${info.lot_count} lots</span>
                </div>
                ${summaryHtml}
                <ul class="lot-list">${lotsHtml}</ul>
            `;
            container.appendChild(card);
        }
    }

    async function init() {
        const mode = resolveMode();
        applyModeUI(mode);
        const data = await loadStatus(mode);
        renderStatus(data, mode);
    }

    init();
})();
