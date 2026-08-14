// docs/js/models/chart-model.js
// 순수 변환 계층: 차트 JSON -> 렌더러가 바로 쓸 수 있는 형태.
// DOM/차트 라이브러리에 의존하지 않는다 (Infrastructure).
window.ChartModel = (function () {
    'use strict';

    // 지표 컬럼별 표시 이름/색. 색은 style.css의 --chart-* 토큰과 짝을 이룬다.
    const SERIES_META = {
        close:      { label: '종가',        color: '#1f77b4', width: 2 },
        mid:        { label: '추세 중심선',  color: '#8e7cc3', width: 1 },
        support:    { label: '하단 채널선',  color: '#e06666', width: 1 },
        resistance: { label: '상단 저항선',  color: '#6aa84f', width: 1 },
        ema20:      { label: '20EMA',       color: '#f6b26b', width: 1 },
        sma50:      { label: '50MA',        color: '#e06666', width: 1 },
        sma200:     { label: '200MA',       color: '#999999', width: 1 },
        chandelier: { label: 'Chandelier',  color: '#c27ba0', width: 1 }
    };

    const REGIME_META = {
        uptrend:   { label: '상승', color: 'rgba(106, 168, 79, 0.10)' },
        downtrend: { label: '하락', color: 'rgba(224, 102, 102, 0.10)' },
        sideways:  { label: '횡보', color: 'rgba(153, 153, 153, 0.06)' }
    };

    const LINE_META = {
        buy:     { color: '#3d85c6', dashed: false },
        sell:    { color: '#6aa84f', dashed: true },
        add:     { color: '#3d85c6', dashed: true },
        stop:    { color: '#cc0000', dashed: true },
        lock:    { color: '#e69138', dashed: false },
        current: { color: '#666666', dashed: false }
    };

    /**
     * cols/rows 형태의 압축 시계열을 컬럼별 {time, value} 배열로 편다.
     * 값이 null인 지점은 건너뛴다 (지표 히스토리 부족 구간).
     */
    function toSeries(chart) {
        if (!chart || !Array.isArray(chart.cols) || !Array.isArray(chart.rows)) return [];
        const cols = chart.cols;
        const dateIdx = cols.indexOf('date');
        if (dateIdx < 0) return [];

        const out = [];
        for (let c = 0; c < cols.length; c++) {
            if (c === dateIdx) continue;
            const key = cols[c];
            const meta = SERIES_META[key] || { label: key, color: '#888888', width: 1 };
            const points = [];
            for (const row of chart.rows) {
                const value = row[c];
                if (value === null || value === undefined) continue;
                points.push({ time: row[dateIdx], value: value });
            }
            if (points.length) {
                out.push({ key: key, label: meta.label, color: meta.color, width: meta.width, points: points });
            }
        }
        return out;
    }

    /**
     * 오늘자 회귀 채널(직선 오버레이)을 시리즈로 변환한다.
     *
     * rows의 채널선은 봉마다 다시 회귀한 롤링 값이라 곡선이다(과거 판정 검증용).
     * 이건 오늘 회귀 하나를 lookback 구간에 펼친 것으로 현재 채널 기울기를 본다.
     * 같은 개념이라 색은 공유하고 점선으로 구분한다.
     */
    function toChannelSeries(channel, horizon) {
        const cc = channel;
        if (!cc || !Array.isArray(cc.rows) || !cc.rows.length) return [];

        const dateIdx = cc.cols.indexOf('date');
        if (dateIdx < 0) return [];

        const out = [];
        for (let c = 0; c < cc.cols.length; c++) {
            if (c === dateIdx) continue;
            const key = cc.cols[c];
            const meta = SERIES_META[key] || { label: key, color: '#888888' };
            const points = [];
            for (const row of cc.rows) {
                const value = row[c];
                if (value === null || value === undefined) continue;
                points.push({ time: row[dateIdx], value: value });
            }
            if (points.length) {
                out.push({
                    key: horizon + '_channel_' + key,
                    color: horizon === 'long'
                        ? ({ mid: '#674ea7', support: '#a61c00', resistance: '#38761d' }[key] || meta.color)
                        : meta.color,
                    width: 1,
                    dashed: true,
                    points: points,
                    label: `${horizon === 'long' ? '장기' : '단기'} ${meta.label} (${cc.lookback}봉)`
                });
            }
        }
        return out;
    }

    function toCurrentChannelSeries(chart) {
        return toChannelSeries(chart && chart.current_channel, 'short');
    }

    function toLongChannelSeries(chart) {
        return toChannelSeries(chart && chart.long_current_channel, 'long');
    }

    /** 오늘자 회귀 채널의 요약 (범례 옆 설명용). */
    function describeCurrentChannel(chart) {
        const cc = chart && chart.current_channel;
        if (!cc) return null;
        const slope = Number(cc.slope_pct);
        const sign = slope > 0 ? '+' : '';
        return `${cc.lookback}봉 회귀 · 기울기 ${sign}${slope.toFixed(2)}% · 폭 ±${cc.stddev_k}σ`;
    }

    /** 레짐 구간을 배경 밴드 렌더용으로 변환한다 (횡보는 시각적 잡음이라 제외). */
    function describeLongChannel(chart) {
        const cc = chart && chart.long_current_channel;
        if (!cc) return null;
        const slope = Number(cc.slope_pct);
        const sign = slope > 0 ? '+' : '';
        return `장기 ${cc.lookback}봉 회귀 채널 · 기울기 ${sign}${slope.toFixed(2)}% · 폭 ${cc.stddev_k}σ`;
    }

    function toBands(chart) {
        if (!chart || !Array.isArray(chart.regime_bands)) return [];
        return chart.regime_bands
            .filter((b) => b.regime === 'uptrend' || b.regime === 'downtrend')
            .map((b) => ({
                from: b.from,
                to: b.to,
                regime: b.regime,
                label: (REGIME_META[b.regime] || {}).label || b.regime,
                color: (REGIME_META[b.regime] || {}).color || 'rgba(0,0,0,0.05)'
            }));
    }

    /** 수평 기준선에 색/점선 스타일을 입힌다. */
    function toPriceLines(chart) {
        if (!chart || !Array.isArray(chart.lines)) return [];
        return chart.lines.map((line) => {
            const meta = LINE_META[line.kind] || { color: '#888888', dashed: true };
            return {
                label: line.label,
                value: line.value,
                kind: line.kind,
                color: meta.color,
                dashed: meta.dashed
            };
        });
    }

    /**
     * 매매 체결을 차트 마커로 변환한다.
     * 같은 날 여러 건이면 라이브러리가 겹쳐 그리므로 날짜+방향으로 합산한다.
     */
    function toMarkers(chart, formatMoney) {
        if (!chart || !Array.isArray(chart.markers)) return [];
        const merged = new Map();
        for (const m of chart.markers) {
            if (!m || !m.date || !m.action) continue;
            const key = `${m.date}|${m.action}`;
            const prev = merged.get(key);
            if (prev) {
                prev.quantity += Number(m.quantity) || 0;
                prev.realized_pnl += Number(m.realized_pnl) || 0;
            } else {
                merged.set(key, {
                    date: m.date,
                    action: m.action,
                    price: m.price,
                    level: m.level || 0,
                    quantity: Number(m.quantity) || 0,
                    realized_pnl: Number(m.realized_pnl) || 0
                });
            }
        }

        return Array.from(merged.values())
            .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))
            .map((m) => {
                const isBuy = m.action === 'BUY';
                const money = typeof formatMoney === 'function' ? formatMoney : (v) => String(v);
                let text = `${isBuy ? 'B' : 'S'}${m.level ? ' Lv' + m.level : ''}`;
                if (!isBuy && m.realized_pnl) {
                    text += ` ${m.realized_pnl > 0 ? '+' : ''}${money(m.realized_pnl)}`;
                }
                return {
                    time: m.date,
                    position: isBuy ? 'belowBar' : 'aboveBar',
                    color: isBuy ? '#3d85c6' : '#cc0000',
                    shape: isBuy ? 'arrowUp' : 'arrowDown',
                    text: text
                };
            });
    }

    /** 차트 헤더에 띄울 현재 상태 배지 목록. */
    function toStatusBadges(chart) {
        if (!chart || !chart.state) return [];
        const s = chart.state;
        const badges = [];

        const hasMultiHorizon = s.long_trend || s.short_trend || s.long_downtrend_lock || s.aligned_downtrend_reentry_lock;
        if (hasMultiHorizon) {
            const label = { uptrend: '상승', sideways: '횡보', downtrend: '하락', unknown: '미확정' };
            badges.push({ text: `장기 ${label[s.long_trend] || '미확정'} · 단기 ${label[s.short_trend] || '미확정'}`, tone: s.long_trend === 'downtrend' ? 'negative' : 'neutral' });
            if (s.long_downtrend_lock) badges.push({ text: '장기 하락 — 신규·추가매수 차단', tone: 'negative' });
            if (s.aligned_downtrend_reentry_lock) badges.push({ text: '하락 정렬 청산 — 재진입 대기', tone: 'negative' });
            if (s.long_trend === 'sideways') badges.push({ text: '노출 한도 70%', tone: 'warning' });
            if (s.long_trend === 'uptrend' && s.short_trend === 'sideways') badges.push({ text: '일반 익절 기준 1.5×', tone: 'warning' });
            if (s.short_trend === 'downtrend' && !s.long_downtrend_lock) badges.push({ text: '단기 하락 — 신규·추가매수 중단', tone: 'warning' });
        } else if (s.downtrend) {
            badges.push({ text: '하락 래치 - 매수 차단', tone: 'negative' });
        } else if (s.regime === 'uptrend') {
            badges.push({ text: '상승 레짐 - 차수 매도 잠금', tone: 'positive' });
        } else {
            badges.push({ text: '횡보', tone: 'neutral' });
        }

        if (s.trailing_lock) badges.push({ text: '추종 데드라인 추적 중', tone: 'warning' });
        if (s.post_liquidation) {
            const gate = s.reentry_gate === 'midline' ? '중심선' : '상단 저항선';
            badges.push({ text: `재진입 대기 (${gate} 회복 필요)`, tone: 'warning' });
        }
        if (s.breakdown_count > 0) {
            badges.push({ text: `이탈 확정 대기 ${s.breakdown_count}/2`, tone: 'warning' });
        }
        if (s.adds > 0) badges.push({ text: `누적매수 ${s.adds}회`, tone: 'neutral' });

        return badges;
    }

    return {
        toSeries, toBands, toPriceLines, toMarkers, toStatusBadges,
        toCurrentChannelSeries, toLongChannelSeries,
        describeCurrentChannel, describeLongChannel,
        SERIES_META, REGIME_META
    };
})();
