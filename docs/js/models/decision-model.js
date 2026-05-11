// docs/js/models/decision-model.js
window.DecisionModel = (function () {
    'use strict';

    let decisions = [];

    function setDecisions(data) {
        // 백테스트 등에서 날짜 역순으로 올 수 있으므로 정렬 보장 (최신순)
        decisions = [...data].reverse();
    }

    function getDecisions() {
        return decisions;
    }

    return {
        setDecisions,
        getDecisions
    };
})();
