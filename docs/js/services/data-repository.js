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

    async function loadRegimeEvents(mode) {
        const url = `data/${mode}/regime_events.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) return [];
            return await res.json();
        } catch (e) {
            return [];
        }
    }

    // 종목별 차트는 클릭 시점에 개별 로드한다 (초기 로딩에 영향 없음).
    // 파일명 정규화 규칙은 백엔드 JsonRepository._chart_filename과 일치해야 한다.
    async function loadChartSeries(mode, ticker) {
        const safe = String(ticker).replace(/[^A-Za-z0-9._-]/g, '_');
        const url = `data/${mode}/charts/${safe}.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) return null;
            return await res.json();
        } catch (e) {
            return null;
        }
    }

    async function loadTickers() {
        const url = `data/tickers.json?t=${Date.now()}`;
        try {
            const res = await fetch(url);
            if (!res.ok) return [];
            return await res.json();
        } catch (e) {
            return [];
        }
    }

    // 업비트 KRW 마켓 목록 (코인 티커 검색용). tickers.json과 동일한 [코드,이름,거래소] 형식.
    async function loadCryptoMarkets() {
        const url = `data/upbit_markets.json?t=${Date.now()}`;
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
        loadDecisions,
        loadRegimeEvents,
        loadChartSeries,
        loadTickers,
        loadCryptoMarkets
    };
})();
