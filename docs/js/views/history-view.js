// docs/js/views/history-view.js
window.HistoryView = (function () {
    'use strict';

    const { escapeHtml, formatTickerLabel } = window.FormatUtils;

    function renderFilters(tickers, onFilterChange) {
        const container = document.getElementById('history-filters');
        if (!container) return;

        const tickerOptions = ['<option value="">전체 종목</option>']
            .concat(tickers.map(({ ticker, alias }) =>
                `<option value="${escapeHtml(ticker)}">${escapeHtml(formatTickerLabel(ticker, alias))}</option>`
            ))
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
            onFilterChange('ticker', tickerSelect.value);
        });

        container.querySelectorAll('.filter-chip').forEach((btn) => {
            btn.addEventListener('click', () => {
                container.querySelectorAll('.filter-chip').forEach((b) => b.classList.remove('active'));
                btn.classList.add('active');
                onFilterChange('action', btn.dataset.action);
            });
        });
    }

    function buildRowHtml(row, formatCurrencyFn, currentMode) {
        function formatAmount(value) {
            if (formatCurrencyFn) return formatCurrencyFn(value, currentMode);
            return value.toFixed(2);
        }

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
                <td><strong>${escapeHtml(formatTickerLabel(row.ticker, row.alias))}</strong></td>
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

    function renderPage(newRows, isFirstPage, hasMore, formatCurrencyFn, currentMode) {
        const listEl = document.getElementById('history-list');
        const moreBtn = document.getElementById('load-more-btn');
        if (!listEl) return;

        if (isFirstPage) {
            if (newRows.length === 0) {
                listEl.innerHTML = '<div class="card" style="text-align:center;color:var(--text-muted)">거래 내역이 없습니다.</div>';
                if (moreBtn) moreBtn.style.display = 'none';
                return;
            }

            listEl.innerHTML = `
                <div class="card" style="padding:0;overflow-x:auto" tabindex="0" role="region" aria-label="거래 내역 테이블">
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
            tbody.insertAdjacentHTML('beforeend', newRows.map(row => buildRowHtml(row, formatCurrencyFn, currentMode)).join(''));
        }

        if (moreBtn) {
            moreBtn.style.display = hasMore ? '' : 'none';
        }
    }

    return {
        renderFilters,
        renderPage
    };
})();
