#%%writefile live_strategy_functions.py
import numpy as np
import pandas as pd

# --- Helper functions for live adaptation --- #
# These functions are designed to operate on small, recent data windows

def find_swing_highs_live(df_window, window_size):
    """
    Identifies a swing high for the candidate candle in a live context.
    The candidate candle is the one at index `window_size` within `df_window`.
    A swing high is confirmed if this candidate candle's high is the highest
    within the `window_size` candles before and `window_size` candles after it.

    Args:
        df_window (pd.DataFrame): A DataFrame containing at least (2 * window_size + 1) candles.
                                  The candidate swing high is at df_window.iloc[window_size].
        window_size (int): The number of candles before and after to check.

    Returns:
        tuple: (datetime, high_value) if a swing high is confirmed for the candidate,
               otherwise None.
    """
    if len(df_window) < (2 * window_size + 1):
        return None

    candidate_idx_in_window = window_size
    candidate_high = df_window['High'].iloc[candidate_idx_in_window]

    # Check if candidate_high is the maximum in the full window
    # Use .max() on the Series directly for efficiency
    if candidate_high == df_window['High'].iloc[candidate_idx_in_window - window_size : candidate_idx_in_window + window_size + 1].max():
        return (df_window.index[candidate_idx_in_window], candidate_high)
    return None

def find_swing_lows_live(df_window, window_size):
    """
    Identifies a swing low for the candidate candle in a live context.
    The candidate candle is the one at index `window_size` within `df_window`.
    A swing low is confirmed if this candidate candle's low is the lowest
    within the `window_size` candles before and `window_size` candles after it.

    Args:
        df_window (pd.DataFrame): A DataFrame containing at least (2 * window_size + 1) candles.
                                  The candidate swing low is at df_window.iloc[window_size].
        window_size (int): The number of candles before and after to check.

    Returns:
        tuple: (datetime, low_value) if a swing low is confirmed for the candidate,
               otherwise None.
    """
    if len(df_window) < (2 * window_size + 1):
        return None

    candidate_idx_in_window = window_size
    candidate_low = df_window['Low'].iloc[candidate_idx_in_window]

    # Check if candidate_low is the minimum in the full window
    # Use .min() on the Series directly for efficiency
    if candidate_low == df_window['Low'].iloc[candidate_idx_in_window - window_size : candidate_idx_in_window + window_size + 1].min():
        return (df_window.index[candidate_idx_in_window], candidate_low)
    return None

def find_resistance_live(df_window, window_size):
    """
    Identifies a resistance level, similar to a swing high.
    """
    return find_swing_highs_live(df_window, window_size)

def find_support_live(df_window, window_size):
    """
    Identifies a support level, similar to a swing low.
    """
    return find_swing_lows_live(df_window, window_size)

def find_fair_value_gaps_live(df_last_3_candles):
    """
    Identifies bullish or bearish Fair Value Gaps (FVG) based on the last 3 candles.

    Args:
        df_last_3_candles (pd.DataFrame): A DataFrame containing exactly 3 candles.

    Returns:
        tuple: ('BullishFVG', fvg_level) or ('BearishFVG', fvg_level) if an FVG is found,
               otherwise None.
    """
    if len(df_last_3_candles) < 3:
        return None

    candle1 = df_last_3_candles.iloc[0] # First candle in the pattern
    candle2 = df_last_3_candles.iloc[1] # Second candle in the pattern
    candle3 = df_last_3_candles.iloc[2] # Third candle in the pattern
    current_datetime = df_last_3_candles.index[2]

    # Bullish FVG condition:
    # Low of C1 > High of C3, and High of C2 is contained within the gap (i.e., C3_High < C2_Low)
    if candle1['Low'] > candle3['High'] and candle3['High'] < candle2['Low']:
        # FVG is typically defined by the Low of candle1 and High of candle3
        # For a bullish FVG, the lower boundary is the High of C3, and upper is Low of C1
        # We return the top of the gap for a bullish FVG level.
        return ('BullishFVG', candle1['Low'])

    # Bearish FVG condition:
    # High of C1 < Low of C3, and Low of C2 is contained within the gap (i.e., C3_Low > C2_High)
    elif candle1['High'] < candle3['Low'] and candle3['Low'] > candle2['High']:
        # For a bearish FVG, the upper boundary is the Low of C3, and lower is High of C1
        # We return the bottom of the gap for a bearish FVG level.
        return ('BearishFVG', candle1['High'])

    return None


def determine_market_structure_live(current_candle_htf, confirmed_sh_history, confirmed_sl_history):
    """
    Determines the market structure for the current HTF candle based on recent confirmed swing points.

    Args:
        current_candle_htf (pd.Series): The latest completed HTF candle.
        confirmed_sh_history (list): A list of (timestamp, value) for recent confirmed swing highs.
        confirmed_sl_history (list): A list of (timestamp, value) for recent confirmed swing lows.

    Returns:
        str: 'Bullish', 'Bearish', or 'Ranging' for the current candle.
    """
    current_structure = 'Ranging'

    # We need at least two swing highs and two swing lows to make a clear determination
    if len(confirmed_sh_history) >= 2 and len(confirmed_sl_history) >= 2:
        # Get the last two swing highs (most recent first)
        sh2_ts, sh2_val = confirmed_sh_history[-1]
        sh1_ts, sh1_val = confirmed_sh_history[-2]

        # Get the last two swing lows (most recent first)
        sl2_ts, sl2_val = confirmed_sl_history[-1]
        sl1_ts, sl1_val = confirmed_sl_history[-2]

        is_hh = sh2_val > sh1_val
        is_hl = sl2_val > sl1_val
        is_ll = sl2_val < sl1_val
        is_lh = sh2_val < sh1_val

        # The order of confirmed SH/SL is also important for clear structure
        # For Bullish: We need a sequence of (SL < SH) then (SL > previous SL and SH > previous SH)
        # For Bearish: We need a sequence of (SH > SL) then (SH < previous SH and SL < previous SL)
        # Simplified check based on just the last two pairs:
        if is_hh and is_hl and sl2_ts > sh2_ts: # Assuming SL formed after SH for bullish leg (simplified)
            current_structure = 'Bullish'
        elif is_ll and is_lh and sh2_ts > sl2_ts: # Assuming SH formed after SL for bearish leg (simplified)
            current_structure = 'Bearish'
        # Otherwise, it's ranging or unclear

    return current_structure

def identify_bos_live(current_candle_htf, market_structure, last_confirmed_sh, last_confirmed_sl):
    """
    Identifies a Break of Structure (BOS) for the current HTF candle.

    Args:
        current_candle_htf (pd.Series): The latest completed HTF candle.
        market_structure (str): 'Bullish', 'Bearish', or 'Ranging' for the current context.
        last_confirmed_sh (tuple): (timestamp, value) of the most recent confirmed swing high.
        last_confirmed_sl (tuple): (timestamp, value) of the most recent confirmed swing low.

    Returns:
        str: 'Bullish BOS', 'Bearish BOS', or None.
    """
    bos_signal = None
    current_close = current_candle_htf['Close']
    current_timestamp = current_candle_htf.name

    if market_structure == 'Bullish' and last_confirmed_sh is not None:
        sh_ts, sh_val = last_confirmed_sh
        # BOS is confirmed if the current candle closes above the previous swing high after it was established
        if current_timestamp > sh_ts and current_close > sh_val:
            bos_signal = 'Bullish BOS'
    elif market_structure == 'Bearish' and last_confirmed_sl is not None:
        sl_ts, sl_val = last_confirmed_sl
        # BOS is confirmed if the current candle closes below the previous swing low after it was established
        if current_timestamp > sl_ts and current_close < sl_val:
            bos_signal = 'Bearish BOS'

    return bos_signal

def mark_supply_demand_zones_live(latest_confirmed_sh, latest_confirmed_sl):
    """
    Returns the levels for the most recent supply and demand zones based on confirmed swing points.

    Args:
        latest_confirmed_sh (tuple): (timestamp, value) of the most recent confirmed swing high.
        latest_confirmed_sl (tuple): (timestamp, value) of the most recent confirmed swing low.

    Returns:
        tuple: (supply_zone_level, demand_zone_level). Levels are None if no corresponding SH/SL.
    """
    supply_zone = latest_confirmed_sh[1] if latest_confirmed_sh else np.nan
    demand_zone = latest_confirmed_sl[1] if latest_confirmed_sl else np.nan
    return supply_zone, demand_zone

def identify_str_rts_live(current_candle_mtf, last_confirmed_res, last_confirmed_sup,
                          potential_rts_levels, potential_str_levels, retest_tolerance_percent=0.001):
    """
    Identifies STR/RTS reversals for the current MTF candle and updates potential levels.

    Args:
        current_candle_mtf (pd.Series): The latest completed MTF candle.
        last_confirmed_res (tuple): (timestamp, value) of the most recent confirmed resistance.
        last_confirmed_sup (tuple): (timestamp, value) of the most recent confirmed support.
        potential_rts_levels (list): A list of (level_price, breach_timestamp) for potential RTS (modified in place).
        potential_str_levels (list): A list of (level_price, breach_timestamp) for potential STR (modified in place).
        retest_tolerance_percent (float): Percentage tolerance for retest.

    Returns:
        tuple: (rts_signal_info, str_signal_info)
               rts_signal_info/str_signal_info will be (level_price, 'RTS'/'STR') or None.
    """
    rts_signal_info = None
    str_signal_info = None
    current_timestamp = current_candle_mtf.name
    current_close = current_candle_mtf['Close']
    current_high = current_candle_mtf['High']
    current_low = current_candle_mtf['Low']

    # Update potential_rts_levels and potential_str_levels based on new breaches
    if last_confirmed_res and current_close > last_confirmed_res[1] and current_timestamp > last_confirmed_res[0]:
        # Resistance broken, now potentially a support (RTS)
        # Ensure we don't add the same level multiple times if already in potential_rts_levels
        if (last_confirmed_res[1], last_confirmed_res[0]) not in potential_rts_levels:
            potential_rts_levels.append((last_confirmed_res[1], current_timestamp))

    if last_confirmed_sup and current_close < last_confirmed_sup[1] and current_timestamp > last_confirmed_sup[0]:
        # Support broken, now potentially a resistance (STR)
        if (last_confirmed_sup[1], last_confirmed_sup[0]) not in potential_str_levels:
            potential_str_levels.append((last_confirmed_sup[1], current_timestamp))

    # Detect RTS reversal (Resistance-Turn-Support)
    newly_confirmed_rts = []
    for level, breach_ts in list(potential_rts_levels): # Iterate over a copy to allow removal
        if current_timestamp > breach_ts: # Ensure retest happens after breach
            if abs(current_low - level) / level <= retest_tolerance_percent: # Price touches the level within tolerance
                if current_close > level: # Retest confirmed as support (closes above the breached level)
                    rts_signal_info = (level, 'RTS')
                    newly_confirmed_rts.append((level, breach_ts))
    # Remove confirmed RTS levels from the list
    for item in newly_confirmed_rts:
        if item in potential_rts_levels:
            potential_rts_levels.remove(item)

    # Detect STR reversal (Support-Turn-Resistance)
    newly_confirmed_str = []
    for level, breach_ts in list(potential_str_levels): # Iterate over a copy to allow removal
        if current_timestamp > breach_ts: # Ensure retest happens after breach
            if abs(current_high - level) / level <= retest_tolerance_percent: # Price touches the level within tolerance
                if current_close < level: # Retest confirmed as resistance (closes below the breached level)
                    str_signal_info = (level, 'STR')
                    newly_confirmed_str.append((level, breach_ts))
    # Remove confirmed STR levels from the list
    for item in newly_confirmed_str:
        if item in potential_str_levels:
            potential_str_levels.remove(item)

    return rts_signal_info, str_signal_info

def define_entry_conditions_live(current_candle_ltf,
                                 htf_market_structure,
                                 htf_supply_zone_level, htf_demand_zone_level,
                                 mtf_bullish_fvg, mtf_bearish_fvg,
                                 mtf_rts_signal_info, mtf_str_signal_info,
                                 retest_tolerance_percent=0.0005):
    """
    Defines entry signals for the current LTF candle based on combined HTF/MTF analysis.

    Args:
        current_candle_ltf (pd.Series): The latest completed LTF candle.
        htf_market_structure (str): 'Bullish', 'Bearish', or 'Ranging' from HTF.
        htf_supply_zone_level (float): The current HTF supply zone level.
        htf_demand_zone_level (float): The current HTF demand zone level.
        mtf_bullish_fvg (tuple): (level, 'BullishFVG') or None.
        mtf_bearish_fvg (tuple): (level, 'BearishFVG') or None.
        mtf_rts_signal_info (tuple): (level, 'RTS') or None.
        mtf_str_signal_info (tuple): (level, 'STR') or None.
        retest_tolerance_percent (float): Percentage tolerance for retest.

    Returns:
        tuple: ('Long Entry', entry_type) or ('Short Entry', entry_type) or (None, None).
    """
    long_signal = None
    short_signal = None
    current_ltf_low = current_candle_ltf['Low']
    current_ltf_high = current_candle_ltf['High']

    # Extract levels from signal info tuples if they exist
    mtf_bullish_fvg_level = mtf_bullish_fvg[0] if mtf_bullish_fvg else np.nan
    mtf_bearish_fvg_level = mtf_bearish_fvg[0] if mtf_bearish_fvg else np.nan
    mtf_rts_level = mtf_rts_signal_info[0] if mtf_rts_signal_info else np.nan
    mtf_str_level = mtf_str_signal_info[0] if mtf_str_signal_info else np.nan

    # Bullish Entry Conditions
    if htf_market_structure == 'Bullish':
        # Retest of 4h DemandZone
        if pd.notna(htf_demand_zone_level):
            if current_ltf_low <= htf_demand_zone_level and \
               current_ltf_low >= htf_demand_zone_level * (1 - retest_tolerance_percent):
                long_signal = ('Long Entry', '4h Demand Retest')

        # Retest of 30min BullishFVG
        if pd.notna(mtf_bullish_fvg_level) and long_signal is None:
            if current_ltf_low <= mtf_bullish_fvg_level and \
               current_ltf_low >= mtf_bullish_fvg_level * (1 - retest_tolerance_percent):
                long_signal = ('Long Entry', 'MTF FVG Retest')

        # Retest of 30min RTS_Signal
        if pd.notna(mtf_rts_level) and long_signal is None:
            if current_ltf_low <= mtf_rts_level and \
               current_ltf_low >= mtf_rts_level * (1 - retest_tolerance_percent):
                long_signal = ('Long Entry', 'MTF RTS Level Retest')

    # Bearish Entry Conditions
    elif htf_market_structure == 'Bearish':
        # Retest of 4h SupplyZone
        if pd.notna(htf_supply_zone_level):
            if current_ltf_high >= htf_supply_zone_level and \
               current_ltf_high <= htf_supply_zone_level * (1 + retest_tolerance_percent):
                short_signal = ('Short Entry', '4h Supply Retest')

        # Retest of 30min BearishFVG
        if pd.notna(mtf_bearish_fvg_level) and short_signal is None:
            if current_ltf_high >= mtf_bearish_fvg_level and \
               current_ltf_high <= mtf_bearish_fvg_level * (1 + retest_tolerance_percent):
                short_signal = ('Short Entry', 'MTF FVG Retest')

        # Retest of 30min STR_Signal
        if pd.notna(mtf_str_level) and short_signal is None:
            if current_ltf_high >= mtf_str_level and \
               current_ltf_high <= mtf_str_level * (1 + retest_tolerance_percent):
                short_signal = ('Short Entry', 'MTF STR Level Retest')

    return long_signal, short_signal

def define_exit_conditions_live(current_candle_ltf, open_trade_details):
    """
    Checks if an open trade's exit conditions (SL/TP) are met by the current LTF candle.

    Args:
        current_candle_ltf (pd.Series): The latest completed LTF candle.
        open_trade_details (dict): Dictionary containing details of the open trade:
                                   'EntryPrice', 'Type', 'StopLoss', 'TakeProfit'.

    Returns:
        tuple: (exit_price, trade_result) if trade closes, otherwise (None, None).
    """
    if open_trade_details is None:
        return None, None

    exit_price = None
    trade_result = None
    current_high = current_candle_ltf['High']
    current_low = current_candle_ltf['Low']

    if open_trade_details['Type'] == 'Long':
        if current_low <= open_trade_details['StopLoss']:
            exit_price = open_trade_details['StopLoss']
            trade_result = 'Loss'
        elif current_high >= open_trade_details['TakeProfit']:
            exit_price = open_trade_details['TakeProfit']
            trade_result = 'Win'
    elif open_trade_details['Type'] == 'Short':
        if current_high >= open_trade_details['StopLoss']:
            exit_price = open_trade_details['StopLoss']
            trade_result = 'Loss'
        elif current_low <= open_trade_details['TakeProfit']:
            exit_price = open_trade_details['TakeProfit']
            trade_result = 'Win'

    return exit_price, trade_result
