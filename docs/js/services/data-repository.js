// docs/js/services/data-repository.js
window.DataRepository = (function () {
    'use strict';

    async function loadStatus(mode) {
        const url = `data/${mode}/status.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            console.error(`Failed to load status (${mode}):`, e);
            return null; // Signals offline/error
        }
    }

    async function loadHistory(mode) {
        const url = `data/${mode}/history.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (e) {
            console.error(`Failed to load history (${mode}):`, e);
            return null;
        }
    }

    return {
        loadStatus,
        loadHistory
    };
})();
