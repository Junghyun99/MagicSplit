// docs/js/views/config-view.js
window.ConfigView = (function () {
    'use strict';

    function showBanner(msg, type = 'info') {
        const banner = document.getElementById('message-banner');
        banner.className = `reason-banner ${type}`;
        banner.textContent = msg;
        banner.style.display = '';
    }

    function renderGlobalConfig(globalConfig) {
        const globalNotif = document.getElementById('global-notification');
        globalNotif.checked = globalConfig?.notification_enabled !== false;

        document.getElementById('global-max-exposure').value = globalConfig?.max_exposure_pct !== undefined ? globalConfig.max_exposure_pct : '';
        document.getElementById('global-trailing-drop').value = globalConfig?.trailing_drop_pct !== undefined ? globalConfig.trailing_drop_pct : '';
    }

    function renderTickerList(stocks, activeIndex, onSelect) {
        const list = document.getElementById('ticker-list');
        list.innerHTML = '';
        stocks.forEach((stock, idx) => {
            const li = document.createElement('li');
            li.textContent = stock.ticker || '(New Ticker)';
            if (idx === activeIndex) li.className = 'active-ticker';
            li.onclick = () => onSelect(idx);
            list.appendChild(li);
        });
    }

    function showTickerEditor(stock, isPresetMode) {
        document.getElementById('ticker-editor-pane').style.display = '';
        document.getElementById('current-ticker-title').textContent = stock.ticker ? (isPresetMode ? `${stock.ticker} 프리셋` : `${stock.ticker} 설정`) : (isPresetMode ? '새 프리셋' : '새 종목 설정');

        document.getElementById('edit-ticker-label').textContent = isPresetMode ? 'Preset Name' : 'Ticker';
        document.getElementById('group-exchange').style.display = isPresetMode ? 'none' : '';
        document.getElementById('group-market').style.display = isPresetMode ? 'none' : '';
        document.getElementById('group-preset').style.display = isPresetMode ? 'none' : '';
        document.getElementById('group-enabled').style.display = isPresetMode ? 'none' : 'flex';

        document.getElementById('edit-ticker').value = stock.ticker || '';
        document.getElementById('edit-exchange').value = stock.exchange || '';
        document.getElementById('edit-market').value = stock.market_type || 'overseas';
        document.getElementById('edit-preset').value = stock.preset || '';
        document.getElementById('edit-max-lots').value = stock.max_lots !== undefined ? stock.max_lots : 10;
        document.getElementById('edit-reentry').value = stock.reentry_guard_pct !== undefined ? stock.reentry_guard_pct : '';
        document.getElementById('edit-enabled').checked = stock.enabled !== false;

        document.getElementById('edit-buy-pct').value = stock.buy_threshold_pct !== undefined ? stock.buy_threshold_pct : '';
        document.getElementById('edit-sell-pct').value = stock.sell_threshold_pct !== undefined ? stock.sell_threshold_pct : '';
        document.getElementById('edit-buy-amt').value = stock.buy_amount !== undefined ? stock.buy_amount : '';
        document.getElementById('edit-max-exposure').value = stock.max_exposure_pct !== undefined ? stock.max_exposure_pct : '';
        document.getElementById('edit-trailing-drop').value = stock.trailing_drop_pct !== undefined ? stock.trailing_drop_pct : '';

        renderLevelsTable(stock);
    }

    function hideTickerEditor() {
        document.getElementById('ticker-editor-pane').style.display = 'none';
    }

    function renderLevelsTable(stock) {
        const tbody = document.getElementById('levels-tbody');
        tbody.innerHTML = '';

        const buyPcts = stock.buy_threshold_pcts || [];
        const sellPcts = stock.sell_threshold_pcts || [];
        const buyAmts = stock.buy_amounts || [];
        const trailingDrops = stock.trailing_drop_pcts || [];

        const maxLen = Math.max(buyPcts.length, sellPcts.length, buyAmts.length, trailingDrops.length);
        const rowCount = Math.max(maxLen, 1);

        for (let i = 0; i < rowCount; i++) {
            addLevelRow(i + 1, buyPcts[i], buyAmts[i], sellPcts[i], trailingDrops[i]);
        }
    }

    function addLevelRow(levelNum, buyPct, buyAmt, sellPct, trailingDrop) {
        const tbody = document.getElementById('levels-tbody');
        const tr = document.createElement('tr');

        const lvStr = levelNum || (tbody.children.length + 1);

        tr.innerHTML = `
            <td class="level-num" style="font-weight:bold; color:var(--text-muted); text-align:center;">${lvStr}</td>
            <td><input type="number" step="0.1" class="level-table-input l-buy-pct" value="${buyPct !== undefined ? buyPct : ''}"></td>
            <td><input type="number" class="level-table-input l-buy-amt" value="${buyAmt !== undefined ? buyAmt : ''}"></td>
            <td><input type="number" step="0.1" class="level-table-input l-sell-pct" value="${sellPct !== undefined ? sellPct : ''}"></td>
            <td><input type="number" step="0.1" class="level-table-input l-trailing-drop" value="${trailingDrop !== undefined ? trailingDrop : ''}"></td>
            <td style="text-align:right;"><button type="button" class="btn remove-level-btn" style="background: var(--danger); color: white; padding: 2px 8px;">X</button></td>
        `;

        tbody.appendChild(tr);
    }

    function reindexLevels() {
        const rows = document.getElementById('levels-tbody').querySelectorAll('tr');
        rows.forEach((row, i) => {
            row.querySelector('.level-num').textContent = `${i + 1}`;
        });
    }

    function updateDiffPreview(diffInfo) {
        const btn = document.getElementById('save-config-btn');
        if (diffInfo.hasChanges) {
            document.getElementById('diff-preview').textContent = diffInfo.diffText;
            btn.disabled = false;
        } else {
            document.getElementById('diff-preview').textContent = "변경 사항 없음";
            btn.disabled = true;
        }
    }

    function setSaveButtonState(isSaving) {
        const btn = document.getElementById('save-config-btn');
        if (isSaving) {
            btn.disabled = true;
            btn.textContent = "저장 중...";
        } else {
            btn.disabled = false;
            btn.textContent = "저장 및 GitHub 반영 (Commit)";
        }
    }

    function showConfigSection(isPresetMode) {
        document.getElementById('config-editor-section').style.display = '';
        if (isPresetMode) {
            document.getElementById('global-settings-card').style.display = 'none';
            document.getElementById('add-stock-btn').textContent = '+ 프리셋 추가';
        } else {
            document.getElementById('global-settings-card').style.display = '';
            document.getElementById('add-stock-btn').textContent = '+ 종목 추가';
        }
    }

        const rows = document.getElementById('levels-tbody').querySelectorAll('tr');
        const buyPcts = [];
        const buyAmts = [];
        const sellPcts = [];
        const trailingDrops = [];

        rows.forEach(row => {
            const rowBuyPct = row.querySelector('.l-buy-pct').value;
            const rowBuyAmt = row.querySelector('.l-buy-amt').value;
            const rowSellPct = row.querySelector('.l-sell-pct').value;
            const rowTrailingDrop = row.querySelector('.l-trailing-drop').value;

            buyPcts.push(rowBuyPct !== '' ? parseFloat(rowBuyPct) : NaN);
            buyAmts.push(rowBuyAmt !== '' ? parseFloat(rowBuyAmt) : NaN);
            sellPcts.push(rowSellPct !== '' ? parseFloat(rowSellPct) : NaN);
            trailingDrops.push(rowTrailingDrop !== '' ? parseFloat(rowTrailingDrop) : NaN);
        });

        return {
            ticker: document.getElementById('edit-ticker').value.trim(),
            exchange: document.getElementById('edit-exchange').value.trim(),
            market_type: document.getElementById('edit-market').value,
            preset: document.getElementById('edit-preset').value.trim(),
            max_lots: document.getElementById('edit-max-lots').value,
            reentry_guard_pct: document.getElementById('edit-reentry').value,
            buy_threshold_pct: document.getElementById('edit-buy-pct').value,
            sell_threshold_pct: document.getElementById('edit-sell-pct').value,
            buy_amount: document.getElementById('edit-buy-amt').value,
            max_exposure_pct: document.getElementById('edit-max-exposure').value,
            trailing_drop_pct: document.getElementById('edit-trailing-drop').value,
            enabled: document.getElementById('edit-enabled').checked,
            buyPcts,
            buyAmts,
            sellPcts,
            trailingDrops
        };
    }

    function getGlobalValues() {
        const notif = document.getElementById('global-notification');
        if (!notif) return null;
        return {
            notification_enabled: notif.checked,
            max_exposure_pct: document.getElementById('global-max-exposure').value,
            trailing_drop_pct: document.getElementById('global-trailing-drop').value
        };
    }

    return {
        showBanner,
        renderGlobalConfig,
        renderTickerList,
        showTickerEditor,
        hideTickerEditor,
        addLevelRow,
        reindexLevels,
        updateDiffPreview,
        setSaveButtonState,
        showConfigSection,
        getEditorValues,
        getGlobalValues
    };
})();
