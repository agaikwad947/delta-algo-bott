import streamlit as st
import pandas as pd
import requests
import time
import json
import os
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go

st.set_page_config(page_title="Delta India Pro Crypto Algo Bot", layout="wide")

IST = timezone(timedelta(hours=5, minutes=30))
STATE_FILE = "pro_crypto_state_inr.json"

# --- 1. PERSISTENT CLOUD MEMORY ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                if "open_positions" in data:
                    return data
        except Exception:
            pass
    return {
        "balance_inr": 20000.0,
        "open_positions": {},
        "trade_log": []
    }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

state = load_state()

# --- 2. FETCH ALL INSTRUMENTS FROM DELTA EXCHANGE INDIA ---
@st.cache_data(ttl=60)
def fetch_all_delta_tickers():
    try:
        url = "https://api.india.delta.exchange/v2/tickers"
        r = requests.get(url, timeout=10).json()
        rows = r.get("result", [])
        perps = []
        for item in rows:
            sym = item.get("symbol", "")
            ctype = item.get("contract_type", "")
            close_p = float(item.get("close") or item.get("mark_price") or 0)
            vol_usd = float(item.get("turnover") or item.get("volume") or 0)
            if close_p > 0 and ctype == "perpetual_futures" and sym.endswith("USD"):
                perps.append({"symbol": sym, "price": close_p, "turnover": vol_usd})
        perps_df = pd.DataFrame(perps).sort_values("turnover", ascending=False).reset_index(drop=True)
        return perps_df
    except Exception:
        return pd.DataFrame([
            {"symbol": "BTCUSD", "price": 65000.0, "turnover": 1000000},
            {"symbol": "ETHUSD", "price": 3500.0, "turnover": 800000},
            {"symbol": "SOLUSD", "price": 150.0, "turnover": 600000},
            {"symbol": "XRPUSD", "price": 0.60, "turnover": 500000},
            {"symbol": "DOGEUSD", "price": 0.15, "turnover": 400000}
        ])

perps_df = fetch_all_delta_tickers()
all_perp_symbols = perps_df["symbol"].tolist() if not perps_df.empty else ["BTCUSD", "ETHUSD", "SOLUSD"]

# --- 3. SIDEBAR CONTROLS (PRO CRYPTO SETTINGS) ---
st.sidebar.header("Pro Crypto Strategy Settings")
scan_count = st.sidebar.slider("Auto-Scan Top N Liquid Coins", 5, 25, 15)
max_slots = st.sidebar.slider("Max Simultaneous Open Trades", 1, 5, 4)
margin_per_trade = st.sidebar.number_input("Margin Per Trade (Rs)", value=5000, step=1000)
timeframe = st.sidebar.selectbox("Strategy Timeframe", ["15m", "1h", "5m"], index=0)
leverage = st.sidebar.slider("Safe Crypto Leverage (x)", 1, 10, 5)
atr_sl_mult = st.sidebar.slider("ATR Stop-Loss Multiplier", 1.0, 3.0, 1.5, 0.1)
atr_tp_mult = st.sidebar.slider("ATR Target Multiplier (2:1 R:R)", 2.0, 6.0, 3.0, 0.2)
fee_rate = 0.0005  # 0.05% per order (~Rs 29 round-trip with GST at 5x)

st.sidebar.markdown("---")
st.sidebar.subheader("Chart & Manual Test Inspector")
selected_chart_sym = st.sidebar.selectbox("Inspect Any Crypto Contract", all_perp_symbols, index=0)
c_b1, c_b2 = st.sidebar.columns(2)
force_long = c_b1.button("Force LONG")
force_short = c_b2.button("Force SHORT")
reset_bot = st.sidebar.button("Reset Rs 20,000 Account")

if reset_bot and os.path.exists(STATE_FILE):
    os.remove(STATE_FILE)
    st.rerun()

# --- 4. FETCH CANDLES & CALCULATE EMA + RSI + ATR ---
def fetch_candles_and_indicators(sym, tf):
    tf_map = {"5m": 300, "15m": 900, "1h": 3600}
    tf_sec = tf_map.get(tf, 900)
    end_ts = int(time.time())
    start_ts = end_ts - (90 * tf_sec)
    url = f"https://api.india.delta.exchange/v2/history/candles?resolution={tf}&symbol={sym}&start={start_ts}&end={end_ts}"
    r = requests.get(url, timeout=6).json()
    res = r.get("result", [])
    if not res or len(res) < 55:
        return None
    df = pd.DataFrame(res).sort_values("time").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(IST)

    # Trend EMAs (20 Fast Pullback Zone & 50 Macro Trend)
    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()

    # 14-Period RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    df["RSI"] = 100.0 - (100.0 / (1.0 + rs))

    # 14-Period ATR (Dynamic Volatility Stop & Target Sizing)
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    return df

now_ist = datetime.now(IST).strftime("%d %b %H:%M:%S IST")

scan_symbols = all_perp_symbols[:scan_count]
for open_sym in list(state["open_positions"].keys()):
    if open_sym not in scan_symbols:
        scan_symbols.append(open_sym)
if selected_chart_sym not in scan_symbols:
    scan_symbols.append(selected_chart_sym)

scanner_rows = []
chart_df = None
round_trip_fee_inr = margin_per_trade * leverage * (2 * fee_rate) * 1.18  # Includes 18% GST (~Rs 29.50 at 5x)

# --- 5. PRO CRYPTO TREND + PULLBACK ENGINE ---
for sym in scan_symbols:
    try:
        df = fetch_candles_and_indicators(sym, timeframe)
        if df is None:
            continue
        if sym == selected_chart_sym:
            chart_df = df

        live_p = float(df["close"].iloc[-1])
        last_c = df.iloc[-2]
        c_close = float(last_c["close"])
        c_high  = float(last_c["high"])
        c_low   = float(last_c["low"])
        c_ema20 = float(last_c["EMA20"])
        c_ema50 = float(last_c["EMA50"])
        c_rsi   = float(last_c["RSI"])
        c_atr   = float(last_c["ATR"]) if float(last_c["ATR"]) > 0 else (live_p * 0.01)

        # Pro Crypto Signal Rules:
        # LONG: Uptrend (EMA20 > EMA50) + Pullback touches near EMA20 and bounces above + Healthy RSI (42 to 65)
        long_pullback = (c_ema20 > c_ema50) and (c_low <= c_ema20 * 1.004) and (c_close > c_ema20) and (42 <= c_rsi <= 65)
        # SHORT: Downtrend (EMA20 < EMA50) + Bounce touches near EMA20 and rejects below + Bearish RSI (35 to 58)
        short_pullback = (c_ema20 < c_ema50) and (c_high >= c_ema20 * 0.996) and (c_close < c_ema20) and (35 <= c_rsi <= 58)

        signal_status = "Scanning Trend"
        if long_pullback:
            signal_status = "BUY PULLBACK"
        elif short_pullback:
