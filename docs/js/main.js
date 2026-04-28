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
        const updatedTime = data.last_updated || '-';
        // Convert 'April 14, 2026 15:30 (KST)' into an ISO-like format if possible, otherwise use the string.
        let isoTime = updatedTime;
        try {
            if (updatedTime !== '-') {
                const parsedDate = new Date(updatedTime.replace(' (KST)', ' GMT+0900'));
                if (!isNaN(parsedDate)) {
                    isoTime = parsedDate.toISOString();
                }
            }
        } catch (e) {
            // fallback
        }
        document.getElementById('last-updated').innerHTML =
            `Updated: <time datetime="${isoTime}">${updatedTime}</time>`;
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
                const pctClass = lot.pct_change >= 0 ? 'pct-positive' : 'pct-negative';
                const pctStr = (lot.pct_change >= 0 ? '+' : '') + lot.pct_change.toFixed(1) + '%';
                const ariaLabel = lot.pct_change >= 0 ? `Profit of ${Math.abs(lot.pct_change).toFixed(1)}%` : `Loss of ${Math.abs(lot.pct_change).toFixed(1)}%`;
                const arrow = lot.pct_change >= 0 ? '▲' : '▼';
                lotsHtml += `
                    <li class="lot-item">
                        <span>${lot.buy_date} | ${Number(lot.quantity).toLocaleString('en-US')} shares @$${lot.buy_price.toFixed(2)}</span>
                        <span class="${pctClass}" aria-label="${ariaLabel}">${pctStr} <span aria-hidden="true">${arrow}</span></span>
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
