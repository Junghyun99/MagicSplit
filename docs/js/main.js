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

    function formatCurrency(value) {
        return '$' + Number(value).toLocaleString('en-US', {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0,
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
            formatCurrency(data.portfolio?.total_value || 0);

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
            let lotsHtml = '';
            for (const lot of lots) {
                const pctClass = lot.pct_change >= 0 ? 'pct-positive' : 'pct-negative';
                const pctStr = (lot.pct_change >= 0 ? '+' : '') + lot.pct_change.toFixed(1) + '%';
                lotsHtml += `
                    <li class="lot-item">
                        <span>${lot.buy_date} | ${lot.quantity}shares @$${lot.buy_price.toFixed(2)}</span>
                        <span class="${pctClass}">${pctStr}</span>
                    </li>`;
            }

            card.innerHTML = `
                <div class="card-header">
                    <span class="ticker">${ticker}</span>
                    <span class="price">${info.total_qty} shares | ${info.lot_count} lots</span>
                </div>
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
