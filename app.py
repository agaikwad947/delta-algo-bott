import streamlit as st, pandas as pd, requests, time, json, os, plotly.graph_objects as go
from datetime import datetime, timezone, timedelta

st.set_page_config(page_title="Delta India Pro Crypto Bot", layout="wide")
IST = timezone(timedelta(hours=5, minutes=30))
FN = "pro_state.json"

state = json.load(open(FN)) if os.path.exists(FN) else {"bal": 20000.0, "pos": {}, "log": []}

@st.cache_data(ttl=60)
def get_coins():
    try:
        r = requests.get("https://api.india.delta.exchange/v2/tickers", timeout=8).json().get("result", [])
        rows = [{"s": x["symbol"], "p": float(x.get("close") or 0), "v": float(x.get("turnover") or 0)} for x in r if x.get("contract_type") == "perpetual_futures" and str(x.get("symbol")).endswith("USD") and float(x.get("close") or 0) > 0]
        return pd.DataFrame(rows).sort_values("v", ascending=False)["s"].tolist()
    except Exception:
        return ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD", "BNBUSD", "SUIUSD", "ADAUSD"]

coins = get_coins()
st.sidebar.header("Pro Crypto Settings")
tf = st.sidebar.selectbox("Timeframe", ["15m", "5m", "1h"], index=0)
lev = st.sidebar.slider("Leverage (x)", 1, 10, 5)
margin = st.sidebar.number_input("Margin / Trade (Rs)", value=5000, step=1000)
chart_sym = st.sidebar.selectbox("Inspect Coin Chart", coins, index=0)
f_long = st.sidebar.button("Force LONG on Selected Coin")
f_short = st.sidebar.button("Force SHORT on Selected Coin")
if st.sidebar.button("Reset Rs 20,000"):
    if os.path.exists(FN): os.remove(FN)
    st.rerun()

now_str = datetime.now(IST).strftime("%d %b %H:%M:%S IST")
fee_inr = margin * lev * 0.001 * 1.18
radar = []
chart_df = None

for sym in coins[:12]:
    try:
        sec = 300 if tf == "5m" else (900 if tf == "15m" else 3600)
        end_t = int(time.time())
        u = f"https://api.india.delta.exchange/v2/history/candles?resolution={tf}&symbol={sym}&start={end_t - 75*sec}&end={end_t}"
        res = requests.get(u, timeout=5).json().get("result", [])
        if len(res) < 52: continue
        df = pd.DataFrame(res).sort_values("time").reset_index(drop=True)
        df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(IST)
        df["e20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["e50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
        if sym == chart_sym: chart_df = df

        lp = float(df["close"].iloc[-1])
        c = df.iloc[-2]
        atr = float(c["atr"]) if float(c["atr"]) > 0 else lp * 0.015
        up = float(c["e20"]) > float(c["e50"])
        dip_buy = up and float(c["low"]) <= float(c["e20"]) * 1.003 and float(c["close"]) > float(c["e20"])
        dip_sell = (not up) and float(c["high"]) >= float(c["e20"]) * 0.997 and float(c["close"]) < float(c["e20"])

        if sym in state["pos"]:
            p = state["pos"][sym]
            ep, tp, sl, side = p["ep"], p["tp"], p["sl"], p["side"]
            hit = None
            if side == "LONG" and (lp >= tp or lp <= sl): hit = "TARGET" if lp >= tp else "STOP-LOSS"
            if side == "SHORT" and (lp <= tp or lp >= sl): hit = "TARGET" if lp <= tp else "STOP-LOSS"
            if hit:
                rm = ((lp - ep) / ep) if side == "LONG" else ((ep - lp) / ep)
                pnl = (margin * rm * lev) - fee_inr
                state["bal"] += pnl
                state["log"].insert(0, {"Time": now_str, "Coin": sym, "Side": side, "Entry": round(ep, 4), "Exit": round(lp, 4), "PnL (Rs)": round(pnl, 1), "Result": hit})
                del state["pos"][sym]
        elif len(state["pos"]) < 4:
            if dip_buy: state["pos"][sym] = {"side": "LONG", "ep": lp, "tp": lp + 3*atr, "sl": lp - 1.5*atr, "t": now_str}
            elif dip_sell: state["pos"][sym] = {"side": "SHORT", "ep": lp, "tp": lp - 3*atr, "sl": lp + 1.5*atr, "t": now_str}

        radar.append({"Coin": sym, "Price": round(lp, 4), "Trend": "UPTREND" if up else "DOWNTREND", "ATR %": f"{(atr/lp)*100:.2f}%", "Status": "OPEN TRADE" if sym in state["pos"] else ("BUY SIGNAL" if dip_buy else ("SELL SIGNAL" if dip_sell else "Scanning"))})
    except Exception:
        continue

if chart_df is not None and (f_long or f_short):
    lp = float(chart_df["close"].iloc[-1])
    atr = float(chart_df["atr"].iloc[-2]) if float(chart_df["atr"].iloc[-2]) > 0 else lp * 0.015
    if f_long: state["pos"][chart_sym] = {"side": "LONG", "ep": lp, "tp": lp + 3*atr,
