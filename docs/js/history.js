// MagicSplit Dashboard - history.js
window.MagicSplitHistory = (function () {
    'use strict';

    const PAGE_SIZE = 30;

    let allRows = [];
    let filteredRows = [];
    let visibleCount = 0;
    let currentMode = 'domestic';
    let formatCurrencyFn = null;
    let activeFilters = { ticker: '', action: '' };

    function buildRows(historyData) {
        const rows = [];
        if (!Array.isArray(historyData)) return rows;

        for (const tx of historyData) {
            const txDate = tx.date || '';
            const txReason = tx.reason || '';
            const executions = Array.isArray(tx.executions) ? tx.executions : [];
            for (const ex of executions) {
                const action = (ex.action || '').toUpperCase();
                const price = ex.price || 0;
                const buyPrice = ex.buy_price != null ? ex.buy_price : null;
                const realizedPnl = ex.realized_pnl != null ? ex.realized_pnl : null;
                const profitRate = (action === 'SELL' && buyPrice != null && buyPrice > 0 && realizedPnl != null && (ex.quantity || 0) > 0)
                    ? (realizedPnl / (buyPrice * ex.quantity)) * 100
                    : null;
                rows.push({
                    date: ex.date || txDate,
                    ticker: ex.ticker || '',
                    action,
                    level: ex.level != null ? ex.level : '',
                    quantity: ex.quantity || 0,
                    price,
                    fee: ex.fee || 0,
                    amount: (ex.quantity || 0) * price,
                    buyPrice,
                    realizedPnl,
                    profitRate,
                    txReason: txReason,
                });
            }
        }

        rows.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
        return rows;
    }

    function getUniqueTickers(rows) {
        const set = new Set(rows.map((r) => r.ticker).filter(Boolean));
        return Array.from(set).sort();
    }

    function renderFilters(tickers) {
        const container = document.getElementById('history-filters');
        if (!container) return;

        const tickerOptions = ['<option value="">전체 종목</option>']
            .concat(tickers.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`))
            .join('');

        container.innerHTML = `
            <select id="history-ticker-filter" class="filter-select">
                ${tickerOptions}
            </select>
            <div class="filter-chips" role="group" aria-label="액션 필터">
                <button class="filter-chip active" data-action="">전체</button>
                <button class="filter-chip" data-action="BUY">BUY</button>
                <button class="filter-chip" data-action="SELL">SELL</button>
            </div>
        `;

        const tickerSelect = container.querySelector('#history-ticker-filter');
        tickerSelect.addEventListener('change', () => {
            activeFilters.ticker = tickerSelect.value;
            applyFilters();
        });

        container.querySelectorAll('.filter-chip').forEach((btn) => {
            btn.addEventListener('click', () => {
                container.querySelectorAll('.filter-chip').forEach((b) => b.classList.remove('active'));
                btn.classList.add('active');
                activeFilters.action = btn.dataset.action;
                applyFilters();
            });
        });
    }

    function applyFilters() {
        filteredRows = allRows.filter((row) => {
            if (activeFilters.ticker && row.ticker !== activeFilters.ticker) return false;
            if (activeFilters.action && row.action !== activeFilters.action) return false;
            return true;
        });
        visibleCount = 0;
        renderPage();
    }

    function formatAmount(value) {
        if (formatCurrencyFn) return formatCurrencyFn(value, currentMode);
        return value.toFixed(2);
    }

    function buildRowHtml(row) {
        const actionClass = row.action === 'SELL' ? 'sell' : 'buy';
        const lvDisplay = row.level !== ''
            ? `<span class="level-badge" data-level="${Math.min(Number(row.level), 5)}">Lv${escapeHtml(row.level)}</span>`
            : '-';

        let buyPriceCell = '-';
        let pnlCell = '-';
        let rateCell = '-';
        if (row.action === 'SELL' && row.buyPrice != null) {
            buyPriceCell = formatAmount(row.buyPrice);
        }
        if (row.action === 'SELL' && row.realizedPnl != null) {
            const sign = row.realizedPnl > 0 ? '+' : '';
            const cls = row.realizedPnl > 0 ? 'pct-positive' : (row.realizedPnl < 0 ? 'pct-negative' : '');
            pnlCell = `<span class="${cls}">${sign}${formatAmount(row.realizedPnl)}</span>`;
        }
        if (row.action === 'SELL' && row.profitRate != null) {
            const sign = row.profitRate > 0 ? '+' : '';
            const cls = row.profitRate > 0 ? 'pct-positive' : (row.profitRate < 0 ? 'pct-negative' : '');
            rateCell = `<span class="${cls}">${sign}${row.profitRate.toFixed(2)}%</span>`;
        }

        return `
            <tr class="history-row ${actionClass}" title="${escapeHtml(row.txReason)}">
                <td>${escapeHtml(row.date)}</td>
                <td><strong>${escapeHtml(row.ticker)}</strong></td>
                <td><span class="history-action ${actionClass}">${escapeHtml(row.action)}</span></td>
                <td>${lvDisplay}</td>
                <td>${escapeHtml(row.quantity)}</td>
                <td>${formatAmount(row.price)}</td>
                <td>${buyPriceCell}</td>
                <td>${formatAmount(row.fee)}</td>
                <td>${formatAmount(row.amount)}</td>
                <td>${pnlCell}</td>
                <td>${rateCell}</td>
            </tr>`;
    }

    function renderPage() {
        const listEl = document.getElementById('history-list');
        const moreBtn = document.getElementById('load-more-btn');
        if (!listEl) return;

        if (filteredRows.length === 0) {
            listEl.innerHTML = '<div class="card" style="text-align:center;color:var(--text-muted)">거래 내역이 없습니다.</div>';
            if (moreBtn) moreBtn.style.display = 'none';
            return;
        }

        const newRows = filteredRows.slice(visibleCount, visibleCount + PAGE_SIZE);
        visibleCount += newRows.length;

        if (visibleCount <= PAGE_SIZE) {
            // First render: build full table structure
            listEl.innerHTML = `
                <div class="card" style="padding:0;overflow-x:auto">
                    <table class="history-table">
                        <thead>
                            <tr>
                                <th>날짜</th>
                                <th>종목</th>
                                <th>구분</th>
                                <th>차수</th>
                                <th>수량</th>
                                <th>가격</th>
                                <th>매수가</th>
                                <th>수수료</th>
                                <th>금액</th>
                                <th>수익금</th>
                                <th>수익률</th>
                            </tr>
                        </thead>
                        <tbody id="history-tbody"></tbody>
                    </table>
                </div>`;
        }

        const tbody = document.getElementById('history-tbody');
        if (tbody) {
            tbody.insertAdjacentHTML('beforeend', newRows.map(buildRowHtml).join(''));
        }

        if (moreBtn) {
            moreBtn.style.display = visibleCount < filteredRows.length ? '' : 'none';
        }
    }

    function loadMore() {
        renderPage();
    }

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function renderHistory(historyData, mode, formatFn) {
        currentMode = mode || 'domestic';
        formatCurrencyFn = formatFn || null;
        activeFilters = { ticker: '', action: '' };

        allRows = buildRows(historyData);
        filteredRows = allRows.slice();
        visibleCount = 0;

        const tickers = getUniqueTickers(allRows);
        renderFilters(tickers);
        renderPage();

        const moreBtn = document.getElementById('load-more-btn');
        if (moreBtn) {
            moreBtn.onclick = loadMore;
        }
    }

    return { renderHistory };
})();
