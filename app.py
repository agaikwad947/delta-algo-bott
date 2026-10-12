import streamlit as st
import pandas as pd
import requests
import time
import json
import os
from datetime import datetime, timezone, timedelta
import plotly.graph_objects as go

st.set_page_config(page_title="Delta India Multi-Crypto Algo Bot", layout="wide")

IST = timezone(timedelta(hours=5, minutes=30))
STATE_FILE = "multi_crypto_state_inr.json"

# --- 1. PERSISTENT CLOUD MEMORY (Multi-Position Portfolio) ---
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
        "balance_inr": 20000.0,       # Total Wallet Balance (Starts at Rs 20,000)
        "open_positions": {},         # Holds up to 4 active coins simultaneously
        "trade_log": []               # Full historical ledger across all coins
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
        options = []
        for item in rows:
            sym = item.get("symbol", "")
            ctype = item.get("contract_type", "")
            close_p = float(item.get("close") or item.get("mark_price") or 0)
            vol_usd = float(item.get("turnover") or item.get("volume") or 0)
            if close_p <= 0:
                continue
            if ctype == "perpetual_futures" and sym.endswith("USD"):
                perps.append({"symbol": sym, "price": close_p, "turnover": vol_usd})
            elif "options" in ctype:
                options.append({"symbol": sym, "price": close_p, "turnover": vol_usd})
        perps_df = pd.DataFrame(perps).sort_values("turnover", ascending=False).reset_index(drop=True)
        return perps_df, options
    except Exception:
        fallback = pd.DataFrame([
            {"symbol": "BTCUSD", "price": 65000.0, "turnover": 1000000},
            {"symbol": "ETHUSD", "price": 3500.0, "turnover": 800000},
            {"symbol": "SOLUSD", "price": 150.0, "turnover": 600000},
            {"symbol": "XRPUSD", "price": 0.60, "turnover": 500000},
            {"symbol": "DOGEUSD", "price": 0.15, "turnover": 400000}
        ])
        return fallback, []

perps_df, options_list = fetch_all_delta_tickers()
all_perp_symbols = perps_df["symbol"].tolist() if not perps_df.empty else ["BTCUSD", "ETHUSD", "SOLUSD"]

# --- 3. SIDEBAR CONTROLS ---
st.sidebar.header("All-Market Scanner Settings")
scan_count = st.sidebar.slider("Auto-Scan Top N Liquid Coins", 5, 25, 12)
max_slots = st.sidebar.slider("Max Simultaneous Open Trades", 1, 5, 4)
margin_per_trade = st.sidebar.number_input("Margin Per Trade (Rs)", value=5000, step=1000)
timeframe = st.sidebar.selectbox("Scanner Timeframe", ["5m", "15m"], index=1)
leverage = st.sidebar.slider("Leverage (x)", 5, 50, 20)
vol_mult = st.sidebar.slider("Volume Spike Filter (x Avg)", 1.0, 2.5, 1.2, 0.1)
tp_raw = st.sidebar.number_input("Target Move % (+44% ROI at 20x)", value=2.2, step=0.1) / 100
sl_raw = st.sidebar.number_input("Stop Loss Move % (-14% ROI at 20x)", value=0.7, step=0.1) / 100
fee_rate = 0.0005

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

# --- 4. FETCH CANDLES FOR ANY SYMBOL ---
def fetch_candles(sym, tf):
    tf_sec = 300 if tf == "5m" else 900
    end_ts = int(time.time())
    start_ts = end_ts - (65 * tf_sec)
    url = f"https://api.india.delta.exchange/v2/history/candles?resolution={tf}&symbol={sym}&start={start_ts}&end={end_ts}"
    r = requests.get(url, timeout=6).json()
    res = r.get("result", [])
    if not res or len(res) < 30:
        return None
    df = pd.DataFrame(res).sort_values("time").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(IST)
    df["SMA20"] = df["close"].rolling(20).mean()
    df["STD20"] = df["close"].rolling(20).std()
    df["Upper_BB"] = df["SMA20"] + (2.0 * df["STD20"])
    df["Lower_BB"] = df["SMA20"] - (2.0 * df["STD20"])
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["Vol_Avg"] = df["volume"].rolling(20).mean()
    return df

now_ist = datetime.now(IST).strftime("%d %b %H:%M:%S IST")

# Build Universe of Coins to Scan (Top N by Volume + Any Currently Open Coins)
scan_symbols = all_perp_symbols[:scan_count]
for open_sym in list(state["open_positions"].keys()):
    if open_sym not in scan_symbols:
        scan_symbols.append(open_sym)
if selected_chart_sym not in scan_symbols:
    scan_symbols.append(selected_chart_sym)

scanner_rows = []
chart_df = None

# --- 5. MULTI-CRYPTO AUTO-TRADING ENGINE ---
for sym in scan_symbols:
    try:
        df = fetch_candles(sym, timeframe)
        if df is None:
            continue
        if sym == selected_chart_sym:
            chart_df = df

        live_p = float(df["close"].iloc[-1])
        last_c = df.iloc[-2]
        c_close = float(last_c["close"])
        c_high  = float(last_c["high"])
        c_low   = float(last_c["low"])
        c_upper = float(last_c["Upper_BB"])
        c_lower = float(last_c["Lower_BB"])
        c_ema50 = float(last_c["EMA50"])
        c_vol   = float(last_c["volume"])
        c_vavg  = float(last_c["Vol_Avg"]) if float(last_c["Vol_Avg"]) > 0 else 1.0
        vol_ratio = c_vol / c_vavg

        signal_status = "Watching"
        if c_close > c_upper and c_close > c_ema50 and vol_ratio >= vol_mult:
            signal_status = "LONG BREAKOUT"
        elif c_close < c_lower and c_close < c_ema50 and vol_ratio >= vol_mult:
            signal_status = "SHORT BREAKOUT"

        # A. Manage Existing Open Trade on This Coin
        if sym in state["open_positions"]:
            pos_info = state["open_positions"][sym]
            side = pos_info["side"]
            ep   = pos_info["entry_price"]
            tp   = pos_info["tp_price"]
            sl   = pos_info["sl_price"]

            exit_reason = None
            exit_p = live_p

            if side == "LONG (CE)":
                if live_p <= sl or c_low <= sl:
                    exit_reason, exit_p, raw_move = "STOP-LOSS HIT", sl, -sl_raw
                elif live_p >= tp or c_high >= tp:
                    exit_reason, exit_p, raw_move = "TARGET HIT", tp, tp_raw
            else:
                if live_p >= sl or c_high >= sl:
                    exit_reason, exit_p, raw_move = "STOP-LOSS HIT", sl, -sl_raw
                elif live_p <= tp or c_low <= tp:
                    exit_reason, exit_p, raw_move = "TARGET HIT", tp, tp_raw

            if exit_reason:
                net_roi = (raw_move - (2 * fee_rate)) * leverage
                pnl_inr = margin_per_trade * net_roi - 118.0
                state["balance_inr"] += pnl_inr
                state["trade_log"].insert(0, {
                    "Entry Time": pos_info["entry_time"],
                    "Exit Time": now_ist,
                    "Crypto": sym,
                    "Direction": f"{side} {leverage}x",
                    "Margin": f"Rs {margin_per_trade:,.0f}",
                    "Entry Price": f"{ep:,.4f}",
                    "Exit Price": f"{exit_p:,.4f}",
                    "ROI %": f"{net_roi*100:+.1f}%",
                    "Net Profit (INR)": f"Rs {pnl_inr:+,.0f}",
                    "Result": exit_reason
                })
                del state["open_positions"][sym]

        # B. Open New Trade If Breakout Triggers & Free Slot Available
        elif len(state["open_positions"]) < max_slots:
            if signal_status == "LONG BREAKOUT":
                state["open_positions"][sym] = {
                    "side": "LONG (CE)",
                    "entry_price": live_p,
                    "tp_price": live_p * (1 + tp_raw),
                    "sl_price": live_p * (1 - sl_raw),
                    "entry_time": now_ist
                }
            elif signal_status == "SHORT BREAKOUT":
                state["open_positions"][sym] = {
                    "side": "SHORT (PE)",
                    "entry_price": live_p,
                    "tp_price": live_p * (1 - tp_raw),
                    "sl_price": live_p * (1 + sl_raw),
                    "entry_time": now_ist
                }

        scanner_rows.append({
            "Crypto Contract": sym,
            "Live Price (USD)": f"{live_p:,.4f}",
            "Volume Spike": f"{vol_ratio:.2f}x",
            "Trend (vs EMA50)": "BULLISH" if live_p > c_ema50 else "BEARISH",
            "Scanner Signal": "IN ACTIVE TRADE" if sym in state["open_positions"] else signal_status
        })
    except Exception:
        continue

# Handle Manual Test Buttons for Selected Coin
if selected_chart_sym and chart_df is not None:
    sel_price = float(chart_df["close"].iloc[-1])
    if force_long and selected_chart_sym not in state["open_positions"]:
        state["open_positions"][selected_chart_sym] = {
            "side": "LONG (CE)",
            "entry_price": sel_price,
            "tp_price": sel_price * (1 + tp_raw),
            "sl_price": sel_price * (1 - sl_raw),
            "entry_time": now_ist
        }
    elif force_short and selected_chart_sym not in state["open_positions"]:
        state["open_positions"][selected_chart_sym] = {
            "side": "SHORT (PE)",
            "entry_price": sel_price,
            "tp_price": sel_price * (1 - tp_raw),
            "sl_price": sel_price * (1 + sl_raw),
            "entry_time": now_ist
        }

save_state(state)

# --- 6. RENDER MULTI-CRYPTO DASHBOARD ---
st.title("Delta Exchange India - All-Market Multi-Crypto Algo Terminal")
st.caption(f"Scanning **{len(all_perp_symbols)} Perpetuals** & **{len(options_list)} Live Options** | Capital: **Rs 20,000** | Synced: `{now_ist}`")

# Top Summary Metrics
k1, k2, k3, k4 = st.columns(4)
net_pnl_total = state["balance_inr"] - 20000.0
used_margin = len(state["open_positions"]) * margin_per_trade
free_margin = max(0.0, state["balance_inr"] - used_margin)

k1.metric("Wallet Balance", f"Rs {state['balance_inr']:,.0f}", f"{net_pnl_total:+,.0f} INR")
k2.metric("Active Open Trades", f"{len(state['open_positions'])} / {max_slots} Slots", f"Used Margin: Rs {used_margin:,.0f}")
k3.metric("Free Available Margin", f"Rs {free_margin:,.0f}", f"Scanning {len(scan_symbols)} Coins Live")

tot_closed = len(state["trade_log"])
wins = len([t for t in state["trade_log"] if "+" in t["Net Profit (INR)"]])
wr = (wins / tot_closed * 100) if tot_closed > 0 else 0.0
k4.metric("Total Closed Trades", f"{tot_closed}", f"Win Rate: {wr:.1f}%")

# Section 1: Active Open Positions Across All Coins
st.subheader("Active Multi-Crypto Positions (Live Unrealized PnL)")
if state["open_positions"]:
    open_list = []
    cols_close = st.columns(len(state["open_positions"]))
    for idx, (sym, pdata) in enumerate(list(state["open_positions"].items())):
        match_row = perps_df[perps_df["symbol"] == sym]
        cur_p = float(match_row["price"].iloc[0]) if not match_row.empty else pdata["entry_price"]
        ep = pdata["entry_price"]
        rm = ((cur_p - ep) / ep) if pdata["side"] == "LONG (CE)" else ((ep - cur_p) / ep)
        live_roi_pct = rm * leverage * 100
        live_inr_pnl = (margin_per_trade * rm * leverage) - 118.0
        open_list.append({
            "Crypto": sym,
            "Side": f"{pdata['side']} ({leverage}x)",
            "Margin": f"Rs {margin_per_trade:,.0f}",
            "Entry Price": f"{ep:,.4f}",
            "Live Price": f"{cur_p:,.4f}",
            "Target (+44%)": f"{pdata['tp_price']:,.4f}",
            "Stop-Loss (-14%)": f"{pdata['sl_price']:,.4f}",
            "Live ROI %": f"{live_roi_pct:+.1f}%",
            "Live PnL (INR)": f"Rs {live_inr_pnl:+,.0f}"
        })
        if cols_close[idx].button(f"Close {sym} Now", key=f"close_{sym}"):
            state["balance_inr"] += live_inr_pnl
            state["trade_log"].insert(0, {
                "Entry Time": pdata["entry_time"],
                "Exit Time": now_ist,
                "Crypto": sym,
                "Direction": f"{pdata['side']} {leverage}x",
                "Margin": f"Rs {margin_per_trade:,.0f}",
                "Entry Price": f"{ep:,.4f}",
                "Exit Price": f"{cur_p:,.4f}",
                "ROI %": f"{live_roi_pct:+.1f}%",
                "Net Profit (INR)": f"Rs {live_inr_pnl:+,.0f}",
                "Result": "MANUAL EXIT"
            })
            del state["open_positions"][sym]
            save_state(state)
            st.rerun()
    st.dataframe(pd.DataFrame(open_list), use_container_width=True)
else:
    st.info("No active positions open right now. The scanner below is checking all top coins every 25 seconds for a breakout!")

# Section 2: Side-by-Side Live All-Market Scanner & Selected Chart
col_left, col_right = st.columns([1.1, 1.3])

with col_left:
    st.subheader(f"Live Multi-Coin Radar (Top {len(scanner_rows)} Liquid Coins)")
    if scanner_rows:
        st.dataframe(pd.DataFrame(scanner_rows), use_container_width=True, height=420)

with col_right:
    st.subheader(f"Live Breakout Chart: {selected_chart_sym} ({timeframe})")
    if chart_df is not None:
        fig = go.Figure()
        p_df = chart_df.tail(60)
        fig.add_trace(go.Candlestick(x=p_df["dt"], open=p_df["open"], high=p_df["high"], low=p_df["low"], close=p_df["close"], name=selected_chart_sym))
        fig.add_trace(go.Scatter(x=p_df["dt"], y=p_df["Upper_BB"], line=dict(color="rgba(0,255,100,0.5)", width=1), name="Upper Band"))
        fig.add_trace(go.Scatter(x=p_df["dt"], y=p_df["Lower_BB"], line=dict(color="rgba(255,80,80,0.5)", width=1), name="Lower Band"))
        fig.add_trace(go.Scatter(x=p_df["dt"], y=p_df["EMA50"], line=dict(color="yellow", width=1.5), name="EMA 50"))
        if selected_chart_sym in state["open_positions"]:
            op = state["open_positions"][selected_chart_sym]
            fig.add_hline(y=op["entry_price"], line_dash="dash", line_color="cyan", annotation_text="ENTRY")
            fig.add_hline(y=op["tp_price"], line_dash="dot", line_color="lime", annotation_text="TARGET")
            fig.add_hline(y=op["sl_price"], line_dash="dot", line_color="red", annotation_text="STOP")
        fig.update_layout(height=420, xaxis_rangeslider_visible=False, template="plotly_dark", margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)

# Section 3: Full Completed Trade Ledger
st.subheader("Completed Multi-Crypto Trade History (INR)")
if state["trade_log"]:
    st.dataframe(pd.DataFrame(state["trade_log"]), use_container_width=True)
else:
    st.write("No completed trades yet. Lower the 'Volume Spike Filter' slider on the left to 1.0x or click Force LONG/SHORT to test an immediate multi-coin position!")

time.sleep(25)
st.rerun()
