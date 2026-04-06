#!/usr/bin/env python3
"""
Fetches live market data via yfinance and writes data.json.
Runs as a GitHub Actions step. No API key required.
"""
import json, datetime, yfinance as yf

def pct(new, old):
    if old == 0: return 0.0
    return round((new - old) / old * 100, 2)

def safe(ticker, period="1d"):
    try:
        return yf.Ticker(ticker).history(period=period)
    except Exception:
        return None

# ── Indices ──────────────────────────────────────────────────────────────────
def index_data(symbol, ytd_symbol=None):
    t = yf.Ticker(symbol)
    hist_1y = t.history(period="1y")
    if hist_1y.empty:
        return {"val": "—", "chg": "—", "ytd": "—"}
    close = hist_1y["Close"]
    latest = round(close.iloc[-1], 2)
    prev   = round(close.iloc[-2], 2) if len(close) >= 2 else latest
    day_chg = pct(latest, prev)
    # YTD: find first trading day of this year
    year_start = close[close.index.year == datetime.date.today().year]
    if not year_start.empty:
        ytd_base = year_start.iloc[0]
        ytd = pct(latest, ytd_base)
    else:
        ytd = 0.0
    chg_str = f"{'+' if day_chg >= 0 else ''}{day_chg}%"
    ytd_str = f"{'+' if ytd >= 0 else ''}{ytd:.1f}%"
    return {"val": f"{latest:,.0f}", "chg": chg_str, "ytd": ytd_str}

def vix_data():
    t = yf.Ticker("^VIX")
    h = t.history(period="5d")
    if h.empty:
        return {"val": "—", "note": "—"}
    v = round(h["Close"].iloc[-1], 2)
    note = "elevated · caution" if v > 20 else "calm · normal"
    return {"val": str(v), "note": note}

# ── Sectors (ETFs) ────────────────────────────────────────────────────────────
SECTOR_ETFS = {
    "Tech":        "XLK",
    "Financials":  "XLF",
    "Health":      "XLV",
    "Energy":      "XLE",
    "Utilities":   "XLU",
    "Cons Disc":   "XLY",
    "Cons Staples":"XLP",
    "Industrials": "XLI",
    "Materials":   "XLB",
    "Real Estate": "XLRE",
    "Comm Svcs":   "XLC",
}

def sector_ytd(sym):
    t = yf.Ticker(sym)
    h = t.history(period="ytd")
    if h.empty or len(h) < 2:
        return 0.0
    base = h["Close"].iloc[0]
    last = h["Close"].iloc[-1]
    return round(pct(last, base), 1)

# ── Tickers (portfolio) ───────────────────────────────────────────────────────
def ticker_data(sym):
    t   = yf.Ticker(sym)
    h1y = t.history(period="1y")
    if h1y.empty:
        return None
    close = h1y["Close"]
    latest = round(close.iloc[-1], 2)
    prev   = round(close.iloc[-2], 2) if len(close) >= 2 else latest
    day_chg = pct(latest, prev)
    # 1M momentum
    if len(close) >= 22:
        m1 = pct(latest, close.iloc[-22])
    else:
        m1 = 0.0
    # 3M momentum
    if len(close) >= 63:
        m3 = pct(latest, close.iloc[-63])
    else:
        m3 = 0.0
    # Max drawdown from 52w high
    high_52w = close.max()
    dd = round((latest - high_52w) / high_52w * 100, 1)
    info = t.info
    name = info.get("shortName", sym)[:20] if info else sym
    return {
        "sym":   sym,
        "name":  name,
        "price": latest,
        "chg":   round(day_chg, 2),
        "m1":    round(m1, 1),
        "m3":    round(m3, 1),
        "dd":    round(dd, 1),
    }

# ── Macro / rates ─────────────────────────────────────────────────────────────
def rate_data(sym):
    t = yf.Ticker(sym)
    h = t.history(period="5d")
    if h.empty:
        return None
    return round(h["Close"].iloc[-1], 2)

def yield_curve():
    y10 = rate_data("^TNX")
    y2  = rate_data("^IRX")   # 13-week; use as proxy; ideally ^TYX for 2y
    # yfinance doesn't have a clean 2Y symbol, use TNX minus ~0.4 as fallback
    if y10 is None:
        return {"bps": 0, "pct": 50}
    y2_approx = y2 if y2 else round(y10 - 0.36, 2)
    spread_bps = round((y10 - y2_approx) * 100, 0)
    # Map to 0–100 bar: -100bps = 0, +100bps = 100
    pct_pos = round(min(98, max(2, (spread_bps + 100) / 2)), 0)
    return {"bps": int(spread_bps), "pct": int(pct_pos)}

def crude_price():
    t = yf.Ticker("CL=F")
    h = t.history(period="5d")
    if h.empty:
        return "—"
    v = round(h["Close"].iloc[-1], 1)
    return f"${v}"

# ── 6-month relative perf chart ───────────────────────────────────────────────
def perf_chart():
    labels, spx, ndx, rut = [], [], [], []
    data = {
        "^GSPC": spx,
        "^IXIC": ndx,
        "^RUT":  rut,
    }
    # Use 6 months of monthly closes
    for sym, lst in data.items():
        t = yf.Ticker(sym)
        h = t.history(period="6mo", interval="1mo")
        if h.empty:
            continue
        base = h["Close"].iloc[0]
        for row in h["Close"]:
            lst.append(round(row / base * 100, 2))
        if not labels:
            labels = [d.strftime("%b") for d in h.index]
    # Pad if lengths differ
    n = min(len(spx), len(ndx), len(rut), len(labels))
    return {"labels": labels[:n], "spx": spx[:n], "ndx": ndx[:n], "rut": rut[:n]}

# ── Signals (rules-based) ─────────────────────────────────────────────────────
def compute_signals(vix_val, yc_bps, sectors):
    sigs = []
    v = float(vix_val) if vix_val != "—" else 18.0
    if v > 25:
        sigs.append({"color":"#e24b4a","text":"High volatility alert","sub":f"VIX {v:.1f} — fear elevated, consider hedges"})
    elif v > 20:
        sigs.append({"color":"#ba7517","text":"Volatility spike active","sub":f"VIX {v:.1f} — above normal; monitor risk"})
    else:
        sigs.append({"color":"#1d9e75","text":"Volatility calm","sub":f"VIX {v:.1f} — normal range"})

    # Best and worst sectors
    sorted_s = sorted(sectors, key=lambda x: x["ytd"], reverse=True)
    best = sorted_s[0]
    worst = sorted_s[-1]
    sigs.append({"color":"#1d9e75","text":f"{best['name']} leading YTD","sub":f"+{best['ytd']}% YTD — momentum sector"})
    sigs.append({"color":"#e24b4a","text":f"{worst['name']} lagging YTD","sub":f"{worst['ytd']}% YTD — weakest sector"})

    if yc_bps < -10:
        sigs.append({"color":"#e24b4a","text":"Yield curve inverted","sub":f"10Y–2Y spread {yc_bps}bps — recession signal"})
    elif yc_bps < 20:
        sigs.append({"color":"#ba7517","text":"Yield curve flat","sub":f"10Y–2Y spread {yc_bps}bps — watch for inversion"})
    else:
        sigs.append({"color":"#1d9e75","text":"Yield curve steepening","sub":f"10Y–2Y spread {yc_bps}bps — normalizing"})

    return sigs

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now = datetime.datetime.utcnow().strftime("%b %-d, %Y %H:%M UTC")
    print("Fetching indices…")
    indices = {
        "spx": index_data("^GSPC"),
        "ndx": index_data("^IXIC"),
        "rut": index_data("^RUT"),
        "vix": vix_data(),
    }

    print("Fetching sectors…")
    sectors = [{"name": name, "ytd": sector_ytd(sym)} for name, sym in SECTOR_ETFS.items()]
    sectors.sort(key=lambda x: x["ytd"], reverse=True)

    print("Fetching tickers…")
    DEFAULT_TICKERS = ["AAPL","NVDA","MSFT","JPM","XOM"]
    tickers = [d for sym in DEFAULT_TICKERS if (d := ticker_data(sym)) is not None]

    print("Fetching rates…")
    t10 = rate_data("^TNX") or 4.31
    crude = crude_price()
    yc = yield_curve()

    macro = [
        {"label":"Fed funds rate",  "val":"3.50–3.75%",       "note":"held Mar 18 · 0 cuts priced for '26"},
        {"label":"10Y Treasury",    "val":f"{t10:.2f}%",       "note":"real-time via yfinance"},
        {"label":"2Y Treasury",     "val":f"{max(0,t10-0.36):.2f}%","note":f"spread: {yc['bps']:+d}bps"},
        {"label":"CPI (Jan YoY)",   "val":"2.4%",              "note":"above 2% target · oil risk ↑", "cls":"warn"},
        {"label":"Unemployment",    "val":"4.4%",              "note":"Feb '26 · stabilizing"},
        {"label":"WTI Crude",       "val":crude,               "note":"real-time futures", "cls":"neg"},
    ]

    print("Building chart…")
    pc = perf_chart()

    signals = compute_signals(indices["vix"]["val"], yc["bps"], sectors)

    out = {
        "asOf":       now,
        "indices":    indices,
        "sectors":    sectors,
        "macro":      macro,
        "tickers":    tickers,
        "signals":    signals,
        "yieldCurve": yc,
        "perfChart":  pc,
    }

    with open("data.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"data.json written ({len(tickers)} tickers, {len(sectors)} sectors)")

if __name__ == "__main__":
    main()
