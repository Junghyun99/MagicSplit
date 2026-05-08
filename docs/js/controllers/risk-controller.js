// docs/js/controllers/risk-controller.js
window.RiskController = (function () {
    'use strict';

    function renderRisk() {
        const metrics = window.DashboardModel.calculateRiskMetrics();
        const currencyMode = window.DashboardModel.getCurrencyMode();

        if (!metrics) {
            return;
        }

        window.RiskView.renderRiskHealth(metrics.riskSummary);
        window.RiskView.renderAlerts(metrics.riskSummary.alerts, metrics.riskSummary.sync_error);

        window.RiskView.renderCashRatio(metrics.cashRatio, currencyMode);
        window.RiskView.renderNextLevelNeeds(
            metrics.nextLevelNeeds,
            metrics.maxPotentialExposure,
            metrics.totalValue - (metrics.cashRatio * metrics.totalValue / 100),
            currencyMode
        );
        window.RiskView.renderTickerConcentration(metrics.tickerConcentration, currencyMode);
        window.RiskView.renderLevelDist(metrics.levelDist);
        window.RiskView.renderStalePositions(metrics.staleInfo);
    }

    return {
        renderRisk
    };
})();
