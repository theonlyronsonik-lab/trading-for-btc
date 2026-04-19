import requests
import pandas as pd
import numpy as np
import os
import json
import asyncio
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from telegram import Bot
from telegram.error import TelegramError

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

API_KEY = os.getenv("API_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "")

SYMBOLS = ["XAU/USD", "GBP/USD", "EUR/JPY", "AUD/USD"]
INTERVAL     = "5min"   # Lower timeframe (LTF) — entry timing
HTF_INTERVAL = "1h"     # Higher timeframe (HTF) — divergence filter

RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

SIGNALS_FILE = "signals.json"

# Buffer beyond the divergence-candle wick for SL placement
SL_BUFFERS = {
    "XAU/USD": 0.50,
    "GBP/USD": 0.0003,
    "EUR/JPY": 0.10,
    "AUD/USD": 0.05,
}

# Pip sizes per symbol
PIP_SIZES = {
    "XAU/USD": 0.1,
    "GBP/USD": 0.0001,
    "EUR/JPY": 0.01,
    "AUD/USD": 0.01,
}

LOT_SIZE = 0.06

# State
active_trade   = {}
recent_signals = []
trades_history = []
symbol_state   = {}
last_div_time  = {}   # {symbol: {"BULL": candle_dt_str, "BEAR": candle_dt_str}}
signal_stack   = {}

# HTF divergence state — latched until consumed by an entry or invalidated by opposite
# {symbol: {"bull": bool, "bear": bool, "bull_candle": str, "bear_candle": str}}
htf_div = {}

SESSIONS = {
    "Asia": (2, 10),
    "London": (7, 16),
    "New York": (13, 22),
}


# ─────────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────────


def load_state():
    global recent_signals, trades_history, active_trade
    if not os.path.exists(SIGNALS_FILE):
        return
    try:
        with open(SIGNALS_FILE) as f:
            data = json.load(f)
        recent_signals = data.get("recent_signals", [])
        trades_history = data.get("trades_history", [])
        print(
            f"Loaded {len(trades_history)} historical trades, {len(recent_signals)} recent signals"
        )

        # Restore active trades from the most recent OPEN record per symbol
        for sym in SYMBOLS:
            open_trades = [
                t
                for t in trades_history
                if t["symbol"] == sym and t.get("outcome") == "OPEN"
            ]
            if open_trades:
                last = open_trades[-1]
                active_trade[sym] = {
                    "type": last["type"],
                    "entry": last["entry"],
                    "sl": last["sl"],
                    "tp2": last.get("tp2"),
                    "tp3": last.get("tp3"),
                    "pip_size": PIP_SIZES.get(sym, 0.0001),
                    "trend_aligned": last.get("trend_aligned", False),
                    "label": last.get("label", ""),
                    "session": last.get("session", ""),
                    "open_time": last.get("time", ""),
                    "rsi_alerted": False,
                }
        if active_trade:
            print(f"Restored active trades: {list(active_trade.keys())}")
    except Exception as e:
        print(f"State load error: {e}")


def sync_manual_closes():
    """Adopt trades manually closed via the dashboard before overwriting the file."""
    if not os.path.exists(SIGNALS_FILE):
        return
    try:
        with open(SIGNALS_FILE) as f:
            file_data = json.load(f)
        # Build lookup: (symbol, open_time) → file trade
        file_map = {}
        for t in file_data.get("trades_history", []):
            key = (t.get("symbol"), t.get("time"))
            file_map[key] = t

        for t in trades_history:
            key = (t.get("symbol"), t.get("time"))
            if t.get("outcome") == "OPEN" and key in file_map:
                ft = file_map[key]
                if ft.get("outcome") in ("WIN", "LOSS"):
                    # Adopt the closed outcome into memory
                    t["outcome"] = ft["outcome"]
                    t["close_time"] = ft.get("close_time")
                    t["close_price"] = ft.get("close_price")
                    t["pips"] = ft.get("pips")
                    t["profit"] = ft.get("profit")
                    sym = t["symbol"]
                    # Remove from active_trade if entry matches
                    if sym in active_trade and active_trade[sym].get("entry") == t.get(
                        "entry"
                    ):
                        del active_trade[sym]
                        print(f"Manual close adopted: {sym} {t['type']} @ {t['entry']}")
    except Exception as e:
        print(f"sync_manual_closes error: {e}")


def compute_stats():
    closed = [t for t in trades_history if t.get("outcome") in ("WIN", "LOSS")]
    wins = [t for t in closed if t["outcome"] == "WIN"]

    by_asset = {}
    by_session = {}

    for sym in SYMBOLS:
        sym_trades = [t for t in closed if t["symbol"] == sym]
        sym_wins = [t for t in sym_trades if t["outcome"] == "WIN"]
        by_asset[sym] = {
            "total": len(sym_trades),
            "wins": len(sym_wins),
            "losses": len(sym_trades) - len(sym_wins),
            "win_rate": round(len(sym_wins) / len(sym_trades) * 100, 1)
            if sym_trades
            else 0,
        }

    for sess in list(SESSIONS.keys()) + ["Off-Hours"]:
        sess_trades = [t for t in closed if sess in t.get("session", "")]
        sess_wins = [t for t in sess_trades if t["outcome"] == "WIN"]
        by_session[sess] = {
            "total": len(sess_trades),
            "wins": len(sess_wins),
            "losses": len(sess_trades) - len(sess_wins),
            "win_rate": round(len(sess_wins) / len(sess_trades) * 100, 1)
            if sess_trades
            else 0,
        }

    total = len(closed)
    return {
        "total": total,
        "wins": len(wins),
        "losses": total - len(wins),
        "pending": len([t for t in trades_history if t.get("outcome") == "OPEN"]),
        "win_rate": round(len(wins) / total * 100, 1) if total else 0,
        "by_asset": by_asset,
        "by_session": by_session,
    }


def save_state(session_on, current_sessions):
    # Adopt any dashboard-closed trades before writing
    sync_manual_closes()

    data = {
        "bot_status": "running",
        "last_scan": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "session_active": session_on,
        "current_sessions": current_sessions,
        "symbols": {},
        "recent_signals": recent_signals[-50:],
        "trades_history": trades_history[-200:],
        "stats": compute_stats(),
        "pip_sizes": PIP_SIZES,
        "lot_size": LOT_SIZE,
    }
    for sym in SYMBOLS:
        st = symbol_state.get(sym, {})
        current_price = st.get("price")
        at = active_trade.get(sym)
        if at and current_price is not None:
            live_pips = calc_pips(sym, at["entry"], current_price, at["type"])
            live_profit = calc_profit(live_pips)
            at_data = {
                **at,
                "current_price": current_price,
                "current_pips": live_pips,
                "current_profit": live_profit,
            }
        else:
            at_data = at
        data["symbols"][sym] = {
            "price": current_price,
            "rsi": st.get("rsi"),
            "sma200": st.get("sma200"),
            "atr": st.get("atr"),
            "trend": st.get("trend"),
            "active_trade": at_data,
            "last_signal": next(
                (s for s in reversed(recent_signals) if s["symbol"] == sym), None
            ),
        }
    try:
        with open(SIGNALS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Save error: {e}")


def init_state():
    data = {
        "bot_status": "starting",
        "last_scan": None,
        "session_active": False,
        "current_sessions": [],
        "symbols": {
            sym: {
                "price": None,
                "rsi": None,
                "sma200": None,
                "atr": None,
                "trend": None,
                "active_trade": None,
                "last_signal": None,
            }
            for sym in SYMBOLS
        },
        "recent_signals": recent_signals,
        "trades_history": trades_history,
        "stats": compute_stats(),
        "pip_sizes": PIP_SIZES,
    }
    with open(SIGNALS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ─────────────────────────────────────────────
# SESSIONS
# ─────────────────────────────────────────────


def get_active_sessions():
    hour = datetime.now(timezone.utc).hour
    active = [name for name, (s, e) in SESSIONS.items() if s <= hour <= e]
    return active if active else ["Off-Hours"]


def session_active():
    return get_active_sessions() != ["Off-Hours"]


def session_label(sessions):
    return " / ".join(sessions) if sessions else "Off-Hours"


# ─────────────────────────────────────────────
# MARKET CONTEXT
# ─────────────────────────────────────────────


def get_market_context(symbol, price, rsi, sma200, atr, trend):
    tips = []
    if rsi is None:
        return "Insufficient data."
    if rsi >= RSI_OVERBOUGHT:
        tips.append(f"RSI {rsi:.1f} — overbought, momentum may be exhausting")
    elif rsi >= 60:
        tips.append(f"RSI {rsi:.1f} — elevated, watch for pullback")
    elif rsi <= RSI_OVERSOLD:
        tips.append(f"RSI {rsi:.1f} — oversold, potential bounce zone")
    elif rsi <= 40:
        tips.append(f"RSI {rsi:.1f} — weak, selling pressure present")
    else:
        tips.append(f"RSI {rsi:.1f} — neutral zone")
    if trend == "BULLISH":
        tips.append("Above SMA200 — long-term uptrend")
    elif trend == "BEARISH":
        tips.append("Below SMA200 — long-term downtrend")
    if atr and price:
        vol_pct = (atr / price) * 100
        if vol_pct > 1.0:
            tips.append("High volatility — consider reduced size")
        elif vol_pct < 0.2:
            tips.append("Low volatility — tight conditions")
    hour = datetime.now(timezone.utc).hour
    if 14 <= hour <= 20:
        tips.append("NY session active — peak liquidity window")
    elif 7 <= hour <= 10:
        tips.append("London/Asia overlap — elevated volatility possible")
    return " | ".join(tips)


# ─────────────────────────────────────────────
# ALERTS
# ─────────────────────────────────────────────


async def send_telegram(msg):
    if not BOT_TOKEN:
        print(msg)
        return
    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=msg)
    except TelegramError as e:
        print(f"Telegram error: {e}")


def send_email(subject, body):
    if not (SMTP_USER and SMTP_PASS and ALERT_EMAIL):
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, ALERT_EMAIL, msg.as_string())
        print("Email alert sent")
    except Exception as e:
        print(f"Email error: {e}")


def is_high_quality(trend_aligned):
    hour = datetime.now(timezone.utc).hour
    return trend_aligned and (14 <= hour <= 20)


# ─────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────


def get_data(symbol, interval=None):
    iv = interval or INTERVAL
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbol}&interval={iv}&outputsize=210&apikey={API_KEY}"
    )
    try:
        r = requests.get(url, timeout=15).json()
    except Exception as e:
        print(f"Fetch error {symbol} ({iv}): {e}")
        return None
    if "values" not in r:
        print(f"No data for {symbol} ({iv}): {r.get('message', '')}")
        return None
    df = pd.DataFrame(r["values"]).iloc[::-1].reset_index(drop=True)
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    return df


def scan_htf(symbol):
    """
    Fetch the 1H chart.  Detects regular AND hidden divergence plus BOS structure.
    Updates htf_div[symbol] in-place.
    Returns a list of Telegram messages to send (may be empty).

    Latch rules (per flag):
    - Stays True until an LTF entry consumes it OR the opposite HTF div fires.
    - A new candle in the same direction refreshes (re-notifies).
    """
    df_htf = get_data(symbol, HTF_INTERVAL)
    state  = htf_div.setdefault(symbol, {
        # regular divergence
        "bull": False, "bear": False,
        "bull_candle": None, "bear_candle": None,
        "_bull_notified": False, "_bear_notified": False,
        # hidden divergence (trend-continuation)
        "hidden_bull": False, "hidden_bear": False,
        "hidden_bull_candle": None, "hidden_bear_candle": None,
        "_hidden_bull_notified": False, "_hidden_bear_notified": False,
        # market structure
        "structure": "RANGING",
    })

    notifications = []

    if df_htf is None:
        return notifications

    df_htf["rsi"] = calc_rsi(df_htf["close"])

    bull_h,        bull_i1_h,  bull_i2_h  = bullish_div(df_htf)
    bear_h,        bear_i1_h,  bear_i2_h  = bearish_div(df_htf)
    hbull_h, hbull_i1_h, hbull_i2_h = hidden_bullish_div(df_htf)
    hbear_h, hbear_i1_h, hbear_i2_h = hidden_bearish_div(df_htf)
    struct = market_structure(df_htf)
    state["structure"] = struct

    struct_icon = {"BULLISH": "📈", "BEARISH": "📉", "RANGING": "↔️"}.get(struct, "")

    def candle_id(df, idx):
        return str(df["datetime"].iloc[idx]) if "datetime" in df.columns else str(idx)

    # ── Regular bullish ──
    if bull_h and bull_i2_h is not None:
        cid = candle_id(df_htf, bull_i2_h)
        if state["bull_candle"] != cid:
            state["bull"] = True
            state["bull_candle"] = cid
            state["_bull_notified"] = False
        if not state["_bull_notified"]:
            msg = (
                f"🔔 HTF Alert — {symbol}\n"
                f"Regular Bullish Divergence on {HTF_INTERVAL}\n"
                f"Structure: {struct_icon} {struct}\n"
                f"⏳ Waiting for LTF bullish divergence to enter"
            )
            notifications.append(msg)
            print(f"[HTF {HTF_INTERVAL}] {symbol}: Regular bull div | Structure: {struct}")
            state["_bull_notified"] = True
        if bear_h:
            state["bull"] = False
            state["_bull_notified"] = False

    # ── Regular bearish ──
    if bear_h and bear_i2_h is not None:
        cid = candle_id(df_htf, bear_i2_h)
        if state["bear_candle"] != cid:
            state["bear"] = True
            state["bear_candle"] = cid
            state["_bear_notified"] = False
        if not state["_bear_notified"]:
            msg = (
                f"🔔 HTF Alert — {symbol}\n"
                f"Regular Bearish Divergence on {HTF_INTERVAL}\n"
                f"Structure: {struct_icon} {struct}\n"
                f"⏳ Waiting for LTF bearish divergence to enter"
            )
            notifications.append(msg)
            print(f"[HTF {HTF_INTERVAL}] {symbol}: Regular bear div | Structure: {struct}")
            state["_bear_notified"] = True
        if bull_h:
            state["bear"] = False
            state["_bear_notified"] = False

    # ── Hidden bullish (trend-continuation) ──
    if hbull_h and hbull_i2_h is not None:
        cid = candle_id(df_htf, hbull_i2_h)
        if state["hidden_bull_candle"] != cid:
            state["hidden_bull"] = True
            state["hidden_bull_candle"] = cid
            state["_hidden_bull_notified"] = False
        if not state["_hidden_bull_notified"]:
            msg = (
                f"👀 HTF Hidden Bull — {symbol}\n"
                f"Hidden Bullish Divergence on {HTF_INTERVAL}\n"
                f"Structure: {struct_icon} {struct} | Price: HL, RSI: LL\n"
                f"Trend-continuation setup forming.\n"
                f"⏳ Watching for LTF hidden bull divergence to trigger entry"
            )
            notifications.append(msg)
            print(f"[HTF {HTF_INTERVAL}] {symbol}: Hidden bull div | Structure: {struct}")
            state["_hidden_bull_notified"] = True
        if hbear_h:
            state["hidden_bull"] = False
            state["_hidden_bull_notified"] = False

    # ── Hidden bearish (trend-continuation) ──
    if hbear_h and hbear_i2_h is not None:
        cid = candle_id(df_htf, hbear_i2_h)
        if state["hidden_bear_candle"] != cid:
            state["hidden_bear"] = True
            state["hidden_bear_candle"] = cid
            state["_hidden_bear_notified"] = False
        if not state["_hidden_bear_notified"]:
            msg = (
                f"👀 HTF Hidden Bear — {symbol}\n"
                f"Hidden Bearish Divergence on {HTF_INTERVAL}\n"
                f"Structure: {struct_icon} {struct} | Price: LH, RSI: HH\n"
                f"Trend-continuation setup forming.\n"
                f"⏳ Watching for LTF hidden bear divergence to trigger entry"
            )
            notifications.append(msg)
            print(f"[HTF {HTF_INTERVAL}] {symbol}: Hidden bear div | Structure: {struct}")
            state["_hidden_bear_notified"] = True
        if hbull_h:
            state["hidden_bear"] = False
            state["_hidden_bear_notified"] = False

    return notifications


# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_sma200(series):
    return series.rolling(200).mean()


def calc_atr(df, period=14):
    h = df["high"]
    l = df["low"]
    c = df["close"]
    tr = pd.concat(
        [
            (h - l),
            (h - c.shift()).abs(),
            (l - c.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


# ─────────────────────────────────────────────
# PIVOTS
# ─────────────────────────────────────────────


def pivot_low(series, left=5, right=5):
    pivots = []
    vals = series.values
    for i in range(left, len(vals) - right):
        window = vals[i - left : i + right + 1]
        if vals[i] == np.min(window):
            pivots.append(i)
    return pivots


def pivot_high(series, left=5, right=5):
    pivots = []
    vals = series.values
    for i in range(left, len(vals) - right):
        window = vals[i - left : i + right + 1]
        if vals[i] == np.max(window):
            pivots.append(i)
    return pivots


# ─────────────────────────────────────────────
# SL / TP LEVEL HELPERS
# ─────────────────────────────────────────────


def get_sl_buy(df, i1, i2, symbol):
    """SL = low of the entry candle (most recent bar) minus buffer — tight, within entry candle range."""
    buf = SL_BUFFERS.get(symbol, 0.0001)
    entry_candle_low = df["low"].iloc[-1]
    return round(entry_candle_low - buf, 5)


def get_sl_sell(df, i1, i2, symbol):
    """SL = high of the entry candle (most recent bar) plus buffer — tight, within entry candle range."""
    buf = SL_BUFFERS.get(symbol, 0.0001)
    entry_candle_high = df["high"].iloc[-1]
    return round(entry_candle_high + buf, 5)


def get_tp_levels_buy(df, i1, i2):
    """
    TP2 = highest high between the two bullish-divergence lows (the swing high)
    TP3 = the highest high before i1 in the last 40 candles (the major resistance)
    """
    tp2 = round(df["high"].iloc[i1 : i2 + 1].max(), 5)
    lookback_start = max(0, i1 - 40)
    pre_highs = df["high"].iloc[lookback_start:i1]
    tp3 = round(pre_highs.max(), 5) if len(pre_highs) > 0 else None
    return tp2, tp3


def get_tp_levels_sell(df, i1, i2):
    """
    TP2 = lowest low between the two bearish-divergence highs (the swing low)
    TP3 = the lowest low before i1 in the last 40 candles (the major support)
    """
    tp2 = round(df["low"].iloc[i1 : i2 + 1].min(), 5)
    lookback_start = max(0, i1 - 40)
    pre_lows = df["low"].iloc[lookback_start:i1]
    tp3 = round(pre_lows.min(), 5) if len(pre_lows) > 0 else None
    return tp2, tp3


def calc_pips(symbol, entry, close_price, direction):
    pip = PIP_SIZES.get(symbol, 0.0001)
    diff = (close_price - entry) if direction == "BUY" else (entry - close_price)
    return round(diff / pip, 1)


def calc_profit(pips, lot_size=None):
    ls = lot_size if lot_size is not None else LOT_SIZE
    return round(pips * ls * 10, 2)


# ─────────────────────────────────────────────
# DIVERGENCE
# ─────────────────────────────────────────────


def bullish_div(df):
    lows = pivot_low(df["low"])
    if len(lows) < 2:
        return False, None, None
    i1, i2 = lows[-2], lows[-1]
    price_ll = df["low"].iloc[i2] < df["low"].iloc[i1]
    rsi_hl = df["rsi"].iloc[i2] > df["rsi"].iloc[i1]
    if price_ll and rsi_hl:
        return True, i1, i2
    return False, None, None


def bearish_div(df):
    highs = pivot_high(df["high"])
    if len(highs) < 2:
        return False, None, None
    i1, i2 = highs[-2], highs[-1]
    price_hh = df["high"].iloc[i2] > df["high"].iloc[i1]
    rsi_lh = df["rsi"].iloc[i2] < df["rsi"].iloc[i1]
    if price_hh and rsi_lh:
        return True, i1, i2
    return False, None, None


def hidden_bullish_div(df):
    """
    Hidden bullish divergence: price makes HIGHER LOW, RSI makes LOWER LOW.
    Trend-continuation signal — buy the dip in an uptrend.
    """
    lows = pivot_low(df["low"])
    if len(lows) < 2:
        return False, None, None
    i1, i2 = lows[-2], lows[-1]
    price_hl = df["low"].iloc[i2] > df["low"].iloc[i1]   # price: higher low
    rsi_ll   = df["rsi"].iloc[i2] < df["rsi"].iloc[i1]   # RSI:   lower low
    if price_hl and rsi_ll:
        return True, i1, i2
    return False, None, None


def hidden_bearish_div(df):
    """
    Hidden bearish divergence: price makes LOWER HIGH, RSI makes HIGHER HIGH.
    Trend-continuation signal — sell the rally in a downtrend.
    """
    highs = pivot_high(df["high"])
    if len(highs) < 2:
        return False, None, None
    i1, i2 = highs[-2], highs[-1]
    price_lh = df["high"].iloc[i2] < df["high"].iloc[i1]  # price: lower high
    rsi_hh   = df["rsi"].iloc[i2] > df["rsi"].iloc[i1]    # RSI:   higher high
    if price_lh and rsi_hh:
        return True, i1, i2
    return False, None, None


def market_structure(df):
    """
    BOS-based bias: compare last two swing highs and last two swing lows.
    BULLISH  = HH + HL  (higher highs and higher lows)
    BEARISH  = LH + LL  (lower highs  and lower lows)
    RANGING  = mixed
    """
    highs = pivot_high(df["high"])
    lows  = pivot_low(df["low"])
    if len(highs) < 2 or len(lows) < 2:
        return "RANGING"
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2],  lows[-1]
    hh = df["high"].iloc[h2] > df["high"].iloc[h1]
    hl = df["low"].iloc[l2]  > df["low"].iloc[l1]
    lh = df["high"].iloc[h2] < df["high"].iloc[h1]
    ll = df["low"].iloc[l2]  < df["low"].iloc[l1]
    if hh and hl:
        return "BULLISH"
    if lh and ll:
        return "BEARISH"
    return "RANGING"


# ─────────────────────────────────────────────
# DOUBLE CONFIRMATION
# ─────────────────────────────────────────────


def double_confirm(symbol, signal):
    if symbol not in signal_stack:
        signal_stack[symbol] = []
    signal_stack[symbol].append(signal)
    if len(signal_stack[symbol]) > 2:
        signal_stack[symbol].pop(0)
    if signal_stack[symbol] == ["BUY", "BUY"]:
        return "BUY"
    if signal_stack[symbol] == ["SELL", "SELL"]:
        return "SELL"
    return None


# ─────────────────────────────────────────────
# TRADE RECORDS
# ─────────────────────────────────────────────


def open_trade_record(
    symbol, sig_type, entry, sl, tp2, tp3, trend_aligned, label, sess
):
    rec = {
        "symbol": symbol,
        "type": sig_type,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "close_time": None,
        "entry": entry,
        "sl": sl,
        "tp2": tp2,
        "tp3": tp3,
        "outcome": "OPEN",
        "trend_aligned": trend_aligned,
        "label": label,
        "session": sess,
    }
    trades_history.append(rec)
    return rec


def close_trade_record(symbol, outcome, close_price=None, open_time=None):
    """Close the most recent OPEN trade for symbol, or the one matching open_time."""
    for t in reversed(trades_history):
        if t["symbol"] == symbol and t["outcome"] == "OPEN":
            if open_time and t.get("time") != open_time:
                continue
            t["outcome"] = outcome
            t["close_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if close_price is not None:
                raw_pips = calc_pips(symbol, t["entry"], close_price, t["type"])
                if outcome == "LOSS":
                    raw_pips = -abs(raw_pips)
                t["close_price"] = close_price
                t["pips"] = raw_pips
                t["profit"] = calc_profit(raw_pips)
            print(
                f"Trade closed: {symbol} → {outcome} | pips: {t.get('pips', '?')} | profit: ${t.get('profit', '?')}"
            )
            return t
    return None


# ─────────────────────────────────────────────
# SL CHECK — First touch of candle high/low
# ─────────────────────────────────────────────


async def check_sl(symbol, candle_low, candle_high):
    if symbol not in active_trade:
        return
    trade = active_trade[symbol]
    sl = trade.get("sl")
    if sl is None:
        return

    if trade["type"] == "BUY":
        hit = candle_low <= sl  # candle wick touched or broke SL
        close_at = sl
    else:
        hit = candle_high >= sl
        close_at = sl

    if not hit:
        return

    raw_pips = calc_pips(symbol, trade["entry"], close_at, trade["type"])
    raw_pips = -abs(raw_pips)
    profit = calc_profit(raw_pips)
    msg = (
        f"🛑 SL HIT — {symbol}\n"
        f"{trade['type']} @ {trade['entry']} | SL: {sl}\n"
        f"Close: {close_at} | Pips: {raw_pips} | P&L: ${profit}\n"
        f"Session: {trade.get('session', 'N/A')}"
    )
    print(msg)
    await send_telegram(msg)
    close_trade_record(
        symbol, "LOSS", close_price=close_at, open_time=trade.get("open_time")
    )
    del active_trade[symbol]


# ─────────────────────────────────────────────
# TP CHECKS — First touch, auto-close
# ─────────────────────────────────────────────


async def check_tp1(symbol, rsi):
    """TP1 — RSI reaches overbought/oversold zone → close trade as WIN."""
    if symbol not in active_trade:
        return
    trade = active_trade[symbol]

    hit = (trade["type"] == "BUY" and rsi >= RSI_OVERBOUGHT) or (
        trade["type"] == "SELL" and rsi <= RSI_OVERSOLD
    )

    if not hit:
        # Reset alert flag if RSI came back out of zone
        if trade.get("rsi_alerted"):
            if trade["type"] == "BUY" and rsi < RSI_OVERBOUGHT - 5:
                active_trade[symbol]["rsi_alerted"] = False
            elif trade["type"] == "SELL" and rsi > RSI_OVERSOLD + 5:
                active_trade[symbol]["rsi_alerted"] = False
        return

    if trade.get("rsi_alerted"):
        return  # already fired for this zone entry

    # Get current price from symbol_state for close price
    cur_price = symbol_state.get(symbol, {}).get("price", trade["entry"])
    raw_pips = calc_pips(symbol, trade["entry"], cur_price, trade["type"])
    profit = calc_profit(raw_pips)

    msg = (
        f"✅ TP1 HIT (RSI Zone) — {symbol}\n"
        f"{trade['type']} @ {trade['entry']} | RSI: {rsi:.1f}\n"
        f"Close: {cur_price} | Pips: {raw_pips} | P&L: ${profit}\n"
        f"{'Overbought' if trade['type'] == 'BUY' else 'Oversold'} zone reached\n"
        f"Session: {trade.get('session', 'N/A')}"
    )
    print(msg)
    await send_telegram(msg)
    outcome = "WIN" if raw_pips >= 0 else "LOSS"
    close_trade_record(
        symbol, outcome, close_price=cur_price, open_time=trade.get("open_time")
    )
    del active_trade[symbol]


async def check_tp2(symbol, candle_low, candle_high):
    """
    TP2 — Price takes out the swing high/low that formed between the
    two divergence pivots (first candle touch closes the trade as WIN).
    """
    if symbol not in active_trade:
        return
    trade = active_trade[symbol]
    tp2 = trade.get("tp2")
    if tp2 is None:
        return

    if trade["type"] == "BUY":
        hit = candle_high >= tp2
        close_at = tp2
    else:
        hit = candle_low <= tp2
        close_at = tp2

    if not hit:
        return

    raw_pips = calc_pips(symbol, trade["entry"], close_at, trade["type"])
    profit = calc_profit(raw_pips)
    msg = (
        f"✅ TP2 HIT (Divergence Swing) — {symbol}\n"
        f"{trade['type']} @ {trade['entry']} | TP2: {tp2}\n"
        f"Close: {close_at} | Pips: {raw_pips} | P&L: ${profit}"
    )
    print(msg)
    await send_telegram(msg)
    close_trade_record(
        symbol, "WIN", close_price=close_at, open_time=trade.get("open_time")
    )
    del active_trade[symbol]


async def check_tp3(symbol, candle_low, candle_high):
    """
    TP3 — Price takes out the opposite major swing (before the
    divergence pattern). Closes trade as WIN on first touch.
    """
    if symbol not in active_trade:
        return
    trade = active_trade[symbol]
    tp3 = trade.get("tp3")
    if tp3 is None:
        return

    if trade["type"] == "BUY":
        hit = candle_high >= tp3
        close_at = tp3
    else:
        hit = candle_low <= tp3
        close_at = tp3

    if not hit:
        return

    raw_pips = calc_pips(symbol, trade["entry"], close_at, trade["type"])
    profit = calc_profit(raw_pips)
    msg = (
        f"✅ TP3 HIT (Opposite Swing) — {symbol}\n"
        f"{trade['type']} @ {trade['entry']} | TP3: {tp3}\n"
        f"Close: {close_at} | Pips: {raw_pips} | P&L: ${profit}"
    )
    print(msg)
    await send_telegram(msg)
    close_trade_record(
        symbol, "WIN", close_price=close_at, open_time=trade.get("open_time")
    )
    del active_trade[symbol]


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────


async def main():
    load_state()
    init_state()
    print(f"Bot started | Symbols: {SYMBOLS}")

    await send_telegram(
        f"🤖 Signal Bot Online\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"LTF: {INTERVAL} | HTF: {HTF_INTERVAL}\n"
        f"Filter: MTF confluence — {HTF_INTERVAL} divergence required before {INTERVAL} entry\n"
        f"SL: Divergence candle wick + buffer\n"
        f"TP1: RSI overbought/oversold (auto-close)\n"
        f"TP2: Divergence swing break\n"
        f"TP3: Opposite major swing break"
    )

    scan_count = 0   # used to rate-limit HTF scans to every 3rd LTF scan

    while True:
        try:
            sessions = get_active_sessions()
            sess_on = sessions != ["Off-Hours"]
            sess_str = session_label(sessions)
            scan_count += 1

            if not sess_on:
                now_str = datetime.now(timezone.utc).strftime("%H:%M")
                print(f"[{now_str} UTC] Off-hours, sleeping 60s…")
                save_state(False, sessions)
                await asyncio.sleep(60)
                continue

            for symbol in SYMBOLS:
                df = get_data(symbol)
                if df is None:
                    continue

                df["rsi"] = calc_rsi(df["close"])
                df["sma200"] = calc_sma200(df["close"])
                df["atr"] = calc_atr(df)

                price = round(df["close"].iloc[-1], 5)
                candle_low = round(df["low"].iloc[-1], 5)
                candle_high = round(df["high"].iloc[-1], 5)
                rsi = round(df["rsi"].iloc[-1], 2)
                sma200 = df["sma200"].iloc[-1]
                atr = df["atr"].iloc[-1]

                sma200_val = round(sma200, 5) if not pd.isna(sma200) else None
                atr_val = round(atr, 5) if not pd.isna(atr) else None
                trend = None
                if sma200_val:
                    trend = "BULLISH" if price > sma200_val else "BEARISH"

                # ── Scan HTF for divergence (1H) — every 3rd LTF scan to stay within API limits ──
                if scan_count % 3 == 1:
                    htf_msgs = scan_htf(symbol)
                    for m in htf_msgs:
                        await send_telegram(m)

                s = htf_div.get(symbol, {})
                htf_bull        = s.get("bull",         False)
                htf_bear        = s.get("bear",         False)
                htf_hidden_bull = s.get("hidden_bull",  False)
                htf_hidden_bear = s.get("hidden_bear",  False)
                htf_structure   = s.get("structure",    "RANGING")

                symbol_state[symbol] = {
                    "price":          price,
                    "rsi":            rsi,
                    "sma200":         sma200_val,
                    "atr":            atr_val,
                    "trend":          trend,
                    "htf_bull":       htf_bull,
                    "htf_bear":       htf_bear,
                    "htf_hidden_bull": htf_hidden_bull,
                    "htf_hidden_bear": htf_hidden_bear,
                    "htf_structure":  htf_structure,
                }

                # ── SL/TP checks for active trade (first candle touch) ──
                if symbol in active_trade:
                    await check_sl(symbol, candle_low, candle_high)

                if symbol in active_trade:
                    await check_tp1(symbol, rsi)

                if symbol in active_trade:
                    await check_tp2(symbol, candle_low, candle_high)

                if symbol in active_trade:
                    await check_tp3(symbol, candle_low, candle_high)

                # Skip new signal detection if still in an active trade
                if symbol in active_trade:
                    continue

                # ── Divergence detection (LTF) ──
                bull,  bull_i1,  bull_i2  = bullish_div(df)
                bear,  bear_i1,  bear_i2  = bearish_div(df)
                hbull, hbull_i1, hbull_i2 = hidden_bullish_div(df)
                hbear, hbear_i1, hbear_i2 = hidden_bearish_div(df)

                now = datetime.now(timezone.utc)
                ts  = now.strftime("%Y-%m-%d %H:%M UTC")
                struct_icon = {"BULLISH": "📈", "BEARISH": "📉", "RANGING": "↔️"}.get(htf_structure, "")

                def _cdt(idx):
                    return str(df["datetime"].iloc[idx]) if "datetime" in df.columns else str(idx)

                def _open_buy(entry_label, htf_type, i1, i2, consume_hidden=False):
                    e    = price
                    sl   = get_sl_buy(df, i1, i2, symbol)
                    tp2, tp3 = get_tp_levels_buy(df, i1, i2)
                    psz  = PIP_SIZES.get(symbol, 0.0001)
                    ta   = (trend == "BULLISH")
                    ctx  = get_market_context(symbol, price, rsi, sma200_val, atr_val, trend)
                    rp   = round(abs(e - sl) / psz, 1)
                    m = (
                        f"🟢 BUY ({entry_label}) — {symbol}\n"
                        f"HTF {HTF_INTERVAL}: {htf_type} ✓ | LTF {INTERVAL}: confirmed\n"
                        f"Structure: {struct_icon} {htf_structure}\n"
                        f"Entry: {e} | SL: {sl} | Risk: {rp} pips\n"
                        f"TP2: {tp2} | TP3: {tp3}\n"
                        f"Lot: {LOT_SIZE} | RSI: {rsi} | Trend: {trend}\n"
                        f"Session: {sess_str} | {ts}\n"
                        f"📊 {ctx}"
                    )
                    print(m)
                    asyncio.ensure_future(send_telegram(m))
                    if is_high_quality(ta):
                        send_email(f"⭐ HIGH QUALITY BUY — {symbol}", m)
                    recent_signals.append({
                        "symbol": symbol, "type": "BUY", "time": ts, "entry": e,
                        "sl": sl, "tp2": tp2, "tp3": tp3, "trend_aligned": ta,
                        "label": entry_label, "session": sess_str, "rsi": rsi,
                        "trend": trend, "context": ctx,
                    })
                    open_trade_record(symbol, "BUY", e, sl, tp2, tp3, ta, entry_label, sess_str)
                    active_trade[symbol] = {
                        "type": "BUY", "entry": e, "sl": sl, "tp2": tp2, "tp3": tp3,
                        "pip_size": psz, "trend_aligned": ta, "label": entry_label,
                        "session": sess_str, "open_time": ts, "rsi_alerted": False,
                    }
                    sd = htf_div.setdefault(symbol, {})
                    if consume_hidden:
                        sd["hidden_bull"] = False; sd["_hidden_bull_notified"] = False
                    else:
                        sd["bull"] = False; sd["_bull_notified"] = False

                def _open_sell(entry_label, htf_type, i1, i2, consume_hidden=False):
                    e    = price
                    sl   = get_sl_sell(df, i1, i2, symbol)
                    tp2, tp3 = get_tp_levels_sell(df, i1, i2)
                    psz  = PIP_SIZES.get(symbol, 0.0001)
                    ta   = (trend == "BEARISH")
                    ctx  = get_market_context(symbol, price, rsi, sma200_val, atr_val, trend)
                    rp   = round(abs(sl - e) / psz, 1)
                    m = (
                        f"🔴 SELL ({entry_label}) — {symbol}\n"
                        f"HTF {HTF_INTERVAL}: {htf_type} ✓ | LTF {INTERVAL}: confirmed\n"
                        f"Structure: {struct_icon} {htf_structure}\n"
                        f"Entry: {e} | SL: {sl} | Risk: {rp} pips\n"
                        f"TP2: {tp2} | TP3: {tp3}\n"
                        f"Lot: {LOT_SIZE} | RSI: {rsi} | Trend: {trend}\n"
                        f"Session: {sess_str} | {ts}\n"
                        f"📊 {ctx}"
                    )
                    print(m)
                    asyncio.ensure_future(send_telegram(m))
                    if is_high_quality(ta):
                        send_email(f"⭐ HIGH QUALITY SELL — {symbol}", m)
                    recent_signals.append({
                        "symbol": symbol, "type": "SELL", "time": ts, "entry": e,
                        "sl": sl, "tp2": tp2, "tp3": tp3, "trend_aligned": ta,
                        "label": entry_label, "session": sess_str, "rsi": rsi,
                        "trend": trend, "context": ctx,
                    })
                    open_trade_record(symbol, "SELL", e, sl, tp2, tp3, ta, entry_label, sess_str)
                    active_trade[symbol] = {
                        "type": "SELL", "entry": e, "sl": sl, "tp2": tp2, "tp3": tp3,
                        "pip_size": psz, "trend_aligned": ta, "label": entry_label,
                        "session": sess_str, "open_time": ts, "rsi_alerted": False,
                    }
                    sd = htf_div.setdefault(symbol, {})
                    if consume_hidden:
                        sd["hidden_bear"] = False; sd["_hidden_bear_notified"] = False
                    else:
                        sd["bear"] = False; sd["_bear_notified"] = False

                # ── Opposite-signal close of active trade ──
                if bull and bull_i2 is not None:
                    if last_div_time.setdefault(symbol, {}).get("BULL") == _cdt(bull_i2):
                        bull = False
                    elif symbol in active_trade and active_trade[symbol]["type"] == "SELL":
                        rp = calc_pips(symbol, active_trade[symbol]["entry"], price, "SELL")
                        msg = (f"✅ TP HIT (Opposite Signal) — {symbol}\n"
                               f"SELL @ {active_trade[symbol]['entry']} → Close: {price}\n"
                               f"Pips: {rp} | P&L: ${calc_profit(rp)}")
                        print(msg); await send_telegram(msg)
                        close_trade_record(symbol, "WIN" if rp >= 0 else "LOSS",
                                           close_price=price, open_time=active_trade[symbol].get("open_time"))
                        del active_trade[symbol]

                if bear and bear_i2 is not None:
                    if last_div_time.setdefault(symbol, {}).get("BEAR") == _cdt(bear_i2):
                        bear = False
                    elif symbol in active_trade and active_trade[symbol]["type"] == "BUY":
                        rp = calc_pips(symbol, active_trade[symbol]["entry"], price, "BUY")
                        msg = (f"✅ TP HIT (Opposite Signal) — {symbol}\n"
                               f"BUY @ {active_trade[symbol]['entry']} → Close: {price}\n"
                               f"Pips: {rp} | P&L: ${calc_profit(rp)}")
                        print(msg); await send_telegram(msg)
                        close_trade_record(symbol, "WIN" if rp >= 0 else "LOSS",
                                           close_price=price, open_time=active_trade[symbol].get("open_time"))
                        del active_trade[symbol]

                if symbol in active_trade:
                    continue

                # ─────────────────────────────────────────────
                # ENTRY LOGIC  (priority: trend-following > reversal)
                # ─────────────────────────────────────────────

                # 1. Trend-following BUY — HTF hidden bull + LTF hidden bull
                if hbull and hbull_i2 is not None and htf_hidden_bull:
                    cdt = _cdt(hbull_i2)
                    if last_div_time.setdefault(symbol, {}).get("HBULL") != cdt:
                        if double_confirm(symbol, "BUY") == "BUY":
                            last_div_time[symbol]["HBULL"] = cdt
                            _open_buy("Trend-Following", "Hidden Bull", hbull_i1, hbull_i2, consume_hidden=True)
                            continue

                # 2. Trend-following SELL — HTF hidden bear + LTF hidden bear
                if hbear and hbear_i2 is not None and htf_hidden_bear:
                    cdt = _cdt(hbear_i2)
                    if last_div_time.setdefault(symbol, {}).get("HBEAR") != cdt:
                        if double_confirm(symbol, "SELL") == "SELL":
                            last_div_time[symbol]["HBEAR"] = cdt
                            _open_sell("Trend-Following", "Hidden Bear", hbear_i1, hbear_i2, consume_hidden=True)
                            continue

                # 3. Reversal BUY — HTF regular bull + LTF regular bull
                if bull and bull_i2 is not None and htf_bull:
                    cdt = _cdt(bull_i2)
                    if last_div_time.setdefault(symbol, {}).get("BULL") != cdt:
                        if double_confirm(symbol, "BUY") == "BUY":
                            last_div_time[symbol]["BULL"] = cdt
                            _open_buy("Reversal", "Regular Bull", bull_i1, bull_i2, consume_hidden=False)
                            continue

                # 4. Reversal SELL — HTF regular bear + LTF regular bear
                if bear and bear_i2 is not None and htf_bear:
                    cdt = _cdt(bear_i2)
                    if last_div_time.setdefault(symbol, {}).get("BEAR") != cdt:
                        if double_confirm(symbol, "SELL") == "SELL":
                            last_div_time[symbol]["BEAR"] = cdt
                            _open_sell("Reversal", "Regular Bear", bear_i1, bear_i2, consume_hidden=False)
                            continue

                # ── Watch alerts — LTF signal with no HTF match (inform, don't enter) ──
                async def _watch(key, msg_text):
                    if last_div_time.setdefault(symbol, {}).get(key) != msg_text[:30]:
                        last_div_time[symbol][key] = msg_text[:30]
                        print(msg_text)
                        await send_telegram(msg_text)

                if hbull and hbull_i2 is not None and not htf_hidden_bull:
                    await _watch("W_HBULL", (
                        f"👁 LTF Hidden Bull — {symbol} ({INTERVAL})\n"
                        f"Price: Higher Low | RSI: Lower Low\n"
                        f"Structure: {struct_icon} {htf_structure} | RSI: {rsi}\n"
                        f"No {HTF_INTERVAL} hidden bull yet — watching, not entering"
                    ))

                if hbear and hbear_i2 is not None and not htf_hidden_bear:
                    await _watch("W_HBEAR", (
                        f"👁 LTF Hidden Bear — {symbol} ({INTERVAL})\n"
                        f"Price: Lower High | RSI: Higher High\n"
                        f"Structure: {struct_icon} {htf_structure} | RSI: {rsi}\n"
                        f"No {HTF_INTERVAL} hidden bear yet — watching, not entering"
                    ))

                if bull and bull_i2 is not None and not htf_bull:
                    await _watch("W_BULL", (
                        f"👁 LTF Regular Bull — {symbol} ({INTERVAL})\n"
                        f"Price: Lower Low | RSI: Higher Low\n"
                        f"Structure: {struct_icon} {htf_structure} | RSI: {rsi}\n"
                        f"No {HTF_INTERVAL} regular bull yet — watching, not entering"
                    ))

                if bear and bear_i2 is not None and not htf_bear:
                    await _watch("W_BEAR", (
                        f"👁 LTF Regular Bear — {symbol} ({INTERVAL})\n"
                        f"Price: Higher High | RSI: Lower High\n"
                        f"Structure: {struct_icon} {htf_structure} | RSI: {rsi}\n"
                        f"No {HTF_INTERVAL} regular bear yet — watching, not entering"
                    ))

            save_state(sess_on, sessions)
            await asyncio.sleep(300)

        except Exception as e:
            print(f"Runtime error: {e}")
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
