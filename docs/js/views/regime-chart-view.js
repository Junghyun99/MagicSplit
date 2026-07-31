// docs/js/views/regime-chart-view.js
// 판정 차트 렌더러. Lightweight Charts 의존성을 이 파일 안에 격리한다 (Application).
window.RegimeChartView = (function () {
    'use strict';

    const { escapeHtml: esc, formatTickerLabel } = window.FormatUtils;

    // 모듈 스코프 인스턴스. 재렌더 전 반드시 remove()로 파괴한다.
    let chartInstance = null;
    let closeSeries = null;
    let bandPrimitive = null;
    let resizeObserver = null;

    /**
     * 레짐 구간 배경 밴드를 그리는 series primitive.
     * v5에는 구간 배경이 내장돼 있지 않아 캔버스에 직접 그린다.
     */
    function createBandPrimitive(bands) {
        let chartApi = null;

        const renderer = {
            draw(target) {
                if (!chartApi || !bands.length) return;
                const timeScale = chartApi.timeScale();
                const visible = timeScale.getVisibleRange();
                if (!visible) return;

                target.useBitmapCoordinateSpace((scope) => {
                    const ctx = scope.context;
                    const ratio = scope.horizontalPixelRatio;
                    for (const band of bands) {
                        // 화면 밖 구간은 건너뛰고, 걸친 구간은 보이는 범위로 자른다.
                        // (timeToCoordinate는 데이터에 없는 시각에 null을 준다)
                        if (band.to < visible.from || band.from > visible.to) continue;
                        const fromTime = band.from < visible.from ? visible.from : band.from;
                        const toTime = band.to > visible.to ? visible.to : band.to;

                        const x1 = timeScale.timeToCoordinate(fromTime);
                        const x2 = timeScale.timeToCoordinate(toTime);
                        if (x1 === null || x2 === null) continue;

                        const left = Math.round(x1 * ratio);
                        const right = Math.round(x2 * ratio);
                        ctx.fillStyle = band.color;
                        ctx.fillRect(left, 0, Math.max(right - left, 1), scope.bitmapSize.height);
                    }
                });
            }
        };

        return {
            attached(params) { chartApi = params.chart; },
            detached() { chartApi = null; },
            updateAllViews() {},
            paneViews() {
                return [{ renderer: () => renderer, zOrder: () => 'bottom' }];
            }
        };
    }

    function readThemeColors() {
        const styles = getComputedStyle(document.documentElement);
        return {
            text: (styles.getPropertyValue('--text') || '#111827').trim(),
            border: (styles.getPropertyValue('--border') || '#e5e7eb').trim()
        };
    }

    /** 기존 차트를 파괴한다. 재렌더/탭 이탈 시 반드시 호출. */
    function destroyChart() {
        if (resizeObserver) {
            resizeObserver.disconnect();
            resizeObserver = null;
        }
        if (chartInstance) {
            chartInstance.remove();  // LWC는 destroy()가 아니라 remove()
            chartInstance = null;
        }
        closeSeries = null;
        bandPrimitive = null;
    }

    /** 컨테이너 크기에 맞춰 차트를 다시 맞춘다. */
    function resizeChart() {
        if (!chartInstance) return;
        const el = document.getElementById('regime-chart-canvas');
        if (el) chartInstance.applyOptions({ width: el.clientWidth });
    }

    function renderPlaceholder(message) {
        destroyChart();
        const canvas = document.getElementById('regime-chart-canvas');
        if (canvas) canvas.innerHTML = `<div class="chart-placeholder">${esc(message)}</div>`;
        const header = document.getElementById('regime-chart-header');
        if (header) header.innerHTML = '';
        const legend = document.getElementById('regime-chart-legend');
        if (legend) legend.innerHTML = '';
    }

    /** 종목 선택 버튼 목록을 그린다. */
    function renderTickerList(tickers, activeTicker, aliasMap, onSelect) {
        const container = document.getElementById('regime-chart-tickers');
        if (!container) return;
        container.innerHTML = '';

        if (!tickers.length) {
            container.innerHTML = '<span class="chart-placeholder">표시할 종목이 없습니다.</span>';
            return;
        }

        for (const ticker of tickers) {
            const btn = document.createElement('button');
            btn.className = 'chart-ticker-btn' + (ticker === activeTicker ? ' active' : '');
            btn.textContent = formatTickerLabel(ticker, aliasMap && aliasMap[ticker]);
            btn.title = ticker;
            btn.addEventListener('click', () => onSelect(ticker));
            container.appendChild(btn);
        }
    }

    function renderHeader(chart, aliasMap, badges) {
        const header = document.getElementById('regime-chart-header');
        if (!header) return;

        const name = formatTickerLabel(chart.ticker, aliasMap && aliasMap[chart.ticker]);
        const algo = chart.regime_enabled
            ? (chart.algo === 'channel' ? '회귀 채널' : '이동평균+ADX')
            : '레짐 미사용';
        const badgeHtml = (badges || [])
            .map((b) => `<span class="chart-badge chart-badge-${esc(b.tone)}">${esc(b.text)}</span>`)
            .join('');

        header.innerHTML =
            `<div class="chart-title">${esc(name)}` +
            `<span class="chart-subtitle">${esc(algo)} · 기준일 ${esc(chart.asof || '-')}</span></div>` +
            `<div class="chart-badges">${badgeHtml}</div>`;
    }

    function renderLegend(series, priceLines) {
        const legend = document.getElementById('regime-chart-legend');
        if (!legend) return;

        const seriesHtml = series.map((s) =>
            `<span class="chart-legend-item">` +
            `<i class="chart-swatch" style="background:${esc(s.color)}"></i>${esc(s.label)}</span>`
        ).join('');

        const lineHtml = priceLines.map((l) =>
            `<span class="chart-legend-item">` +
            `<i class="chart-swatch chart-swatch-dashed" style="background:${esc(l.color)}"></i>` +
            `${esc(l.label)}</span>`
        ).join('');

        legend.innerHTML =
            `<div class="chart-legend-row">${seriesHtml}</div>` +
            `<div class="chart-legend-row chart-legend-lines">${lineHtml}</div>`;
    }

    /**
     * 차트를 렌더한다.
     * @param {object} chart 백엔드가 만든 차트 JSON
     * @param {object} aliasMap 티커 -> 한글명
     * @param {function} formatMoney 금액 포맷터
     */
    function renderChart(chart, aliasMap, formatMoney) {
        if (!chart || !Array.isArray(chart.rows) || !chart.rows.length) {
            renderPlaceholder('차트 데이터가 아직 생성되지 않았습니다. 다음 매매 사이클 후 표시됩니다.');
            return;
        }
        if (!window.LightweightCharts) {
            renderPlaceholder('차트 라이브러리를 불러오지 못했습니다.');
            return;
        }

        destroyChart();

        const canvas = document.getElementById('regime-chart-canvas');
        if (!canvas) return;
        canvas.innerHTML = '';

        const series = ChartModel.toSeries(chart);
        const bands = ChartModel.toBands(chart);
        const priceLines = ChartModel.toPriceLines(chart);
        const markers = ChartModel.toMarkers(chart, formatMoney);

        renderHeader(chart, aliasMap, ChartModel.toStatusBadges(chart));
        renderLegend(series, priceLines);

        const theme = readThemeColors();
        chartInstance = LightweightCharts.createChart(canvas, {
            width: canvas.clientWidth,
            height: 420,
            layout: { background: { color: 'transparent' }, textColor: theme.text, fontSize: 11 },
            grid: {
                vertLines: { color: theme.border },
                horzLines: { color: theme.border }
            },
            rightPriceScale: { borderColor: theme.border },
            timeScale: { borderColor: theme.border, rightOffset: 4 },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            localization: {
                priceFormatter: (v) => (typeof formatMoney === 'function' ? formatMoney(v) : String(v))
            }
        });

        for (const s of series) {
            const lineSeries = chartInstance.addSeries(LightweightCharts.LineSeries, {
                color: s.color,
                lineWidth: s.width,
                priceLineVisible: false,
                lastValueVisible: s.key === 'close',
                // 지표선 title은 값이 수렴하는 우측 끝에서 서로 겹친다.
                // 색 스와치가 있는 범례로 식별되므로 종가에만 남긴다.
                title: s.key === 'close' ? s.label : ''
            });
            lineSeries.setData(s.points);
            if (s.key === 'close') closeSeries = lineSeries;
        }

        // 종가 시리즈가 없으면(이론상 불가) 첫 시리즈에라도 부가 요소를 붙인다.
        if (!closeSeries) return;

        for (const line of priceLines) {
            closeSeries.createPriceLine({
                price: line.value,
                color: line.color,
                lineWidth: 1,
                lineStyle: line.dashed
                    ? LightweightCharts.LineStyle.Dashed
                    : LightweightCharts.LineStyle.Solid,
                // 기준선이 10개를 넘을 수 있어 축 라벨을 모두 켜면 서로 겹쳐 읽을 수 없다.
                // 선 위 title과 범례로 식별되므로 축 가격은 현재가에만 표시한다.
                axisLabelVisible: line.kind === 'current',
                title: line.label
            });
        }

        if (markers.length) {
            LightweightCharts.createSeriesMarkers(closeSeries, markers);
        }

        if (bands.length) {
            bandPrimitive = createBandPrimitive(bands);
            closeSeries.attachPrimitive(bandPrimitive);
        }

        chartInstance.timeScale().fitContent();

        // 컨테이너 폭 변화 추적 (탭 전환/창 크기 변경 모두 커버)
        if (window.ResizeObserver) {
            resizeObserver = new ResizeObserver(() => resizeChart());
            resizeObserver.observe(canvas);
        }
    }

    return { renderChart, renderTickerList, renderPlaceholder, resizeChart, destroyChart };
})();
