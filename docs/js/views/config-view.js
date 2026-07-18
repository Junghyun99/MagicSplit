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
        document.getElementById('global-regime-enabled').checked = globalConfig?.regime_enabled === true;
        document.getElementById('global-uptrend-add-reset-pct').value = globalConfig?.uptrend_add_reset_pct !== undefined ? globalConfig.uptrend_add_reset_pct : '';
        document.getElementById('global-trendbreak-use-sma50').checked = globalConfig?.trendbreak_use_sma50 !== false;
        document.getElementById('global-trendbreak-chandelier-k').value = globalConfig?.trendbreak_chandelier_k !== undefined ? globalConfig.trendbreak_chandelier_k : '';
        document.getElementById('global-regime-algo').value = globalConfig?.regime_algo !== undefined ? globalConfig.regime_algo : '';
        document.getElementById('global-channel-lookback').value = globalConfig?.channel_lookback !== undefined ? globalConfig.channel_lookback : '';
        document.getElementById('global-channel-stddev-k').value = globalConfig?.channel_stddev_k !== undefined ? globalConfig.channel_stddev_k : '';
        document.getElementById('global-channel-slope-band-pct').value = globalConfig?.channel_slope_band_pct !== undefined ? globalConfig.channel_slope_band_pct : '';
        document.getElementById('global-channel-breakdown-tolerance-pct').value = globalConfig?.channel_breakdown_tolerance_pct !== undefined ? globalConfig.channel_breakdown_tolerance_pct : '';
        document.getElementById('global-channel-breakdown-uptrend-only').checked = globalConfig?.channel_breakdown_uptrend_only === true;
        document.getElementById('global-channel-reentry-breakout').checked = globalConfig?.channel_reentry_breakout === true;
    }

    function renderTickerList(stocks, activeIndex, onSelect, getDisplayName) {
        const list = document.getElementById('ticker-list');
        list.innerHTML = '';
        stocks.forEach((stock, idx) => {
            const li = document.createElement('li');
            li.textContent = getDisplayName ? getDisplayName(stock.ticker) : (stock.ticker || '(New Ticker)');
            if (idx === activeIndex) li.className = 'active-ticker';
            li.onclick = () => onSelect(idx);
            list.appendChild(li);
        });
    }

    function showTickerEditor(stock, isPresetMode, displayName) {
        document.getElementById('ticker-editor-pane').style.display = '';
        const titleText = displayName || stock.ticker;
        document.getElementById('current-ticker-title').textContent = titleText ? (isPresetMode ? `${titleText} 프리셋` : `${titleText} 설정`) : (isPresetMode ? '새 프리셋' : '새 종목 설정');

        document.getElementById('edit-ticker-label').textContent = isPresetMode ? 'Preset Name' : 'Ticker';
        document.getElementById('group-preset').style.display = isPresetMode ? 'none' : '';
        document.getElementById('group-enabled').style.display = isPresetMode ? 'none' : 'flex';

        document.getElementById('edit-ticker').value = stock.ticker || '';
        document.getElementById('edit-preset').value = stock.preset || '';
        document.getElementById('edit-max-lots').value = stock.max_lots !== undefined ? stock.max_lots : '';
        document.getElementById('edit-reentry').value = stock.reentry_guard_pct !== undefined ? stock.reentry_guard_pct : '';
        document.getElementById('edit-priority').value = stock.priority !== undefined ? stock.priority : '';
        document.getElementById('edit-enabled').checked = stock.enabled !== false;

        document.getElementById('edit-buy-pct').value = stock.buy_threshold_pct !== undefined ? stock.buy_threshold_pct : '';
        document.getElementById('edit-sell-pct').value = stock.sell_threshold_pct !== undefined ? stock.sell_threshold_pct : '';
        document.getElementById('edit-buy-amt').value = stock.buy_amount !== undefined ? stock.buy_amount : '';
        document.getElementById('edit-max-exposure').value = stock.max_exposure_pct !== undefined ? stock.max_exposure_pct : '';
        document.getElementById('edit-trailing-drop').value = stock.trailing_drop_pct !== undefined ? stock.trailing_drop_pct : '';
        document.getElementById('edit-spread-threshold').value = stock.spread_threshold_pct !== undefined ? stock.spread_threshold_pct : '';

        document.getElementById('edit-uptrend-max-adds').value = stock.uptrend_max_adds !== undefined ? stock.uptrend_max_adds : '';
        document.getElementById('edit-uptrend-pullback-band-pct').value = stock.uptrend_pullback_band_pct !== undefined ? stock.uptrend_pullback_band_pct : '';
        document.getElementById('edit-uptrend-add-reset-pct').value = stock.uptrend_add_reset_pct !== undefined ? stock.uptrend_add_reset_pct : '';
        document.getElementById('edit-trendbreak-partial-sell-pct').value = stock.trendbreak_partial_sell_pct !== undefined ? stock.trendbreak_partial_sell_pct : '';
        document.getElementById('edit-trendbreak-trailing-drop-pct').value = stock.trendbreak_trailing_drop_pct !== undefined ? stock.trendbreak_trailing_drop_pct : '';

        renderLevelsTable(stock);
        renderUptrendAmountsTable(stock);
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

    function renderUptrendAmountsTable(stock) {
        const tbody = document.getElementById('uptrend-amounts-tbody');
        tbody.innerHTML = '';
        const amounts = stock.uptrend_add_amounts || [];
        const rowCount = Math.max(amounts.length, 1);
        for (let i = 0; i < rowCount; i++) {
            addUptrendAmountRow(i + 1, amounts[i]);
        }
    }

    function addUptrendAmountRow(rowNum, amount) {
        const tbody = document.getElementById('uptrend-amounts-tbody');
        const tr = document.createElement('tr');
        const num = rowNum || (tbody.children.length + 1);
        tr.innerHTML = `
            <td class="uptrend-row-num" style="font-weight:bold; color:var(--text-muted); text-align:center;">${num}</td>
            <td><input type="number" class="level-table-input u-add-amt" value="${amount !== undefined ? amount : ''}"></td>
            <td style="text-align:right;"><button type="button" class="btn remove-uptrend-btn" style="background: var(--danger); color: white; padding: 2px 8px;">X</button></td>
        `;
        tbody.appendChild(tr);
    }

    function reindexUptrendAmounts() {
        document.getElementById('uptrend-amounts-tbody').querySelectorAll('tr').forEach((row, i) => {
            row.querySelector('.uptrend-row-num').textContent = `${i + 1}`;
        });
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
    function getEditorValues() {
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

        const uptrendAmounts = [];
        document.getElementById('uptrend-amounts-tbody').querySelectorAll('tr').forEach(row => {
            const v = row.querySelector('.u-add-amt').value;
            uptrendAmounts.push(v !== '' ? parseFloat(v) : NaN);
        });

        return {
            ticker: document.getElementById('edit-ticker').value.trim(),
            preset: document.getElementById('edit-preset').value.trim(),
            max_lots: document.getElementById('edit-max-lots').value,
            reentry_guard_pct: document.getElementById('edit-reentry').value,
            priority: document.getElementById('edit-priority').value,
            buy_threshold_pct: document.getElementById('edit-buy-pct').value,
            sell_threshold_pct: document.getElementById('edit-sell-pct').value,
            buy_amount: document.getElementById('edit-buy-amt').value,
            max_exposure_pct: document.getElementById('edit-max-exposure').value,
            trailing_drop_pct: document.getElementById('edit-trailing-drop').value,
            spread_threshold_pct: document.getElementById('edit-spread-threshold').value,
            enabled: document.getElementById('edit-enabled').checked,
            uptrend_max_adds: document.getElementById('edit-uptrend-max-adds').value,
            uptrend_pullback_band_pct: document.getElementById('edit-uptrend-pullback-band-pct').value,
            uptrend_add_reset_pct: document.getElementById('edit-uptrend-add-reset-pct').value,
            trendbreak_partial_sell_pct: document.getElementById('edit-trendbreak-partial-sell-pct').value,
            trendbreak_trailing_drop_pct: document.getElementById('edit-trendbreak-trailing-drop-pct').value,
            buyPcts,
            buyAmts,
            sellPcts,
            trailingDrops,
            uptrendAmounts
        };
    }

    function getGlobalValues() {
        const notif = document.getElementById('global-notification');
        if (!notif) return null;
        return {
            notification_enabled: notif.checked,
            max_exposure_pct: document.getElementById('global-max-exposure').value,
            trailing_drop_pct: document.getElementById('global-trailing-drop').value,
            regime_enabled: document.getElementById('global-regime-enabled').checked,
            uptrend_add_reset_pct: document.getElementById('global-uptrend-add-reset-pct').value,
            trendbreak_use_sma50: document.getElementById('global-trendbreak-use-sma50').checked,
            trendbreak_chandelier_k: document.getElementById('global-trendbreak-chandelier-k').value,
            regime_algo: document.getElementById('global-regime-algo').value,
            channel_lookback: document.getElementById('global-channel-lookback').value,
            channel_stddev_k: document.getElementById('global-channel-stddev-k').value,
            channel_slope_band_pct: document.getElementById('global-channel-slope-band-pct').value,
            channel_breakdown_tolerance_pct: document.getElementById('global-channel-breakdown-tolerance-pct').value,
            channel_breakdown_uptrend_only: document.getElementById('global-channel-breakdown-uptrend-only').checked,
            channel_reentry_breakout: document.getElementById('global-channel-reentry-breakout').checked
        };
    }

    function renderTickerSearchResults(results, onSelect) {
        const container = document.getElementById('ticker-search-results');
        container.innerHTML = '';
        if (results.length === 0) {
            container.style.display = 'none';
            return;
        }

        results.forEach(r => {
            const div = document.createElement('div');
            div.className = 'search-item';
            div.innerHTML = `
                <span class="ticker-alias">${r.alias}</span>
                <span>
                    <span class="ticker-id">${r.ticker}</span>
                    <span class="ticker-ex">${r.exchange}</span>
                </span>
            `;
            div.onclick = () => {
                onSelect(r);
                container.style.display = 'none';
            };
            container.appendChild(div);
        });
        container.style.display = 'block';
    }

    function hideTickerSearchResults() {
        document.getElementById('ticker-search-results').style.display = 'none';
    }

    return {
        showBanner,
        renderGlobalConfig,
        renderTickerList,
        showTickerEditor,
        hideTickerEditor,
        addLevelRow,
        reindexLevels,
        addUptrendAmountRow,
        reindexUptrendAmounts,
        updateDiffPreview,
        setSaveButtonState,
        showConfigSection,
        getEditorValues,
        getGlobalValues,
        renderTickerSearchResults,
        hideTickerSearchResults
    };
})();
