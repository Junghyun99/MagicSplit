// docs/js/models/config-model.js
window.ConfigModel = (function () {
    'use strict';

    let originalConfigObj = null;
    let currentConfigObj = null;
    let configSha = null;
    let currentConfigPath = null;
    let activeStockIndex = null;

    function setConfigData(path, content, sha) {
        currentConfigPath = path;
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
        } else {
            currentConfigObj = JSON.parse(JSON.stringify(originalConfigObj));
            if (!currentConfigObj.stocks) currentConfigObj.stocks = [];
        }
        activeStockIndex = null;
    }

    function getConfig() {
        return currentConfigObj;
    }

    function getSha() {
        return configSha;
    }

    function getPath() {
        return currentConfigPath;
    }

    function isPresetMode() {
        return currentConfigPath === 'presets.json';
    }

    function setActiveStockIndex(index) {
        activeStockIndex = index;
    }

    function getActiveStockIndex() {
        return activeStockIndex;
    }

    function getActiveStock() {
        if (activeStockIndex === null || !currentConfigObj || !currentConfigObj.stocks[activeStockIndex]) return null;
        return currentConfigObj.stocks[activeStockIndex];
    }

    function addStock() {
        if (!currentConfigObj) return;
        const market_type = currentConfigPath === 'config_domestic.json' ? 'domestic' : 'overseas';
        currentConfigObj.stocks.push({ ticker: '', market_type, enabled: true, max_lots: 10 });
        return currentConfigObj.stocks.length - 1;
    }

    function deleteActiveStock() {
        if (activeStockIndex === null || !currentConfigObj) return false;
        currentConfigObj.stocks.splice(activeStockIndex, 1);
        activeStockIndex = null;
        return true;
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

    function getSaveObject() {
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
        return newObjToSave;
    }

    function getDiff() {
        if (!originalConfigObj || !currentConfigObj) return { hasChanges: false, diffText: '변경 사항 없음' };
        
        const newObjToSave = getSaveObject();
        const origStr = stringifyConfig(originalConfigObj);
        const newStr = stringifyConfig(newObjToSave);

        if (origStr === newStr) {
            return { hasChanges: false, diffText: '변경 사항 없음' };
        } else {
            return { hasChanges: true, diffText: newStr };
        }
    }

    function getSaveContent() {
        return stringifyConfig(getSaveObject()) + '\n';
    }

    return {
        setConfigData,
        getConfig,
        getSha,
        getPath,
        isPresetMode,
        setActiveStockIndex,
        getActiveStockIndex,
        getActiveStock,
        addStock,
        deleteActiveStock,
        getDiff,
        getSaveContent
    };
})();
