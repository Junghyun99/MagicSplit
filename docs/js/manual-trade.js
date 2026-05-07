// docs/js/manual-trade.js
(function () {
    'use strict';

    let githubApi = null;
    let tickers = [];
    let currentMarket = 'domestic';

    // UI Elements
    const authCard = document.getElementById('auth-card');
    const githubToken = document.getElementById('github-token');
    const githubOwner = document.getElementById('github-owner');
    const githubRepo = document.getElementById('github-repo');
    const saveSettingsBtn = document.getElementById('save-settings-btn');

    const tickerSearch = document.getElementById('ticker-search');
    const tickerResults = document.getElementById('ticker-results');
    const selectedTicker = document.getElementById('selected-ticker');
    const orderQty = document.getElementById('order-qty');
    const executeTradeBtn = document.getElementById('execute-trade-btn');
    const statusFeedback = document.getElementById('status-feedback');

    const marketBtns = document.querySelectorAll('.market-btn');
    const actionBtns = document.querySelectorAll('.action-btn');

    // Initialize
    async function init() {
        loadSettings();
        tickers = await DataRepository.loadTickers();
        setupEventListeners();
    }

    function loadSettings() {
        githubToken.value = localStorage.getItem('github_token') || '';
        githubOwner.value = localStorage.getItem('github_owner') || '';
        githubRepo.value = localStorage.getItem('github_repo') || '';
        
        if (githubToken.value && githubOwner.value && githubRepo.value) {
            githubApi = new GitHubAPI(githubToken.value, githubOwner.value, githubRepo.value);
        }
    }

    function setupEventListeners() {
        saveSettingsBtn.onclick = () => {
            const token = githubToken.value.trim();
            const owner = githubOwner.value.trim();
            const repo = githubRepo.value.trim();

            if (!token || !owner || !repo) {
                alert('모든 GitHub 설정을 입력해 주세요.');
                return;
            }

            localStorage.setItem('github_token', token);
            localStorage.setItem('github_owner', owner);
            localStorage.setItem('github_repo', repo);
            githubApi = new GitHubAPI(token, owner, repo);
            alert('설정이 저장되었습니다.');
        };

        marketBtns.forEach(btn => {
            btn.onclick = () => {
                marketBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentMarket = btn.dataset.market;
                tickerSearch.value = '';
                selectedTicker.value = '';
            };
        });

        actionBtns.forEach(btn => {
            btn.onclick = () => {
                actionBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            };
        });

        tickerSearch.oninput = (e) => {
            const val = e.target.value.trim().toLowerCase();
            if (val.length < 1) {
                tickerResults.style.display = 'none';
                return;
            }

            const results = tickers.filter(t => 
                t.market_type === currentMarket && 
                (t.ticker.toLowerCase().includes(val) || t.alias.toLowerCase().includes(val))
            ).slice(0, 10);

            renderSearchResults(results);
        };

        // Close search results when clicking outside
        document.addEventListener('click', (e) => {
            if (!tickerSearch.contains(e.target) && !tickerResults.contains(e.target)) {
                tickerResults.style.display = 'none';
            }
        });

        executeTradeBtn.onclick = executeTrade;
    }

    function renderSearchResults(results) {
        tickerResults.innerHTML = '';
        if (results.length === 0) {
            tickerResults.style.display = 'none';
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
                tickerSearch.value = `${r.alias} (${r.ticker})`;
                selectedTicker.value = r.ticker;
                tickerResults.style.display = 'none';
            };
            tickerResults.appendChild(div);
        });
        tickerResults.style.display = 'block';
    }

    async function executeTrade() {
        if (!githubApi) {
            showStatus('GitHub 설정을 먼저 저장해 주세요.', 'error');
            return;
        }

        const ticker = selectedTicker.value;
        const action = document.querySelector('.action-btn.active').dataset.action;
        const qty = orderQty.value;

        if (!ticker) {
            showStatus('종목을 검색하여 선택해 주세요.', 'error');
            return;
        }
        if (!qty || qty <= 0) {
            showStatus('올바른 수량을 입력해 주세요.', 'error');
            return;
        }

        const marketName = currentMarket === 'domestic' ? '국내' : '해외';
        if (!confirm(`${marketName} 시장에서 ${ticker} 종목을 ${qty}주 ${action === 'buy' ? '매수' : '매도'} 하시겠습니까?`)) {
            return;
        }

        try {
            setLoading(true);
            showStatus('GitHub Action 트리거 중...', 'info');

            await githubApi.triggerWorkflow('manual-trade.yml', {
                market_type: currentMarket,
                ticker: ticker,
                action: action,
                quantity: qty.toString()
            });

            showStatus('매매 요청이 성공적으로 전송되었습니다! 1~2분 후 데이터 업데이트가 완료되면 대시보드에 반영됩니다.', 'success');
            
            // 최신 실행 링크 가져오기 (약간의 지연 필요할 수 있음)
            setTimeout(async () => {
                try {
                    const runInfo = await githubApi.getLatestWorkflowRun('manual-trade.yml');
                    if (runInfo) {
                        const link = document.createElement('a');
                        link.href = runInfo.html_url;
                        link.target = '_blank';
                        link.textContent = ' 🚀 GitHub Action 실행 로그 보기';
                        link.style.display = 'block';
                        link.style.marginTop = '12px';
                        link.style.padding = '10px';
                        link.style.background = '#f1f5f9';
                        link.style.borderRadius = '6px';
                        link.style.textDecoration = 'none';
                        link.style.color = 'var(--primary)';
                        link.style.fontWeight = 'bold';
                        link.style.textAlign = 'center';
                        statusFeedback.appendChild(link);
                    }
                } catch (e) {
                    console.error('Failed to fetch latest run:', e);
                }
            }, 2000);

        } catch (e) {
            showStatus(`오류 발생: ${e.message}`, 'error');
        } finally {
            setLoading(false);
        }
    }

    function showStatus(msg, type) {
        statusFeedback.textContent = msg;
        statusFeedback.className = `status-feedback ${type}`;
    }

    function setLoading(isLoading) {
        executeTradeBtn.disabled = isLoading;
        if (isLoading) {
            executeTradeBtn.innerHTML = '<span class="loading-spinner"></span> 요청 중...';
        } else {
            executeTradeBtn.textContent = '매매 실행 (GitHub Action)';
        }
    }

    init();
})();
