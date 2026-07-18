// docs/js/utils/format-utils.js
window.FormatUtils = (function () {
    'use strict';

    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatTickerLabel(ticker, alias) {
        if (!alias || alias === ticker) return ticker;
        return `${alias} (${ticker})`;
    }

    // 원화(KRW) 마켓 여부. 국내주식과 코인(업비트 원화마켓)은 KRW, 해외는 USD.
    function isKrwMode(mode) {
        return mode === 'domestic' || mode === 'crypto';
    }

    return { escapeHtml, formatTickerLabel, isKrwMode };
})();
