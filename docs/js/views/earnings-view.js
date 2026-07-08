// docs/js/views/earnings-view.js
window.EarningsView = (function () {
    'use strict';

    const { escapeHtml, formatTickerLabel } = window.FormatUtils;

    let selectedYear = undefined;  // undefined=not init, null=all, "2026"=specific year
    let selectedMonth = null;      // "05" or null (all)
    let _currencyMode = 'domestic';

    // Level analysis state
    let levelMetric = 'total_pnl'; // 'total_pnl' | 'avg_profit_rate'

    // Ticker summary state
    let tickerSortKey = 'total_pnl';
    let tickerSortAsc = false;
    let tickerStatusFilter = '';   // '' | '보유중' | '청산완료'
    let tickerSearch = '';
    let selectedTicker = null;

    function formatAmt(value, mode) {
        const isDomestic = (mode || 'domestic') === 'domestic';
        return new Intl.NumberFormat(isDomestic ? 'ko-KR' : 'en-US', {
            style: 'currency',
            currency: isDomestic ? 'KRW' : 'USD',
            minimumFractionDigits: isDomestic ? 0 : 2,
            maximumFractionDigits: isDomestic ? 0 : 2,
        }).format(Number(value));
    }

    // 해외(USD) 모드에서 저장 시점 환율 기준 원화 환산액 + 환율을 보여주는 보조 문자열 (없으면 '')
    function krwSubLabel(usdValue, mode) {
        if ((mode || 'domestic') !== 'overseas') return '';
        const rate = Number(EarningsModel.getExchangeRate());
        if (!rate || isNaN(rate)) return '';
        const numVal = Number(usdValue);
        if (isNaN(numVal)) return '';
        const rateLabel = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(rate);
        return `<div class="earnings-card-subvalue">약 ${escapeHtml(formatAmt(numVal * rate, 'domestic'))} (환율 ₩${escapeHtml(rateLabel)})</div>`;
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
        renderPeriodTickerTable();
        renderTickerSummarySection();
        renderLevelAnalysisSection();
    }

    // ── Period filters ───────────────────────────────────────────────────────

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
            return `<button class="filter-chip${isActive ? ' active' : ''}${hasData ? '' : ' dim'}" data-month="${m}" ${hasData ? '' : 'disabled'}>${MONTH_LABELS[i]}</button>`;
        }).join('');

        container.innerHTML = `<span class="earnings-filter-label">월</span>${allBtn}${monthBtns}`;

        container.querySelectorAll('.filter-chip:not([disabled])').forEach(btn => {
            btn.addEventListener('click', () => {
                selectedMonth = btn.dataset.month || null;
                render(_currencyMode);
            });
        });
    }

    // ── Summary cards ────────────────────────────────────────────────────────

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
                ${krwSubLabel(rPnl, mode)}
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
                ${krwSubLabel(uPnl, mode)}
                <div class="earnings-card-note">현재 시점 기준 (과거 월 소급 불가)</div>
            </div>`;
    }

    // ── Bar charts ───────────────────────────────────────────────────────────

    function renderBarChart() {
        const container = document.getElementById('earnings-bar-chart');
        if (!container) return;
        if (!selectedYear) { renderYearBarChart(container); return; }
        renderMonthBarChart(container, selectedYear);
    }

    function buildBarSvg(labels, values, W, H, PAD, mode, highlightIdx) {
        const chartW = W - PAD.left - PAD.right;
        const chartH = H - PAD.top - PAD.bottom;
        const barW = Math.max(4, Math.floor(chartW / labels.length) - 4);
        const SUCCESS = '#10b981', DANGER = '#ef4444', MUTED = '#6b7280';

        const yMin = Math.min(0, ...values) * 1.15;
        const yMax = Math.max(0, ...values) * 1.15;
        const yRange = (yMax - yMin) || 1;

        const yPos = v => PAD.top + chartH - ((v - yMin) / yRange) * chartH;
        const zeroY = yPos(0).toFixed(1);

        const sym = mode === 'domestic' ? '₩' : '$';
        const compactLabel = v => {
            const abs = Math.abs(v);
            const sign = v < 0 ? '-' : '';
            if (abs >= 1e9) return sign + sym + (abs / 1e9).toFixed(1) + 'B';
            if (abs >= 1e6) return sign + sym + (abs / 1e6).toFixed(1) + 'M';
            if (abs >= 1e3) return sign + sym + (abs / 1e3).toFixed(1) + 'K';
            return sign + sym + abs.toFixed(0);
        };

        let barsHtml = '', xLabelsHtml = '';
        values.forEach((v, i) => {
            const x = PAD.left + i * (chartW / labels.length) + (chartW / labels.length - barW) / 2;
            const color = v >= 0 ? SUCCESS : DANGER;
            const barTop = Math.min(yPos(v), Number(zeroY));
            const barH = Math.abs(yPos(v) - Number(zeroY));
            const opacity = (highlightIdx == null || highlightIdx === i) ? '1' : '0.35';
            if (v !== 0) {
                barsHtml += `<rect x="${x.toFixed(1)}" y="${barTop.toFixed(1)}" width="${barW}" height="${Math.max(barH, 1).toFixed(1)}" fill="${color}" opacity="${opacity}" rx="2"><title>${escapeHtml(labels[i])}: ${formatAmt(v, mode)}</title></rect>`;
            }
            const labelFill = v !== 0 ? '#374151' : MUTED;
            xLabelsHtml += `<text x="${(x + barW / 2).toFixed(1)}" y="${(PAD.top + chartH + 14).toFixed(1)}" text-anchor="middle" class="earnings-bar-axis" fill="${labelFill}">${escapeHtml(labels[i])}</text>`;
        });

        const tickValues = [yMin, (yMin + yMax) / 2, yMax].filter((v, i, a) => a.indexOf(v) === i);
        const yTicksHtml = tickValues.map(v =>
            `<text x="${(PAD.left - 6).toFixed(1)}" y="${yPos(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" class="earnings-bar-axis">${compactLabel(v)}</text>`
        ).join('');

        return `<line x1="${PAD.left}" y1="${zeroY}" x2="${(PAD.left + chartW).toFixed(1)}" y2="${zeroY}" stroke="#d1d5db" stroke-width="1"/>${barsHtml}${yTicksHtml}${xLabelsHtml}`;
    }

    function renderMonthBarChart(container, year) {
        const months = EarningsModel.getYearMonthly(year);
        const MONTH_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const labels = MONTH_SHORT;
        const values = months.map(m => m.realized_pnl);
        const highlightIdx = selectedMonth ? (parseInt(selectedMonth, 10) - 1) : null;
        const PAD = { top: 24, right: 12, bottom: 36, left: 64 };
        const inner = buildBarSvg(labels, values, 560, 200, PAD, _currencyMode, highlightIdx);
        container.innerHTML = `<svg viewBox="0 0 560 200" class="earnings-bar-svg" role="img" aria-label="${escapeHtml(year)} 월별 실현수익">${inner}</svg>`;
    }

    function renderYearBarChart(container) {
        const years = EarningsModel.getAvailableYears();
        if (years.length === 0) { container.innerHTML = '<p class="empty-state">데이터 없음</p>'; return; }
        const values = years.map(y => EarningsModel.getPeriodSummary(y, null).realized_pnl);
        const PAD = { top: 24, right: 12, bottom: 36, left: 64 };
        const inner = buildBarSvg(years, values, 560, 180, PAD, _currencyMode, null);
        container.innerHTML = `<svg viewBox="0 0 560 180" class="earnings-bar-svg" role="img" aria-label="연도별 실현수익">${inner}</svg>`;
    }

    // ── Period ticker breakdown (existing, period-filtered) ──────────────────

    function renderPeriodTickerTable() {
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
                    <thead><tr>
                        <th>종목</th>
                        <th style="text-align:right">실현수익</th>
                        <th style="text-align:center">매도건수</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;
    }

    // ── Ticker comprehensive summary (all-time, from status.json) ────────────

    function renderTickerSummarySection() {
        const section = document.getElementById('earnings-ticker-summary-section');
        if (!section) return;

        const summaries = EarningsModel.getTickerSummaries();
        const mode = _currencyMode;

        // search + status filter controls
        section.innerHTML = `
            <div class="ticker-summary-controls">
                <input id="ticker-summary-search" class="ticker-search-input" type="text" placeholder="종목명/코드 검색..." value="${escapeHtml(tickerSearch)}">
                <div class="filter-chips" role="group">
                    <button class="filter-chip${tickerStatusFilter === '' ? ' active' : ''}" data-status="">전체</button>
                    <button class="filter-chip${tickerStatusFilter === '보유중' ? ' active' : ''}" data-status="보유중">보유중</button>
                    <button class="filter-chip${tickerStatusFilter === '청산완료' ? ' active' : ''}" data-status="청산완료">청산완료</button>
                </div>
            </div>
            <div id="ticker-summary-table-wrap"></div>
            <div id="ticker-detail-panel" class="ticker-detail-panel" style="display:none"></div>`;

        // search input event
        const searchInput = section.querySelector('#ticker-summary-search');
        searchInput.addEventListener('input', () => {
            tickerSearch = searchInput.value;
            renderTickerSummaryTable(summaries, mode);
        });

        // status filter events
        section.querySelectorAll('.filter-chips .filter-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                tickerStatusFilter = btn.dataset.status;
                section.querySelectorAll('.filter-chips .filter-chip').forEach(b => b.classList.toggle('active', b.dataset.status === tickerStatusFilter));
                renderTickerSummaryTable(summaries, mode);
            });
        });

        renderTickerSummaryTable(summaries, mode);

        if (selectedTicker) {
            renderTickerDetailPanel(selectedTicker, mode);
        }
    }

    function getFilteredSummaries(summaries) {
        return summaries
            .filter(s => {
                if (tickerStatusFilter && s.status !== tickerStatusFilter) return false;
                if (tickerSearch) {
                    const q = tickerSearch.toLowerCase();
                    if (!s.ticker.toLowerCase().includes(q) && !s.alias.toLowerCase().includes(q)) return false;
                }
                return true;
            })
            .sort((a, b) => {
                const va = a[tickerSortKey];
                const vb = b[tickerSortKey];
                const dir = tickerSortAsc ? 1 : -1;
                if (va == null && vb == null) return 0;
                if (va == null) return 1;
                if (vb == null) return -1;
                return va < vb ? -dir : va > vb ? dir : 0;
            });
    }

    function renderTickerSummaryTable(summaries, mode) {
        const wrap = document.getElementById('ticker-summary-table-wrap');
        if (!wrap) return;

        const filtered = getFilteredSummaries(summaries);

        if (filtered.length === 0) {
            wrap.innerHTML = '<p class="empty-state">해당 조건의 종목이 없습니다.</p>';
            return;
        }

        const COLS = [
            { key: 'alias',         label: '종목',     align: 'left'  },
            { key: 'total_pnl',     label: '총손익',    align: 'right' },
            { key: 'realized_pnl',  label: '실현손익',  align: 'right' },
            { key: 'unrealized_pnl',label: '평가손익',  align: 'right' },
            { key: 'sell_count',    label: '매도건수',  align: 'center'},
            { key: 'win_rate',      label: '승률',      align: 'center'},
            { key: 'status',        label: '상태',      align: 'center'},
        ];

        const headerCells = COLS.map(c => {
            const isActive = tickerSortKey === c.key;
            const arrow = isActive ? (tickerSortAsc ? ' ▲' : ' ▼') : '';
            return `<th class="ts-th${isActive ? ' ts-th-active' : ''}" data-sort-key="${c.key}" style="text-align:${c.align};cursor:pointer">${c.label}${arrow}</th>`;
        }).join('');

        const rows = filtered.map(s => {
            const isSelected = selectedTicker === s.ticker;
            const totalCls = s.total_pnl >= 0 ? 'pct-positive' : 'pct-negative';
            const totalSign = s.total_pnl > 0 ? '+' : '';
            const realCls = s.realized_pnl >= 0 ? 'pct-positive' : 'pct-negative';
            const realSign = s.realized_pnl > 0 ? '+' : '';
            const unrCls = s.unrealized_pnl >= 0 ? 'pct-positive' : 'pct-negative';
            const unrSign = s.unrealized_pnl > 0 ? '+' : '';
            const winRateStr = s.win_rate != null ? s.win_rate.toFixed(0) + '%' : '-';
            const statusBadge = s.status === '보유중'
                ? `<span class="ts-status-badge ts-status-held">보유중</span>`
                : `<span class="ts-status-badge ts-status-closed">청산</span>`;
            const label = escapeHtml(formatTickerLabel(s.ticker, s.alias));
            return `<tr class="ts-row${isSelected ? ' ts-row-selected' : ''}" data-ticker="${escapeHtml(s.ticker)}" style="cursor:pointer">
                <td><strong>${label}</strong></td>
                <td class="${totalCls}" style="text-align:right">${totalSign}${escapeHtml(formatAmt(s.total_pnl, mode))}</td>
                <td class="${realCls}" style="text-align:right">${realSign}${escapeHtml(formatAmt(s.realized_pnl, mode))}</td>
                <td class="${s.unrealized_pnl !== 0 ? unrCls : ''}" style="text-align:right">${s.unrealized_pnl !== 0 ? unrSign + escapeHtml(formatAmt(s.unrealized_pnl, mode)) : '-'}</td>
                <td style="text-align:center;color:var(--text-muted)">${s.sell_count}건</td>
                <td style="text-align:center">${winRateStr}</td>
                <td style="text-align:center">${statusBadge}</td>
            </tr>`;
        }).join('');

        wrap.innerHTML = `
            <div class="card" style="padding:0;overflow-x:auto">
                <table class="history-table ts-table">
                    <thead><tr>${headerCells}</tr></thead>
                    <tbody>${rows}</tbody>
                </table>
                <div class="ts-note">* 전체 기간 기준 (상단 연도/월 필터 미적용)</div>
            </div>`;

        // sort header click
        wrap.querySelectorAll('.ts-th').forEach(th => {
            th.addEventListener('click', () => {
                const key = th.dataset.sortKey;
                if (tickerSortKey === key) {
                    tickerSortAsc = !tickerSortAsc;
                } else {
                    tickerSortKey = key;
                    tickerSortAsc = key === 'alias';
                }
                renderTickerSummaryTable(summaries, mode);
            });
        });

        // row click -> detail panel
        wrap.querySelectorAll('.ts-row').forEach(row => {
            row.addEventListener('click', () => {
                const ticker = row.dataset.ticker;
                if (selectedTicker === ticker) {
                    selectedTicker = null;
                    const panel = document.getElementById('ticker-detail-panel');
                    if (panel) panel.style.display = 'none';
                    row.classList.remove('ts-row-selected');
                } else {
                    selectedTicker = ticker;
                    wrap.querySelectorAll('.ts-row').forEach(r => r.classList.toggle('ts-row-selected', r.dataset.ticker === ticker));
                    renderTickerDetailPanel(ticker, mode, true);
                }
            });
        });
    }

    function renderTickerDetailPanel(ticker, mode, shouldScroll = false) {
        const panel = document.getElementById('ticker-detail-panel');
        if (!panel) return;

        const detail = EarningsModel.getTickerDetail(ticker);
        panel.style.display = '';

        // mini monthly bar chart for this ticker
        let chartHtml = '';
        if (detail.monthly.length > 0) {
            const labels = detail.monthly.map(m => m.yearMonth.slice(2)); // "26-05"
            const values = detail.monthly.map(m => m.pnl);
            const PAD = { top: 16, right: 8, bottom: 28, left: 56 };
            const inner = buildBarSvg(labels, values, 480, 140, PAD, mode, null);
            chartHtml = `
                <div class="ticker-detail-chart">
                    <div class="ticker-detail-section-title">월별 실현수익</div>
                    <svg viewBox="0 0 480 140" class="earnings-bar-svg" role="img" aria-label="${escapeHtml(detail.alias)} 월별 실현수익">${inner}</svg>
                </div>`;
        }

        // lots (current holdings)
        let lotsHtml = '';
        if (detail.lots.length > 0) {
            const lotRows = detail.lots.map(lot => {
                const pct = lot.pct_change != null ? Number(lot.pct_change) : null;
                const pctStr = pct != null ? `<span class="${pct >= 0 ? 'pct-positive' : 'pct-negative'}">${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%</span>` : '-';
                return `<tr>
                    <td>${escapeHtml(lot.buy_date || '')}</td>
                    <td style="text-align:center">${lot.level != null ? `Lv${lot.level}` : '-'}</td>
                    <td style="text-align:right">${lot.quantity}주 @${escapeHtml(formatAmt(lot.buy_price, mode))}</td>
                    <td style="text-align:right">${pctStr}</td>
                </tr>`;
            }).join('');
            lotsHtml = `
                <div class="ticker-detail-section-title" style="margin-top:12px">현재 보유 Lot</div>
                <div style="overflow-x:auto">
                    <table class="history-table">
                        <thead><tr><th>매수일</th><th style="text-align:center">차수</th><th style="text-align:right">수량/단가</th><th style="text-align:right">수익률</th></tr></thead>
                        <tbody>${lotRows}</tbody>
                    </table>
                </div>`;
        }

        // trade history
        let tradesHtml = '';
        if (detail.trades.length > 0) {
            const tradeRows = detail.trades.map(t => {
                const pnlCls = t.realized_pnl >= 0 ? 'pct-positive' : 'pct-negative';
                const pnlSign = t.realized_pnl > 0 ? '+' : '';
                const rateStr = t.profit_rate != null
                    ? `<span class="${t.profit_rate >= 0 ? 'pct-positive' : 'pct-negative'}">${t.profit_rate >= 0 ? '+' : ''}${t.profit_rate.toFixed(2)}%</span>`
                    : '-';
                const lvDisplay = t.level !== '' ? `Lv${t.level}` : '-';
                return `<tr>
                    <td>${escapeHtml(t.date)}</td>
                    <td style="text-align:center">${lvDisplay}</td>
                    <td style="text-align:right">${t.qty}주 @${escapeHtml(formatAmt(t.sell_price, mode))}</td>
                    <td style="text-align:right">${t.buy_price != null ? escapeHtml(formatAmt(t.buy_price, mode)) : '-'}</td>
                    <td class="${pnlCls}" style="text-align:right">${pnlSign}${escapeHtml(formatAmt(t.realized_pnl, mode))}</td>
                    <td style="text-align:right">${rateStr}</td>
                </tr>`;
            }).join('');
            tradesHtml = `
                <div class="ticker-detail-section-title" style="margin-top:12px">매도 내역</div>
                <div style="overflow-x:auto">
                    <table class="history-table">
                        <thead><tr><th>날짜</th><th style="text-align:center">차수</th><th style="text-align:right">수량/매도가</th><th style="text-align:right">매수가</th><th style="text-align:right">수익금</th><th style="text-align:right">수익률</th></tr></thead>
                        <tbody>${tradeRows}</tbody>
                    </table>
                </div>`;
        } else {
            tradesHtml = '<p class="empty-state" style="margin-top:8px">매도 내역 없음</p>';
        }

        panel.innerHTML = `
            <div class="ticker-detail-header">
                <span class="ticker-detail-title">${escapeHtml(formatTickerLabel(ticker, detail.alias))}</span>
                <button class="ticker-detail-close" id="ticker-detail-close-btn">✕ 닫기</button>
            </div>
            ${chartHtml}
            ${lotsHtml}
            ${tradesHtml}`;

        panel.querySelector('#ticker-detail-close-btn').addEventListener('click', () => {
            selectedTicker = null;
            panel.style.display = 'none';
            const wrap = document.getElementById('ticker-summary-table-wrap');
            if (wrap) wrap.querySelectorAll('.ts-row').forEach(r => r.classList.remove('ts-row-selected'));
        });

        if (shouldScroll) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // ── Level analysis section ───────────────────────────────────────────────

    function renderLevelAnalysisSection() {
        const section = document.getElementById('earnings-level-section');
        if (!section) return;

        const stats = EarningsModel.getLevelStats(selectedYear, selectedMonth);
        const mode = _currencyMode;

        if (stats.length === 0) {
            section.innerHTML = '<p class="empty-state">해당 기간에 차수별 매도 데이터가 없습니다.</p>';
            return;
        }

        // metric toggle
        const toggleHtml = `
            <div class="earnings-filter-bar" style="margin-bottom:14px">
                <span class="earnings-filter-label">지표</span>
                <button class="filter-chip${levelMetric === 'total_pnl' ? ' active' : ''}" data-metric="total_pnl">총 실현수익</button>
                <button class="filter-chip${levelMetric === 'avg_profit_rate' ? ' active' : ''}" data-metric="avg_profit_rate">평균 수익률</button>
            </div>`;

        section.innerHTML = toggleHtml +
            '<div id="level-bar-chart"></div>' +
            '<div id="level-stats-table" style="margin-top:16px"></div>';

        section.querySelectorAll('[data-metric]').forEach(btn => {
            btn.addEventListener('click', () => {
                levelMetric = btn.dataset.metric;
                renderLevelAnalysisSection();
            });
        });

        renderLevelBarChart(stats, mode);
        renderLevelStatsTable(stats, mode);
    }

    function renderLevelBarChart(stats, mode) {
        const container = document.getElementById('level-bar-chart');
        if (!container) return;

        const labels = stats.map(s => `Lv${s.level}`);
        const values = levelMetric === 'total_pnl'
            ? stats.map(s => s.total_pnl)
            : stats.map(s => s.avg_profit_rate != null ? s.avg_profit_rate : 0);

        const PAD = { top: 24, right: 12, bottom: 36, left: levelMetric === 'total_pnl' ? 64 : 44 };
        const W = 560, H = 180;
        const chartW = W - PAD.left - PAD.right;
        const chartH = H - PAD.top - PAD.bottom;
        const barW = Math.max(8, Math.floor(chartW / labels.length) - 6);

        const SUCCESS = '#10b981', DANGER = '#ef4444';
        const yMin = Math.min(0, ...values) * 1.15;
        const yMax = Math.max(0, ...values) * 1.15;
        const yRange = (yMax - yMin) || 1;

        const yPos = v => PAD.top + chartH - ((v - yMin) / yRange) * chartH;
        const zeroY = yPos(0).toFixed(1);

        const sym = mode === 'domestic' ? '₩' : '$';
        const fmtTick = v => {
            if (levelMetric === 'avg_profit_rate') return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
            const abs = Math.abs(v);
            const sign = v < 0 ? '-' : '';
            if (abs >= 1e9) return sign + sym + (abs / 1e9).toFixed(1) + 'B';
            if (abs >= 1e6) return sign + sym + (abs / 1e6).toFixed(1) + 'M';
            if (abs >= 1e3) return sign + sym + (abs / 1e3).toFixed(1) + 'K';
            return sign + sym + abs.toFixed(0);
        };

        const fmtTooltip = (s) => levelMetric === 'total_pnl'
            ? `총 실현수익: ${(s.total_pnl >= 0 ? '+' : '') + fmtTick(s.total_pnl)}`
            : `평균 수익률: ${s.avg_profit_rate != null ? (s.avg_profit_rate >= 0 ? '+' : '') + s.avg_profit_rate.toFixed(2) + '%' : '-'}`;

        let barsHtml = '', xLabelsHtml = '';
        stats.forEach((s, i) => {
            const v = values[i];
            const x = PAD.left + i * (chartW / labels.length) + (chartW / labels.length - barW) / 2;
            const color = v >= 0 ? SUCCESS : DANGER;
            const barTop = Math.min(yPos(v), Number(zeroY));
            const barH = Math.abs(yPos(v) - Number(zeroY));
            barsHtml += `<rect x="${x.toFixed(1)}" y="${barTop.toFixed(1)}" width="${barW}" height="${Math.max(barH, 1).toFixed(1)}" fill="${color}" rx="2"><title>Lv${s.level} | ${fmtTooltip(s)} | ${s.sell_count}건 승률${s.win_rate != null ? s.win_rate.toFixed(0) + '%' : '-'}</title></rect>`;
            xLabelsHtml += `<text x="${(x + barW / 2).toFixed(1)}" y="${(PAD.top + chartH + 14).toFixed(1)}" text-anchor="middle" class="earnings-bar-axis">Lv${escapeHtml(String(s.level))}</text>`;
        });

        const tickValues = [yMin, (yMin + yMax) / 2, yMax].filter((v, i, a) => a.indexOf(v) === i);
        const yTicksHtml = tickValues.map(v =>
            `<text x="${(PAD.left - 5).toFixed(1)}" y="${yPos(v).toFixed(1)}" text-anchor="end" dominant-baseline="middle" class="earnings-bar-axis">${escapeHtml(fmtTick(v))}</text>`
        ).join('');

        const ariaLabel = levelMetric === 'total_pnl' ? '차수별 총 실현수익' : '차수별 평균 수익률';
        container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="earnings-bar-svg" role="img" aria-label="${ariaLabel}">
            <line x1="${PAD.left}" y1="${zeroY}" x2="${(PAD.left + chartW).toFixed(1)}" y2="${zeroY}" stroke="#d1d5db" stroke-width="1"/>
            ${barsHtml}${yTicksHtml}${xLabelsHtml}
        </svg>`;
    }

    function renderLevelStatsTable(stats, mode) {
        const el = document.getElementById('level-stats-table');
        if (!el) return;

        const isDomestic = mode === 'domestic';

        const rows = stats.map(s => {
            const pnlCls = s.total_pnl >= 0 ? 'pct-positive' : 'pct-negative';
            const pnlSign = s.total_pnl > 0 ? '+' : '';
            const pnlStr = new Intl.NumberFormat(isDomestic ? 'ko-KR' : 'en-US', {
                style: 'currency', currency: isDomestic ? 'KRW' : 'USD',
                minimumFractionDigits: isDomestic ? 0 : 2, maximumFractionDigits: isDomestic ? 0 : 2
            }).format(s.total_pnl);

            const rateCls = s.avg_profit_rate != null ? (s.avg_profit_rate >= 0 ? 'pct-positive' : 'pct-negative') : '';
            const rateStr = s.avg_profit_rate != null
                ? `<span class="${rateCls}">${s.avg_profit_rate >= 0 ? '+' : ''}${s.avg_profit_rate.toFixed(2)}%</span>` : '-';

            // win rate dot indicator (up to 5 dots)
            const winDots = s.win_rate != null ? buildWinDots(s.win_rate) : '-';
            const winRateStr = s.win_rate != null ? s.win_rate.toFixed(0) + '%' : '-';

            return `<tr>
                <td style="text-align:center"><span class="level-badge" data-level="${Math.min(s.level, 5)}">Lv${s.level}</span></td>
                <td style="text-align:center;color:var(--text-muted)">${s.sell_count}건</td>
                <td style="text-align:center"><span class="win-dots">${winDots}</span> ${winRateStr}</td>
                <td style="text-align:right">${rateStr}</td>
                <td class="${pnlCls}" style="text-align:right">${pnlSign}${escapeHtml(pnlStr)}</td>
            </tr>`;
        }).join('');

        el.innerHTML = `
            <div class="card" style="padding:0;overflow-x:auto">
                <table class="history-table">
                    <thead><tr>
                        <th style="text-align:center">차수</th>
                        <th style="text-align:center">매도건수</th>
                        <th style="text-align:center">승률</th>
                        <th style="text-align:right">평균 수익률</th>
                        <th style="text-align:right">총 실현수익</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;
    }

    function buildWinDots(winRate) {
        // 5 dots: filled proportional to win rate
        const filled = Math.round(winRate / 20); // 0-5
        return Array.from({ length: 5 }, (_, i) =>
            `<span class="win-dot${i < filled ? ' filled' : ''}"></span>`
        ).join('');
    }

    return { render };
})();
