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

    // 범례 클릭으로 개별 토글하기 위한 등록부. key -> 숨김/표시 적용 함수.
    // 차트 파괴 시 함께 비운다.
    let visibilityTargets = new Map();

    const HIDDEN_KEYS_STORAGE = 'chartHiddenSeries';

    // 숨김 상태를 기억할 단위. 종목마다 보고 싶은 선이 달라 따로 저장한다.
    // 마켓이 다르면 같은 티커라도 다른 종목이므로 마켓까지 붙인다.
    let currentScope = '';

    function setScope(chart) {
        currentScope = `${(chart && chart.market_type) || 'unknown'}:${(chart && chart.ticker) || ''}`;
    }

    /**
     * 종목별 숨김 목록 전체를 읽는다. {"overseas:TSLA": ["resistance", ...], ...}
     *
     * 초기 구현은 종목 구분 없는 평면 배열이었다. 그 형식이 남아 있으면
     * 어느 종목의 설정인지 알 수 없으므로 버리고 빈 상태로 시작한다.
     */
    function loadHiddenMap() {
        try {
            const raw = JSON.parse(localStorage.getItem(HIDDEN_KEYS_STORAGE) || '{}');
            if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
            return raw;
        } catch (e) {
            return {};
        }
    }

    /** 현재 종목의 숨김 키 집합. */
    function loadHiddenKeys() {
        const scoped = loadHiddenMap()[currentScope];
        return new Set(Array.isArray(scoped) ? scoped : []);
    }

    function saveHiddenKeys(keys) {
        try {
            const map = loadHiddenMap();
            if (keys.size) {
                map[currentScope] = Array.from(keys);
            } else {
                delete map[currentScope];  // 빈 항목이 쌓이지 않게 정리
            }
            localStorage.setItem(HIDDEN_KEYS_STORAGE, JSON.stringify(map));
        } catch (e) {
            /* 저장 실패해도 이번 세션 토글은 동작해야 한다 */
        }
    }

    function isHidden(key) {
        return loadHiddenKeys().has(key);
    }

    /**
     * 범례 항목 하나의 표시 여부를 뒤집는다.
     * 차트 요소와 범례 항목의 흐림 처리를 함께 갱신한다.
     */
    function toggleKey(key) {
        const keys = loadHiddenKeys();
        const nowHidden = !keys.has(key);
        if (nowHidden) {
            keys.add(key);
        } else {
            keys.delete(key);
        }
        saveHiddenKeys(keys);

        const apply = visibilityTargets.get(key);
        if (apply) apply(!nowHidden);

        const item = document.querySelector(`.chart-legend-item[data-key="${CSS.escape(key)}"]`);
        if (item) {
            item.classList.toggle('is-off', nowHidden);
            item.setAttribute('aria-pressed', String(!nowHidden));
        }
    }

    function registerSeriesTarget(key, series) {
        visibilityTargets.set(key, (visible) => series.applyOptions({ visible: visible }));
    }

    function registerPriceLineTarget(key, priceLine, axisLabelVisible) {
        visibilityTargets.set(key, (visible) => priceLine.applyOptions({
            lineVisible: visible,
            // 축 라벨을 켜 둔 선(현재가)은 숨길 때 라벨도 함께 감춘다.
            axisLabelVisible: visible && axisLabelVisible,
        }));
    }

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
        visibilityTargets = new Map();
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

    /** 수평 기준선의 토글 키. 라벨은 종목이 달라도 같은 의미라 그대로 쓴다. */
    function priceLineKey(line) {
        return 'line:' + line.label;
    }

    /** 범례 항목 하나를 클릭 가능한 버튼으로 만든다. */
    function legendItemHtml(entry) {
        const off = isHidden(entry.key) ? ' is-off' : '';
        const swatch = entry.dashed ? 'chart-swatch chart-swatch-dashed' : 'chart-swatch';
        return (
            `<button type="button" class="chart-legend-item${off}" ` +
            `data-key="${esc(entry.key)}" aria-pressed="${isHidden(entry.key) ? 'false' : 'true'}" ` +
            `title="클릭하면 이 선만 숨기거나 다시 표시합니다">` +
            `<i class="${swatch}" style="background:${esc(entry.color)}"></i>` +
            `${esc(entry.label)}</button>`
        );
    }

    function renderLegend(rows, channelInfo) {
        const legend = document.getElementById('regime-chart-legend');
        if (!legend) return;

        const rowsHtml = rows
            .filter((row) => row.entries.length)
            .map((row) =>
                `<div class="chart-legend-row${row.className ? ' ' + row.className : ''}">` +
                row.entries.map(legendItemHtml).join('') +
                `</div>`
            ).join('');

        const bandGuide =
            '<div class="chart-legend-note">' +
            '배경 음영 = 추세 판정 이력 · 녹색: 상승 추세 · 빨강: 하락 추세 · 횡보는 음영 없음 (매수·매도 신호 아님)' +
            '</div>';
        const channelNote = channelInfo
            ? `<div class="chart-legend-note">점선 = 오늘 회귀 채널 · ${esc(channelInfo)}</div>`
            : '';

        legend.innerHTML = rowsHtml + bandGuide + channelNote;

        legend.querySelectorAll('.chart-legend-item').forEach((btn) => {
            btn.addEventListener('click', () => toggleKey(btn.dataset.key));
        });
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
        // 범례 렌더와 시리즈 생성이 모두 isHidden()을 보므로 가장 먼저 정한다.
        setScope(chart);

        const canvas = document.getElementById('regime-chart-canvas');
        if (!canvas) return;
        canvas.innerHTML = '';

        const series = ChartModel.toSeries(chart);
        const bands = ChartModel.toBands(chart);
        const priceLines = ChartModel.toPriceLines(chart);
        const markers = ChartModel.toMarkers(chart, formatMoney);
        const channelSeries = ChartModel.toCurrentChannelSeries(chart);
        const channelInfo = channelSeries.length
            ? ChartModel.describeCurrentChannel(chart) : null;

        renderHeader(chart, aliasMap, ChartModel.toStatusBadges(chart));
        renderLegend([
            { entries: series.map((s) => ({ key: s.key, label: s.label, color: s.color })) },
            {
                className: 'chart-legend-channel',
                entries: channelSeries.map((s) => ({
                    key: s.key, label: s.label, color: s.color, dashed: true,
                })),
            },
            {
                className: 'chart-legend-lines',
                entries: priceLines.map((l) => ({
                    key: priceLineKey(l), label: l.label, color: l.color, dashed: true,
                })),
            },
        ], channelInfo);

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
                title: s.key === 'close' ? s.label : '',
                visible: !isHidden(s.key)
            });
            lineSeries.setData(s.points);
            registerSeriesTarget(s.key, lineSeries);
            // 종가 시리즈는 숨겨져도 기준선/마커의 부착 대상으로 계속 필요하다.
            if (s.key === 'close') closeSeries = lineSeries;
        }

        // 오늘자 회귀 채널 오버레이 (점선). 롤링 곡선과 같은 색을 써서
        // "같은 선의 오늘자 스냅샷"으로 읽히게 한다.
        for (const s of channelSeries) {
            const line = chartInstance.addSeries(LightweightCharts.LineSeries, {
                color: s.color,
                lineWidth: s.width,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                priceLineVisible: false,
                lastValueVisible: false,
                title: '',
                visible: !isHidden(s.key)
            });
            line.setData(s.points);
            registerSeriesTarget(s.key, line);
        }

        // 종가 시리즈가 없으면(이론상 불가) 첫 시리즈에라도 부가 요소를 붙인다.
        if (!closeSeries) return;

        for (const line of priceLines) {
            const key = priceLineKey(line);
            const hidden = isHidden(key);
            // 기준선이 10개를 넘을 수 있어 축 라벨을 모두 켜면 서로 겹쳐 읽을 수 없다.
            // 선 위 title과 범례로 식별되므로 축 가격은 현재가에만 표시한다.
            const axisLabelVisible = line.kind === 'current';
            const priceLine = closeSeries.createPriceLine({
                price: line.value,
                color: line.color,
                lineWidth: 1,
                lineStyle: line.dashed
                    ? LightweightCharts.LineStyle.Dashed
                    : LightweightCharts.LineStyle.Solid,
                axisLabelVisible: axisLabelVisible && !hidden,
                lineVisible: !hidden,
                title: line.label
            });
            registerPriceLineTarget(key, priceLine, axisLabelVisible);
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
