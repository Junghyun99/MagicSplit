import sys
import pandas as pd
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from pathlib import Path

# workspace 경로를 sys.path에 추가
workspace = Path(__file__).resolve().parent.parent
sys.path.append(str(workspace))

from src.backtest.cache import BacktestDataCache
from src.utils.logger import TradeLogger

# 한글 폰트 설정 (Windows 맑은 고딕)
plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

# 1. 파일 경로 정의
history_path = workspace / "docs" / "data" / "backtest" / "history.json"
output_image = workspace / "docs" / "data" / "backtest" / "backtest_chart.png"

# 2. 주가 데이터 로드 (2020년 1월 1일 이전 지표 계산을 위해 2018-10-01부터 로드)
cache = BacktestDataCache(logger=TradeLogger())
df = cache.get_ohlc(["005930"], "2018-10-01", "2026-05-01")
close_series = df[("Close", "005930")]

# 3. 보조 지표 계산
ema20 = close_series.ewm(span=20, adjust=False).mean()
ma50 = close_series.rolling(window=50).mean()
ma200 = close_series.rolling(window=200).mean()

# 4. 차트 필터링 범위 (2020-01-01 ~ 2026-04-30)
start_date = "2020-01-01"
end_date = "2026-04-30"

close_plt = close_series.loc[start_date:end_date]
ema20_plt = ema20.loc[start_date:end_date]
ma50_plt = ma50.loc[start_date:end_date]
ma200_plt = ma200.loc[start_date:end_date]

# 5. 거래 내역 로드
with open(history_path, "r", encoding="utf-8") as f:
    history = json.load(f)

normal_buys_x, normal_buys_y = [], []
regime_buys_x, regime_buys_y = [], []
sells_x, sells_y = [], []
bulk_sells_x, bulk_sells_y = [], []

# 3세대 분할청산 및 데드라인 청산 마커 리스트
split_sells_x, split_sells_y = [], []
lock_triggered_sells_x, lock_triggered_sells_y = [], []

for tx in history:
    tx_date = pd.to_datetime(tx["date"])
    # 차트 범위 내의 거래만 필터링
    if tx_date < pd.Timestamp(start_date) or tx_date > pd.Timestamp(end_date):
        continue
        
    reason = tx["reason"]
    is_bulk = "Bulk Sell" in reason or "일괄 청산" in reason or "일괄청산" in reason or "전량 청산(Bulk)" in reason
    is_regime_buy = "상승장 누적 매수" in reason or "20EMA 눌림" in reason or "add" in reason
    
    # 3세대 추가 기능 분할청산 및 데드라인 청산 감지
    is_split_sell = "분할 청산" in reason or "분할청산" in reason or "추세 이탈 분할" in reason
    is_lock_triggered = "데드라인 발동" in reason or "데드라인 이탈" in reason or "추종 데드라인 발동" in reason
    
    for exec in tx["executions"]:
        if exec["ticker"] == "005930":
            action = exec["action"]
            price = exec["price"]
            
            if action == "BUY":
                if is_regime_buy:
                    regime_buys_x.append(tx_date)
                    regime_buys_y.append(price)
                else:
                    normal_buys_x.append(tx_date)
                    normal_buys_y.append(price)
            elif action == "SELL":
                if is_bulk:
                    bulk_sells_x.append(tx_date)
                    bulk_sells_y.append(price)
                elif is_split_sell:
                    split_sells_x.append(tx_date)
                    split_sells_y.append(price)
                elif is_lock_triggered:
                    lock_triggered_sells_x.append(tx_date)
                    lock_triggered_sells_y.append(price)
                else:
                    sells_x.append(tx_date)
                    sells_y.append(price)

# 6. 로그 파일 파싱을 통한 3세대 Trailing Lock 활성화 및 해제(정상복귀) 날짜 추출
log_path = workspace / "logs" / "backtest" / "2026-05-28_31_domestic.log"
lock_active_dates = []
lock_recovery_dates = []

if log_path.exists():
    full_trading_days = df.index.tolist()
    current_day_idx = -1
    
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if ">>> Step 1: Portfolio & Price Fetch" in line:
                current_day_idx += 1
            if current_day_idx < 0 or current_day_idx >= len(full_trading_days):
                continue
            
            sim_date = full_trading_days[current_day_idx]
            if sim_date < pd.Timestamp(start_date) or sim_date > pd.Timestamp(end_date):
                continue
                
            if "추종 데드라인(Trailing Lock) 상태 활성화" in line:
                lock_active_dates.append(sim_date)
            elif "✅ 추종 데드라인 해제" in line:
                lock_recovery_dates.append(sim_date)

# 락 활성화 지점 주가 추출
lock_active_x = []
lock_active_y = []
for d in lock_active_dates:
    if d in close_series.index:
        lock_active_x.append(d)
        lock_active_y.append(close_series.loc[d])

# 락 해제 지점 주가 추출
lock_recovery_x = []
lock_recovery_y = []
for d in lock_recovery_dates:
    if d in close_series.index:
        lock_recovery_x.append(d)
        lock_recovery_y.append(close_series.loc[d])


# 7. 차트 그리기
plt.figure(figsize=(18, 10), dpi=150)

# 7.1. 상승 레짐 영역 배경 하이라이트 (20EMA > 50MA > 200MA 조건)
regime_mask = (ema20_plt > ma50_plt) & (ma50_plt > ma200_plt)
in_regime = False
regime_start = None

for idx, val in regime_mask.items():
    if val and not in_regime:
        regime_start = idx
        in_regime = True
    elif not val and in_regime:
        plt.axvspan(regime_start, idx, color="#e2f0d9", alpha=0.5, zorder=0)
        in_regime = False
if in_regime: 
    plt.axvspan(regime_start, regime_mask.index[-1], color="#e2f0d9", alpha=0.5, zorder=0)

# 7.2. 주가 및 이평선 플롯
plt.plot(close_plt.index, close_plt.values, label="삼성전자 종가 (Close)", color="#1f77b4", linewidth=2.5, zorder=2)
plt.plot(ema20_plt.index, ema20_plt.values, label="20 EMA (눌림목 기준선)", color="#ff7f0e", linestyle="--", linewidth=1.2, alpha=0.8, zorder=2)
plt.plot(ma50_plt.index, ma50_plt.values, label="50 MA (추세 이탈선)", color="#d62728", linestyle="-.", linewidth=1.2, alpha=0.8, zorder=2)
plt.plot(ma200_plt.index, ma200_plt.values, label="200 MA (장기 이평선)", color="#7f7f7f", linestyle=":", linewidth=1.2, alpha=0.6, zorder=2)

# 7.3. 거래 마커 플롯
# 횡보장 일반 매수 마커 (녹색 얇은 삼각형)
if normal_buys_x:
    plt.scatter(normal_buys_x, normal_buys_y, color="#2ca02c", edgecolor="darkgreen", marker="^", s=90, zorder=4, label="횡보장 일반 매수 (BUY)", alpha=0.9)

# 상승레짐 눌림목 추가매수 마커 (보라색 다이아몬드)
if regime_buys_x:
    plt.scatter(regime_buys_x, regime_buys_y, color="#9467bd", edgecolor="indigo", marker="D", s=80, zorder=4, label="상승레짐 눌림 매수 (ADD)", alpha=0.9)

# 일반 분할 매도 마커 (적색 역삼각형)
if sells_x:
    plt.scatter(sells_x, sells_y, color="red", marker="v", s=70, zorder=4, label="분할 매도 (SELL)", alpha=0.7)

# 일괄 청산 (Bulk Sell) 마커
if bulk_sells_x:
    plt.scatter(bulk_sells_x, bulk_sells_y, color="gold", edgecolor="darkorange", marker="*", s=250, zorder=5, label="추세이탈 일괄 청산 (Bulk Sell)")

# 7.4. 3세대 추가 기능 데드라인 락 관련 상태 플로팅
# 7.4.1. 데드라인 락 활성화 (주황색 테두리가 있는 원형 홀 마커)
if lock_active_x:
    plt.scatter(lock_active_x, lock_active_y, color="darkorange", edgecolor="orangered", marker="o", facecolors='none', s=130, linewidths=2.5, zorder=3, label="데드라인 락 활성화 (Lock Active)")

# 7.4.2. 락 해제 (초록색 P 플러스형 마커)
if lock_recovery_x:
    plt.scatter(lock_recovery_x, lock_recovery_y, color="lime", edgecolor="darkgreen", marker="P", s=140, zorder=4, label="데드라인 락 해제 (Recovery)")

# 7.4.3. 분할 청산 (주황색 역삼각형 마커)
if split_sells_x:
    plt.scatter(split_sells_x, split_sells_y, color="orange", edgecolor="chocolate", marker="v", s=110, zorder=4, label="추세이탈 분할 청산 (Split Sell)")

# 7.4.4. 데드라인 최종 이탈 청산 (빨간색 굵은 X 마커)
if lock_triggered_sells_x:
    plt.scatter(lock_triggered_sells_x, lock_triggered_sells_y, color="crimson", marker="x", s=160, linewidths=3.5, zorder=5, label="데드라인 이탈 청산 (Lock Triggered)")


# 레짐 하이라이트 범례 임의 추가 (배경색 범례 표시용)
import matplotlib.patches as mpatches
regime_patch = mpatches.Patch(color='#e2f0d9', label='상승 레짐 가동 구간 (EMA20 > MA50 > MA200)')

# 차트 데코레이션
plt.title("삼성전자(005930) 3세대 백테스트 분석 차트 - 분할청산 및 데드라인 락 적용 (2020년 ~ 2026년)", fontsize=18, fontweight="bold", pad=25)
plt.xlabel("날짜", fontsize=12)
plt.ylabel("주가 (KRW)", fontsize=12)
plt.grid(True, linestyle="--", alpha=0.3)

# 범례 처리 (마커 우선순위 및 가독성 고려)
handles, labels = plt.gca().get_legend_handles_labels()
handles.append(regime_patch)
plt.legend(handles=handles, loc="upper left", fontsize=10, shadow=True)

# x축 연도별 정렬
plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
plt.gca().xaxis.set_major_locator(mdates.YearLocator())
plt.gcf().autofmt_xdate()

# 이미지 저장
output_image.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(output_image, bbox_inches="tight")
print(f"차트 저장 완료: {output_image}")
