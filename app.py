import streamlit as st, pandas as pd, requests, time, json, os
import plotly.graph_objects as go

st.set_page_config(layout="wide")
FN = "bot_state.json"
S = json.load(open(FN)) if os.path.exists(FN) else {"bal": 20000.0, "pos": {}, "log": []}

COINS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD", "BNBUSD", "SUIUSD", "ADAUSD"]
lev = st.sidebar.slider("Leverage (x)", 1, 10, 5)
csym = st.sidebar.selectbox("Chart Coin", COINS)
flong = st.sidebar.button("Force LONG")
fshort = st.sidebar.button("Force SHORT")

radar, cdf = [], None
for s in COINS:
    try:
        t2 = int(time.time())
        t1 = t2 - 60000
        u = "https://api.india.delta.exchange/v2/history/candles?resolution=15m&symbol=" + s + f"&start={t1}&end={t2}"
        df = pd.DataFrame(requests.get(u, timeout=5).json()["result"]).sort_values("time")
        df["e20"] = df["close"].ewm(span=20).mean()
        df["e50"] = df["close"].ewm(span=50).mean()
        if s == csym: cdf = df
        lp = float(df["close"].iloc[-1])
        e20, e50 = float(df["e20"].iloc[-2]), float(df["e50"].iloc[-2])
        lo, hi = float(df["low"].iloc[-2]), float(df["high"].iloc[-2])
        up = e20 > e50
        if s in S["pos"]:
            p = S["pos"][s]
            rm = (lp - p["ep"]) / p["ep"] if p["side"] == "LONG" else (p["ep"] - lp) / p["ep"]
            if rm >= 0.025 or rm <= -0.012:
                pnl = round(5000 * rm * lev - 29, 1)
                S["bal"] += pnl
                S["log"].insert(0, {"Coin": s, "Side": p["side"], "Entry": p["ep"], "Exit": lp, "PnL_Rs": pnl})
                del S["pos"][s]
        elif len(S["pos"]) < 4:
            if up and lo <= e20 * 1.003 and lp > e20: S["pos"][s] = {"side": "LONG", "ep": lp}
            elif not up and hi >= e20 * 0.997 and lp < e20: S["pos"][s] = {"side": "SHORT", "ep": lp}
        radar.append({"Coin": s, "Price": lp, "Trend": "UP" if up else "DOWN", "Trade": S["pos"].get(s, {}).get("side", "Scanning")})
    except Exception: pass

if cdf is not None and (flong or fshort):
    S["pos"][csym] = {"side": "LONG" if flong else "SHORT", "ep": float(cdf["close"].iloc[-1])}
json.dump(S, open(FN, "w"))

st.title(f"Delta India Pro Crypto Bot (Rs {S['bal']:,.0f})")
c1, c2 = st.columns(2)
c1.subheader("Live Multi-Coin Radar & Active Trades")
c1.dataframe(pd.DataFrame(radar), use_container_width=True)
if S["log"]: c1.dataframe(pd.DataFrame(S["log"]), use_container_width=True)
if cdf is not None:
    fig = go.Figure(go.Candlestick(open=cdf["open"], high=cdf["high"], low=cdf["low"], close=cdf["close"]))
    fig.update_layout(height=400, xaxis_rangeslider_visible=False, template="plotly_dark")
    c2.plotly_chart(fig, use_container_width=True)
time.sleep(25)
st.rerun()
