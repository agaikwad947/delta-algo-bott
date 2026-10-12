import streamlit as st
import pandas as pd
import requests
import time
import json
import os
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta

st.set_page_config(layout="wide")
IST = timezone(timedelta(hours=5, minutes=30))
FN = "pro_state.json"

if os.path.exists(FN):
    with open(FN, "r") as f:
        state = json.load(f)
else:
    state = {
        "bal": 20000.0,
        "pos": {},
        "log": []
    }

@st.cache_data(ttl=60)
def get_coins():
    try:
        u = "https://api.india.delta.exchange/v2/tickers"
        r = requests.get(u, timeout=8).json()
        res = r.get("result", [])
        rows = []
        for x in res:
            ct = x.get("contract_type", "")
            sym = str(x.get("symbol", ""))
            cp = float(x.get("close") or 0)
            tv = float(x.get("turnover") or 0)
            if ct == "perpetual_futures":
                if sym.endswith("USD") and cp > 0:
                    rows.append({"s": sym, "v": tv})
        df_c = pd.DataFrame(rows)
        df_c = df_c.sort_values("v", ascending=False)
        return df_c["s"].tolist()
    except Exception:
        return [
            "BTCUSD", "ETHUSD", "SOLUSD",
            "XRPUSD", "DOGEUSD", "BNBUSD",
            "SUIUSD", "ADAUSD", "AVAXUSD"
        ]

coins = get_coins()

st.sidebar.header("Pro Crypto Settings")
tf = st.sidebar.selectbox(
    "Timeframe", ["15m", "5m", "1h"], index=0
)
lev = st.sidebar.slider("Leverage (x)", 1, 10, 5)
margin = st.sidebar.number_input(
    "Margin / Trade (Rs)", value=5000, step=1000
)
chart_sym = st.sidebar.selectbox(
    "Inspect Coin Chart", coins, index=0
)
f_long = st.sidebar.button("Force LONG")
f_short = st.sidebar.button("Force SHORT")
reset_btn = st.sidebar.button("Reset Rs 20,000")

if reset_btn:
    if os.path.exists(FN):
        os.remove(FN)
    st.rerun()

now_str = datetime.now(IST).strftime("%H:%M:%S IST")
fee_inr = margin * lev * 0.001 * 1.18
radar = []
chart_df = None

for sym in coins[:12]:
    try:
        sec = 900
        if tf == "5m":
            sec = 300
        if tf == "1h":
            sec = 3600
        end_t = int(time.time())
        start_t = end_t - (75 * sec)
        b_url = "https://api.india.delta.exchange"
        path = "/v2/history/candles"
        q = f"?resolution={tf}&symbol={sym}"
        q2 = f"&start={start_t}&end={end_t}"
        u = b_url + path + q + q2
        r = requests.get(u, timeout=5).json()
        res = r.get("result", [])
        if len(res) < 52:
            continue
        df = pd.DataFrame(res)
        df = df.
