// MagicSplit Dashboard - main.js
(function () {
    'use strict';

    const STATUS_URL = 'data/status.json';

    async function loadStatus() {
        try {
            const ts = Date.now();
            const res = await fetch(`${STATUS_URL}?t=${ts}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            console.error('Failed to load status:', e);
            return null;
        }
    }

    function formatCurrency(value) {
        return '$' + Number(value).toLocaleString('en-US', {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0,
        });
    }

    function renderStatus(data) {
        if (!data) {
            document.getElementById('loading').textContent = 'No data available.';
            return;
        }

        document.getElementById('loading').style.display = 'none';

        // Status bar
        document.getElementById('last-updated').textContent =
            'Updated: ' + (data.last_updated || '-');
        document.getElementById('total-value').textContent =
            formatCurrency(data.portfolio?.total_value || 0);

        // Positions
        const container = document.getElementById('positions-container');
        container.innerHTML = '';

        const positions = data.positions || {};
        if (Object.keys(positions).length === 0) {
            container.innerHTML = '<div class="card">No positions yet.</div>';
            return;
        }

        for (const [ticker, info] of Object.entries(positions)) {
            const card = document.createElement('div');
            card.className = 'card';

            const lots = info.lots || [];
            let lotsHtml = '';
            for (const lot of lots) {
                const isPositive = lot.pct_change >= 0;
                const pctClass = isPositive ? 'pct-positive' : 'pct-negative';
                const pctStr = (isPositive ? '+' : '') + lot.pct_change.toFixed(1) + '%';
                const ariaLabel = isPositive ? `Profit of ${lot.pct_change.toFixed(1)}%` : `Loss of ${Math.abs(lot.pct_change).toFixed(1)}%`;
                const arrowIndicator = isPositive ? '▲' : '▼';

                lotsHtml += `
                    <li class="lot-item">
                        <span>${lot.buy_date} | ${lot.quantity}shares @$${lot.buy_price.toFixed(2)}</span>
                        <span class="${pctClass}" aria-label="${ariaLabel}">
                            ${pctStr} <span aria-hidden="true">${arrowIndicator}</span>
                        </span>
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
        const data = await loadStatus();
        renderStatus(data);
    }

    init();
})();
