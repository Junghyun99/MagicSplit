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
        document.getElementById('global-channel-breakdown-tolerance-pct').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-channel-breakdown-atr-multiplier').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-multi-horizon-regime-enabled').addEventListener('change', () => {
            ConfigView.syncMultiHorizonSettings();
            saveGlobalConfigToModel();
        });
        document.getElementById('global-trend-only-enabled').addEventListener('change', () => {
            ConfigView.syncMultiHorizonSettings();
            saveGlobalConfigToModel();
        });
        document.getElementById('global-trend-entry-mode').addEventListener('change', () => {
            ConfigView.syncMultiHorizonSettings();
            saveGlobalConfigToModel();
        });
        document.getElementById('global-rebound-entry-confirm-bars').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-rebound-entry-require-midline').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-staged-rebound-probe-pct').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-staged-rebound-allow-long-sideways').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-staged-rebound-require-long-midline').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-staged-rebound-require-nonnegative-long-slope').addEventListener('change', saveGlobalConfigToModel);
        document.getElementById('global-staged-rebound-wait-probe-enabled').addEventListener('change', () => {
            ConfigView.syncMultiHorizonSettings();
            saveGlobalConfigToModel();
        });
        document.getElementById('global-staged-rebound-wait-probe-pct').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-post-liquidation-recovery-probe-enabled').addEventListener('change', () => {
            ConfigView.syncMultiHorizonSettings();
            saveGlobalConfigToModel();
        });
        document.getElementById('global-post-liquidation-early-probe-enabled').addEventListener('change', () => {
            ConfigView.syncMultiHorizonSettings();
            saveGlobalConfigToModel();
        });
        for (const id of [
            'global-post-liquidation-early-probe-pct',
            'global-post-liquidation-early-probe-confirm-bars',
            'global-post-liquidation-early-probe-max-ema-atr',
            'global-post-liquidation-early-probe-stop-atr-multiplier'
        ]) document.getElementById(id).addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-pullback-rebound-confirm-bars').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-pullback-rebound-max-wait-bars').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-long-channel-lookback').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-long-sideways-exposure-multiplier').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-long-uptrend-sideways-sell-multiplier').addEventListener('input', saveGlobalConfigToModel);
        document.getElementById('global-uptrend-profit-trailing-enabled').addEventListener('change', () => {
            ConfigView.syncMultiHorizonSettings();
            saveGlobalConfigToModel();
        });
        document.getElementById('global-uptrend-profit-recovery-add-enabled').addEventListener('change', () => {
            ConfigView.syncMultiHorizonSettings();
            saveGlobalConfigToModel();
        });
        for (const id of [
            'global-uptrend-sideways-transition-partial-sell-pct',
            'global-uptrend-sideways-transition-confirm-bars',
            'global-uptrend-profit-trailing-atr-multiplier',
            'global-uptrend-profit-trailing-max-distance-pct',
            'global-uptrend-profit-recovery-confirm-bars',
            'global-uptrend-profit-recovery-restore-pct',
            'global-uptrend-profit-recovery-max-ema-atr',
            'global-uptrend-profit-recovery-max-ema-distance-pct',
            'global-uptrend-profit-recovery-min-stop-headroom-atr',
            'global-transition-residual-atr-multiplier',
            'global-transition-residual-min-distance-pct',
            'global-transition-residual-max-distance-pct'
        ]) {
            document.getElementById(id).addEventListener('input', saveGlobalConfigToModel);
        }

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
            if (vals.channel_breakdown_tolerance_pct !== '') config.global.channel_breakdown_tolerance_pct = parseFloat(vals.channel_breakdown_tolerance_pct); else delete config.global.channel_breakdown_tolerance_pct;
            if (vals.channel_breakdown_atr_multiplier !== '') config.global.channel_breakdown_atr_multiplier = parseFloat(vals.channel_breakdown_atr_multiplier); else delete config.global.channel_breakdown_atr_multiplier;
            config.global.multi_horizon_regime_enabled = vals.multi_horizon_regime_enabled;
            config.global.trend_only_enabled = vals.trend_only_enabled;
            config.global.trend_entry_mode = vals.trend_entry_mode;
            if (vals.rebound_entry_confirm_bars !== '') config.global.rebound_entry_confirm_bars = parseInt(vals.rebound_entry_confirm_bars, 10); else delete config.global.rebound_entry_confirm_bars;
            config.global.rebound_entry_require_midline = vals.rebound_entry_require_midline;
            if (vals.staged_rebound_probe_pct !== '') config.global.staged_rebound_probe_pct = parseFloat(vals.staged_rebound_probe_pct); else delete config.global.staged_rebound_probe_pct;
            config.global.staged_rebound_allow_long_sideways = vals.staged_rebound_allow_long_sideways;
            config.global.staged_rebound_require_long_midline = vals.staged_rebound_require_long_midline;
            config.global.staged_rebound_require_nonnegative_long_slope = vals.staged_rebound_require_nonnegative_long_slope;
            config.global.staged_rebound_wait_probe_enabled = vals.staged_rebound_wait_probe_enabled;
            if (vals.staged_rebound_wait_probe_pct !== '') config.global.staged_rebound_wait_probe_pct = parseFloat(vals.staged_rebound_wait_probe_pct); else delete config.global.staged_rebound_wait_probe_pct;
            config.global.post_liquidation_recovery_probe_enabled = vals.post_liquidation_recovery_probe_enabled;
            config.global.post_liquidation_early_probe_enabled = vals.post_liquidation_early_probe_enabled;
            if (vals.post_liquidation_early_probe_pct !== '') config.global.post_liquidation_early_probe_pct = parseFloat(vals.post_liquidation_early_probe_pct); else delete config.global.post_liquidation_early_probe_pct;
            if (vals.post_liquidation_early_probe_confirm_bars !== '') config.global.post_liquidation_early_probe_confirm_bars = parseInt(vals.post_liquidation_early_probe_confirm_bars, 10); else delete config.global.post_liquidation_early_probe_confirm_bars;
            if (vals.post_liquidation_early_probe_max_ema_atr !== '') config.global.post_liquidation_early_probe_max_ema_atr = parseFloat(vals.post_liquidation_early_probe_max_ema_atr); else delete config.global.post_liquidation_early_probe_max_ema_atr;
            if (vals.post_liquidation_early_probe_stop_atr_multiplier !== '') config.global.post_liquidation_early_probe_stop_atr_multiplier = parseFloat(vals.post_liquidation_early_probe_stop_atr_multiplier); else delete config.global.post_liquidation_early_probe_stop_atr_multiplier;
            if (vals.pullback_rebound_confirm_bars !== '') config.global.pullback_rebound_confirm_bars = parseInt(vals.pullback_rebound_confirm_bars, 10); else delete config.global.pullback_rebound_confirm_bars;
            if (vals.pullback_rebound_max_wait_bars !== '') config.global.pullback_rebound_max_wait_bars = parseInt(vals.pullback_rebound_max_wait_bars, 10); else delete config.global.pullback_rebound_max_wait_bars;
            if (vals.long_channel_lookback !== '') config.global.long_channel_lookback = parseInt(vals.long_channel_lookback, 10); else delete config.global.long_channel_lookback;
            if (vals.long_sideways_exposure_multiplier !== '') config.global.long_sideways_exposure_multiplier = parseFloat(vals.long_sideways_exposure_multiplier); else delete config.global.long_sideways_exposure_multiplier;
            if (vals.long_uptrend_sideways_sell_multiplier !== '') config.global.long_uptrend_sideways_sell_multiplier = parseFloat(vals.long_uptrend_sideways_sell_multiplier); else delete config.global.long_uptrend_sideways_sell_multiplier;
            if (vals.uptrend_sideways_transition_partial_sell_pct !== '') config.global.uptrend_sideways_transition_partial_sell_pct = parseFloat(vals.uptrend_sideways_transition_partial_sell_pct); else delete config.global.uptrend_sideways_transition_partial_sell_pct;
            if (vals.uptrend_sideways_transition_confirm_bars !== '') config.global.uptrend_sideways_transition_confirm_bars = parseInt(vals.uptrend_sideways_transition_confirm_bars, 10); else delete config.global.uptrend_sideways_transition_confirm_bars;
            config.global.uptrend_profit_trailing_enabled = vals.uptrend_profit_trailing_enabled;
            config.global.uptrend_profit_recovery_add_enabled = vals.uptrend_profit_recovery_add_enabled;
            if (vals.uptrend_profit_recovery_confirm_bars !== '') config.global.uptrend_profit_recovery_confirm_bars = parseInt(vals.uptrend_profit_recovery_confirm_bars, 10); else delete config.global.uptrend_profit_recovery_confirm_bars;
            for (const key of ['uptrend_profit_trailing_atr_multiplier', 'uptrend_profit_trailing_max_distance_pct', 'uptrend_profit_recovery_restore_pct', 'uptrend_profit_recovery_max_ema_atr', 'uptrend_profit_recovery_max_ema_distance_pct', 'uptrend_profit_recovery_min_stop_headroom_atr', 'transition_residual_atr_multiplier', 'transition_residual_min_distance_pct', 'transition_residual_max_distance_pct']) {
                if (vals[key] !== '') config.global[key] = parseFloat(vals[key]); else delete config.global[key];
            }
            ConfigView.updateDiffPreview(ConfigModel.getDiff());
        }
    }

    function validateMultiHorizonConfig() {
        const global = ConfigModel.getConfig()?.global || {};
        if (global.trend_only_enabled && !global.multi_horizon_regime_enabled) {
            return 'Trend Only 모드는 장·단기 레짐이 필요합니다.';
        }
        if (!global.multi_horizon_regime_enabled) return null;
        if (global.regime_enabled !== true || global.regime_algo !== 'channel') {
            return '장·단기 레짐은 Regime Enabled와 channel 알고리즘이 필요합니다.';
        }
        if (global.long_channel_lookback !== undefined && global.long_channel_lookback < 21) {
            return '장기 채널 기간은 21봉 이상이어야 합니다.';
        }
        const exposure = global.long_sideways_exposure_multiplier;
        if (exposure !== undefined && !(exposure > 0 && exposure <= 1)) {
            return '장기 횡보 노출 배율은 0보다 크고 1 이하여야 합니다.';
        }
        if (global.long_uptrend_sideways_sell_multiplier !== undefined
            && global.long_uptrend_sideways_sell_multiplier < 1) {
            return '장기 상승·단기 횡보 익절 배율은 1 이상이어야 합니다.';
        }
        if (global.trend_entry_mode === 'rebound' || global.trend_entry_mode === 'staged_rebound') {
            if (!global.trend_only_enabled) return '반등 확인 진입은 추세 전용 모드가 필요합니다.';
            const confirm = global.rebound_entry_confirm_bars ?? 2;
            const pullbackConfirm = global.pullback_rebound_confirm_bars ?? 1;
            const maxWait = global.pullback_rebound_max_wait_bars ?? 10;
            if (!Number.isInteger(confirm) || confirm < 1) return '반등 확인 기간은 1 이상의 정수여야 합니다.';
            if (!Number.isInteger(pullbackConfirm) || pullbackConfirm < 1) return '눌림 재상승 확인 기간은 1 이상의 정수여야 합니다.';
            if (!Number.isInteger(maxWait) || maxWait < pullbackConfirm) return '눌림 최대 대기는 재상승 확인 기간 이상이어야 합니다.';
        }
        if (global.trend_entry_mode === 'staged_rebound') {
            const probePct = global.staged_rebound_probe_pct ?? 50;
            if (!(probePct > 0 && probePct < 100)) return '단계형 탐색 진입 비율은 0 초과 100 미만이어야 합니다.';
            const waitProbePct = global.staged_rebound_wait_probe_pct ?? 25;
            if (!(waitProbePct > 0 && waitProbePct < 100)) return '완화 탐색 비율은 0 초과 100 미만이어야 합니다.';
            if (global.post_liquidation_early_probe_enabled) {
                const earlyPct = global.post_liquidation_early_probe_pct ?? 20;
                const earlyBars = global.post_liquidation_early_probe_confirm_bars ?? 2;
                const earlyEmaAtr = global.post_liquidation_early_probe_max_ema_atr ?? 1;
                const earlyStopAtr = global.post_liquidation_early_probe_stop_atr_multiplier ?? 2;
                if (!(earlyPct > 0 && earlyPct < 100)) return '조기 탐색 비율은 0 초과 100 미만이어야 합니다.';
                if (!Number.isInteger(earlyBars) || earlyBars < 1) return '조기 반등 확인 기간은 1 이상의 정수여야 합니다.';
                if (!(earlyEmaAtr > 0) || !(earlyStopAtr > 0)) return '조기 탐색 ATR 설정은 양수여야 합니다.';
            }
        } else if (global.staged_rebound_wait_probe_enabled
                || global.post_liquidation_recovery_probe_enabled
                || global.post_liquidation_early_probe_enabled) {
            return '상태지속·게이트 해제 탐색은 단계형 반등 진입 방식이 필요합니다.';
        }
        const transitionPct = global.uptrend_sideways_transition_partial_sell_pct;
        if (transitionPct !== undefined && !(transitionPct >= 0 && transitionPct < 100)) {
            return '상승→횡보 선제청산 비율은 0 이상 100 미만이어야 합니다.';
        }
        if (transitionPct > 0 && !global.multi_horizon_regime_enabled) {
            return '상승→횡보 선제청산은 장·단기 레짐이 필요합니다.';
        }
        const transitionBars = global.uptrend_sideways_transition_confirm_bars;
        if (transitionBars !== undefined && (!Number.isInteger(transitionBars) || transitionBars < 1)) {
            return '상승→횡보 확인 기간은 1 이상의 정수여야 합니다.';
        }
        if (global.uptrend_profit_trailing_enabled) {
            if (!(transitionPct > 0)) return '상승 ATR 수익보호는 0보다 큰 선제청산 비율이 필요합니다.';
            const positiveKeys = ['uptrend_profit_trailing_atr_multiplier', 'uptrend_profit_trailing_max_distance_pct', 'transition_residual_atr_multiplier', 'transition_residual_min_distance_pct', 'transition_residual_max_distance_pct'];
            for (const key of positiveKeys) {
                if (global[key] !== undefined && !(global[key] > 0)) return `${key}는 양수여야 합니다.`;
            }
            const minDistance = global.transition_residual_min_distance_pct ?? 5;
            const maxDistance = global.transition_residual_max_distance_pct ?? 10;
            if (minDistance > maxDistance) return '잔량 보호 최소 거리는 최대 거리 이하여야 합니다.';
        }
        if (global.uptrend_profit_recovery_add_enabled) {
            if (!global.uptrend_profit_trailing_enabled) return '상승 복귀 확인매수는 상승 ATR 수익보호가 필요합니다.';
            const bars = global.uptrend_profit_recovery_confirm_bars ?? 2;
            if (!Number.isInteger(bars) || bars < 1) return '복귀 확인 기간은 1 이상의 정수여야 합니다.';
            const restorePct = global.uptrend_profit_recovery_restore_pct ?? 50;
            if (!(restorePct > 0 && restorePct <= 100)) return '감축대금 복원 비율은 0 초과 100 이하여야 합니다.';
            for (const key of ['uptrend_profit_recovery_max_ema_atr', 'uptrend_profit_recovery_max_ema_distance_pct', 'uptrend_profit_recovery_min_stop_headroom_atr']) {
                if (global[key] !== undefined && !(global[key] > 0)) return `${key}는 양수여야 합니다.`;
            }
        }
        const stocks = ConfigModel.getConfig()?.stocks || [];
        for (const stock of stocks) {
            const stockPct = stock.uptrend_sideways_transition_partial_sell_pct;
            if (stockPct !== undefined && !(stockPct >= 0 && stockPct < 100)) {
                return `${stock.ticker || '종목'}: 상승→횡보 선제청산 비율은 0 이상 100 미만이어야 합니다.`;
            }
            const effectivePct = stockPct !== undefined ? stockPct : (transitionPct || 0);
            if (effectivePct > 0 && !global.multi_horizon_regime_enabled) {
                return `${stock.ticker || '종목'}: 상승→횡보 선제청산은 장·단기 레짐이 필요합니다.`;
            }
            const stockBars = stock.uptrend_sideways_transition_confirm_bars;
            if (stockBars !== undefined && (!Number.isInteger(stockBars) || stockBars < 1)) {
                return `${stock.ticker || '종목'}: 상승→횡보 확인 기간은 1 이상의 정수여야 합니다.`;
            }
            const effectiveEnabled = stock.uptrend_profit_trailing_enabled !== undefined
                ? stock.uptrend_profit_trailing_enabled : global.uptrend_profit_trailing_enabled;
            if (effectiveEnabled) {
                if (!(effectivePct > 0)) return `${stock.ticker || '종목'}: 상승 ATR 수익보호는 0보다 큰 선제청산 비율이 필요합니다.`;
                const minDistance = stock.transition_residual_min_distance_pct
                    ?? global.transition_residual_min_distance_pct ?? 5;
                const maxDistance = stock.transition_residual_max_distance_pct
                    ?? global.transition_residual_max_distance_pct ?? 10;
                if (minDistance > maxDistance) return `${stock.ticker || '종목'}: 잔량 보호 최소 거리는 최대 거리 이하여야 합니다.`;
            }
            const effectiveRecovery = stock.uptrend_profit_recovery_add_enabled !== undefined
                ? stock.uptrend_profit_recovery_add_enabled : global.uptrend_profit_recovery_add_enabled;
            if (effectiveRecovery) {
                if (!effectiveEnabled) return `${stock.ticker || '종목'}: 상승 복귀 확인매수는 상승 ATR 수익보호가 필요합니다.`;
                const bars = stock.uptrend_profit_recovery_confirm_bars
                    ?? global.uptrend_profit_recovery_confirm_bars ?? 2;
                if (!Number.isInteger(bars) || bars < 1) return `${stock.ticker || '종목'}: 복귀 확인 기간은 1 이상의 정수여야 합니다.`;
                const restorePct = stock.uptrend_profit_recovery_restore_pct
                    ?? global.uptrend_profit_recovery_restore_pct ?? 50;
                if (!(restorePct > 0 && restorePct <= 100)) return `${stock.ticker || '종목'}: 감축대금 복원 비율은 0 초과 100 이하여야 합니다.`;
                for (const key of ['uptrend_profit_recovery_max_ema_atr', 'uptrend_profit_recovery_max_ema_distance_pct', 'uptrend_profit_recovery_min_stop_headroom_atr']) {
                    const value = stock[key] ?? global[key];
                    if (value !== undefined && !(value > 0)) return `${stock.ticker || '종목'}: ${key}는 양수여야 합니다.`;
                }
            }
        }
        return null;
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
        if (vals.uptrend_sideways_transition_partial_sell_pct !== '') stock.uptrend_sideways_transition_partial_sell_pct = parseFloat(vals.uptrend_sideways_transition_partial_sell_pct); else delete stock.uptrend_sideways_transition_partial_sell_pct;
        if (vals.uptrend_sideways_transition_confirm_bars !== '') stock.uptrend_sideways_transition_confirm_bars = parseInt(vals.uptrend_sideways_transition_confirm_bars, 10); else delete stock.uptrend_sideways_transition_confirm_bars;
        if (vals.uptrend_profit_trailing_enabled === '') delete stock.uptrend_profit_trailing_enabled;
        else stock.uptrend_profit_trailing_enabled = vals.uptrend_profit_trailing_enabled === 'true';
        if (vals.uptrend_profit_recovery_add_enabled === '') delete stock.uptrend_profit_recovery_add_enabled;
        else stock.uptrend_profit_recovery_add_enabled = vals.uptrend_profit_recovery_add_enabled === 'true';
        if (vals.uptrend_profit_recovery_confirm_bars !== '') stock.uptrend_profit_recovery_confirm_bars = parseInt(vals.uptrend_profit_recovery_confirm_bars, 10); else delete stock.uptrend_profit_recovery_confirm_bars;
        for (const key of ['uptrend_profit_trailing_atr_multiplier', 'uptrend_profit_trailing_max_distance_pct', 'uptrend_profit_recovery_restore_pct', 'uptrend_profit_recovery_max_ema_atr', 'uptrend_profit_recovery_max_ema_distance_pct', 'uptrend_profit_recovery_min_stop_headroom_atr', 'transition_residual_atr_multiplier', 'transition_residual_min_distance_pct', 'transition_residual_max_distance_pct']) {
            if (vals[key] !== '') stock[key] = parseFloat(vals[key]); else delete stock[key];
        }

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
        const validationError = validateMultiHorizonConfig();
        if (validationError) {
            ConfigView.showBanner(validationError, 'danger');
            return;
        }
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
