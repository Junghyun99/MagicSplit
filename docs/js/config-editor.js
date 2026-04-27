// docs/js/config-editor.js (Entry Point)
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', () => {
        if (window.ConfigController) {
            window.ConfigController.init();
        }
    });
})();
