// docs/js/controllers/config-controller.js
window.ConfigController = (function () {
    'use strict';

    let githubApi = null;
    let allTickers = [];
    let cryptoMarkets = [];   // 업비트 KRW 마켓 [코드,이름,'KRW'] — 코인 티커 검색용
    let tickerMap = {};

    async function init() {
        initAuthForm();
        bindGlobalEvents();
        bindEditorEvents();

        // 주식 티커 + 코인 마켓을 병렬 로드하되, tickerMap 초기화/채우기는 둘 다
        // 끝난 뒤 한 번만 수행한다 (개별 .then에서 채우면 loadTickers의 tickerMap={}가
        // 먼저 끝난 코인 데이터를 덮어써 한글명이 유실되는 레이스 컨디션 발생).
        Promise.all([
            DataRepository.loadTickers().catch(err => {
                console.error("Failed to load tickers.json:", err);
                return [];
            }),
            DataRepository.loadCryptoMarkets().catch(err => {
                console.error("Failed to load upbit_markets.json:", err);
                return [];
            }),
        ]).then(([tickers, crypto]) => {
            allTickers = tickers || [];
            cryptoMarkets = crypto || [];

            tickerMap = {};
            allTickers.forEach(t => { tickerMap[t[0]] = t[1]; });       // 주식: 코드 -> 별칭
            cryptoMarkets.forEach(m => { tickerMap[m[0]] = m[1]; });    // 코인: KRW-BTC -> 비트코인

            console.log(`Loaded ${allTickers.length} tickers, ${cryptoMarkets.length} Upbit markets.`);
            if (allTickers.length === 0) {
                console.warn("Tickers data is empty. Stock search will not work.");
            }

            // 이미 config가 로드돼 있으면 별칭 표시를 위해 한 번만 갱신 (깜빡임 방지)
            if (ConfigModel.getConfig()) {
                ConfigView.renderTickerList(ConfigModel.getConfig().stocks, ConfigModel.getActiveStockIndex(), onSelectTicker, getTickerDisplayName);
                const activeStock = ConfigModel.getActiveStock();
                if (activeStock) {
                    ConfigView.showTickerEditor(activeStock, ConfigModel.isPresetMode(), getTickerDisplayName(activeStock.ticker));
                }
            }
        });
    }

    function getTickerDisplayName(ticker) {
        if (!ticker) return '(New Ticker)';
        const alias = tickerMap[ticker];
        return alias ? `${alias} (${ticker})` : ticker;
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

            ConfigView.renderTickerList(ConfigModel.getConfig().stocks, null, onSelectTicker, getTickerDisplayName);

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

        ConfigView.renderTickerList(ConfigModel.getConfig().stocks, index, onSelectTicker, getTickerDisplayName);

        const stock = ConfigModel.getActiveStock();
        if (stock) {
            ConfigView.showTickerEditor(stock, ConfigModel.isPresetMode(), getTickerDisplayName(stock.ticker));
            bindLevelEvents();
            bindUptrendEvents();
        }
    }

    function bindGlobalEvents() {
        document.getElementById('global-notification').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-regime-enabled').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-max-exposure').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-trailing-drop').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-uptrend-add-reset-pct').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-trendbreak-use-sma50').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-trendbreak-chandelier-k').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-regime-algo').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-channel-lookback').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-channel-stddev-k').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-channel-slope-band-pct').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-channel-slope-up-band-pct').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-channel-breakdown-tolerance-pct').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-channel-breakdown-uptrend-only').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-channel-reentry-breakout').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-channel-uptrend-exit-ma').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-channel-reentry-line').addEventListener('change', saveGlobalConfigToModel);

        document.getElementById('add-stock-btn').addEventListener('click', () => {
            if (!ConfigModel.getConfig()) return;
            saveCurrentTickerToModel();
            const newIndex = ConfigModel.addStock();
            ConfigView.renderTickerList(ConfigModel.getConfig().stocks, ConfigModel.getActiveStockIndex(), onSelectTicker, getTickerDisplayName);
            onSelectTicker(newIndex);
        });

        document.getElementById('delete-stock-btn').addEventListener('click', () => {
            if (confirm('이 종목 설정을 삭제하시겠습니까?')) {
                ConfigModel.deleteActiveStock();
                ConfigView.renderTickerList(ConfigModel.getConfig().stocks, ConfigModel.getActiveStockIndex(), onSelectTicker, getTickerDisplayName);

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

        document.getElementById('add-uptrend-amount-btn').addEventListener('click', () => {
            ConfigView.addUptrendAmountRow();
            bindUptrendEvents();
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

            // 코인(config_crypto.json): 업비트 KRW 마켓 목록에서 검색 (주식과 동일 UX).
            // 주식 tickers 로드 여부와 무관하므로 그 체크보다 먼저 처리한다.
            if (ConfigModel.getPath().includes('crypto.json')) {
                const cryptoResults = cryptoMarkets.filter(m => {
                    const code = m[0];
                    const name = m[1];
                    return (code && code.toLowerCase().includes(query)) ||
                           (name && name.toLowerCase().includes(query));
                }).slice(0, 50).map(m => ({ ticker: m[0], alias: m[1], exchange: m[2] }));
                ConfigView.renderTickerSearchResults(cryptoResults, (selected) => {
                    tickerInput.value = selected.ticker;
                    saveCurrentTickerToModel();
                });
                return;
            }

            if (allTickers.length === 0) {
                console.warn("Still loading tickers or load failed.");
                return;
            }

            // 현재 편집 중인 설정 파일에 맞춰 필터링
            const isDomesticFile = ConfigModel.getPath().includes('domestic.json');

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

    function bindUptrendEvents() {
        const inputs = document.getElementById('uptrend-amounts-tbody').querySelectorAll('input');
        inputs.forEach(input => {
            input.removeEventListener('input', saveCurrentTickerToModel);
            input.addEventListener('input', saveCurrentTickerToModel);
        });

        const removeBtns = document.getElementById('uptrend-amounts-tbody').querySelectorAll('.remove-uptrend-btn');
        removeBtns.forEach(btn => {
            btn.removeEventListener('click', onRemoveUptrendAmount);
            btn.addEventListener('click', onRemoveUptrendAmount);
        });
    }

    function onRemoveUptrendAmount(e) {
        e.target.closest('tr').remove();
        ConfigView.reindexUptrendAmounts();
        saveCurrentTickerToModel();
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
            config.global.regime_enabled = vals.regime_enabled;
            if (vals.uptrend_add_reset_pct !== '') config.global.uptrend_add_reset_pct = parseFloat(vals.uptrend_add_reset_pct); else delete config.global.uptrend_add_reset_pct;
            config.global.trendbreak_use_sma50 = vals.trendbreak_use_sma50;
            if (vals.trendbreak_chandelier_k !== '') config.global.trendbreak_chandelier_k = parseFloat(vals.trendbreak_chandelier_k); else delete config.global.trendbreak_chandelier_k;
            if (vals.regime_algo !== '') config.global.regime_algo = vals.regime_algo; else delete config.global.regime_algo;
            if (vals.channel_lookback !== '') config.global.channel_lookback = parseInt(vals.channel_lookback, 10); else delete config.global.channel_lookback;
            if (vals.channel_stddev_k !== '') config.global.channel_stddev_k = parseFloat(vals.channel_stddev_k); else delete config.global.channel_stddev_k;
            if (vals.channel_slope_band_pct !== '') config.global.channel_slope_band_pct = parseFloat(vals.channel_slope_band_pct); else delete config.global.channel_slope_band_pct;
            if (vals.channel_slope_up_band_pct !== '') config.global.channel_slope_up_band_pct = parseFloat(vals.channel_slope_up_band_pct); else delete config.global.channel_slope_up_band_pct;
            if (vals.channel_breakdown_tolerance_pct !== '') config.global.channel_breakdown_tolerance_pct = parseFloat(vals.channel_breakdown_tolerance_pct); else delete config.global.channel_breakdown_tolerance_pct;
            if (vals.channel_breakdown_uptrend_only) config.global.channel_breakdown_uptrend_only = true; else delete config.global.channel_breakdown_uptrend_only;
            if (vals.channel_reentry_breakout) config.global.channel_reentry_breakout = true; else delete config.global.channel_reentry_breakout;
            if (vals.channel_uptrend_exit_ma) config.global.channel_uptrend_exit_ma = true; else delete config.global.channel_uptrend_exit_ma;
            if (vals.channel_reentry_line !== '') config.global.channel_reentry_line = vals.channel_reentry_line; else delete config.global.channel_reentry_line;
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
        if (vals.max_lots) stock.max_lots = parseInt(vals.max_lots, 10); else delete stock.max_lots;
        if (vals.reentry_guard_pct) stock.reentry_guard_pct = parseFloat(vals.reentry_guard_pct); else delete stock.reentry_guard_pct;
        if (vals.priority !== '') stock.priority = parseInt(vals.priority, 10); else delete stock.priority;
        if (vals.buy_threshold_pct) stock.buy_threshold_pct = parseFloat(vals.buy_threshold_pct); else delete stock.buy_threshold_pct;
        if (vals.sell_threshold_pct) stock.sell_threshold_pct = parseFloat(vals.sell_threshold_pct); else delete stock.sell_threshold_pct;
        if (vals.buy_amount) stock.buy_amount = parseFloat(vals.buy_amount); else delete stock.buy_amount;
        if (vals.max_exposure_pct) stock.max_exposure_pct = parseFloat(vals.max_exposure_pct); else delete stock.max_exposure_pct;
        if (vals.trailing_drop_pct) stock.trailing_drop_pct = parseFloat(vals.trailing_drop_pct); else delete stock.trailing_drop_pct;
        if (vals.spread_threshold_pct !== '') stock.spread_threshold_pct = parseFloat(vals.spread_threshold_pct); else delete stock.spread_threshold_pct;
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

        if (vals.uptrend_max_adds !== '') stock.uptrend_max_adds = parseInt(vals.uptrend_max_adds, 10); else delete stock.uptrend_max_adds;
        if (vals.uptrend_pullback_band_pct !== '') stock.uptrend_pullback_band_pct = parseFloat(vals.uptrend_pullback_band_pct); else delete stock.uptrend_pullback_band_pct;
        if (vals.uptrend_add_reset_pct !== '') stock.uptrend_add_reset_pct = parseFloat(vals.uptrend_add_reset_pct); else delete stock.uptrend_add_reset_pct;
        if (vals.trendbreak_partial_sell_pct !== '') stock.trendbreak_partial_sell_pct = parseFloat(vals.trendbreak_partial_sell_pct); else delete stock.trendbreak_partial_sell_pct;
        if (vals.trendbreak_trailing_drop_pct !== '') stock.trendbreak_trailing_drop_pct = parseFloat(vals.trendbreak_trailing_drop_pct); else delete stock.trendbreak_trailing_drop_pct;

        const cleanUptrendAmounts = filterNaNs(vals.uptrendAmounts);
        if (cleanUptrendAmounts !== undefined) stock.uptrend_add_amounts = cleanUptrendAmounts; else delete stock.uptrend_add_amounts;

        const activeIdx = ConfigModel.getActiveStockIndex();
        const lis = document.getElementById('ticker-list').querySelectorAll('li');
        const displayName = getTickerDisplayName(stock.ticker);
        if (lis[activeIdx]) {
            lis[activeIdx].textContent = displayName;
        }
        document.getElementById('current-ticker-title').textContent = stock.ticker ? (ConfigModel.isPresetMode() ? `${displayName} 프리셋` : `${displayName} 설정`) : (ConfigModel.isPresetMode() ? '새 프리셋' : '새 종목 설정');

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
