// docs/js/config-editor.js
(function () {
    'use strict';

    let originalConfigObj = null;
    let currentConfigObj = null;
    let configSha = null;
    let githubApi = null;
    let currentConfigPath = null;
    let activeStockIndex = null;

    function showBanner(msg, type = 'info') {
        const banner = document.getElementById('message-banner');
        banner.className = `reason-banner ${type}`;
        banner.textContent = msg;
        banner.style.display = '';
    }

    function initAuthForm() {
        const tokenInput = document.getElementById('github-token');
        const ownerInput = document.getElementById('github-owner');
        const repoInput = document.getElementById('github-repo');
        const pathInput = document.getElementById('config-path');

        tokenInput.value = localStorage.getItem('githubToken') || '';
        ownerInput.value = localStorage.getItem('githubOwner') || 'Junghyun99';
        repoInput.value = localStorage.getItem('githubRepo') || 'MagicSplit';
        
        const savedPath = localStorage.getItem('githubConfigPath') || 'config_overseas.json';
        if (Array.from(pathInput.options).some(o => o.value === savedPath)) {
            pathInput.value = savedPath;
        } else {
            pathInput.value = 'config_overseas.json';
        }

        document.getElementById('load-config-btn').addEventListener('click', async () => {
            const token = tokenInput.value.trim();
            const owner = ownerInput.value.trim();
            const repo = repoInput.value.trim();
            const path = pathInput.value.trim();

            if (!token || !owner || !repo || !path) {
                showBanner('토큰, Owner, Repo, File Path를 모두 입력해주세요.', 'danger');
                return;
            }

            localStorage.setItem('githubToken', token);
            localStorage.setItem('githubOwner', owner);
            localStorage.setItem('githubRepo', repo);
            localStorage.setItem('githubConfigPath', path);

            githubApi = new GitHubAPI(token, owner, repo);
            currentConfigPath = path;
            await loadConfig();
        });
    }

    async function loadConfig() {
        showBanner('설정을 불러오는 중...', 'info');
        activeStockIndex = null; // Reset to prevent saving old DOM to new config
        try {
            const { content, sha } = await githubApi.getFile(currentConfigPath);
            originalConfigObj = JSON.parse(content);
            configSha = sha;
            
            if (currentConfigPath === 'presets.json') {
                currentConfigObj = {
                    stocks: Object.keys(originalConfigObj).map(key => {
                        return {
                            ticker: key,
                            ...originalConfigObj[key]
                        };
                    }),
                    global: {}
                };
                document.getElementById('global-settings-card').style.display = 'none';
                document.getElementById('add-stock-btn').textContent = '+ 프리셋 추가';
            } else {
                currentConfigObj = JSON.parse(JSON.stringify(originalConfigObj));
                if (!currentConfigObj.stocks) currentConfigObj.stocks = [];
                document.getElementById('global-settings-card').style.display = '';
                document.getElementById('add-stock-btn').textContent = '+ 종목 추가';
            }
            
            renderGlobalConfig();
            renderTickerList();
            
            if (currentConfigObj.stocks.length > 0) {
                selectTicker(0);
            } else {
                document.getElementById('ticker-editor-pane').style.display = 'none';
            }
            
            document.getElementById('config-editor-section').style.display = '';
            showBanner('설정을 성공적으로 불러왔습니다.', 'success');
            updateDiffPreview();
        } catch (e) {
            showBanner(`오류: ${e.message}`, 'danger');
            console.error(e);
        }
    }

    function renderGlobalConfig() {
        const globalInterval = document.getElementById('global-interval');
        globalInterval.value = currentConfigObj.global?.check_interval_minutes || 60;
        
        const globalNotif = document.getElementById('global-notification');
        globalNotif.checked = currentConfigObj.global?.notification_enabled !== false;
    }

    function saveGlobalConfig() {
        if (!currentConfigObj) return;
        if (!currentConfigObj.global) currentConfigObj.global = {};
        
        currentConfigObj.global.check_interval_minutes = parseInt(document.getElementById('global-interval').value || '60', 10);
        currentConfigObj.global.notification_enabled = document.getElementById('global-notification').checked;
        updateDiffPreview();
    }

    function renderTickerList() {
        const list = document.getElementById('ticker-list');
        list.innerHTML = '';
        currentConfigObj.stocks.forEach((stock, idx) => {
            const li = document.createElement('li');
            li.textContent = stock.ticker || '(New Ticker)';
            if (idx === activeStockIndex) li.className = 'active-ticker';
            li.onclick = () => selectTicker(idx);
            list.appendChild(li);
        });
    }

    function selectTicker(index) {
        saveCurrentTicker(); // Save previous state
        
        activeStockIndex = index;
        renderTickerList();
        
        const stock = currentConfigObj.stocks[index];
        if (!stock) return;
        
        const isPresetMode = currentConfigPath === 'presets.json';
        
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
        
        renderLevelsTable(stock);
    }

    function renderLevelsTable(stock) {
        const tbody = document.getElementById('levels-tbody');
        tbody.innerHTML = '';
        
        const buyPcts = stock.buy_threshold_pcts || [];
        const sellPcts = stock.sell_threshold_pcts || [];
        const buyAmts = stock.buy_amounts || [];
        
        const maxLen = Math.max(buyPcts.length, sellPcts.length, buyAmts.length);
        
        // Render at least one row if they don't have presets, but let's just render maxLen.
        // If maxLen is 0, we can add an empty row.
        const rowCount = Math.max(maxLen, 1);
        
        for (let i = 0; i < rowCount; i++) {
            addLevelRow(i + 1, buyPcts[i], buyAmts[i], sellPcts[i]);
        }
    }

    function addLevelRow(levelNum, buyPct, buyAmt, sellPct) {
        const tbody = document.getElementById('levels-tbody');
        const tr = document.createElement('tr');
        
        const lvStr = levelNum || (tbody.children.length + 1);
        
        tr.innerHTML = `
            <td style="font-weight:bold; color:var(--text-muted); text-align:center;">${lvStr}</td>
            <td><input type="number" step="0.1" class="level-table-input l-buy-pct" value="${buyPct !== undefined ? buyPct : ''}"></td>
            <td><input type="number" class="level-table-input l-buy-amt" value="${buyAmt !== undefined ? buyAmt : ''}"></td>
            <td><input type="number" step="0.1" class="level-table-input l-sell-pct" value="${sellPct !== undefined ? sellPct : ''}"></td>
            <td style="text-align:right;"><button type="button" class="btn remove-level-btn" style="background: var(--danger); color: white; padding: 2px 8px;">X</button></td>
        `;
        
        tr.querySelector('.remove-level-btn').onclick = () => {
            tr.remove();
            reindexLevels();
            saveCurrentTicker();
        };
        
        tr.querySelectorAll('input').forEach(input => {
            input.addEventListener('input', saveCurrentTicker);
        });
        
        tbody.appendChild(tr);
    }

    function reindexLevels() {
        const rows = document.getElementById('levels-tbody').querySelectorAll('tr');
        rows.forEach((row, i) => {
            row.children[0].textContent = `${i + 1}`;
        });
    }

    function saveCurrentTicker() {
        if (activeStockIndex === null || !currentConfigObj || !currentConfigObj.stocks[activeStockIndex]) return;
        
        const stock = currentConfigObj.stocks[activeStockIndex];
        
        stock.ticker = document.getElementById('edit-ticker').value.trim();
        const exchange = document.getElementById('edit-exchange').value.trim();
        if (exchange) stock.exchange = exchange; else delete stock.exchange;
        
        stock.market_type = document.getElementById('edit-market').value;
        
        const preset = document.getElementById('edit-preset').value.trim();
        if (preset) stock.preset = preset; else delete stock.preset;
        
        const maxLots = document.getElementById('edit-max-lots').value;
        if (maxLots) stock.max_lots = parseInt(maxLots, 10);
        
        const reentry = document.getElementById('edit-reentry').value;
        if (reentry) stock.reentry_guard_pct = parseFloat(reentry); else delete stock.reentry_guard_pct;
        
        const bPct = document.getElementById('edit-buy-pct').value;
        if (bPct) stock.buy_threshold_pct = parseFloat(bPct); else delete stock.buy_threshold_pct;
        
        const sPct = document.getElementById('edit-sell-pct').value;
        if (sPct) stock.sell_threshold_pct = parseFloat(sPct); else delete stock.sell_threshold_pct;
        
        const bAmt = document.getElementById('edit-buy-amt').value;
        if (bAmt) stock.buy_amount = parseFloat(bAmt); else delete stock.buy_amount;
        
        stock.enabled = document.getElementById('edit-enabled').checked;
        
        // Extract levels
        const rows = document.getElementById('levels-tbody').querySelectorAll('tr');
        const buyPcts = [];
        const buyAmts = [];
        const sellPcts = [];
        
        rows.forEach(row => {
            const rowBuyPct = row.querySelector('.l-buy-pct').value;
            const rowBuyAmt = row.querySelector('.l-buy-amt').value;
            const rowSellPct = row.querySelector('.l-sell-pct').value;
            
            buyPcts.push(rowBuyPct !== '' ? parseFloat(rowBuyPct) : NaN);
            buyAmts.push(rowBuyAmt !== '' ? parseFloat(rowBuyAmt) : NaN);
            sellPcts.push(rowSellPct !== '' ? parseFloat(rowSellPct) : NaN);
        });
        
        const filterNaNs = (arr) => {
            let lastValid = -1;
            for (let i = 0; i < arr.length; i++) {
                if (!isNaN(arr[i])) lastValid = i;
            }
            if (lastValid === -1) return undefined;
            // Leave empty cells as null so JSON has null (or if backend expects numbers, use 0. But Python backend might prefer None. Let's use 0 to be safe with float cast).
            return arr.slice(0, lastValid + 1).map(x => isNaN(x) ? 0 : x);
        };
        
        const cleanBuyPcts = filterNaNs(buyPcts);
        const cleanBuyAmts = filterNaNs(buyAmts);
        const cleanSellPcts = filterNaNs(sellPcts);
        
        if (cleanBuyPcts !== undefined) stock.buy_threshold_pcts = cleanBuyPcts; else delete stock.buy_threshold_pcts;
        if (cleanBuyAmts !== undefined) stock.buy_amounts = cleanBuyAmts; else delete stock.buy_amounts;
        if (cleanSellPcts !== undefined) stock.sell_threshold_pcts = cleanSellPcts; else delete stock.sell_threshold_pcts;
        
        const lis = document.getElementById('ticker-list').querySelectorAll('li');
        if (lis[activeStockIndex]) {
            lis[activeStockIndex].textContent = stock.ticker || '(New Ticker)';
        }
        document.getElementById('current-ticker-title').textContent = stock.ticker ? `${stock.ticker} 설정` : '새 종목 설정';
        
        updateDiffPreview();
    }

    function stringifyConfig(obj) {
        let str = JSON.stringify(obj, null, 4);
        // collapse numerical arrays into a single line
        str = str.replace(/\[\s*([-0-9.,\s]+?)\s*\]/g, (match, inner) => {
            const collapsed = inner.replace(/\s+/g, '').replace(/,/g, ', ');
            return '[' + collapsed + ']';
        });
        return str;
    }

    function updateDiffPreview() {
        if (!originalConfigObj || !currentConfigObj) return;
        
        let newObjToSave;
        if (currentConfigPath === 'presets.json') {
            newObjToSave = {};
            currentConfigObj.stocks.forEach(stock => {
                const key = stock.ticker;
                if (!key) return;
                const cloned = { ...stock };
                delete cloned.ticker;
                delete cloned.exchange;
                delete cloned.market_type;
                delete cloned.enabled;
                delete cloned.preset;
                newObjToSave[key] = cloned;
            });
        } else {
            newObjToSave = currentConfigObj;
        }

        const origStr = stringifyConfig(originalConfigObj);
        const newStr = stringifyConfig(newObjToSave);

        if (origStr === newStr) {
            document.getElementById('diff-preview').textContent = "변경 사항 없음";
            document.getElementById('save-config-btn').disabled = true;
        } else {
            document.getElementById('diff-preview').textContent = newStr;
            document.getElementById('save-config-btn').disabled = false;
        }
    }

    async function saveConfig() {
        if (!githubApi || !configSha || !currentConfigObj) return;

        saveCurrentTicker();
        
        const btn = document.getElementById('save-config-btn');
        btn.disabled = true;
        btn.textContent = "저장 중...";

        try {
            let newObjToSave;
            if (currentConfigPath === 'presets.json') {
                newObjToSave = {};
                currentConfigObj.stocks.forEach(stock => {
                    const key = stock.ticker;
                    if (!key) return;
                    const cloned = { ...stock };
                    delete cloned.ticker;
                    delete cloned.exchange;
                    delete cloned.market_type;
                    delete cloned.enabled;
                    delete cloned.preset;
                    newObjToSave[key] = cloned;
                });
            } else {
                newObjToSave = currentConfigObj;
            }

            const contentStr = stringifyConfig(newObjToSave) + '\n';
            const msg = `chore(config): update rules via web editor`;

            await githubApi.updateFile(currentConfigPath, contentStr, msg, configSha);
            
            showBanner('성공적으로 저장되었습니다! GitHub Actions가 스케줄에 따라 실행될 때 적용됩니다.', 'success');
            
            setTimeout(() => {
                btn.textContent = "저장 및 GitHub 반영 (Commit)";
                loadConfig();
            }, 1500);

        } catch (e) {
            showBanner(`저장 실패: ${e.message}`, 'danger');
            btn.disabled = false;
            btn.textContent = "저장 및 GitHub 반영 (Commit)";
            console.error(e);
        }
    }

    function init() {
        initAuthForm();
        
        document.getElementById('global-interval').addEventListener('input', saveGlobalConfig);
        document.getElementById('global-notification').addEventListener('change', saveGlobalConfig);
        
        const editorInputs = document.getElementById('ticker-editor-pane').querySelectorAll('input:not(.level-table-input), select');
        editorInputs.forEach(input => {
            input.addEventListener('input', saveCurrentTicker);
            input.addEventListener('change', saveCurrentTicker);
        });
        
        document.getElementById('add-stock-btn').addEventListener('click', () => {
            if (!currentConfigObj) return;
            saveCurrentTicker();
            currentConfigObj.stocks.push({ ticker: '', market_type: 'overseas', enabled: true, max_lots: 10 });
            renderTickerList();
            selectTicker(currentConfigObj.stocks.length - 1);
        });
        
        document.getElementById('delete-stock-btn').addEventListener('click', () => {
            if (activeStockIndex === null || !currentConfigObj) return;
            if (confirm('이 종목 설정을 삭제하시겠습니까?')) {
                currentConfigObj.stocks.splice(activeStockIndex, 1);
                activeStockIndex = null;
                renderTickerList();
                
                if (currentConfigObj.stocks.length > 0) {
                    selectTicker(0);
                } else {
                    document.getElementById('ticker-editor-pane').style.display = 'none';
                }
                updateDiffPreview();
            }
        });
        
        document.getElementById('add-level-btn').addEventListener('click', () => {
            addLevelRow();
            saveCurrentTicker();
        });
        
        document.getElementById('save-config-btn').addEventListener('click', saveConfig);
    }

    init();
})();
