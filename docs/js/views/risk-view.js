// docs/js/views/risk-view.js
window.RiskView = (function () {
    'use strict';

    const { escapeHtml: esc, formatCurrency } = window.FormatUtils;

    function renderCashRatio(cashRatio, mode) {
        const container = document.getElementById('cash-ratio-container');
        if (!container) return;

        const ratio = Math.max(0, Math.min(100, cashRatio));
        const stockRatio = 100 - ratio;
        
        let colorClass = 'success';
        if (ratio < 5) colorClass = 'danger';
        else if (ratio < 10) colorClass = 'warning';

        const html = `
            <div class="risk-metric">
                <span class="risk-label">현금 비중</span>
                <span class="risk-value text-${colorClass}">${ratio.toFixed(2)}%</span>
            </div>
            <div class="progress-bar-container">
                <div class="progress-bar bg-primary" style="width: ${stockRatio}%" title="주식 비중: ${stockRatio.toFixed(2)}%"></div>
                <div class="progress-bar bg-${colorClass}" style="width: ${ratio}%" title="현금 비중: ${ratio.toFixed(2)}%"></div>
            </div>
            <div class="risk-legend">
                <span><span class="legend-dot bg-primary"></span>주식</span>
                <span><span class="legend-dot bg-${colorClass}"></span>현금</span>
            </div>
        `;
        container.innerHTML = html;
    }

    function renderTickerConcentration(concentration, mode) {
        const container = document.getElementById('ticker-concentration-container');
        if (!container) return;

        if (!concentration || concentration.length === 0) {
            container.innerHTML = '<div class="empty-state">보유 종목이 없습니다.</div>';
            return;
        }

        const maxWeight = Math.max(...concentration.map(c => c.weight), 1);

        const listHtml = concentration.map(c => {
            const barWidth = (c.weight / maxWeight) * 100;
            const barClass = c.isWarning ? 'bg-warning' : 'bg-primary';
            const valueClass = c.isWarning ? 'text-warning' : '';
            return `
                <div class="concentration-item">
                    <div class="concentration-info">
                        <span class="concentration-name" title="${esc(c.ticker)}">${esc(c.alias)}</span>
                        <span class="concentration-value ${valueClass}">${c.weight.toFixed(2)}%</span>
                    </div>
                    <div class="progress-bar-container slim">
                        <div class="progress-bar ${barClass}" style="width: ${barWidth}%"></div>
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = `<div class="concentration-list">${listHtml}</div>`;
    }

    function renderLevelDist(levelDist) {
        const container = document.getElementById('level-dist-container');
        if (!container) return;

        if (!levelDist || levelDist.length === 0) {
            container.innerHTML = '<div class="empty-state">보유 종목이 없습니다.</div>';
            return;
        }

        const maxCount = Math.max(...levelDist.map(d => d.count), 1);

        const barsHtml = levelDist.map(d => {
            const height = (d.count / maxCount) * 100;
            let barClass = 'bg-primary';
            if (d.level >= 8) barClass = 'bg-danger';
            else if (d.level >= 4) barClass = 'bg-warning';

            return `
                <div class="histogram-bar-wrapper">
                    <div class="histogram-count">${d.count}</div>
                    <div class="histogram-bar-container">
                        <div class="histogram-bar ${barClass}" style="height: ${height}%" title="Lv${d.level}: ${d.count}개"></div>
                    </div>
                    <div class="histogram-label">Lv${d.level}</div>
                </div>
            `;
        }).join('');

        container.innerHTML = `<div class="histogram">${barsHtml}</div>`;
    }

    function showRiskSection() {
        const section = document.getElementById('risk-section');
        if (section) section.style.display = '';
    }

    function hideRiskSection() {
        const section = document.getElementById('risk-section');
        if (section) section.style.display = 'none';
    }

    function renderNextLevelNeeds(nextLevelNeeds, maxExposure, currentInvested, mode) {
        const container = document.getElementById('next-level-needs-container');
        if (!container) return;

        const fmt = (val) => window.DashboardView.formatCurrency(val, mode);
        
        const totalPotential = Math.max(maxExposure, currentInvested + nextLevelNeeds, 1);
        const investedPct = (currentInvested / totalPotential) * 100;
        const needsPct = (nextLevelNeeds / totalPotential) * 100;
        const remainingPct = Math.max(0, 100 - investedPct - needsPct);

        const html = `
            <div class="risk-metric">
                <span class="risk-label">차기 매수 소요 자금</span>
                <span class="risk-value text-warning">${fmt(nextLevelNeeds)}</span>
            </div>
            <div class="progress-bar-container">
                <div class="progress-bar bg-primary" style="width: ${investedPct}%" title="현재 투입: ${fmt(currentInvested)}"></div>
                <div class="progress-bar bg-warning" style="width: ${needsPct}%" title="차기 소요: ${fmt(nextLevelNeeds)}"></div>
                <div class="progress-bar bg-secondary" style="width: ${remainingPct}%" title="잠재적 여유: ${fmt(totalPotential - currentInvested - nextLevelNeeds)}"></div>
            </div>
            <div class="risk-legend">
                <span><span class="legend-dot bg-primary"></span>현재 투입</span>
                <span><span class="legend-dot bg-warning"></span>차기 소요</span>
                <span><span class="legend-dot bg-secondary"></span>최대 노출 여유</span>
            </div>
            <div class="risk-footer-info">
                이론적 최대 노출액: ${fmt(maxExposure)}
            </div>
        `;
        container.innerHTML = html;
    }

    return {
        renderCashRatio,
        renderNextLevelNeeds,
        renderTickerConcentration,
        renderLevelDist,
        showRiskSection,
        hideRiskSection
    };
})();
