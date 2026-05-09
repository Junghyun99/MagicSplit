// docs/js/manual-trade.js
(function () {
    'use strict';

    let githubApi = null;
    let currentMarket = 'domestic';
    let currentConfig = null;
    let currentStatus = null;
    let tickers = []; // Merged data

    // UI Elements
    const githubToken = document.getElementById('github-token');
    const githubOwner = document.getElementById('github-owner');
    const githubRepo = document.getElementById('github-repo');

    const tickerTbody = document.getElementById('ticker-tbody');

    const orderModal = document.getElementById('order-modal');
    const modalTitle = document.getElementById('modal-title');
    const inputLabel = document.getElementById('input-label');
    const orderValue = document.getElementById('order-value');
    const inputHint = document.getElementById('input-hint');
    const confirmTradeBtn = document.getElementById('confirm-trade-btn');
    const cancelTradeBtn = document.getElementById('cancel-trade-btn');
    const statusFeedback = document.getElementById('status-feedback');

    let activeOrderParams = null; // { ticker, alias, action, marketType }

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

    // Init
    async function init() {
        loadSettings();
        setupEventListeners();
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
            await refreshData();
        };

        cancelTradeBtn.onclick = () => orderModal.style.display = 'none';
        confirmTradeBtn.onclick = executeTrade;
        
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
                    const maxLevel = info.lots && info.lots.length > 0 
                        ? Math.max(...info.lots.map(l => l.level || 0)) 
                        : 0;
                    statusMap[ticker] = { 
                        level: maxLevel, 
                        quantity: info.total_qty || 0 
                    };
                });
            } else {
                // Legacy: positions is a flat array of lots
                posData.forEach(pos => {
                    if (!statusMap[pos.ticker]) statusMap[pos.ticker] = { level: 0, quantity: 0 };
                    statusMap[pos.ticker].level = Math.max(statusMap[pos.ticker].level, pos.level || 0);
                    statusMap[pos.ticker].quantity += (pos.quantity || 0);
                });
            }
        }

        tickers = stocks.map(rule => {
            const status = statusMap[rule.ticker] || { level: 0, quantity: 0 };
            return {
                ticker: rule.ticker,
                alias: rule.alias || rule.ticker,
                enabled: rule.enabled !== false,
                currentLevel: status.level,
                currentQty: status.quantity,
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

            // Actions Column
            const actionsTd = document.createElement('td');
            actionsTd.className = 'trade-actions';
            
            const buyBtn = document.createElement('button');
            buyBtn.className = 'btn btn-sm btn-buy';
            buyBtn.textContent = '매수';
            buyBtn.onclick = () => openOrderModal(t, 'buy');

            const sellBtn = document.createElement('button');
            sellBtn.className = 'btn btn-sm btn-sell';
            sellBtn.textContent = '매도';
            if (t.currentQty <= 0) {
                sellBtn.style.opacity = '0.5';
                sellBtn.style.cursor = 'not-allowed';
            } else {
                sellBtn.onclick = () => openOrderModal(t, 'sell');
            }

            actionsTd.appendChild(buyBtn);
            actionsTd.appendChild(sellBtn);

            tr.appendChild(infoTd);
            tr.appendChild(statusTd);
            tr.appendChild(invTd);
            tr.appendChild(actionsTd);
            tickerTbody.appendChild(tr);
        });
    }

    function openOrderModal(tickerObj, action) {
        activeOrderParams = { 
            ticker: tickerObj.ticker, 
            alias: tickerObj.alias,
            action: action,
            marketType: currentMarket 
        };

        modalTitle.textContent = `${tickerObj.alias} (${tickerObj.ticker}) ${action === 'buy' ? '매수' : '매도'}`;
        statusFeedback.style.display = 'none';
        confirmTradeBtn.disabled = false;
        confirmTradeBtn.textContent = '실행';

        if (action === 'buy') {
            inputLabel.textContent = currentMarket === 'domestic' ? '매수 금액 (원)' : '매수 금액 ($)';
            const nextLv = tickerObj.currentLevel + 1;
            const lvAmount = tickerObj.config.buy_amounts && tickerObj.config.buy_amounts[nextLv - 1];
            const defaultAmt = lvAmount || tickerObj.config.buy_amount || 0;
            orderValue.value = defaultAmt;
            inputHint.textContent = `현재 ${tickerObj.currentLevel}차 -> ${nextLv}차 매수 설정값: ${formatAmount(defaultAmt, currentMarket)}`;
        } else {
            inputLabel.textContent = '매도 수량 (주)';
            // Find qty of highest level
            const lots = currentStatus && currentStatus.positions ? currentStatus.positions.filter(p => p.ticker === tickerObj.ticker) : [];
            const highestLvLot = lots.sort((a,b) => b.level - a.level)[0];
            const defaultQty = highestLvLot ? highestLvLot.quantity : 0;
            orderValue.value = defaultQty;
            inputHint.textContent = `현재 최상위(${tickerObj.currentLevel}차) 보유량: ${defaultQty}주 / 총 ${tickerObj.currentQty}주`;
        }

        orderModal.style.display = 'flex';
    }

    async function executeTrade() {
        if (!activeOrderParams || !githubApi) return;

        const val = parseFloat(orderValue.value);
        if (isNaN(val) || val <= 0) {
            showFeedback('올바른 값을 입력해 주세요.', 'error');
            return;
        }

        const isBuy = activeOrderParams.action === 'buy';
        const valDisplay = isBuy ? formatAmount(val, currentMarket) : `${val.toLocaleString()}주`;
        if (!confirm(`${activeOrderParams.alias} 종목을 ${valDisplay} ${isBuy ? '매수' : '매도'} 하시겠습니까?`)) {
            return;
        }

        try {
            setLoading(true);
            showFeedback('GitHub Action 트리거 중...', 'info');

            const inputs = {
                market_type: activeOrderParams.marketType,
                ticker: activeOrderParams.ticker,
                action: activeOrderParams.action
            };

            if (activeOrderParams.action === 'buy') {
                inputs.amount = val.toString();
                inputs.quantity = "";
            } else {
                inputs.quantity = val.toString();
                inputs.amount = "";
            }

            await githubApi.triggerWorkflow('manual-trade.yml', inputs);

            showFeedback('매매 요청 성공! 1~2분 후 대시보드 데이터 업데이트가 완료되면 반영됩니다.', 'success');
            
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
                } catch(e) {}
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
