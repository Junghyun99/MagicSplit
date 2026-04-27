// docs/js/config-editor.js
(function () {
    'use strict';

    let originalConfigObj = null;
    let configSha = null;
    let githubApi = null;
    const CONFIG_PATH = 'config.json';

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

        tokenInput.value = localStorage.getItem('githubToken') || '';
        ownerInput.value = localStorage.getItem('githubOwner') || 'Junghyun99';
        repoInput.value = localStorage.getItem('githubRepo') || 'MagicSplit';

        document.getElementById('load-config-btn').addEventListener('click', async () => {
            const token = tokenInput.value.trim();
            const owner = ownerInput.value.trim();
            const repo = repoInput.value.trim();

            if (!token || !owner || !repo) {
                showBanner('토큰, Owner, Repo를 모두 입력해주세요.', 'danger');
                return;
            }

            localStorage.setItem('githubToken', token);
            localStorage.setItem('githubOwner', owner);
            localStorage.setItem('githubRepo', repo);

            githubApi = new GitHubAPI(token, owner, repo);
            await loadConfig();
        });
    }

    async function loadConfig() {
        showBanner('설정을 불러오는 중...', 'info');
        try {
            const { content, sha } = await githubApi.getFile(CONFIG_PATH);
            originalConfigObj = JSON.parse(content);
            configSha = sha;
            
            renderConfigForm(originalConfigObj);
            document.getElementById('config-editor-section').style.display = '';
            showBanner('설정을 성공적으로 불러왔습니다.', 'success');
            updateDiffPreview();
        } catch (e) {
            showBanner(`오류: ${e.message}`, 'danger');
            console.error(e);
        }
    }

    function createStockCard(stock, index) {
        const card = document.createElement('div');
        card.className = 'stock-rule-card';
        card.innerHTML = `
            <button type="button" class="remove-btn" title="삭제">삭제</button>
            <div class="form-row">
                <div class="form-group">
                    <label>Ticker</label>
                    <input type="text" class="form-control s-ticker" value="${stock.ticker || ''}">
                </div>
                <div class="form-group">
                    <label>Exchange</label>
                    <input type="text" class="form-control s-exchange" value="${stock.exchange || ''}" placeholder="NAS">
                </div>
                <div class="form-group">
                    <label>Market Type</label>
                    <select class="form-control s-market">
                        <option value="overseas" ${stock.market_type === 'overseas' || !stock.market_type ? 'selected' : ''}>Overseas</option>
                        <option value="domestic" ${stock.market_type === 'domestic' ? 'selected' : ''}>Domestic</option>
                    </select>
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>매수 트리거 (%)</label>
                    <input type="number" step="0.1" class="form-control s-buy-pct" value="${stock.buy_threshold_pct !== undefined ? stock.buy_threshold_pct : -5.0}">
                </div>
                <div class="form-group">
                    <label>매도 트리거 (%)</label>
                    <input type="number" step="0.1" class="form-control s-sell-pct" value="${stock.sell_threshold_pct !== undefined ? stock.sell_threshold_pct : 10.0}">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>1차 매수 금액/수량</label>
                    <input type="number" class="form-control s-buy-amt" value="${stock.buy_amount || 500}">
                </div>
                <div class="form-group">
                    <label>최대 차수</label>
                    <input type="number" class="form-control s-max-lots" value="${stock.max_lots || 10}">
                </div>
                <div class="form-group" style="display:flex; flex-direction:column; justify-content:center; align-items:center;">
                    <label>활성화</label>
                    <input type="checkbox" class="s-enabled" ${stock.enabled !== false ? 'checked' : ''} style="width:20px; height:20px;">
                </div>
            </div>
        `;

        card.querySelector('.remove-btn').addEventListener('click', () => {
            card.remove();
            updateDiffPreview();
        });

        const inputs = card.querySelectorAll('input, select');
        inputs.forEach(input => {
            input.addEventListener('input', updateDiffPreview);
            input.addEventListener('change', updateDiffPreview);
        });

        return card;
    }

    function renderConfigForm(config) {
        const globalInterval = document.getElementById('global-interval');
        globalInterval.value = config.global?.check_interval_minutes || 60;
        globalInterval.addEventListener('input', updateDiffPreview);

        const container = document.getElementById('stocks-container');
        container.innerHTML = '';

        const stocks = config.stocks || [];
        stocks.forEach((stock, idx) => {
            container.appendChild(createStockCard(stock, idx));
        });

        document.getElementById('add-stock-btn').onclick = () => {
            container.appendChild(createStockCard({}, container.children.length));
            updateDiffPreview();
        };
    }

    function generateCurrentConfig() {
        const newConfig = {
            stocks: [],
            global: {
                check_interval_minutes: parseInt(document.getElementById('global-interval').value || '60', 10)
            }
        };

        const cards = document.querySelectorAll('.stock-rule-card');
        cards.forEach(card => {
            const stock = {};
            stock.ticker = card.querySelector('.s-ticker').value.trim();
            if (!stock.ticker) return;

            const exchange = card.querySelector('.s-exchange').value.trim();
            if (exchange) stock.exchange = exchange;
            
            stock.market_type = card.querySelector('.s-market').value;
            
            const buyPct = card.querySelector('.s-buy-pct').value;
            stock.buy_threshold_pct = buyPct ? parseFloat(buyPct) : -5.0;

            const sellPct = card.querySelector('.s-sell-pct').value;
            stock.sell_threshold_pct = sellPct ? parseFloat(sellPct) : 10.0;

            const buyAmt = card.querySelector('.s-buy-amt').value;
            stock.buy_amount = buyAmt ? parseFloat(buyAmt) : 500;

            const maxLots = card.querySelector('.s-max-lots').value;
            stock.max_lots = maxLots ? parseInt(maxLots, 10) : 10;

            stock.enabled = card.querySelector('.s-enabled').checked;

            newConfig.stocks.push(stock);
        });

        return newConfig;
    }

    function updateDiffPreview() {
        if (!originalConfigObj) return;
        const newConfig = generateCurrentConfig();
        
        // Simple visual diff by comparing formatted JSON
        const origStr = JSON.stringify(originalConfigObj, null, 4);
        const newStr = JSON.stringify(newConfig, null, 4);

        if (origStr === newStr) {
            document.getElementById('diff-preview').textContent = "변경 사항 없음";
            document.getElementById('save-config-btn').disabled = true;
        } else {
            document.getElementById('diff-preview').textContent = newStr;
            document.getElementById('save-config-btn').disabled = false;
        }
    }

    async function saveConfig() {
        if (!githubApi || !configSha) return;

        const btn = document.getElementById('save-config-btn');
        btn.disabled = true;
        btn.textContent = "저장 중...";

        try {
            const newConfig = generateCurrentConfig();
            const contentStr = JSON.stringify(newConfig, null, 4) + '\n';
            
            const msg = `chore(config): update rules via web editor`;

            await githubApi.updateFile(CONFIG_PATH, contentStr, msg, configSha);
            
            showBanner('성공적으로 저장되었습니다! GitHub Actions가 스케줄에 따라 실행될 때 적용됩니다.', 'success');
            
            // Reload to get new sha
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
        document.getElementById('save-config-btn').addEventListener('click', saveConfig);
    }

    init();
})();
