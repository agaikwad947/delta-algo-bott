import streamlit as st
import pandas as pd
import requests
import time
import json
import os
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go

st.set_page_config(page_title="Delta India ₹20k Algo Bot", layout="wide")

IST = timezone(timedelta(hours=5, minutes=30))
STATE_FILE = "paper_state_inr.json"
USDT_INR_RATE = 86.5  # Approximate 1 USDT in INR

# --- 1. PERSISTENT CLOUD MEMORY (Survives Laptop Shutting Down) ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "balance_inr": 20000.0,       # Your ₹20,000 Starting Capital
        "position": None,             # None, "LONG (CE)", or "SHORT (PE)"
        "entry_price": 0.0,
        "tp_price": 0.0,
        "sl_price": 0.0,
        "entry_time": "",
        "trade_margin_inr": 5000.0,   # Exactly ₹5,000 per trade (25% margin)
        "lots": 0,
        "last_processed_ts": 0,
        "trade_log": []
    }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

state = load_state()

# --- 2. SIDEBAR PARAMETERS ---
st.sidebar.header("⚙️ Bot Controls (Delta India)")
symbol = st.sidebar.selectbox("Delta India Contract", ["BTCUSD", "ETHUSD", "SOLUSD"], index=0)
timeframe = st.sidebar.selectbox("Candle Timeframe", ["5m", "15m"], index=1)
leverage = st.sidebar.slider("Leverage (x)", 5, 50, 20)
vol_mult = st.sidebar.slider("Volume Spike Filter (x Avg)", 1.0, 2.5, 1.3, 0.1)
tp_raw = st.sidebar.number_input("Target Move % (+44% ROI at 20x)", value=2.2, step=0.1) / 100
sl_raw = st.sidebar.number_input("Stop Loss Move % (-14% ROI at 20x)", value=0.7, step=0.1) / 100
margin_inr_fixed = 5000.0  # Fixed ₹5,000 margin allocation per trade
fee_rate = 0.0005          # Delta Exchange 0.05% taker fee

st.sidebar.markdown("---")
st.sidebar.subheader("🧪 Manual Instant Test")
c_b1, c_b2 = st.sidebar.columns(2)
force_long = c_b1.button("Force LONG")
force_short = c_b2.button("Force SHORT")
force_close = st.sidebar.button("Exit Active Trade")
reset_bot = st.sidebar.button("🔄 Reset to ₹20,000")

if reset_bot and os.path.exists(STATE_FILE):
    os.remove(STATE_FILE)
    st.rerun()

# --- 3. PULL LIVE DELTA EXCHANGE INDIA CANDLES ---
def fetch_delta_candles(sym, tf):
    tf_sec = 300 if tf == "5m" else 900
    end_ts = int(time.time())
    start_ts = end_ts - (160 * tf_sec)
    url = f"https://api.india.delta.exchange/v2/history/candles?resolution={tf}&symbol={sym}&start={start_ts}&end={end_ts}"
    r = requests.get(url, timeout=10).json()
    df = pd.DataFrame(r["result"]).sort_values("time").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(IST)
    df["SMA20"] = df["close"].rolling(20).mean()
    df["STD20"] = df["close"].rolling(20).std()
    df["Upper_BB"] = df["SMA20"] + (2.0 * df["STD20"])
    df["Lower_BB"] = df["SMA20"] - (2.0 * df["STD20"])
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["Vol_Avg"] = df["volume"].rolling(20).mean()
    return df

df = fetch_delta_candles(symbol, timeframe)
live_price = float(df["close"].iloc[-1])
now_ist = datetime.now(IST).strftime("%d %b %H:%M:%S IST")

if state["last_processed_ts"] == 0:
    state["last_processed_ts"] = int(df["time"].iloc[50])

# --- 4. 24/7 GAPLESS ENGINE (REPLAYS CANDLES MISSED WHILE ASLEEP) ---
for i in range(51, len(df) - 1):
    candle = df.iloc[i]
    c_ts = int(candle["time"])
    if c_ts <= state["last_processed_ts"]:
        continue

    c_time_str = candle["dt"].strftime("%d %b %H:%M IST")
    c_close = float(candle["close"])
    c_high  = float(candle["high"])
    c_low   = float(candle["low"])
    c_upper = float(candle["Upper_BB"])
    c_lower = float(candle["Lower_BB"])
    c_ema50 = float(candle["EMA50"])
    c_vol   = float(candle["volume"])
    c_vavg  = float(candle["Vol_Avg"])

    if state["position"] is None:
        if c_close > c_upper and c_close > c_ema50 and c_vol > (c_vavg * vol_mult):
            state["position"] = "LONG (CE)"
            state["entry_price"] = c_close
            state["tp_price"] = c_close * (1 + tp_raw)
            state["sl_price"] = c_close * (1 - sl_raw)
            state["entry_time"] = c_time_str
            # Calculate Delta India contract lots for ₹1,00,000 notional (₹5,000 margin x 20)
            notional_usd = (margin_inr_fixed * leverage) / USDT_INR_RATE
            state["lots"] = max(1, int(notional_usd / (c_close * 0.001))) if sym := "BTCUSD" else 10
        elif c_close < c_lower and c_close < c_ema50 and c_vol > (c_vavg * vol_mult):
            state["position"] = "SHORT (PE)"
            state["entry_price"] = c_close
            state["tp_price"] = c_close * (1 - tp_raw)
            state["sl_price"] = c_close * (1 + sl_raw)
            state["entry_time"] = c_time_str
            notional_usd = (margin_inr_fixed * leverage) / USDT_INR_RATE
            state["lots"] = max(1, int(notional_usd / (c_close * 0.001))) if sym := "BTCUSD" else 10
    else:
        pos = state["position"]
        ep  = state["entry_price"]
        tp  = state["tp_price"]
        sl  = state["sl_price"]
        exit_reason = None
        exit_p = c_close

        if pos == "LONG (CE)":
            if c_low <= sl:
                exit_reason, exit_p, raw_move = "STOP-LOSS HIT 🛑", sl, -sl_raw
            elif c_high >= tp:
                exit_reason, exit_p, raw_move = "TARGET HIT 🎯", tp, tp_raw
        else:
            if c_high >= sl:
                exit_reason, exit_p, raw_move = "STOP-LOSS HIT 🛑", sl, -sl_raw
            elif c_low <= tp:
                exit_reason, exit_p, raw_move = "TARGET HIT 🎯", tp, tp_raw

        if exit_reason:
            net_roi = (raw_move - (2 * fee_rate)) * leverage
            pnl_inr = margin_inr_fixed * net_roi - 118.0  # Net of exchange fees & GST
            state["balance_inr"] += pnl_inr
            state["trade_log"].insert(0, {
                "Entry Time": state["entry_time"],
                "Exit Time": c_time_str,
                "Contract": symbol,
                "Position": f"{pos} {leverage}x",
                "Margin Used": f"₹{margin_inr_fixed:,.0f}",
                "Entry Price": f"${ep:,.2f}",
                "Exit Price": f"${exit_p:,.2f}",
                "ROI on Margin": f"{net_roi*100:+.2f}%",
                "Net Profit (INR)": f"₹{pnl_inr:+,.0f}",
                "Status": exit_reason
            })
            state["position"] = None

    state["last_processed_ts"] = c_ts

# Check Manual Force Triggers
if state["position"] is None:
    if force_long:
        state["position"] = "LONG (CE)"
        state["entry_price"] = live_price
        state["tp_price"] = live_price * (1 + tp_raw)
        state["sl_price"] = live_price * (1 - sl_raw)
        state["entry_time"] = now_ist
        state["lots"] = 12
    elif force_short:
        state["position"] = "SHORT (PE)"
        state["entry_price"] = live_price
        state["tp_price"] = live_price * (1 - tp_raw)
        state["sl_price"] = live_price * (1 + sl_raw)
        state["entry_time"] = now_ist
        state["lots"] = 12
elif force_close:
    pos = state["position"]
    ep = state["entry_price"]
    raw_move = ((live_price - ep) / ep) if pos == "LONG (CE)" else ((ep - live_price) / ep)
    net_roi = (raw_move - (2 * fee_rate)) * leverage
    pnl_inr = margin_inr_fixed * net_roi - 118.0
    state["balance_inr"] += pnl_inr
    state["trade_log"].insert(0, {
        "Entry Time": state["entry_time"],
        "Exit Time": now_ist,
        "Contract": symbol,
        "Position": f"{pos} {leverage}x",
        "Margin Used": f"₹{margin_inr_fixed:,.0f}",
        "Entry Price": f"${ep:,.2f}",
        "Exit Price": f"${live_price:,.2f}",
        "ROI on Margin": f"{net_roi*100:+.2f}%",
        "Net Profit (INR)": f"₹{pnl_inr:+,.0f}",
        "Status": "MANUAL EXIT ⚡"
    })
    state["position"] = None

save_state(state)

# --- 5. RENDER THE INR LIVE DASHBOARD ---
st.title(f"⚡ Delta Exchange India — 20x Automated Paper Terminal")
st.caption(f"24/7 Cloud Engine | Starting Base: **₹20,000** | Trade Margin: **₹5,000 (20x)** | Last Sync: `{now_ist}`")

m1, m2, m3, m4 = st.columns(4)
total_pnl = state["balance_inr"] - 20000.0
m1.metric("Account Balance", f"₹{state['balance_inr']:,.0f}", f"{total_pnl:+,.0f} ₹")
m2.metric(f"Live {symbol} Price", f"${live_price:,.2f}", f"Vol Ratio: {df['volume'].iloc[-2]/df['Vol_Avg'].iloc[-2]:.2f}x")

if state["position"]:
    ep = state["entry_price"]
    rm = ((live_price - ep)/ep) if state["position"] == "LONG (CE)" else ((ep - live_price)/ep)
    live_roi = rm * leverage * 100
    m3.metric("Active Trade", f"{state['position']} (20x)", f"{live_roi:+.1f}% Live ROI")
else:
    m3.metric("Active Trade", "SCANNING...", "Waiting for 1.3x Vol Breakout")

tot_trades = len(state["trade_log"])
wins = len([t for t in state["trade_log"] if "+" in t["Net Profit (INR)"]])
wr = (wins / tot_trades * 100) if tot_trades > 0 else 0.0
m4.metric("Completed Trades", f"{tot_trades}", f"Win Rate: {wr:.1f}%")

if state["position"]:
    st.info(f"🔥 **OPEN {state['position']} (20x)** | **Margin:** ₹5,000 | **Entry:** ${state['entry_price']:,.2f} | **Target (+44%):** ${state['tp_price']:,.2f} \vert{} **Stop-Loss (-14\%):**${state['sl_price']:,.2f}")

fig = go.Figure()
plot_df = df.tail(80)
fig.add_trace(go.Candlestick(x=plot_df["dt"], open=plot_df["open"], high=plot_df["high"], low=plot_df["low"], close=plot_df["close"], name=symbol))
fig.add_trace(go.Scatter(x=plot_df["dt"], y=plot_df["Upper_BB"], line=dict(color="rgba(0,255,100,0.5)", width=1), name="Upper Breakout"))
fig.add_trace(go.Scatter(x=plot_df["dt"], y=plot_df["Lower_BB"], line=dict(color="rgba(255,80,80,0.5)", width=1), name="Lower Breakout"))
fig.add_trace(go.Scatter(x=plot_df["dt"], y=plot_df["EMA50"], line=dict(color="yellow", width=1.5), name="EMA 50"))

if state["position"]:
    fig.add_hline(y=state["entry_price"], line_dash="dash", line_color="cyan", annotation_text="ENTRY")
    fig.add_hline(y=state["tp_price"], line_dash="dot", line_color="lime", annotation_text="TARGET (+44%)")
    fig.add_hline(y=state["sl_price"], line_dash="dot", line_color="red", annotation_text="STOP-LOSS (-14%)")

fig.update_layout(height=450, xaxis_rangeslider_visible=False, template="plotly_dark", margin=dict(l=10, r=10, t=25, b=10))
st.plotly_chart(fig, use_container_width=True)

st.subheader("📋 Trade Ledger (Rupees Net PnL)")
if state["trade_log"]:
    st.dataframe(pd.DataFrame(state["trade_log"]), use_container_width=True)
else:
    st.info("No breakouts triggered in the last 24h. You can click 'Force LONG' or 'Force SHORT' in the sidebar to simulate an instant trade!")

time.sleep(20)
st.rerun()
