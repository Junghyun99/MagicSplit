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

    return { escapeHtml, formatTickerLabel };
})();
