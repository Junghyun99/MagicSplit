// docs/js/views/earnings-view.js
window.EarningsView = (function () {
    'use strict';

    const { escapeHtml, formatTickerLabel } = window.FormatUtils;

    let selectedYear = undefined;  // undefined=not init, null=all, "2026"=specific year
    let selectedMonth = null; // "05" or null (all)
    let _currencyMode = 'domestic';

    function formatAmt(value, mode) {
        const isDomestic = (mode || 'domestic') === 'domestic';
        return new Intl.NumberFormat(isDomestic ? 'ko-KR' : 'en-US', {
            style: 'currency',
            currency: isDomestic ? 'KRW' : 'USD',
            minimumFractionDigits: isDomestic ? 0 : 2,
            maximumFractionDigits: isDomestic ? 0 : 2,
        }).format(Number(value));
    }

    function render(currencyMode) {
        _currencyMode = currencyMode || 'domestic';

        const years = EarningsModel.getAvailableYears();

        // Default: select latest year on first render if not set
        if (selectedYear === undefined || (selectedYear !== null && years.length > 0 && !years.includes(selectedYear))) {
            selectedYear = years.length > 0 ? years[years.length - 1] : null;
            selectedMonth = null;
        }

        renderYearFilter(years);
        renderMonthFilter();
        renderSummaryCards();
        renderBarChart();
        renderTickerTable();
    }

    function renderYearFilter(years) {
        const container = document.getElementById('earnings-year-filter');
        if (!container) return;

        const allBtn = `<button class="filter-chip${selectedYear === null ? ' active' : ''}" data-year="">전체</button>`;
        const yearBtns = years.map(y =>
            `<button class="filter-chip${selectedYear === y ? ' active' : ''}" data-year="${escapeHtml(y)}">${escapeHtml(y)}</button>`
        ).join('');

        container.innerHTML = `<span class="earnings-filter-label">연도</span>${allBtn}${yearBtns}`;

        container.querySelectorAll('.filter-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                selectedYear = btn.dataset.year || null;
                selectedMonth = null;
                render(_currencyMode);
            });
        });
    }

    function renderMonthFilter() {
        const container = document.getElementById('earnings-month-filter');
        if (!container) return;

        const monthsWithData = EarningsModel.getMonthsWithData(selectedYear);
        const MONTH_LABELS = ['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];

        const allBtn = `<button class="filter-chip${selectedMonth === null ? ' active' : ''}" data-month="">전체</button>`;
        const monthBtns = Array.from({ length: 12 }, (_, i) => {
            const m = String(i + 1).padStart(2, '0');
            const hasData = monthsWithData.has(m);
            const isActive = selectedMonth === m;
            const dimClass = hasData ? '' : ' dim';
            return `<button class="filter-chip${isActive ? ' active' : ''}${dimClass}" data-month="${m}" ${hasData ? '' : 'disabled'}>${MONTH_LABELS[i]}</button>`;
        }).join('');

        container.innerHTML = `<span class="earnings-filter-label">월</span>${allBtn}${monthBtns}`;

        container.querySelectorAll('.filter-chip:not([disabled])').forEach(btn => {
            btn.addEventListener('click', () => {
                selectedMonth = btn.dataset.month || null;
                render(_currencyMode);
            });
        });
    }

    function renderSummaryCards() {
        const container = document.getElementById('earnings-summary-cards');
        if (!container) return;

        const summary = EarningsModel.getPeriodSummary(selectedYear, selectedMonth);
        const unrealized = EarningsModel.getCurrentUnrealized();
        const mode = _currencyMode;

        const rPnl = summary.realized_pnl;
        const rClass = rPnl >= 0 ? 'pct-positive' : 'pct-negative';
        const rSign = rPnl > 0 ? '+' : '';

        const uPnl = unrealized.total;
        const uClass = uPnl >= 0 ? 'pct-positive' : 'pct-negative';
        const uSign = uPnl > 0 ? '+' : '';

        let periodLabel = '전체';
        if (selectedYear && selectedMonth) periodLabel = `${selectedYear}-${selectedMonth}`;
        else if (selectedYear) periodLabel = selectedYear;

        container.innerHTML = `
            <div class="earnings-card">
                <div class="earnings-card-label">실현수익 (${escapeHtml(periodLabel)})</div>
                <div class="earnings-card-value ${rClass}">${rSign}${escapeHtml(formatAmt(rPnl, mode))}</div>
                <div class="earnings-card-note">SELL 체결 기준 누적</div>
            </div>
            <div class="earnings-card">
                <div class="earnings-card-label">매도 건수 (${escapeHtml(periodLabel)})</div>
                <div class="earnings-card-value">${summary.sell_count}건</div>
                <div class="earnings-card-note">SELL 체결 횟수</div>
            </div>
            <div class="earnings-card">
                <div class="earnings-card-label">현재 평가손익</div>
                <div class="earnings-card-value ${uClass}">${uSign}${escapeHtml(formatAmt(uPnl, mode))}</div>
                <div class="earnings-card-note">현재 시점 기준 (과거 월 소급 불가)</div>
            </div>`;
    }

    function renderBarChart() {
        const container = document.getElementById('earnings-bar-chart');
        if (!container) return;

        // Bar chart only makes sense for a specific year or all-time with monthly breakdown
        const year = selectedYear;
        if (!year) {
            // Show all-years summary bar chart (one bar per year)
            renderYearBarChart(container);
            return;
        }
        renderMonthBarChart(container, year);
    }

    function renderMonthBarChart(container, year) {
        const months = EarningsModel.getYearMonthly(year);
        const MONTH_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

        const values = months.map(m => m.realized_pnl);

        const W = 560, H = 200;
        const PAD = { top: 24, right: 12, bottom: 36, left: 64 };
        const chartW = W - PAD.left - PAD.right;
        const chartH = H - PAD.top - PAD.bottom;
        const barW = Math.floor(chartW / 12) - 4;

        const mode = _currencyMode;
        const SUCCESS = '#10b981';
        const DANGER = '#ef4444';
        const MUTED = '#6b7280';

        const yMin = Math.min(0, ...values) * 1.15;
        const yMax = Math.max(0, ...values) * 1.15;
        const yRange = (yMax - yMin) || 1;

        function yPos(v) {
            return PAD.top + chartH - ((v - yMin) / yRange) * chartH;
        }

        const zeroY = yPos(0).toFixed(1);

        const compactLabel = (v) => {
            const abs = Math.abs(v);
            const sym = mode === 'domestic' ? '₩' : '$';
            if (abs >= 1_000_000_000) return sym + (v / 1_000_000_000).toFixed(1) + 'B';
            if (abs >= 1_000_000) return sym + (v / 1_000_000).toFixed(1) + 'M';
            if (abs >= 1_000) return sym + (v / 1_000).toFixed(1) + 'K';
            return sym + v.toFixed(0);
        };

        let barsHtml = '';
        let xLabelsHtml = '';

        months.forEach((m, i) => {
            const x = PAD.left + i * (chartW / 12) + (chartW / 12 - barW) / 2;
            const v = m.realized_pnl;
            const color = v >= 0 ? SUCCESS : DANGER;
            const barTop = Math.min(yPos(v), Number(zeroY));
            const barH = Math.abs(yPos(v) - Number(zeroY));

            const isSelected = selectedMonth === m.month;
            const opacity = (!selectedMonth || isSelected) ? '1' : '0.35';

            if (m.has_data) {
                barsHtml += `<rect x="${x.toFixed(1)}" y="${barTop.toFixed(1)}" width="${barW}" height="${Math.max(barH, 1).toFixed(1)}" fill="${color}" opacity="${opacity}" rx="2">
                    <title>${MONTH_SHORT[i]}: ${formatAmt(v, mode)}</title>
                </rect>`;
            }

            const labelX = (x + barW / 2).toFixed(1);
            const labelY = (PAD.top + chartH + 14).toFixed(1);
            const labelFill = m.has_data ? (mode === 'domestic' ? '#374151' : MUTED) : MUTED;
            xLabelsHtml += `<text x="${labelX}" y="${labelY}" text-anchor="middle" class="earnings-bar-axis" fill="${labelFill}">${MONTH_SHORT[i]}</text>`;
        });

        // y-axis ticks
        const tickValues = [yMin, (yMin + yMax) / 2, yMax].filter((v, i, a) => a.indexOf(v) === i);
        const yTicksHtml = tickValues.map(v => {
            const y = yPos(v).toFixed(1);
            return `<text x="${(PAD.left - 6).toFixed(1)}" y="${y}" text-anchor="end" dominant-baseline="middle" class="earnings-bar-axis">${compactLabel(v)}</text>`;
        }).join('');

        container.innerHTML = `
            <svg viewBox="0 0 ${W} ${H}" class="earnings-bar-svg" role="img" aria-label="${escapeHtml(year)} 월별 실현수익">
                <line x1="${PAD.left}" y1="${zeroY}" x2="${(PAD.left + chartW).toFixed(1)}" y2="${zeroY}" stroke="#d1d5db" stroke-width="1"/>
                ${barsHtml}
                ${yTicksHtml}
                ${xLabelsHtml}
            </svg>`;
    }

    function renderYearBarChart(container) {
        const years = EarningsModel.getAvailableYears();
        if (years.length === 0) {
            container.innerHTML = '<p class="empty-state">데이터 없음</p>';
            return;
        }

        const yearData = years.map(y => {
            const s = EarningsModel.getPeriodSummary(y, null);
            return { year: y, realized_pnl: s.realized_pnl };
        });

        const W = 560, H = 180;
        const PAD = { top: 24, right: 12, bottom: 36, left: 64 };
        const chartW = W - PAD.left - PAD.right;
        const chartH = H - PAD.top - PAD.bottom;
        const barW = Math.min(60, Math.floor(chartW / yearData.length) - 8);
        const mode = _currencyMode;
        const SUCCESS = '#10b981';
        const DANGER = '#ef4444';

        const values = yearData.map(d => d.realized_pnl);
        const yMin = Math.min(0, ...values) * 1.15;
        const yMax = Math.max(0, ...values) * 1.15 || 1;
        const yRange = (yMax - yMin) || 1;

        function yPos(v) {
            return PAD.top + chartH - ((v - yMin) / yRange) * chartH;
        }
        const zeroY = yPos(0).toFixed(1);

        const compactLabel = (v) => {
            const abs = Math.abs(v);
            const sym = mode === 'domestic' ? '₩' : '$';
            if (abs >= 1_000_000_000) return sym + (v / 1_000_000_000).toFixed(1) + 'B';
            if (abs >= 1_000_000) return sym + (v / 1_000_000).toFixed(1) + 'M';
            if (abs >= 1_000) return sym + (v / 1_000).toFixed(1) + 'K';
            return sym + v.toFixed(0);
        };

        let barsHtml = '';
        let xLabelsHtml = '';

        yearData.forEach((d, i) => {
            const x = PAD.left + i * (chartW / yearData.length) + (chartW / yearData.length - barW) / 2;
            const v = d.realized_pnl;
            const color = v >= 0 ? SUCCESS : DANGER;
            const barTop = Math.min(yPos(v), Number(zeroY));
            const barH = Math.abs(yPos(v) - Number(zeroY));
            barsHtml += `<rect x="${x.toFixed(1)}" y="${barTop.toFixed(1)}" width="${barW}" height="${Math.max(barH, 1).toFixed(1)}" fill="${color}" rx="2">
                <title>${escapeHtml(d.year)}: ${formatAmt(v, mode)}</title>
            </rect>`;
            const labelX = (x + barW / 2).toFixed(1);
            xLabelsHtml += `<text x="${labelX}" y="${(PAD.top + chartH + 14).toFixed(1)}" text-anchor="middle" class="earnings-bar-axis">${escapeHtml(d.year)}</text>`;
        });

        const tickValues = [yMin, (yMin + yMax) / 2, yMax].filter((v, i, a) => a.indexOf(v) === i);
        const yTicksHtml = tickValues.map(v => {
            const y = yPos(v).toFixed(1);
            return `<text x="${(PAD.left - 6).toFixed(1)}" y="${y}" text-anchor="end" dominant-baseline="middle" class="earnings-bar-axis">${compactLabel(v)}</text>`;
        }).join('');

        container.innerHTML = `
            <svg viewBox="0 0 ${W} ${H}" class="earnings-bar-svg" role="img" aria-label="연도별 실현수익">
                <line x1="${PAD.left}" y1="${zeroY}" x2="${(PAD.left + chartW).toFixed(1)}" y2="${zeroY}" stroke="#d1d5db" stroke-width="1"/>
                ${barsHtml}
                ${yTicksHtml}
                ${xLabelsHtml}
            </svg>`;
    }

    function renderTickerTable(container) {
        const el = document.getElementById('earnings-ticker-table');
        if (!el) return;

        const summary = EarningsModel.getPeriodSummary(selectedYear, selectedMonth);
        const breakdown = summary.ticker_breakdown;
        const mode = _currencyMode;

        if (breakdown.length === 0) {
            el.innerHTML = '<p class="empty-state">해당 기간에 매도 기록이 없습니다.</p>';
            return;
        }

        const rows = breakdown.map(td => {
            const pnl = td.pnl;
            const cls = pnl >= 0 ? 'pct-positive' : 'pct-negative';
            const sign = pnl > 0 ? '+' : '';
            const label = escapeHtml(formatTickerLabel(td.ticker, td.alias));
            return `<tr>
                <td><strong>${label}</strong></td>
                <td class="${cls}" style="text-align:right">${sign}${escapeHtml(formatAmt(pnl, mode))}</td>
                <td style="text-align:center;color:var(--text-muted)">${td.count}건</td>
            </tr>`;
        }).join('');

        el.innerHTML = `
            <div class="card" style="padding:0;overflow-x:auto">
                <table class="history-table">
                    <thead>
                        <tr>
                            <th>종목</th>
                            <th style="text-align:right">실현수익</th>
                            <th style="text-align:center">매도건수</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;
    }

    return { render };
})();
