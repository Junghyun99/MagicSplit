// docs/js/controllers/config-controller.js
window.ConfigController = (function () {
    'use strict';

    let githubApi = null;
    let allTickers = [];

    async function init() {
        initAuthForm();
        bindGlobalEvents();
        bindEditorEvents();

        // Load tickers for search
        DataRepository.loadTickers().then(data => {
            allTickers = data;
            console.log(`Loaded ${allTickers.length} tickers for search.`);
            if (allTickers.length === 0) {
                console.warn("Tickers data is empty. Search will not work.");
            }
        }).catch(err => {
            console.error("Failed to load tickers.json:", err);
        });
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
                ConfigView.showBanner('토큰, Owner, Repo, File Path를 모두 입력해주세요.', 'danger');
                return;
            }

            localStorage.setItem('githubToken', token);
            localStorage.setItem('githubOwner', owner);
            localStorage.setItem('githubRepo', repo);
            localStorage.setItem('githubConfigPath', path);

            githubApi = new GitHubAPI(token, owner, repo);
            await loadConfig(path);
        });
    }

    async function loadConfig(path) {
        ConfigView.showBanner('설정을 불러오는 중...', 'info');
        try {
            const { content, sha } = await githubApi.getFile(path);
            ConfigModel.setConfigData(path, content, sha);

            ConfigView.showConfigSection(ConfigModel.isPresetMode());
            ConfigView.renderGlobalConfig(ConfigModel.getConfig().global);

            ConfigView.renderTickerList(ConfigModel.getConfig().stocks, null, onSelectTicker);

            if (ConfigModel.getConfig().stocks.length > 0) {
                onSelectTicker(0);
            } else {
                ConfigView.hideTickerEditor();
            }

            ConfigView.showBanner('설정을 성공적으로 불러왔습니다.', 'success');
            ConfigView.updateDiffPreview(ConfigModel.getDiff());
        } catch (e) {
            ConfigView.showBanner(`오류: ${e.message}`, 'danger');
            console.error(e);
        }
    }

    function onSelectTicker(index) {
        saveCurrentTickerToModel();
        ConfigModel.setActiveStockIndex(index);

        ConfigView.renderTickerList(ConfigModel.getConfig().stocks, index, onSelectTicker);

        const stock = ConfigModel.getActiveStock();
        if (stock) {
            ConfigView.showTickerEditor(stock, ConfigModel.isPresetMode());
            bindLevelEvents();
        }
    }

    function bindGlobalEvents() {
        document.getElementById('global-notification').addEventListener('change', saveGlobalConfigToModel);

        document.getElementById('add-stock-btn').addEventListener('click', () => {
            if (!ConfigModel.getConfig()) return;
            saveCurrentTickerToModel();
            const newIndex = ConfigModel.addStock();
            ConfigView.renderTickerList(ConfigModel.getConfig().stocks, ConfigModel.getActiveStockIndex(), onSelectTicker);
            onSelectTicker(newIndex);
        });

        document.getElementById('delete-stock-btn').addEventListener('click', () => {
            if (confirm('이 종목 설정을 삭제하시겠습니까?')) {
                ConfigModel.deleteActiveStock();
                ConfigView.renderTickerList(ConfigModel.getConfig().stocks, ConfigModel.getActiveStockIndex(), onSelectTicker);

                if (ConfigModel.getConfig().stocks.length > 0) {
                    onSelectTicker(0);
                } else {
                    ConfigView.hideTickerEditor();
                }
                ConfigView.updateDiffPreview(ConfigModel.getDiff());
            }
        });

        document.getElementById('add-level-btn').addEventListener('click', () => {
            ConfigView.addLevelRow();
            bindLevelEvents();
            saveCurrentTickerToModel();
        });

        document.getElementById('save-config-btn').addEventListener('click', saveConfigToGithub);
    }

    function bindEditorEvents() {
        const editorInputs = document.getElementById('ticker-editor-pane').querySelectorAll('input:not(.level-table-input), select');
        editorInputs.forEach(input => {
            input.addEventListener('input', saveCurrentTickerToModel);
            input.addEventListener('change', saveCurrentTickerToModel);
        });

        const tickerInput = document.getElementById('edit-ticker');
        tickerInput.addEventListener('input', (e) => {
            if (ConfigModel.isPresetMode()) return;

            const query = e.target.value.trim().toLowerCase();
            if (query.length < 1) {
                ConfigView.hideTickerSearchResults();
                return;
            }

            if (allTickers.length === 0) {
                console.warn("Still loading tickers or load failed.");
                return;
            }

            // 현재 편집 중인 설정 파일에 맞춰 필터링
            const isDomesticFile = ConfigModel.getPath() === 'config_domestic.json';

            const results = allTickers.filter(t => {
                const ticker = t[0];
                const alias = t[1];
                const exchange = t[2];

                // 검색어 일치 확인
                const matches = (alias && alias.toLowerCase().includes(query)) ||
                                (ticker && ticker.toLowerCase().includes(query));
                
                if (!matches) return false;

                // 마켓 타입 필터링 (KS, KQ는 국내)
                const isDomesticTicker = (exchange === 'KS' || exchange === 'KQ');
                return isDomesticFile ? isDomesticTicker : !isDomesticTicker;
            }).slice(0, 50).map(t => ({
                ticker: t[0],
                alias: t[1],
                exchange: t[2]
            }));

            console.log(`Search query: ${query}, Results: ${results.length} (${isDomesticFile ? 'Domestic' : 'Overseas'})`);
            ConfigView.renderTickerSearchResults(results, (selected) => {
                tickerInput.value = selected.ticker;
                saveCurrentTickerToModel();
            });
        });

        document.addEventListener('click', (e) => {
            if (!e.target.closest('.form-group')) {
                ConfigView.hideTickerSearchResults();
            }
        });
    }

    function bindLevelEvents() {
        const inputs = document.getElementById('levels-tbody').querySelectorAll('input');
        inputs.forEach(input => {
            input.removeEventListener('input', saveCurrentTickerToModel);
            input.addEventListener('input', saveCurrentTickerToModel);
        });

        const removeBtns = document.getElementById('levels-tbody').querySelectorAll('.remove-level-btn');
        removeBtns.forEach(btn => {
            btn.removeEventListener('click', onRemoveLevel);
            btn.addEventListener('click', onRemoveLevel);
        });
    }

    function onRemoveLevel(e) {
        e.target.closest('tr').remove();
        ConfigView.reindexLevels();
        saveCurrentTickerToModel();
    }

    function saveGlobalConfigToModel() {
        const config = ConfigModel.getConfig();
        if (!config) return;
        if (!config.global) config.global = {};

        const vals = ConfigView.getGlobalValues();
        if (vals) {
            config.global.notification_enabled = vals.notification_enabled;
            if (vals.max_exposure_pct) config.global.max_exposure_pct = parseFloat(vals.max_exposure_pct); else delete config.global.max_exposure_pct;
            if (vals.trailing_drop_pct) config.global.trailing_drop_pct = parseFloat(vals.trailing_drop_pct); else delete config.global.trailing_drop_pct;
            ConfigView.updateDiffPreview(ConfigModel.getDiff());
        }
    }

    function saveCurrentTickerToModel() {
        const stock = ConfigModel.getActiveStock();
        if (!stock) return;

        const vals = ConfigView.getEditorValues();

        stock.ticker = vals.ticker;
        delete stock.exchange;
        if (vals.preset) stock.preset = vals.preset; else delete stock.preset;
        if (vals.max_lots) stock.max_lots = parseInt(vals.max_lots, 10);
        if (vals.reentry_guard_pct) stock.reentry_guard_pct = parseFloat(vals.reentry_guard_pct); else delete stock.reentry_guard_pct;
        if (vals.buy_threshold_pct) stock.buy_threshold_pct = parseFloat(vals.buy_threshold_pct); else delete stock.buy_threshold_pct;
        if (vals.sell_threshold_pct) stock.sell_threshold_pct = parseFloat(vals.sell_threshold_pct); else delete stock.sell_threshold_pct;
        if (vals.buy_amount) stock.buy_amount = parseFloat(vals.buy_amount); else delete stock.buy_amount;
        if (vals.max_exposure_pct) stock.max_exposure_pct = parseFloat(vals.max_exposure_pct); else delete stock.max_exposure_pct;
        if (vals.trailing_drop_pct) stock.trailing_drop_pct = parseFloat(vals.trailing_drop_pct); else delete stock.trailing_drop_pct;
        stock.enabled = vals.enabled;

        const filterNaNs = (arr) => {
            let lastValid = -1;
            for (let i = 0; i < arr.length; i++) {
                if (!isNaN(arr[i])) lastValid = i;
            }
            if (lastValid === -1) return undefined;
            return arr.slice(0, lastValid + 1).map(x => isNaN(x) ? 0 : x);
        };

        const cleanBuyPcts = filterNaNs(vals.buyPcts);
        const cleanBuyAmts = filterNaNs(vals.buyAmts);
        const cleanSellPcts = filterNaNs(vals.sellPcts);
        const cleanTrailingDrops = filterNaNs(vals.trailingDrops);

        if (cleanBuyPcts !== undefined) stock.buy_threshold_pcts = cleanBuyPcts; else delete stock.buy_threshold_pcts;
        if (cleanBuyAmts !== undefined) stock.buy_amounts = cleanBuyAmts; else delete stock.buy_amounts;
        if (cleanSellPcts !== undefined) stock.sell_threshold_pcts = cleanSellPcts; else delete stock.sell_threshold_pcts;
        if (cleanTrailingDrops !== undefined) stock.trailing_drop_pcts = cleanTrailingDrops; else delete stock.trailing_drop_pcts;

        const activeIdx = ConfigModel.getActiveStockIndex();
        const lis = document.getElementById('ticker-list').querySelectorAll('li');
        if (lis[activeIdx]) {
            lis[activeIdx].textContent = stock.ticker || '(New Ticker)';
        }
        document.getElementById('current-ticker-title').textContent = stock.ticker ? (ConfigModel.isPresetMode() ? `${stock.ticker} 프리셋` : `${stock.ticker} 설정`) : (ConfigModel.isPresetMode() ? '새 프리셋' : '새 종목 설정');

        ConfigView.updateDiffPreview(ConfigModel.getDiff());
    }

    async function saveConfigToGithub() {
        if (!githubApi || !ConfigModel.getSha() || !ConfigModel.getConfig()) return;

        saveCurrentTickerToModel();
        ConfigView.setSaveButtonState(true);

        try {
            const contentStr = ConfigModel.getSaveContent();
            const msg = `chore(config): update rules via web editor`;

            await githubApi.updateFile(ConfigModel.getPath(), contentStr, msg, ConfigModel.getSha());

            ConfigView.showBanner('성공적으로 저장되었습니다! GitHub Actions가 스케줄에 따라 실행될 때 적용됩니다.', 'success');

            setTimeout(() => {
                ConfigView.setSaveButtonState(false);
                loadConfig(ConfigModel.getPath());
            }, 1500);

        } catch (e) {
            ConfigView.showBanner(`저장 실패: ${e.message}`, 'danger');
            ConfigView.setSaveButtonState(false);
            console.error(e);
        }
    }

    return { init };
})();
