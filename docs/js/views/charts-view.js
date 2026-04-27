// docs/js/views/charts-view.js
window.ChartsView = (function () {
    'use strict';

    const LEVEL_CAP = 5;

    function buildAxisRow(months) {
        const row = document.createElement('div');
        row.className = 'heatmap-row';

        const corner = document.createElement('div');
        corner.className = 'heatmap-label heatmap-axis';
        corner.textContent = '';
        row.appendChild(corner);

        for (const m of months) {
            const cell = document.createElement('div');
            cell.className = 'heatmap-axis';
            cell.textContent = m.slice(2);  // "25-01"
            cell.title = m;
            row.appendChild(cell);
        }
        return row;
    }

    function buildTickerRow(ticker, months, grid, trades) {
        const row = document.createElement('div');
        row.className = 'heatmap-row';

        const label = document.createElement('div');
        label.className = 'heatmap-label';
        label.textContent = ticker;
        row.appendChild(label);

        for (const m of months) {
            const cell = document.createElement('div');
            cell.className = 'heatmap-cell';
            const level = (grid[ticker] && grid[ticker][m]) || 0;
            const count = (trades[ticker] && trades[ticker][m]) || 0;
            const clamped = Math.min(level, LEVEL_CAP);
            cell.dataset.level = String(clamped);
            cell.dataset.ticker = ticker;
            cell.dataset.month = m;
            cell.dataset.rawLevel = String(level);
            const levelLabel = level > 0 ? `Lv${level}` : '보유 없음';
            const tradeLabel = count > 0 ? ` (거래 ${count}건)` : '';
            cell.title = `${ticker} | ${m} | ${levelLabel}${tradeLabel}`;
            if (level > 0) cell.textContent = String(level);
            row.appendChild(cell);
        }
        return row;
    }

    function hideHeatmap() {
        const section = document.getElementById('level-heatmap-section');
        if (section) section.style.display = 'none';
    }

    function renderLevelHeatmap(data, mode, onCellClick) {
        const container = document.getElementById('level-heatmap');
        const section = document.getElementById('level-heatmap-section');
        if (!container || !section) return;

        container.textContent = '';

        if (!data || data.months.length === 0 || data.tickers.length === 0) {
            section.style.display = 'none';
            return;
        }

        const columnCount = data.months.length + 1;
        container.style.gridTemplateColumns = `minmax(64px, auto) repeat(${data.months.length}, minmax(28px, 1fr))`;

        container.appendChild(buildAxisRow(data.months));
        for (const t of data.tickers) {
            container.appendChild(buildTickerRow(t, data.months, data.grid, data.trades));
        }

        if (!container.dataset.listenersBound) {
            container.addEventListener('click', (e) => {
                const cell = e.target.closest('.heatmap-cell');
                if (!cell) return;
                const level = parseInt(cell.dataset.rawLevel || '0', 10);
                if (level === 0) return;
                if (onCellClick) onCellClick(cell.dataset.ticker, cell.dataset.month, level);
            });
            container.dataset.listenersBound = '1';
        }

        section.style.display = '';
        section.dataset.mode = mode || '';
        container.dataset.columns = String(columnCount);
    }

    function esc(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function renderEquityCurve(pts, mode, formatCurrencyFn) {
        const section = document.getElementById('equity-curve-section');
        const container = document.getElementById('equity-curve');
        if (!section || !container) return;

        if (!pts || pts.length < 2) {
            section.style.display = 'none';
            return;
        }

        const W = 560, H = 200;
        const PAD = { top: 32, right: 20, bottom: 32, left: 60 };
        const chartW = W - PAD.left - PAD.right;
        const chartH = H - PAD.top - PAD.bottom;

        const values = pts.map(p => p.value);
        const rawMin = Math.min(...values);
        const rawMax = Math.max(...values);
        const rawRange = rawMax - rawMin || Math.abs(rawMin) * 0.1 || 1;
        const vPad = rawRange * 0.12;
        const minY = rawMin - vPad;
        const maxY = rawMax + vPad;
        const rangeY = maxY - minY;

        const initialValue = pts[0].value;
        const currentValue = pts[pts.length - 1].value;
        const returnPct = ((currentValue - initialValue) / initialValue) * 100;
        const returnSign = returnPct >= 0 ? '+' : '';
        const lineColor = returnPct >= 0 ? '#16a34a' : '#dc2626';

        const xScale = (i) => PAD.left + (i / (pts.length - 1)) * chartW;
        const yScale = (v) => PAD.top + chartH - ((v - minY) / rangeY) * chartH;

        const linePath = pts.map((p, i) =>
            `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(p.value).toFixed(1)}`
        ).join(' ');

        const bottomY = (PAD.top + chartH).toFixed(1);
        const areaPath = `${linePath} L${xScale(pts.length - 1).toFixed(1)},${bottomY} L${xScale(0).toFixed(1)},${bottomY}Z`;

        const baseY = yScale(initialValue).toFixed(1);

        const maxLabels = Math.min(pts.length, 6);
        const xLabelHtml = Array.from({ length: maxLabels }, (_, k) => {
            const i = maxLabels === 1 ? 0 : Math.round(k * (pts.length - 1) / (maxLabels - 1));
            const x = xScale(i).toFixed(1);
            const label = pts[i].date.length >= 7 ? pts[i].date.slice(2) : pts[i].date;
            return `<text x="${x}" y="${H - 4}" text-anchor="middle" class="ec-axis">${esc(label)}</text>`;
        }).join('');

        const yTickValues = [rawMin, (rawMin + rawMax) / 2, rawMax];
        const yLabelHtml = yTickValues.map(v => {
            const y = yScale(v).toFixed(1);
            const label = Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'K' : v.toFixed(0);
            return `<text x="${PAD.left - 5}" y="${y}" text-anchor="end" dominant-baseline="middle" class="ec-axis">${esc(label)}</text>`;
        }).join('');

        const MARKER_CAP = 60;
        const dotsHtml = pts.length <= MARKER_CAP ? pts.map((p, i) => {
            const x = xScale(i).toFixed(1);
            const y = yScale(p.value).toFixed(1);
            return `<circle cx="${x}" cy="${y}" r="3.5" fill="white" stroke="${lineColor}" stroke-width="1.8"><title>${esc(p.date)}: ${p.value.toFixed(2)}</title></circle>`;
        }).join('') : '';

        const fmtVal = (v) => formatCurrencyFn ? formatCurrencyFn(v, mode) : v.toFixed(2);

        container.innerHTML = `
            <div class="ec-summary">
                <span class="ec-label">수익률</span>
                <span class="ec-return" style="color:${lineColor}">${returnSign}${returnPct.toFixed(2)}%</span>
                <span class="ec-detail">(${esc(pts[0].date)} ${esc(fmtVal(initialValue))} → ${esc(pts[pts.length - 1].date)} ${esc(fmtVal(currentValue))})</span>
            </div>
            <svg viewBox="0 0 ${W} ${H}" class="equity-curve-svg" role="img" aria-label="자산 곡선">
                <defs>
                    <linearGradient id="ec-grad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stop-color="${lineColor}" stop-opacity="0.2"/>
                        <stop offset="100%" stop-color="${lineColor}" stop-opacity="0.02"/>
                    </linearGradient>
                </defs>
                <path d="${areaPath}" fill="url(#ec-grad)"/>
                <line x1="${PAD.left}" y1="${baseY}" x2="${(PAD.left + chartW).toFixed(1)}" y2="${baseY}" class="ec-baseline"/>
                <path d="${linePath}" fill="none" stroke="${lineColor}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
                ${yLabelHtml}
                ${xLabelHtml}
                ${dotsHtml}
            </svg>`;

        section.style.display = '';
    }

    return {
        renderLevelHeatmap,
        renderEquityCurve,
        hideHeatmap
    };
})();
