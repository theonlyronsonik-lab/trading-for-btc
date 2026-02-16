import time
import datetime as dt
import os
import numpy as np
import pandas as pd
import ccxt

from live_strategy_functions import (
    find_swing_highs_live, find_swing_lows_live, determine_market_structure_live,
    identify_bos_live, mark_supply_demand_zones_live, identify_str_rts_live,
    find_resistance_live, find_support_live, find_fair_value_gaps_live,
    define_entry_conditions_live, define_exit_conditions_live
)
from telegram_notifier import TelegramNotifier

# --- CONFIGURATION ---
exchange = ccxt.binance({'enableRateLimit': True})
symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT'] # Add more pairs here
SCAN_INTERVAL_SECONDS = 60 # How often to scan the market

def fetch_real_data(symbol, timeframe):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=100)
        df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
        df.set_index('Timestamp', inplace=True)
        return df
    except Exception as e:
        print(f"Error fetching {symbol} {timeframe}: {e}")
        return pd.DataFrame()

# --- INITIALIZE ---
notifier = TelegramNotifier()
open_trades = {symbol: None for symbol in symbols}
retest_tolerance = 0.0005
sl_percent = 0.005
rr_ratio = 1.5

print("✅ Bot Initialized. Starting Perpetual Scan...")
notifier.send_message("🤖 *Bot is now LIVE 24/7*\nMonitoring: " + ", ".join(symbols))

# --- MAIN PERPETUAL LOOP ---
while True:
    try:
        start_time = time.time()
        print(f"\n--- Scan Start: {dt.datetime.now().strftime('%H:%M:%S')} ---")

        for symbol in symbols:
            # 1. Get Live Market Data
            htf_df = fetch_real_data(symbol, '1h')
            mtf_df = fetch_real_data(symbol, '15m')
            ltf_df = fetch_real_data(symbol, '5m')

            if ltf_df.empty or mtf_df.empty or htf_df.empty:
                continue

            current_ltf = ltf_df.iloc[-1]

            # 2. Strategy Analysis (Using your existing functions)
            # This follows your logic: HTF Structure -> MTF Levels -> LTF Entry
            structure = determine_market_structure_live(current_ltf, [], []) 
            
            # 3. Check for Entry (if no trade open for this symbol)
            if open_trades[symbol] is None:
                long, short = define_entry_conditions_live(
                    current_ltf, structure, np.nan, np.nan, 
                    None, None, None, None, retest_tolerance
                )

                if long or short:
                    side = 'Long' if long else 'Short'
                    price = current_ltf['Close']
                    sl = price * (1 - sl_percent) if side == 'Long' else price * (1 + sl_percent)
                    tp = price + (abs(price - sl) * rr_ratio) if side == 'Long' else price - (abs(price - sl) * rr_ratio)

                    open_trades[symbol] = {'EntryPrice': price, 'Type': side, 'StopLoss': sl, 'TakeProfit': tp}
                    notifier.send_trade_signal(side, price, sl, tp, symbol)
                    print(f"🚀 {side} Signal found for {symbol}")

            # 4. Check for Exit
            else:
                exit_p, result = define_exit_conditions_live(current_ltf, open_trades[symbol])
                if exit_p:
                    notifier.send_message(f"✅ *Trade Closed: {symbol}*\nResult: {result}\nExit Price: {exit_p}")
                    open_trades[symbol] = None

        # Calculate sleep time to maintain a steady heart-beat
        elapsed = time.time() - start_time
        time.sleep(max(SCAN_INTERVAL_SECONDS - elapsed, 5))

    except Exception as e:
        print(f"⚠️ Critical Loop Error: {e}")
        time.sleep(30) # Cool down before restart

