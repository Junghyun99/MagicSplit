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

    function renderRiskHealth(riskSummary) {
        const container = document.getElementById('risk-health-container');
        if (!container) return;

        const score = riskSummary.risk_score;
        let status = '안전';
        let colorClass = 'text-success';

        if (score < 50) {
            status = '위험';
            colorClass = 'text-danger';
        } else if (score < 80) {
            status = '주의';
            colorClass = 'text-warning';
        }

        let deductionsHtml = '';
        if (riskSummary.deductions && riskSummary.deductions.length > 0) {
            const items = riskSummary.deductions.map(d => `
                <div class="deduction-item">
                    <span class="deduction-reason">${esc(d.reason)}</span>
                    <span class="deduction-points">-${d.points}</span>
                </div>
            `).join('');
            deductionsHtml = `
                <div class="health-deductions">
                    <div class="deduction-header-mini">감점 요인</div>
                    ${items}
                </div>
            `;
        }

        container.innerHTML = `
            <div class="health-label">리스크 상태</div>
            <div class="health-score ${colorClass}">${score}</div>
            <div class="health-status ${colorClass}">${status}</div>
            ${deductionsHtml}
        `;
    }

    function renderAlerts(alerts, syncError) {
        const container = document.getElementById('risk-alerts-container');
        if (!container) return;

        let alertsHtml = '';
        if (alerts && alerts.length > 0) {
            const listHtml = alerts.map(alert => {
                const isDanger = alert.includes('불일치') || alert.includes('위험') || alert.includes('중단');
                return `<div class="alert-item ${isDanger ? 'danger' : ''}">${alert}</div>`;
            }).join('');
            alertsHtml = `<div class="alerts-list">${listHtml}</div>`;
        } else {
            alertsHtml = `
                <div class="empty-alerts">
                    <span>✅</span> 모든 리스크 지표가 정상 범위 내에 있습니다.
                </div>
            `;
        }

        container.innerHTML = `
            <h2>리스크 알림판</h2>
            ${alertsHtml}
        `;
    }

    function renderTickerConcentration(concentration, mode) {
        const container = document.getElementById('ticker-concentration-container');
        if (!container) return;

        if (!concentration || concentration.length === 0) {
            container.innerHTML = '<div class="empty-state">보유 종목이 없습니다.</div>';
            return;
        }

        // Sort by weight descending
        const sorted = [...concentration].sort((a, b) => b.weight - a.weight);
        const maxWeight = Math.max(...sorted.map(c => c.weight), 15); // At least 15 for scale

        const listHtml = sorted.map(c => {
            const barWidth = (c.weight / maxWeight) * 100;
            const thresholdPos = (15 / maxWeight) * 100;
            
            const isOver = c.weight > 15;
            const barClass = isOver ? 'bg-danger' : 'bg-primary';
            const valueClass = isOver ? 'text-danger' : '';

            return `
                <div class="concentration-item">
                    <div class="concentration-info">
                        <span class="concentration-name" title="${esc(c.ticker)}">${esc(c.alias)}</span>
                        <span class="concentration-value ${valueClass}">${c.weight.toFixed(2)}%</span>
                    </div>
                    <div class="progress-bar-container slim" style="position:relative">
                        <div class="progress-bar ${barClass}" style="width: ${barWidth}%"></div>
                        <div class="threshold-marker" style="left: ${thresholdPos}%; position: absolute; top: 0; bottom: 0; width: 2px; background: rgba(239, 68, 68, 0.4); z-index: 1;" title="임계치: 15%"></div>
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

    function renderStalePositions(staleInfo) {
        const container = document.getElementById('stale-positions-container');
        if (!container) return;

        // Filter to show only Caution (15+) or Serious (30+) levels
        const filteredStale = (staleInfo || []).filter(item => item.days_stale >= 15);

        if (filteredStale.length === 0) {
            container.innerHTML = '<div class="empty-state">주의 이상의 정체 종목이 없습니다.</div>';
            return;
        }

        const listHtml = filteredStale.map(item => {
            let statusClass = 'text-success'; // Should not happen with filtering but kept for safety
            if (item.days_stale >= 30) statusClass = 'text-danger';
            else if (item.days_stale >= 15) statusClass = 'text-warning';

            return `
                <div class="stale-item">
                    <div class="stale-info">
                        <span class="stale-name" title="${esc(item.ticker)}">${esc(item.alias)}</span>
                        <span class="stale-date">마지막 거래: ${item.last_trade_date}</span>
                    </div>
                    <div class="stale-days ${statusClass}">
                        ${item.days_stale}일 경과
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = `<div class="stale-list">${listHtml}</div>`;
    }

    return {
        renderCashRatio,
        renderNextLevelNeeds,
        renderStalePositions,
        renderTickerConcentration,
        renderLevelDist,
        renderRiskHealth,
        renderAlerts,
        showRiskSection,
        hideRiskSection
    };
})();
