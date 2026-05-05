// docs/js/views/decision-view.js
window.DecisionView = (function () {
    'use strict';

    const { escapeHtml } = window.FormatUtils;

    function renderDecisions(decisions) {
        const container = document.getElementById('decisions-list');
        if (!container) return;

        if (decisions.length === 0) {
            container.innerHTML = '<div class="card" style="text-align:center;color:var(--text-muted)">판단 내역이 없습니다.</div>';
            return;
        }

        const rows = decisions.map(d => {
            const reasonType = window.DashboardModel ? window.DashboardModel.classifyReason(d.reason) : 'info';
            const badgeClass = reasonType === 'active' ? 'badge-active' : (reasonType === 'blocked' ? 'badge-blocked' : 'badge-info');
            
            return `
                <div class="decision-card card">
                    <div class="decision-header">
                        <span class="decision-date">${escapeHtml(d.date)}</span>
                        <span class="reason-badge ${badgeClass}">${escapeHtml(reasonType.toUpperCase())}</span>
                    </div>
                    <div class="decision-reason">${escapeHtml(d.reason)}</div>
                </div>
            `;
        }).join('');

        container.innerHTML = `
            <div class="decisions-container">
                <h2>최근 판단 내역 (Decision Logs)</h2>
                ${rows}
            </div>
        `;
    }

    return {
        renderDecisions
    };
})();
