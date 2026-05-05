// docs/js/services/data-repository.js
window.DataRepository = (function () {
    'use strict';

    async function loadStatus(mode) {
        const url = `data/${mode}/status.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) return null;
            return await res.json();
        } catch (e) {
            return null;
        }
    }

    async function loadHistory(mode) {
        const url = `data/${mode}/history.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) return [];
            return await res.json();
        } catch (e) {
            return [];
        }
    }

    async function loadDecisions(mode) {
        const url = `data/${mode}/decisions.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) return [];
            return await res.json();
        } catch (e) {
            return [];
        }
    }

    return {
        loadStatus,
        loadHistory,
        loadDecisions
    };
})();
