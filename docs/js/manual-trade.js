// docs/js/manual-trade.js
(function () {
    'use strict';

    let githubApi = null;
    let currentMarket = 'domestic';
    let currentConfig = null;
    let currentStatus = null;
    let tickers = []; // Merged data
    let tickerMap = {};
    // 전송 대기 트레이: [{ ticker, alias, action, amount|null, marketType }]
    // 종목당 1개 엔트리만 유지(재선택 시 교체)해 같은 종목의 상충 주문을 방지한다.
    let tray = [];

    // UI Elements
    const githubToken = document.getElementById('github-token');
    const githubOwner = document.getElementById('github-owner');
    const githubRepo = document.getElementById('github-repo');

    const tickerTbody = document.getElementById('ticker-tbody');

    const trayList = document.getElementById('tray-list');
    const trayCount = document.getElementById('tray-count');
    const sendBatchBtn = document.getElementById('send-batch-btn');
    const clearTrayBtn = document.getElementById('clear-tray-btn');

    const orderModal = document.getElementById('order-modal');
    const modalTitle = document.getElementById('modal-title');
    const orderSummary = document.getElementById('order-summary');
    const inputHint = document.getElementById('input-hint');
    const confirmTradeBtn = document.getElementById('confirm-trade-btn');
    const cancelTradeBtn = document.getElementById('cancel-trade-btn');
    const statusFeedback = document.getElementById('status-feedback');

    /** market_type에 맞춰 통화 단위와 함께 금액을 포매팅한다. */
    function formatAmount(value, marketType) {
        const isDomestic = marketType === 'domestic';
        return new Intl.NumberFormat(isDomestic ? 'ko-KR' : 'en-US', {
            style: 'currency',
            currency: isDomestic ? 'KRW' : 'USD',
            minimumFractionDigits: isDomestic ? 0 : 2,
            maximumFractionDigits: isDomestic ? 0 : 2,
        }).format(Number(value));
    }

    function actionLabel(action) {
        return action === 'buy' ? '매수' : action === 'sell_all' ? '일괄매도' : '매도';
    }

    // Init
    async function init() {
        loadSettings();
        setupEventListeners();
        renderTray();

        // Load ticker mapping
        try {
            const tickerData = await DataRepository.loadTickers();
            tickerMap = {};
            tickerData.forEach(t => {
                tickerMap[t[0]] = t[1];
            });
        } catch (e) {
            console.error("Failed to load tickers:", e);
        }

        if (githubApi) {
            await refreshData();
        }
    }

    function loadSettings() {
        githubToken.value = localStorage.getItem('githubToken') || '';
        githubOwner.value = localStorage.getItem('githubOwner') || 'Junghyun99';
        githubRepo.value = localStorage.getItem('githubRepo') || 'MagicSplit';

        const configPathInput = document.getElementById('config-path');
        const savedPath = localStorage.getItem('githubConfigPath') || 'config_overseas.json';
        configPathInput.value = savedPath;
        updateMarketByPath(savedPath);

        if (githubToken.value && githubOwner.value && githubRepo.value) {
            githubApi = new GitHubAPI(githubToken.value, githubOwner.value, githubRepo.value);
        }
    }

    function updateMarketByPath(path) {
        if (!path) {
            currentMarket = 'domestic';
            return;
        }
        const lowerPath = path.toLowerCase();
        if (lowerPath.includes('overseas')) currentMarket = 'overseas';
        else if (lowerPath.includes('domestic')) currentMarket = 'domestic';
        else currentMarket = 'domestic';
    }

    function setupEventListeners() {
        const loadConfigBtn = document.getElementById('load-config-btn');
        const configPathInput = document.getElementById('config-path');

        loadConfigBtn.onclick = async () => {
            const token = githubToken.value.trim();
            const owner = githubOwner.value.trim();
            const repo = githubRepo.value.trim();
            const path = configPathInput.value;

            if (!token || !owner || !repo || !path) { alert('모든 설정을 입력해 주세요.'); return; }

            localStorage.setItem('githubToken', token);
            localStorage.setItem('githubOwner', owner);
            localStorage.setItem('githubRepo', repo);
            localStorage.setItem('githubConfigPath', path);

            githubApi = new GitHubAPI(token, owner, repo);
            updateMarketByPath(path);
            // 마켓이 바뀌면 담아둔 트레이는 초기화 (배치는 단일 마켓 전제)
            tray = [];
            renderTray();
            await refreshData();
        };

        clearTrayBtn.onclick = () => { tray = []; renderTray(); };
        sendBatchBtn.onclick = openBatchConfirm;

        cancelTradeBtn.onclick = () => orderModal.style.display = 'none';
        confirmTradeBtn.onclick = executeBatch;

        // Close modal when clicking overlay
        orderModal.onclick = (e) => {
            if (e.target === orderModal) orderModal.style.display = 'none';
        };
    }

    async function refreshData() {
        tickerTbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 40px;">데이터를 불러오는 중...</td></tr>';

        try {
            if (!githubApi) {
                tickerTbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 40px; color: var(--danger);">GitHub 설정을 먼저 완료해 주세요.</td></tr>';
                return;
            }

            // 1. Fetch Config from GitHub (Real-time)
            const path = localStorage.getItem('githubConfigPath') || 'config_overseas.json';
            updateMarketByPath(path);
            const { content } = await githubApi.getFile(path);
            currentConfig = JSON.parse(content);

            // 2. Fetch Status from Repository (Dashboard data)
            currentStatus = await DataRepository.loadStatus(currentMarket);

            // 3. Merge Data
            mergeData();

            // 4. Render
            renderTable();

        } catch (e) {
            console.error(e);
            tickerTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding: 40px; color: var(--danger);">로드 실패: ${e.message}</td></tr>`;
        }
    }

    function mergeData() {
        if (!currentConfig) return;
        const stocks = currentConfig.stocks || currentConfig.rules || [];

        const statusMap = {};
        if (currentStatus && currentStatus.positions) {
            const posData = currentStatus.positions;

            // If positions is an object (ticker -> summary), as produced by recent status_builder.py
            if (!Array.isArray(posData)) {
                Object.entries(posData).forEach(([ticker, info]) => {
                    const lots = info.lots || [];
                    const maxLevel = lots.length > 0
                        ? Math.max(...lots.map(l => l.level || 0))
                        : 0;
                    const highestLot = lots.find(l => (l.level || 0) === maxLevel);
                    statusMap[ticker] = {
                        level: maxLevel,
                        quantity: info.total_qty || 0,
                        highestLvQty: highestLot ? highestLot.quantity : 0,
                        lotsCount: lots.length,
                    };
                });
            } else {
                // Legacy: positions is a flat array of lots
                posData.forEach(pos => {
                    const t = pos.ticker;
                    if (!statusMap[t]) statusMap[t] = { level: 0, quantity: 0, highestLvQty: 0, lotsCount: 0 };

                    if ((pos.level || 0) > statusMap[t].level) {
                        statusMap[t].level = pos.level || 0;
                        statusMap[t].highestLvQty = pos.quantity || 0;
                    } else if ((pos.level || 0) === statusMap[t].level) {
                        statusMap[t].highestLvQty += (pos.quantity || 0);
                    }
                    statusMap[t].quantity += (pos.quantity || 0);
                    statusMap[t].lotsCount += 1;
                });
            }
        }

        tickers = stocks.map(rule => {
            const status = statusMap[rule.ticker] || { level: 0, quantity: 0, highestLvQty: 0, lotsCount: 0 };
            const resolvedAlias = rule.alias || tickerMap[rule.ticker] || rule.ticker;

            return {
                ticker: rule.ticker,
                alias: resolvedAlias,
                enabled: rule.enabled !== false,
                currentLevel: status.level,
                currentQty: status.quantity,
                highestLvQty: status.highestLvQty,
                lotsCount: status.lotsCount,
                config: rule
            };
        });
    }

    function renderTable() {
        tickerTbody.innerHTML = '';
        if (tickers.length === 0) {
            tickerTbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 40px;">설정된 종목이 없습니다.</td></tr>';
            return;
        }

        tickers.forEach(t => {
            const tr = document.createElement('tr');

            // Info Column
            const infoTd = document.createElement('td');
            infoTd.innerHTML = `
                <div class="ticker-info">
                    <span class="ticker-name">${t.alias}</span>
                    <span class="ticker-symbol">${t.ticker}</span>
                </div>
            `;

            // Status Column
            const statusTd = document.createElement('td');
            statusTd.innerHTML = `
                <span class="badge ${t.enabled ? 'badge-enabled' : 'badge-disabled'}">${t.enabled ? 'Active' : 'Paused'}</span>
            `;

            // Inventory Column
            const invTd = document.createElement('td');
            invTd.innerHTML = `
                <span class="badge badge-level">${t.currentLevel}차</span>
                <span style="font-size:0.9rem; margin-left:5px;">${t.currentQty}주</span>
            `;

            // Actions Column - 클릭 시 즉시 실행이 아니라 트레이에 담는다.
            const actionsTd = document.createElement('td');
            actionsTd.className = 'trade-actions';
            const queuedAction = (tray.find(e => e.ticker === t.ticker) || {}).action;

            const buyBtn = document.createElement('button');
            buyBtn.className = 'btn btn-sm btn-buy' + (queuedAction === 'buy' ? ' selected' : '');
            buyBtn.textContent = '매수';
            buyBtn.onclick = () => addToTray(t, 'buy');

            const sellBtn = document.createElement('button');
            sellBtn.className = 'btn btn-sm btn-sell' + (queuedAction === 'sell' ? ' selected' : '');
            sellBtn.textContent = '매도';
            if (t.currentQty <= 0) {
                sellBtn.style.opacity = '0.5';
                sellBtn.style.cursor = 'not-allowed';
            } else {
                sellBtn.onclick = () => addToTray(t, 'sell');
            }

            const sellAllBtn = document.createElement('button');
            sellAllBtn.className = 'btn btn-sm btn-sell-all' + (queuedAction === 'sell_all' ? ' selected' : '');
            sellAllBtn.textContent = '일괄매도';
            if (t.currentQty <= 0) {
                sellAllBtn.style.opacity = '0.5';
                sellAllBtn.style.cursor = 'not-allowed';
            } else {
                sellAllBtn.onclick = () => addToTray(t, 'sell_all');
            }

            actionsTd.appendChild(buyBtn);
            actionsTd.appendChild(sellBtn);
            actionsTd.appendChild(sellAllBtn);

            tr.appendChild(infoTd);
            tr.appendChild(statusTd);
            tr.appendChild(invTd);
            tr.appendChild(actionsTd);
            tickerTbody.appendChild(tr);
        });
    }

    /** 매수 기본 금액: 다음 차수의 buy_amounts[nextLv-1], 없으면 buy_amount. */
    function defaultBuyAmount(t) {
        const nextLv = (t.currentLevel || 0) + 1;
        const lvAmount = t.config && t.config.buy_amounts && t.config.buy_amounts[nextLv - 1];
        return lvAmount || (t.config && t.config.buy_amount) || 0;
    }

    /** 종목/액션을 트레이에 담는다(종목당 1건, 재선택 시 교체). */
    function addToTray(t, action) {
        const entry = {
            ticker: t.ticker,
            alias: t.alias,
            action: action,
            amount: action === 'buy' ? defaultBuyAmount(t) : null,
            marketType: currentMarket,
        };
        const idx = tray.findIndex(e => e.ticker === t.ticker);
        if (idx >= 0) tray[idx] = entry; else tray.push(entry);
        renderTray();
        renderTable(); // 버튼 선택 표시 갱신
    }

    function removeFromTray(ticker) {
        tray = tray.filter(e => e.ticker !== ticker);
        renderTray();
        renderTable();
    }

    function renderTray() {
        // 표시 순서: 매도 -> 매수 (실제 실행 순서와 동일)
        const ordered = tray.slice().sort((a, b) => (a.action === 'buy' ? 1 : 0) - (b.action === 'buy' ? 1 : 0));

        if (ordered.length === 0) {
            trayList.innerHTML = '<span class="tray-empty">전송할 종목이 없습니다. 위 표에서 매수/매도/일괄매도를 눌러 담으세요.</span>';
            trayCount.textContent = '0건 선택됨';
            sendBatchBtn.disabled = true;
            sendBatchBtn.textContent = '일괄 전송';
            return;
        }

        trayList.innerHTML = '';
        ordered.forEach(e => {
            const chip = document.createElement('div');
            chip.className = 'tray-chip';

            const name = document.createElement('span');
            name.innerHTML = `<span class="chip-action ${e.action}">${actionLabel(e.action)}</span> ${e.alias}`;
            chip.appendChild(name);

            if (e.action === 'buy') {
                const amt = document.createElement('input');
                amt.type = 'number';
                amt.min = '1';
                amt.step = '1';
                amt.className = 'chip-amount';
                amt.value = e.amount || 0;
                amt.title = `매수 금액 (${e.marketType === 'domestic' ? '원' : 'USD'})`;
                amt.oninput = () => {
                    const v = parseFloat(amt.value);
                    e.amount = (!isNaN(v) && v > 0) ? v : 0;
                };
                chip.appendChild(amt);
            }

            const rm = document.createElement('button');
            rm.className = 'chip-remove';
            rm.textContent = 'x';
            rm.title = '제거';
            rm.onclick = () => removeFromTray(e.ticker);
            chip.appendChild(rm);

            trayList.appendChild(chip);
        });

        trayCount.textContent = `${ordered.length}건 선택됨`;
        sendBatchBtn.disabled = false;
        sendBatchBtn.textContent = `${ordered.length}건 일괄 전송`;
    }

    /** 전송 전 확인 모달에 전체 목록을 표시한다. */
    function openBatchConfirm() {
        if (!githubApi) { alert('GitHub 설정을 먼저 완료해 주세요.'); return; }
        if (tray.length === 0) return;

        const ordered = tray.slice().sort((a, b) => (a.action === 'buy' ? 1 : 0) - (b.action === 'buy' ? 1 : 0));
        modalTitle.textContent = `${ordered.length}건 일괄 수동매매`;

        const rows = ordered.map(e => {
            let line = `<b>${e.alias}</b> (${e.ticker}) — ${actionLabel(e.action)}`;
            if (e.action === 'buy' && e.amount > 0) {
                line += ` · ${formatAmount(e.amount, e.marketType)}`;
            }
            return `<div style="padding:4px 0; border-bottom:1px solid var(--border);">${line}</div>`;
        }).join('');

        orderSummary.innerHTML = rows;
        inputHint.textContent = '매도(일괄매도 포함)를 먼저 실행한 뒤 매수를 진행합니다. 수량은 엔진이 자동 계산합니다.';
        statusFeedback.style.display = 'none';
        confirmTradeBtn.disabled = false;
        confirmTradeBtn.textContent = '실행';
        orderModal.style.display = 'flex';
    }

    async function executeBatch() {
        if (!githubApi || tray.length === 0) return;

        const market = tray[0].marketType;
        const trades = tray.map(e => {
            const o = { ticker: e.ticker, action: e.action };
            if (e.action === 'buy' && e.amount > 0) o.amount = e.amount;
            return o;
        });

        try {
            setLoading(true);
            showFeedback('GitHub Action 트리거 중...', 'info');

            await githubApi.triggerWorkflow('manual-trade.yml', {
                market_type: market,
                trades: JSON.stringify(trades),
            });

            showFeedback(`${trades.length}건 일괄 매매 요청 성공! 1~2분 후 대시보드 데이터 업데이트가 완료되면 반영됩니다.`, 'success');
            tray = [];
            renderTray();
            renderTable();

            setTimeout(async () => {
                try {
                    const run = await githubApi.getLatestWorkflowRun('manual-trade.yml');
                    if (run) {
                        const link = document.createElement('a');
                        link.href = run.html_url;
                        link.target = '_blank';
                        link.textContent = ' 🚀 GitHub Action 실행 로그 보기';
                        link.style.display = 'block';
                        link.style.marginTop = '12px';
                        link.style.padding = '8px';
                        link.style.background = '#f1f5f9';
                        link.style.borderRadius = '4px';
                        link.style.textDecoration = 'none';
                        link.style.textAlign = 'center';
                        link.style.fontWeight = 'bold';
                        statusFeedback.appendChild(link);
                    }
                } catch (e) {}
            }, 3000);

        } catch (e) {
            showFeedback(`오류: ${e.message}`, 'error');
        } finally {
            setLoading(false);
        }
    }

    function showFeedback(msg, type) {
        statusFeedback.textContent = msg;
        statusFeedback.className = `status-feedback ${type}`;
        statusFeedback.style.display = 'block';
    }

    function setLoading(isLoading) {
        confirmTradeBtn.disabled = isLoading;
        if (isLoading) {
            confirmTradeBtn.innerHTML = '<span class="loading-spinner"></span> 요청 중...';
        } else {
            confirmTradeBtn.textContent = '실행';
        }
    }

    init();
})();
