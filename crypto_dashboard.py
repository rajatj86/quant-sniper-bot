#!/usr/bin/env python3
"""
=============================================================
  FREE AI CRYPTO TRADING BOT DASHBOARD (19-Point System)
  Binance Futures + Gemini AI + HTML Dashboard
=============================================================
"""

import requests, hmac, hashlib, time, json, threading, webbrowser, os
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify

# ===================== CONFIG =====================
BINANCE_KEY      = "aerqxpwACAnaZPcNA9DwQc8zRIAuTgEW9PEk5yeJRq8cTFEfX4AG3lZON79Jh9zQ"
BINANCE_SECRET   = "LzF31S9uMqIWbIbpeqb9AhyjOt7NopHKwgp1ngWs17iGiJn296dbGzJzIoraz6YV"
GEMINI_KEY       = "AIzaSyA0UMYMS7e11lK2t-c-IkOydYAtWj6EuuE"
TELEGRAM_TOKEN   = "8005708874:AAFgb-KNDWwNz03KSkQav_-WZda-cCYxAPg"
TELEGRAM_CHAT_ID = "687828695D"

FUTURES_MODE     = True
DEFAULT_LEVERAGE = 3
MARGIN_PER_TRADE = 3.0
SCAN_INTERVAL    = 300   # 5 minutes
MIN_SCORE        = 15    # Base score for valid signals. SMC concepts add bonus points.

# --- DUMMY / PAPER TRADING CONFIG ---
AUTO_TRADE_ENABLED = True
DUMMY_WALLET       = 20.0
MAX_DUMMY_TRADES   = 4
dummy_positions    = {} # {symbol: {entry, qty, side, sl, tp, score, etc}}
dummy_history      = [] # List of closed trades for performance tracking
# ==================================================

FAPI = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
BASE = FAPI if FUTURES_MODE else SAPI

app = Flask(__name__)
pending_signals = {}
active_trades = {}
live_scores = {}
funding_rates = {}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=10
        )
    except: pass

def _sign(params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def b_get(path, params=None):
    r = requests.get(f"{BASE}{path}", params=params or {}, timeout=10)
    return r.json()

def b_auth_post(path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    query = "&".join(f"{k}={v}" for k, v in p.items()) + f"&signature={_sign(p)}"
    r = requests.post(f"{BASE}{path}?{query}", headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=10)
    return r.json()

def b_auth_get(path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    query = "&".join(f"{k}={v}" for k, v in p.items()) + f"&signature={_sign(p)}"
    r = requests.get(f"{BASE}{path}?{query}", headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=10)
    return r.json()

def b_auth_delete(path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    query = "&".join(f"{k}={v}" for k, v in p.items()) + f"&signature={_sign(p)}"
    r = requests.delete(f"{BASE}{path}?{query}", headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=10)
    return r.json()

# --- MATH FUNCTIONS ---
def calc_ema(prices, length):
    if not prices: return 0
    k = 2 / (length + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_rsi(prices, period=14):
    if len(prices) < period + 1: return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(change if change > 0 else 0)
        losses.append(abs(change) if change < 0 else 0)
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis = []
    
    for i in range(period, len(prices)):
        change = prices[i] - prices[i-1]
        gain = change if change > 0 else 0
        loss = abs(change) if change < 0 else 0
        
        avg_gain = (avg_gain * 13 + gain) / 14
        avg_loss = (avg_loss * 13 + loss) / 14
        
        if avg_loss == 0: rsis.append(100)
        else: rsis.append(100 - (100 / (1 + (avg_gain / avg_loss))))
    return rsis[-1] if rsis else 50

def calc_atr(klines, period=14):
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    subset = trs[-period:]
    return sum(subset) / len(subset) if subset else 0.0

def calc_bollinger(prices, period=20, std_dev=2):
    """Returns (upper_band, middle_band, lower_band, bandwidth, percent_b)"""
    if len(prices) < period: return 0, 0, 0, 0, 0.5
    subset = prices[-period:]
    sma = sum(subset) / period
    variance = sum((p - sma) ** 2 for p in subset) / period
    std = variance ** 0.5
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    bw = ((upper - lower) / sma) * 100 if sma > 0 else 0  # Bandwidth %
    pb = (prices[-1] - lower) / (upper - lower) if (upper - lower) > 0 else 0.5  # %B
    return upper, sma, lower, bw, pb

def calc_cmf(klines, period=20):
    """Chaikin Money Flow - Hidden institutional indicator.
    Measures buying/selling pressure using price AND volume.
    CMF > +0.1 = Institutions BUYING (Accumulation)
    CMF < -0.1 = Institutions SELLING (Distribution)
    Most retail traders use RSI/MACD and miss this completely."""
    if len(klines) < period: return 0
    cmf_sum = 0; vol_sum = 0
    for k in klines[-period:]:
        h, l, c, v = float(k[2]), float(k[3]), float(k[4]), float(k[5])
        if h == l: mfm = 0
        else: mfm = ((c - l) - (h - c)) / (h - l)  # Money Flow Multiplier
        mfv = mfm * v  # Money Flow Volume
        cmf_sum += mfv; vol_sum += v
    return cmf_sum / vol_sum if vol_sum > 0 else 0

def calc_adx(klines, period=14):
    """Average Directional Index - Measures Trend Strength.
    ADX > 25 = Strong Trend (Perfect for breakout/sniper)
    ADX < 20 = Ranging/Weak (Avoid trading)"""
    if len(klines) < period * 2: return 0
    up_moves, down_moves = [], []
    for i in range(1, len(klines)):
        h, l, ph, pl = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][2]), float(klines[i-1][3])
        up = h - ph; down = pl - l
        up_moves.append(up if up > down and up > 0 else 0)
        down_moves.append(down if down > up and down > 0 else 0)
    
    # Simplified ADX logic for performance
    plus_di = sum(up_moves[-period:]) / period
    minus_di = sum(down_moves[-period:]) / period
    if (plus_di + minus_di) == 0: return 0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
    return dx

def detect_fvg(klines):
    """Fair Value Gap - Institutional Inefficiency.
    Returns +5 for Bullish FVG, -5 for Bearish FVG, else 0."""
    if len(klines) < 3: return 0
    # Bullish FVG: Gap between candle 1 high and candle 3 low
    k1_h, k3_l = float(klines[-3][2]), float(klines[-1][3])
    if k3_l > k1_h: return 5
    # Bearish FVG: Gap between candle 1 low and candle 3 high
    k1_l, k3_h = float(klines[-3][3]), float(klines[-1][2])
    if k3_h < k1_l: return -5
    return 0

def detect_ob(klines):
    """Order Block - Supply/Demand Zones.
    Detects if current price is within a recent institutional zone."""
    if len(klines) < 10: return 0
    # Simplified: Check if last 10 candles have a massive volume surge (>2x avg)
    vols = [float(k[5]) for k in klines[-20:-1]]
    avg_vol = sum(vols) / len(vols)
    for i in range(-5, -1):
        if float(klines[i][5]) > avg_vol * 2.5:
            # Found a high-vol candle (Institutional footprint)
            # If it was green, it's a Demand Zone (Long), if red, Supply (Short)
            if float(klines[i][4]) > float(klines[i][1]): return 5 # Demand
            else: return -5 # Supply
    return 0

def knn_predict(klines, pattern_length=5, lookforward=3, k=3):
    """
    Quant AI: K-Nearest Neighbors Pattern Matching Algorithm.
    Searches historical data for patterns similar to the current one
    and predicts the win probability based on past outcomes.
    """
    if len(klines) < pattern_length * 3 + lookforward: return 0.0, 0.0

    closes = [float(candle[4]) for candle in klines]
    curr_slice = closes[-pattern_length:]
    curr_base = curr_slice[0]
    if curr_base == 0: return 0.0, 0.0
    
    curr_pattern = [(p - curr_base) / curr_base * 100 for p in curr_slice]
    distances = []
    
    for i in range(len(closes) - pattern_length - lookforward - 1):
        hist_slice = closes[i : i + pattern_length]
        hist_base = hist_slice[0]
        if hist_base == 0: continue
        
        hist_pattern = [(p - hist_base) / hist_base * 100 for p in hist_slice]
        dist = sum((curr_pattern[j] - hist_pattern[j])**2 for j in range(pattern_length)) ** 0.5
        
        future_price = closes[i + pattern_length + lookforward]
        end_of_pattern_price = closes[i + pattern_length - 1]
        if end_of_pattern_price == 0: continue
        future_move_pct = (future_price - end_of_pattern_price) / end_of_pattern_price * 100
        
        distances.append((dist, future_move_pct))
    
    distances.sort(key=lambda x: x[0])
    top_k = distances[:k]
    if not top_k: return 0.0, 0.0
    
    avg_future_move = sum(match[1] for match in top_k) / k
    if avg_future_move > 0:
        win_prob = (sum(1 for match in top_k if match[1] > 0) / k) * 100
    else:
        win_prob = (sum(1 for match in top_k if match[1] < 0) / k) * 100
        
    return win_prob, avg_future_move

# --- SCORING ENGINE (65-POINT ML INSTITUTIONAL) ---
def get_19_point_score(symbol):
    try:
        k_1h = b_get("/fapi/v1/klines" if FUTURES_MODE else "/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": 40})
        k_15m = b_get("/fapi/v1/klines" if FUTURES_MODE else "/api/v3/klines", {"symbol": symbol, "interval": "15m", "limit": 200}) # 200 for ML
        k_4h = b_get("/fapi/v1/klines" if FUTURES_MODE else "/api/v3/klines", {"symbol": symbol, "interval": "4h", "limit": 40})
        k_5m = b_get("/fapi/v1/klines" if FUTURES_MODE else "/api/v3/klines", {"symbol": symbol, "interval": "5m", "limit": 40})
        k_1w = b_get("/fapi/v1/klines" if FUTURES_MODE else "/api/v3/klines", {"symbol": symbol, "interval": "1w", "limit": 25})
        
        c_1h = [float(k[4]) for k in k_1h]
        c_15m = [float(k[4]) for k in k_15m]
        c_4h = [float(k[4]) for k in k_4h]
        c_5m = [float(k[4]) for k in k_5m]
        c_1w = [float(k[4]) for k in k_1w]
        
        if len(c_1h) < 25 or len(c_15m) < 25: return 0, "LONG", {}, []
        
        price = c_15m[-1]
        
        # --- LONG SCORING ---
        l_score = 0; l_det = {}
        
        fr = funding_rates.get(symbol, 0)
        fr_s = 0
        if fr < -0.005: fr_s = 3
        elif fr < -0.001: fr_s = 1
        l_score += fr_s; l_det['Funding Sqz'] = f"{fr*100:.3f}% (+{fr_s})"
        
        if len(k_1h) >= 24:
            vol_sum = sum(float(k[5]) for k in k_1h[-24:])
            pv_sum = sum(((float(k[2])+float(k[3])+float(k[4]))/3) * float(k[5]) for k in k_1h[-24:])
            vwap = pv_sum / vol_sum if vol_sum > 0 else c_1h[-1]
        else: vwap = c_1h[-1]
        
        vwap_s = 2 if price > vwap else 0
        l_score += vwap_s; l_det['VWAP'] = f"Above (+{vwap_s})" if vwap_s > 0 else "Below (+0)"
        
        lows_4h = [float(k[3]) for k in k_4h[-11:-1]] if len(k_4h) > 10 else []
        support_4h = min(lows_4h) if lows_4h else float(k_15m[-1][3])
        c_low = float(k_15m[-1][3])
        c_close = float(k_15m[-1][4])
        sweep_s = 3 if (c_low < support_4h and c_close > support_4h) else 0
        l_score += sweep_s; l_det['Liq Sweep'] = f"Yes (+{sweep_s})" if sweep_s > 0 else "No (+0)"
        
        rsi_curr = calc_rsi(c_15m)
        rsi_prev = calc_rsi(c_15m[:-5]) if len(c_15m) > 10 else 50
        div_s = 0
        if len(c_15m) > 10 and c_15m[-1] < c_15m[-6] and rsi_curr > rsi_prev: div_s = 3
        l_score += div_s; l_det['RSI Div'] = f"Bullish (+{div_s})" if div_s > 0 else "None (+0)"

        rsi_1h = calc_rsi(c_1h)
        if 48 <= rsi_1h <= 66: s=3
        elif 44 <= rsi_1h < 48: s=1
        else: s=0
        l_score += s; l_det['RSI 1h'] = f"{rsi_1h:.1f} (+{s})"
        
        rsi_15m = calc_rsi(c_15m)
        if 48 <= rsi_15m <= 66: s=2
        elif 44 <= rsi_15m < 48: s=1
        else: s=0
        l_score += s; l_det['RSI 15m'] = f"{rsi_15m:.1f} (+{s})"
        
        aligns = 0
        if calc_ema(c_4h, 9) > calc_ema(c_4h, 21): aligns += 1
        if calc_ema(c_1h, 9) > calc_ema(c_1h, 21): aligns += 1
        if calc_ema(c_15m, 9) > calc_ema(c_15m, 21): aligns += 1
        if calc_ema(c_5m, 9) > calc_ema(c_5m, 21): aligns += 1
        if price > calc_ema(c_1h, 9): aligns += 1
        if price > calc_ema(c_15m, 9): aligns += 1
        if aligns >= 5: s=3
        elif aligns >= 3: s=2
        else: s=0
        l_score += s; l_det['TF Align'] = f"{aligns}/6 (+{s})"
        
        v_15m = [float(k[5]) for k in k_15m]
        v_last2 = sum(v_15m[-2:]) / 2
        v_prev4 = sum(v_15m[-6:-2]) / 4 if len(v_15m) >= 6 else 1
        surge = (v_last2 / v_prev4) if v_prev4 > 0 else 1
        if surge >= 1.5: s=3
        elif surge >= 1.2: s=2
        elif surge > 1.0: s=1
        else: s=0
        l_score += s; l_det['Vol Surge'] = f"{surge:.2f}x (+{s})"
        s_vol = s; s_vol_text = l_det['Vol Surge']
        
        c_high = float(k_15m[-1][2])
        c_low = float(k_15m[-1][3])
        c_close = float(k_15m[-1][4])
        c_open = float(k_15m[-1][1])
        c_size = c_high - c_low
        upper_wick = c_high - max(c_open, c_close) if c_size > 0 else 0
        lower_wick = min(c_open, c_close) - c_low if c_size > 0 else 0
        
        if c_size > 0 and (upper_wick / c_size) > 0.45:
            s = -3
            l_score += s; l_det['Fake Pump Risk'] = f"Wick Rejection ({s})"
        
        ticker24 = b_get("/fapi/v1/ticker/24hr" if FUTURES_MODE else "/api/v3/ticker/24hr", {"symbol": symbol})
        if isinstance(ticker24, dict):
            h24, l24 = float(ticker24.get('highPrice', price)), float(ticker24.get('lowPrice', price))
            c24 = float(ticker24.get('priceChangePercent', 0))
            range_pct = (price - l24) / (h24 - l24) * 100 if h24 > l24 else 0
        else: range_pct = 0; c24 = 0
            
        if 35 <= range_pct <= 75: s=2
        else: s=0
        l_score += s; l_det['Range'] = f"{range_pct:.1f}% (+{s})"
        
        if c_15m[-1] > c_15m[-3] > c_15m[-5]: s=2
        elif c_1h[-1] > c_1h[-2]: s=1
        else: s=0
        l_score += s; l_det['15m Mom'] = f"True (+{s})"
        
        b = 0
        if c24 > 5: b+=1
        if c_5m[-1] > c_5m[-3] > c_5m[-5]: b+=1
        grn = sum(1 for i in range(-5, 0) if float(k_15m[i][4]) > float(k_15m[i][1]))
        if grn >= 3: b+=1
        s = min(b, 3)
        l_score += s; l_det['Bonus'] = f"G:{c24:.1f}% Grn:{grn}/5 (+{s})"
        
        # --- MULTI-TF BOLLINGER BANDS (LONG) ---
        bb_upper, bb_mid, bb_lower, bb_bw, bb_pb = calc_bollinger(c_1h)
        bb_4h_upper, bb_4h_mid, bb_4h_lower, bb_4h_bw, bb_4h_pb = calc_bollinger(c_4h)
        bb_w_upper, bb_w_mid, bb_w_lower, bb_w_bw, bb_w_pb = calc_bollinger(c_1w)
        
        # Multi-TF BB Map (for display)
        l_det['BB 1H'] = f"%B:{bb_pb:.2f} | {'Upper' if bb_pb>0.8 else 'Mid' if bb_pb>0.4 else 'Lower'}"
        l_det['BB 4H'] = f"%B:{bb_4h_pb:.2f} | {'Upper' if bb_4h_pb>0.8 else 'Mid' if bb_4h_pb>0.4 else 'Lower'}"
        l_det['BB Weekly'] = f"%B:{bb_w_pb:.2f} | {'Upper' if bb_w_pb>0.8 else 'Mid' if bb_w_pb>0.4 else 'Lower'}"
        
        # 1. Multi-TF BB Alignment (Max +3): Multiple TF showing same zone
        bb_align = 0
        if bb_pb <= 0.35: bb_align += 1  # 1H near lower
        if bb_4h_pb <= 0.35: bb_align += 1  # 4H near lower
        if bb_w_pb <= 0.60: bb_align += 1  # Weekly not overbought (below mid-upper)
        bb_pos_s = min(bb_align, 3)
        l_score += bb_pos_s; l_det['BB Align'] = f"{bb_align}/3 TF Low (+{bb_pos_s})"
        
        # 2. BB Squeeze (Max +2): Tight bands = big move coming
        bb_sqz_s = 0
        if bb_4h_bw < 3.0: bb_sqz_s = 2  # Very tight squeeze
        elif bb_4h_bw < 5.0: bb_sqz_s = 1  # Moderate squeeze
        l_score += bb_sqz_s; l_det['BB Squeeze'] = f"BW:{bb_4h_bw:.1f}% (+{bb_sqz_s})"
        
        # 3. Fake Pump Filter (Max -2): Price above upper band WITHOUT volume = FAKE
        if bb_pb > 1.0 and surge < 1.3:
            fake_bb = -2
            l_score += fake_bb; l_det['BB Fake Pump'] = f"Above Band, Low Vol ({fake_bb})"
        
        # --- CMF: CHAIKIN MONEY FLOW (HIDDEN GEM) ---
        cmf_1h = calc_cmf(k_1h)
        cmf_4h = calc_cmf(k_4h)
        cmf_s = 0
        if cmf_1h > 0.10 and cmf_4h > 0.05: cmf_s = 3   # Strong institutional buying on both TFs
        elif cmf_1h > 0.05: cmf_s = 2                     # Moderate accumulation
        elif cmf_1h > 0.0: cmf_s = 1                       # Slight buying pressure
        elif cmf_1h < -0.10: cmf_s = -2                    # Institutions dumping = DO NOT BUY
        l_score += cmf_s; l_det['CMF (Hidden)'] = f"1H:{cmf_1h:.3f} 4H:{cmf_4h:.3f} ({'+' if cmf_s>=0 else ''}{cmf_s})"

        # --- ADVANCED INSTITUTIONAL (SMC) ---
        adx_1h = calc_adx(k_1h)
        adx_s = 5 if adx_1h > 25 else 2 if adx_1h > 20 else 0
        l_score += adx_s; l_det['ADX Trend'] = f"{adx_1h:.1f} ({'+' if adx_s>0 else ''}{adx_s})"
        
        fvg = detect_fvg(k_15m)
        fvg_s = 5 if fvg > 0 else 0
        l_score += fvg_s; l_det['SMC: FVG'] = "Bullish (+5)" if fvg_s > 0 else "None (+0)"
        
        ob = detect_ob(k_1h)
        ob_s = 5 if ob > 0 else 0
        l_score += ob_s; l_det['SMC: OrderBlock'] = "Demand Zone (+5)" if ob_s > 0 else "None (+0)"

        # --- ML PREDICTION (KNN) ---
        win_prob, avg_move = knn_predict(k_15m)
        ml_s = 0
        if avg_move > 0:
            if win_prob >= 80: ml_s = 5
            elif win_prob >= 65: ml_s = 3
        l_score += ml_s; l_det['Quant AI (KNN)'] = f"{win_prob:.0f}% Win Probability (+{ml_s})"

        # --- SHORT SCORING ---
        s_score = 0; s_det = {}
        
        fr_s_short = 0
        if fr > 0.005: fr_s_short = 3
        elif fr > 0.001: fr_s_short = 1
        s_score += fr_s_short; s_det['Funding Sqz'] = f"{fr*100:.3f}% (+{fr_s_short})"
        
        vwap_s_short = 2 if price < vwap else 0
        s_score += vwap_s_short; s_det['VWAP'] = f"Below (+{vwap_s_short})" if vwap_s_short > 0 else "Above (+0)"
        
        highs_4h = [float(k[2]) for k in k_4h[-11:-1]] if len(k_4h) > 10 else []
        res_4h = max(highs_4h) if highs_4h else float(k_15m[-1][2])
        c_high = float(k_15m[-1][2])
        sweep_s_short = 3 if (c_high > res_4h and c_close < res_4h) else 0
        s_score += sweep_s_short; s_det['Liq Sweep'] = f"Yes (+{sweep_s_short})" if sweep_s_short > 0 else "No (+0)"
        
        div_s_short = 0
        if len(c_15m) > 10 and c_15m[-1] > c_15m[-6] and rsi_curr < rsi_prev: div_s_short = 3
        s_score += div_s_short; s_det['RSI Div'] = f"Bearish (+{div_s_short})" if div_s_short > 0 else "None (+0)"

        if 34 <= rsi_1h <= 52: s=3
        elif 52 < rsi_1h <= 56: s=1
        else: s=0
        s_score += s; s_det['RSI 1h'] = f"{rsi_1h:.1f} (+{s})"
        
        if 34 <= rsi_15m <= 52: s=2
        elif 52 < rsi_15m <= 56: s=1
        else: s=0
        s_score += s; s_det['RSI 15m'] = f"{rsi_15m:.1f} (+{s})"
        
        aligns_s = 0
        if calc_ema(c_4h, 9) < calc_ema(c_4h, 21): aligns_s += 1
        if calc_ema(c_1h, 9) < calc_ema(c_1h, 21): aligns_s += 1
        if calc_ema(c_15m, 9) < calc_ema(c_15m, 21): aligns_s += 1
        if calc_ema(c_5m, 9) < calc_ema(c_5m, 21): aligns_s += 1
        if price < calc_ema(c_1h, 9): aligns_s += 1
        if price < calc_ema(c_15m, 9): aligns_s += 1
        if aligns_s >= 5: s=3
        elif aligns_s >= 3: s=2
        else: s=0
        s_score += s; s_det['TF Align'] = f"{aligns_s}/6 (+{s})"
        
        s_score += s_vol; s_det['Vol Surge'] = s_vol_text
        
        if c_size > 0 and (lower_wick / c_size) > 0.45:
            s = -3
            s_score += s; s_det['Fake Dump Risk'] = f"Wick Rejection ({s})"
        
        if 25 <= range_pct <= 65: s=2
        else: s=0
        s_score += s; s_det['Range'] = f"{range_pct:.1f}% (+{s})"
        
        if c_15m[-1] < c_15m[-3] < c_15m[-5]: s=2
        elif c_1h[-1] < c_1h[-2]: s=1
        else: s=0
        s_score += s; s_det['15m Mom'] = f"True (+{s})"
        
        b = 0
        if c24 < -5: b+=1
        if c_5m[-1] < c_5m[-3] < c_5m[-5]: b+=1
        red = sum(1 for i in range(-5, 0) if float(k_15m[i][4]) < float(k_15m[i][1]))
        if red >= 3: b+=1
        s = min(b, 3)
        s_score += s; s_det['Bonus'] = f"R:{c24:.1f}% Red:{red}/5 (+{s})"
        
        # --- MULTI-TF BOLLINGER BANDS (SHORT) ---
        s_det['BB 1H'] = f"%B:{bb_pb:.2f} | {'Upper' if bb_pb>0.8 else 'Mid' if bb_pb>0.4 else 'Lower'}"
        s_det['BB 4H'] = f"%B:{bb_4h_pb:.2f} | {'Upper' if bb_4h_pb>0.8 else 'Mid' if bb_4h_pb>0.4 else 'Lower'}"
        s_det['BB Weekly'] = f"%B:{bb_w_pb:.2f} | {'Upper' if bb_w_pb>0.8 else 'Mid' if bb_w_pb>0.4 else 'Lower'}"
        
        # 1. Multi-TF BB Alignment (Max +3): Multiple TF near upper = overbought SHORT
        bb_align_s = 0
        if bb_pb >= 0.65: bb_align_s += 1
        if bb_4h_pb >= 0.65: bb_align_s += 1
        if bb_w_pb >= 0.40: bb_align_s += 1  # Weekly not oversold
        bb_pos_s_short = min(bb_align_s, 3)
        s_score += bb_pos_s_short; s_det['BB Align'] = f"{bb_align_s}/3 TF High (+{bb_pos_s_short})"
        
        # 2. BB Squeeze (Max +2): Same logic
        bb_sqz_s_short = 0
        if bb_4h_bw < 3.0: bb_sqz_s_short = 2
        elif bb_4h_bw < 5.0: bb_sqz_s_short = 1
        s_score += bb_sqz_s_short; s_det['BB Squeeze'] = f"BW:{bb_4h_bw:.1f}% (+{bb_sqz_s_short})"
        
        # 3. Fake Dump Filter (Max -2)
        if bb_pb < 0.0 and surge < 1.3:
            fake_bb_s = -2
            s_score += fake_bb_s; s_det['BB Fake Dump'] = f"Below Band, Low Vol ({fake_bb_s})"
        
        # --- CMF: CHAIKIN MONEY FLOW (SHORT) ---
        cmf_s_short = 0
        if cmf_1h < -0.10 and cmf_4h < -0.05: cmf_s_short = 3  # Strong distribution
        elif cmf_1h < -0.05: cmf_s_short = 2                     # Moderate selling
        elif cmf_1h < 0.0: cmf_s_short = 1                       # Slight selling
        elif cmf_1h > 0.10: cmf_s_short = -2                     # Institutions buying = DON'T SHORT
        s_score += cmf_s_short; s_det['CMF (Hidden)'] = f"1H:{cmf_1h:.3f} 4H:{cmf_4h:.3f} ({'+' if cmf_s_short>=0 else ''}{cmf_s_short})"

        # --- ADVANCED INSTITUTIONAL (SMC) ---
        s_score += adx_s; s_det['ADX Trend'] = f"{adx_1h:.1f} ({'+' if adx_s>0 else ''}{adx_s})"
        
        fvg_short = detect_fvg(k_15m)
        fvg_s_short = 5 if fvg_short < 0 else 0
        s_score += fvg_s_short; s_det['SMC: FVG'] = "Bearish (+5)" if fvg_s_short > 0 else "None (+0)"
        
        ob_short = detect_ob(k_1h)
        ob_s_short = 5 if ob_short < 0 else 0
        s_score += ob_s_short; s_det['SMC: OrderBlock'] = "Supply Zone (+5)" if ob_s_short > 0 else "None (+0)"
        
        # --- ML PREDICTION (KNN) ---
        win_prob_s, avg_move_s = knn_predict(k_15m)
        ml_s_short = 0
        if avg_move_s < 0:
            if win_prob_s >= 80: ml_s_short = 5
            elif win_prob_s >= 65: ml_s_short = 3
        s_score += ml_s_short; s_det['Quant AI (KNN)'] = f"{win_prob_s:.0f}% Win Probability (+{ml_s_short})"
        
        if l_score >= s_score: return l_score, "LONG", l_det, k_15m
        else: return s_score, "SHORT", s_det, k_15m
        
    except Exception as e:
        log(f"Scoring error {symbol}: {e}")
        return 0, "LONG", {}, []

def get_ai_signal(symbol, price, score_details, klines, direction):
    atr = calc_atr(klines)
    if direction == "LONG":
        sl  = round(price - 1.5 * atr, 8)
        tp1 = round(price + 2.0 * atr, 8)
        tp2 = round(price + 3.5 * atr, 8)
    else:
        sl  = round(price + 1.5 * atr, 8)
        tp1 = round(price - 2.0 * atr, 8)
        tp2 = round(price - 3.5 * atr, 8)
    
    prompt = f"""You are a crypto AI trader. Analyze {symbol}:
Price: {price}
Direction: {direction}
Score Details: {json.dumps(score_details)}
Pre-calc SL: {sl}, TP1: {tp1}, TP2: {tp2}
Reply JSON ONLY: {{"signal":"{direction}","confidence":"HIGH","sl":{sl},"tp1":{tp1},"tp2":{tp2},"reason":"..."}}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}]}, timeout=10)
        data = r.json()
        if "error" in data:
            return {"signal": direction, "confidence": "SYSTEM_CALC", "sl": sl, "tp1": tp1, "tp2": tp2, "reason": f"AI Error: {data['error'].get('message', 'Unknown API Error')}"}
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw.strip().replace("```json","").replace("```","").strip())
    except Exception as e:
        return {"signal": direction, "confidence": "SYSTEM_CALC", "sl": sl, "tp1": tp1, "tp2": tp2, "reason": "30-point system passed"}

def fetch_funding_rates():
    global funding_rates
    try:
        data = b_get("/fapi/v1/premiumIndex")
        funding_rates = {d['symbol']: float(d['lastFundingRate']) for d in data if 'lastFundingRate' in d}
    except:
        pass

# --- SCANNER FUNCTION ---
def run_scan():
    log("🔍 Manual Scan Triggered: Searching top 150 volume coins...")
    fetch_funding_rates()
    try:
        path = "/fapi/v1/ticker/24hr" if FUTURES_MODE else "/api/v3/ticker/24hr"
        valid = [t for t in b_get(path) if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > 1000000]
        valid.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
        
        # Scan top 150 coins to catch MITO, DOGS etc.
        for t in valid[:150]:
            sym = t["symbol"]
            if sym in pending_signals or sym in active_trades: continue
            
            score, direction, details, klines = get_19_point_score(sym)
            if score >= MIN_SCORE:
                price = float(t["lastPrice"])
                
                try:
                    ls_res = b_get("/futures/data/topLongShortPositionRatio", {"symbol": sym, "period": "15m", "limit": 1})
                    if isinstance(ls_res, list) and len(ls_res) > 0:
                        ls_ratio = float(ls_res[0].get("longShortRatio", 1.0))
                        details['Whales L/S Ratio'] = str(ls_ratio)
                        
                        if direction == "LONG" and ls_ratio > 1.2:
                            score += 2
                            details['Whale Bonus'] = "Longs Dominating (+2)"
                        elif direction == "SHORT" and ls_ratio < 0.8:
                            score += 2
                            details['Whale Bonus'] = "Shorts Dominating (+2)"
                            
                    # Fetch Open Interest History
                    oi_res = b_get("/futures/data/openInterestHist", {"symbol": sym, "period": "15m", "limit": 2})
                    if isinstance(oi_res, list) and len(oi_res) == 2:
                        oi_old = float(oi_res[0].get("sumOpenInterestValue", 1))
                        oi_new = float(oi_res[1].get("sumOpenInterestValue", 1))
                        oi_change = ((oi_new - oi_old) / oi_old) * 100
                        details['OI Change (15m)'] = f"{oi_change:+.2f}%"
                        
                        if oi_change > 1.0: 
                            score += 2
                            details['OI Surge'] = "New Money Entering (+2)"
                        elif oi_change < -1.0:
                            score -= 1
                            details['OI Drop'] = "Money Leaving (-1)"
                            
                except Exception as e:
                    pass

                ai_data = get_ai_signal(sym, price, details, klines, direction)
                
                pending_signals[sym] = {
                    "symbol": sym,
                    "price": price,
                    "score": score,
                    "direction": direction,
                    "details": details,
                    "ai": ai_data,
                    "timestamp": time.time()
                }
                grade = "⭐️⭐️⭐️⭐️⭐️ PERFECT" if score >= 55 else "⭐️⭐️⭐️⭐️ STRONG"
                dir_icon = "🟢" if direction == "LONG" else "🔴"
                log(f"🔔 NEW SIGNAL: {dir_icon} {direction} {sym} | Score: {score}/65 | {grade}")
                telegram(f"🔔 <b>NEW PENDING TRADE: {sym}</b>\nDir: {dir_icon} {direction}\nScore: {score}/65 ({grade})\nCheck Dashboard to Approve!")

                # --- AUTO DUMMY TRADE LOGIC ---
                if AUTO_TRADE_ENABLED and len(dummy_positions) < MAX_DUMMY_TRADES:
                    if score >= 38: # Boosted threshold for the new 65-point system
                        atr = calc_atr(klines)
                        sl = round(price - 1.5 * atr if direction == "LONG" else price + 1.5 * atr, 8)
                        tp = round(price + 2.5 * atr if direction == "LONG" else price - 2.5 * atr, 8)
                        
                        # Use 5 USDT margin per dummy trade (1/4 of $20)
                        margin = 5.0
                        qty = (margin * DEFAULT_LEVERAGE) / price
                        
                        dummy_positions[sym] = {
                            "symbol": sym, "entry": price, "qty": qty, "side": direction,
                            "sl": sl, "tp": tp, "score": score, "time": time.time(), "pnl": 0.0
                        }
                        log(f"🤖 AUTO-DUMMY: Entered {direction} {sym} at {price} (Score: {score})")
                        telegram(f"🤖 <b>AUTO-DUMMY ENTRY: {sym}</b>\nSide: {direction}\nPrice: {price}\nScore: {score}/65")
    except Exception as e:
        log(f"Scanner error: {e}")
    log("✅ Scan Complete.")

def update_scores_loop():
    while True:
        try:
            pos = b_auth_get("/fapi/v2/positionRisk")
            active_syms = [p['symbol'] for p in pos if float(p['positionAmt']) != 0]
            to_update = set(active_syms) | set(pending_signals.keys())
            
            for sym in to_update:
                score, direction, details, _ = get_19_point_score(sym)
                
                try:
                    ls_res = b_get("/futures/data/topLongShortPositionRatio", {"symbol": sym, "period": "15m", "limit": 1})
                    if isinstance(ls_res, list) and len(ls_res) > 0:
                        ls_ratio = float(ls_res[0].get("longShortRatio", 1.0))
                        details['Whales L/S Ratio'] = str(ls_ratio)
                        if direction == "LONG" and ls_ratio > 1.2:
                            score += 2; details['Whale Bonus'] = "Longs Dominating (+2)"
                        elif direction == "SHORT" and ls_ratio < 0.8:
                            score += 2; details['Whale Bonus'] = "Shorts Dominating (+2)"
                            
                    oi_res = b_get("/futures/data/openInterestHist", {"symbol": sym, "period": "15m", "limit": 2})
                    if isinstance(oi_res, list) and len(oi_res) == 2:
                        oi_old = float(oi_res[0].get("sumOpenInterestValue", 1))
                        oi_new = float(oi_res[1].get("sumOpenInterestValue", 1))
                        oi_change = ((oi_new - oi_old) / oi_old) * 100
                        details['OI Change (15m)'] = f"{oi_change:+.2f}%"
                        if oi_change > 1.0: 
                            score += 2; details['OI Surge'] = "New Money Entering (+2)"
                        elif oi_change < -1.0:
                            score -= 1; details['OI Drop'] = "Money Leaving (-1)"
                except: pass
                
                live_scores[sym] = {"score": score, "direction": direction, "details": details}
                
                if sym in pending_signals:
                    pending_signals[sym]['score'] = score
                    pending_signals[sym]['details'] = details
        except: pass
        time.sleep(30)

threading.Thread(target=update_scores_loop, daemon=True).start()

def dummy_trade_monitor():
    global DUMMY_WALLET
    while True:
        try:
            for sym in list(dummy_positions.keys()):
                pos = dummy_positions[sym]
                # Get current price
                ticker = b_get("/fapi/v1/ticker/price", {"symbol": sym})
                curr_price = float(ticker['price'])
                
                # Calculate PNL (Leveraged)
                diff = (curr_price - pos['entry']) / pos['entry']
                if pos['side'] == "SHORT": diff = -diff
                pos['pnl'] = diff * DEFAULT_LEVERAGE * 5.0 # on $5 margin
                
                # Check SL/TP
                closed = False
                reason = ""
                if pos['side'] == "LONG":
                    if curr_price <= pos['sl']: closed = True; reason = "STOP LOSS"
                    elif curr_price >= pos['tp']: closed = True; reason = "TAKE PROFIT"
                else:
                    if curr_price >= pos['sl']: closed = True; reason = "STOP LOSS"
                    elif curr_price <= pos['tp']: closed = True; reason = "TAKE PROFIT"
                
                if closed:
                    final_pnl = pos['pnl']
                    DUMMY_WALLET += final_pnl
                    dummy_history.append({**pos, "exit": curr_price, "final_pnl": final_pnl, "reason": reason})
                    del dummy_positions[sym]
                    log(f"🏁 DUMMY CLOSED: {sym} | Reason: {reason} | PnL: ${final_pnl:.2f}")
                    telegram(f"🏁 <b>DUMMY TRADE CLOSED: {sym}</b>\nReason: {reason}\nFinal PnL: ${final_pnl:.2f}\nNew Wallet: ${DUMMY_WALLET:.2f}")
        except Exception as e:
            log(f"Dummy monitor error: {e}")
        time.sleep(10)

threading.Thread(target=dummy_trade_monitor, daemon=True).start()

# --- FLASK DASHBOARD ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <title>Pro Crypto AI Terminal</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #0b0e11; color: #EAECEF; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .card { background-color: #181a20; border: 1px solid #2b3139; }
        .text-success { color: #0ecb81 !important; }
        .text-danger { color: #f6465d !important; }
        .sidebar { height: 100vh; overflow-y: auto; border-right: 1px solid #2b3139; background-color: #181a20; }
        .signal-item { cursor: pointer; transition: 0.2s; border-bottom: 1px solid #2b3139; padding: 15px; }
        .signal-item:hover, .signal-item.active { background-color: #2b3139; border-left: 4px solid #F0B90B; }
        .nav-tabs .nav-link { color: #848E9C; border: none; padding: 15px 20px; }
        .nav-tabs .nav-link.active { background-color: transparent; color: #F0B90B; border-bottom: 2px solid #F0B90B; }
        .score-badge { font-size: 1.2rem; font-weight: bold; color: #F0B90B; }
        .form-control { background-color: #2b3139; border: 1px solid #474D57; color: white; }
        .form-control:focus { background-color: #2b3139; color: white; border-color: #F0B90B; box-shadow: none; }
        .input-group-text { background-color: #181a20; border: 1px solid #474D57; color: #848E9C; }
        .top-bar { background-color: #181a20; border-bottom: 1px solid #2b3139; padding: 10px 20px; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #0b0e11; }
        ::-webkit-scrollbar-thumb { background: #2b3139; border-radius: 3px; }
    </style>
</head>
<body>
<div class="container-fluid p-0">
    <div class="row m-0">
        <!-- Sidebar -->
        <div class="col-md-3 sidebar p-0 d-flex flex-column">
            <div class="p-3 border-bottom border-dark d-flex justify-content-between align-items-center">
                <h4 class="m-0 text-white">⚡ AI Terminal</h4>
                <button class="btn btn-sm btn-outline-warning" id="scanBtn" onclick="startScan()">🔍 Scan Now</button>
            </div>
            
                <li class="nav-item w-50 text-center">
                    <a class="nav-link active" id="tab-signals" onclick="switchTab('signals')" style="cursor:pointer">Signals</a>
                </li>
                <li class="nav-item w-50 text-center">
                    <a class="nav-link" id="tab-positions" onclick="switchTab('positions')" style="cursor:pointer">Positions</a>
                </li>
                <li class="nav-item w-100 text-center border-top border-dark d-flex">
                    <a class="nav-link w-50 border-end border-dark" id="tab-paper" onclick="switchTab('paper')" style="cursor:pointer; color: #0dcaf0;">🧪 Paper</a>
                    <a class="nav-link w-50" id="tab-ai" onclick="switchTab('ai')" style="cursor:pointer; color: #a270ff;">🤖 AI Chat</a>
                </li>
            </ul>
            
            <!-- Signals List -->
            <div id="list-signals" style="flex: 1; overflow-y: auto;">
                <div class="p-4 text-center text-muted">Click 'Scan Now' to find trades.</div>
            </div>
            
            <!-- Positions List -->
            <div id="list-positions" style="display:none; flex: 1; overflow-y: auto;">
                <div class="p-4 text-center text-muted">No active positions.</div>
            </div>

            <!-- Paper Trading List -->
            <div id="list-paper" style="display:none; flex: 1; overflow-y: auto;">
                <div class="p-3 bg-dark-subtle border-bottom border-info text-info small fw-bold">
                    🧪 DUMMY WALLET: $<span id="dummyBalance">20.00</span>
                </div>
                <div id="paper-items">
                    <div class="p-4 text-center text-muted">No paper trades active.</div>
                </div>
            </div>

            <!-- AI Chat List -->
            <div id="list-ai" style="display:none; flex: 1; flex-direction: column; background-color:#181a20;">
                <div id="ai-chat-box" style="flex:1; overflow-y:auto; padding:15px; font-size:14px; display:flex; flex-direction:column; gap:10px;">
                    <div class="p-2 rounded bg-dark border border-secondary text-warning"><b>Quant AI:</b> Hello Boss! I am your Institutional AI Assistant. You can ask me to analyze any coin's trend or explain SMC concepts. How can I help today?</div>
                </div>
                <div class="p-2 border-top border-secondary d-flex">
                    <input type="text" id="aiInput" class="form-control form-control-sm bg-dark text-white border-secondary me-2" placeholder="Ask AI..." onkeypress="if(event.key==='Enter') sendAI()">
                    <button class="btn btn-sm" style="background-color:#a270ff; color:white;" onclick="sendAI()">Send</button>
                </div>
            </div>
        </div>
        
        <!-- Main Content -->
        <div class="col-md-9 p-0 d-flex flex-column" style="height: 100vh;">
            
            <!-- Top Bar (Wallet Info) -->
            <div class="top-bar d-flex justify-content-between align-items-center">
                <div class="d-flex align-items-center gap-3">
                    <h5 class="m-0" id="coinTitle">Select a Trade</h5>
                    <div class="input-group input-group-sm" style="width: 220px;">
                        <input type="text" id="searchInput" class="form-control bg-dark text-white border-secondary" placeholder="Search (e.g. BTC)" onkeypress="if(event.key==='Enter') searchCoin()">
                        <button class="btn btn-outline-warning" id="searchBtn" onclick="searchCoin()">🔍</button>
                    </div>
                </div>
                <div class="d-flex align-items-center gap-4">
                    <div><small class="text-muted">Wallet Balance:</small> <b class="text-white" id="wBalance">$0.00</b></div>
                    <div><small class="text-muted">Unrealized PNL:</small> <b id="wUPNL">$0.00</b></div>
                </div>
            </div>

            <div class="p-4 flex-grow-1 overflow-auto" id="mainContent" style="display:none;">
                <div class="row h-100">
                    <div class="col-lg-8 d-flex flex-column">
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <span class="score-badge" id="coinScore">25/65 ⭐️⭐️⭐️⭐️</span>
                        </div>
                        <!-- TradingView Widget -->
                        <div id="tvWidget" class="flex-grow-1 border border-secondary rounded" style="min-height: 500px;"></div>
                    </div>
                    
                    <div class="col-lg-4">
                        <div class="card p-3 mb-3 border-warning">
                            <h5 class="text-warning">⚡ Trade Setup</h5>
                            <div class="row g-2 mb-2">
                                <div class="col-6">
                                    <label class="small text-muted">Leverage (x)</label>
                                    <input type="number" id="leverage" class="form-control form-control-sm text-center fw-bold" value="{{leverage}}">
                                </div>
                                <div class="col-6">
                                    <label class="small text-muted">Margin ($)</label>
                                    <input type="number" id="margin" class="form-control form-control-sm text-center fw-bold" value="{{margin}}">
                                </div>
                            </div>
                            
                            <hr class="border-secondary my-2">
                            
                            <div class="mb-2">
                                <label class="small text-muted">Entry Price (Market)</label>
                                <div class="input-group input-group-sm">
                                    <span class="input-group-text">$</span>
                                    <input type="text" id="pEntry" class="form-control" readonly>
                                </div>
                            </div>
                            <div class="mb-2">
                                <label class="small text-muted">Stop Loss (SL)</label>
                                <div class="input-group input-group-sm">
                                    <span class="input-group-text text-danger border-danger">SL</span>
                                    <input type="number" step="any" id="pSL" class="form-control text-danger fw-bold border-danger">
                                </div>
                            </div>
                            <div class="mb-3">
                                <label class="small text-muted">Take Profit (TP)</label>
                                <div class="input-group input-group-sm">
                                    <span class="input-group-text text-success border-success">TP</span>
                                    <input type="number" step="any" id="pTP" class="form-control text-success fw-bold border-success">
                                </div>
                            </div>
                            
                            <div class="d-grid gap-2">
                                <button class="btn btn-success fw-bold" onclick="approveTrade()">✅ EXECUTE TRADE</button>
                                <button class="btn btn-outline-danger btn-sm" onclick="rejectTrade()">❌ Discard Signal</button>
                            </div>
                        </div>
                        
                        <div class="card p-3 mb-3">
                            <h6 class="mb-2">🤖 AI Analysis</h6>
                            <p id="aiReason" class="text-muted small mb-0"></p>
                        </div>

                        <div class="card p-3">
                            <h6 class="mb-2">📊 65-Point Quant AI Breakdown</h6>
                            <ul class="list-unstyled mb-0 small text-muted" id="scoreDetails"></ul>
                        </div>
                    </div>
                </div>
            </div>
            
        </div>
    </div>
</div>

<script src="https://s3.tradingview.com/tv.js"></script>
<script>
let currentSymbol = null;
let activeTab = 'signals';

function switchTab(tab) {
    activeTab = tab;
    document.getElementById('tab-signals').classList.remove('active');
    document.getElementById('tab-positions').classList.remove('active');
    document.getElementById('tab-paper').classList.remove('active');
    if (document.getElementById('tab-ai')) document.getElementById('tab-ai').classList.remove('active');
    
    if (document.getElementById('tab-' + tab)) document.getElementById('tab-' + tab).classList.add('active');
    
    document.getElementById('list-signals').style.display = tab === 'signals' ? 'block' : 'none';
    document.getElementById('list-positions').style.display = tab === 'positions' ? 'block' : 'none';
    document.getElementById('list-paper').style.display = tab === 'paper' ? 'block' : 'none';
    if (document.getElementById('list-ai')) document.getElementById('list-ai').style.display = tab === 'ai' ? 'flex' : 'none';
}

async function sendAI() {
    const input = document.getElementById('aiInput');
    if(!input) return;
    const text = input.value.trim();
    if(!text) return;
    
    const chatBox = document.getElementById('ai-chat-box');
    chatBox.innerHTML += `<div class="p-2 rounded align-self-end text-white mb-2" style="background-color:#2b3139; max-width:80%; align-self: flex-end;"><b>You:</b> ${text}</div>`;
    input.value = '';
    chatBox.scrollTop = chatBox.scrollHeight;
    
    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message: text})
        });
        const data = await res.json();
        chatBox.innerHTML += `<div class="p-2 rounded text-warning mb-2" style="background-color:#1e2329; border: 1px solid #474D57; max-width:90%; align-self: flex-start;"><b>Quant AI:</b> ${data.reply}</div>`;
        chatBox.scrollTop = chatBox.scrollHeight;
    } catch(e) {
        chatBox.innerHTML += `<div class="p-2 text-danger">Error reaching AI.</div>`;
    }
}

function loadPaperTrades() {
    fetch('/api/dummy_data').then(r=>r.json()).then(res => {
        document.getElementById('dummyBalance').innerText = res.balance.toFixed(2);
        let html = '';
        if(res.positions.length === 0) {
            html = '<div class="p-4 text-center text-muted">No paper trades active.</div>';
        } else {
            res.positions.forEach(p => {
                let pnlClass = p.pnl >= 0 ? 'text-success' : 'text-danger';
                html += `<div class="signal-item" onclick="selectCoin('${p.symbol}')">
                            <div class="d-flex justify-content-between">
                                <h6 class="m-0">${p.symbol} <small class="${p.side==='LONG'?'text-success':'text-danger'}">(${p.side})</small></h6>
                                <b class="${pnlClass}">$${p.pnl.toFixed(2)}</b>
                            </div>
                            <div class="small text-muted">Entry: ${p.entry.toFixed(4)} | Score: ${p.score}</div>
                         </div>`;
            });
        }
        document.getElementById('paper-items').innerHTML = html;
    });
}

function loadWallet() {
    fetch('/api/balance').then(r=>r.json()).then(data => {
        document.getElementById('wBalance').innerText = '$' + data.balance.toFixed(2);
        let upnl = document.getElementById('wUPNL');
        upnl.innerText = '$' + data.unrealized.toFixed(2);
        upnl.className = data.unrealized >= 0 ? 'text-success fw-bold' : 'text-danger fw-bold';
    });
}

function loadData() {
    fetch('/api/data').then(r=>r.json()).then(data => {
        const list = document.getElementById('list-signals');
        if(Object.keys(data).length === 0) {
            list.innerHTML = '<div class="p-4 text-center text-muted">No pending signals.<br>Click Scan Now.</div>';
            return;
        }
        let dataArray = Object.values(data);
        dataArray.sort((a, b) => b.score - a.score);
        
        let html = '';
        for(let t of dataArray) {
            let sym = t.symbol;
            let grade = t.score >= 55 ? '⭐️⭐️⭐️⭐️⭐️' : '⭐️⭐️⭐️';
            let dClass = t.direction === 'LONG' ? 'text-success' : 'text-danger';
            html += `<div class="signal-item ${currentSymbol===sym?'active':''}" onclick="selectCoin('${sym}')">
                        <div class="d-flex justify-content-between">
                            <h5 class="m-0">${sym} <small class="${dClass}">(${t.direction})</small></h5>
                            <span class="text-warning fw-bold">${t.score}/65</span>
                        </div>
                        <small class="text-muted">${grade}</small>
                     </div>`;
        }
        list.innerHTML = html;
    });
}

function selectCoin(sym) {
    currentSymbol = sym;
    document.getElementById('mainContent').style.display = 'block';
    
    fetch('/api/data').then(r=>r.json()).then(data => {
        let t = data[sym];
        if(!t) return;
        
        let grade = t.score >= 55 ? 'PERFECT' : 'STRONG';
        let dClass = t.direction === 'LONG' ? 'text-success' : 'text-danger';
        document.getElementById('coinTitle').innerHTML = `${sym} <small class="${dClass}">${t.direction}</small>`;
        document.getElementById('coinScore').innerText = `${t.score}/65 ${grade}`;
        
        let sd = '';
        for(let k in t.details) {
            sd += `<li><b>${k}:</b> <span class="float-end">${t.details[k]}</span></li>`;
        }
        document.getElementById('scoreDetails').innerHTML = sd;
        
        document.getElementById('aiReason').innerText = t.ai.reason;
        document.getElementById('pEntry').value = t.price;
        document.getElementById('pSL').value = t.ai.sl;
        document.getElementById('pTP').value = t.ai.tp1;
        
        document.getElementById('tvWidget').innerHTML = `<div id="tv_${sym}" style="height:100%; min-height:500px;"></div>`;
        
        let tvSym = "BINANCE:" + sym.replace("USDT", "USDT.P");
        new TradingView.widget({
            "autosize": true,
            "symbol": tvSym,
            "interval": "15",
            "timezone": "Asia/Kolkata",
            "theme": "dark",
            "style": "1",
            "locale": "en",
            "toolbar_bg": "#181a20",
            "hide_side_toolbar": false,
            "allow_symbol_change": true,
            "studies": ["MASimple@tv-basicstudies", "RSI@tv-basicstudies"],
            "container_id": "tv_" + sym
        });
    });
    loadData();
}

let activePositionsData = {};

function loadPositions() {
    fetch('/api/positions').then(r=>r.json()).then(data => {
        let list = document.getElementById('list-positions');
        if(data.length === 0) {
            list.innerHTML = '<div class="p-4 text-center text-muted">No active positions</div>';
            return;
        }
        
        activePositionsData = {};
        let html = '';
        data.forEach(p => {
            activePositionsData[p.symbol] = p;
            let pnl = parseFloat(p.unRealizedProfit);
            let pnlClass = pnl >= 0 ? 'text-success' : 'text-danger';
            let sideClass = parseFloat(p.positionAmt) > 0 ? 'text-success' : 'text-danger';
            let side = parseFloat(p.positionAmt) > 0 ? 'LONG' : 'SHORT';
            
            html += `<div class="signal-item border-bottom border-dark" onclick="selectPosition('${p.symbol}')" style="cursor:pointer">
                <div class="d-flex justify-content-between align-items-center mb-1">
                    <h6 class="m-0">${p.symbol} <small class="${sideClass}">(${side})</small></h6>
                    <b class="${pnlClass}">$${pnl.toFixed(2)}</b>
                </div>
                <div class="d-flex justify-content-between small text-muted mb-1">
                    <span>Live Score: <b class="${p.score < 30 ? 'text-danger' : 'text-warning'}">${p.score}/65</b></span>
                    <span>Entry: ${parseFloat(p.entryPrice).toFixed(4)}</span>
                </div>
                <div class="d-flex justify-content-between small text-muted mb-2">
                    <span>SL: <b class="text-danger">${p.target_sl}</b> | TP: <b class="text-success">${p.target_tp}</b></span>
                </div>
                <button class="btn btn-sm btn-danger w-100 fw-bold" onclick="event.stopPropagation(); closePosition('${p.symbol}')">Market Close</button>
            </div>`;
        });
        list.innerHTML = html;
    });
}

function selectPosition(sym) {
    currentSymbol = sym;
    document.getElementById('mainContent').style.display = 'block';
    
    let p = activePositionsData[sym];
    document.getElementById('coinTitle').innerHTML = `${sym} <small class="text-info">ACTIVE POSITION</small>`;
    
    if (p && p.score > 0) {
        let grade = p.score >= 55 ? 'PERFECT' : 'STRONG';
        document.getElementById('coinScore').innerText = `${p.score}/65 ${grade} (LIVE CHART)`;
        
        let sd = '';
        for(let k in p.details) {
            sd += `<li><b>${k}:</b> <span class="float-end">${p.details[k]}</span></li>`;
        }
        document.getElementById('scoreDetails').innerHTML = sd;
        document.getElementById('aiReason').innerText = p.ai.reason;
    } else {
        document.getElementById('coinScore').innerText = `LIVE CHART`;
        document.getElementById('scoreDetails').innerHTML = '<li>Monitor your live trade here.</li>';
        document.getElementById('aiReason').innerText = 'Position is running. SL and TP are set on Binance.';
    }
    
    document.getElementById('pEntry').value = 'LIVE';
    document.getElementById('pSL').value = '...';
    document.getElementById('pTP').value = '...';
    
    document.getElementById('tvWidget').innerHTML = `<div id="tv_${sym}" style="height:100%; min-height:500px;"></div>`;
    
    let tvSym = "BINANCE:" + sym.replace("USDT", "USDT.P");
    new TradingView.widget({
        "autosize": true,
        "symbol": tvSym,
        "interval": "15",
        "timezone": "Asia/Kolkata",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "toolbar_bg": "#181a20",
        "hide_side_toolbar": false,
        "allow_symbol_change": true,
        "studies": ["MASimple@tv-basicstudies", "RSI@tv-basicstudies"],
        "container_id": "tv_" + sym
    });
}

function closePosition(sym) {
    if(!confirm(`Close ${sym} at Market Price?`)) return;
    fetch('/api/close', {
        method: 'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({symbol: sym})
    }).then(r=>r.json()).then(res => {
        alert(res.msg);
        loadPositions();
        loadWallet();
    });
}

function approveTrade() {
    if(!currentSymbol) return;
    let lev = document.getElementById('leverage').value;
    let mar = document.getElementById('margin').value;
    let sl = document.getElementById('pSL').value;
    let tp = document.getElementById('pTP').value;
    
    fetch('/api/approve', {
        method: 'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({symbol: currentSymbol, leverage: lev, margin: mar, sl: sl, tp: tp})
    }).then(r=>r.json()).then(res => {
        alert(res.msg);
        if(res.status === 'success') {
            currentSymbol = null;
            document.getElementById('mainContent').style.display = 'none';
            document.getElementById('coinTitle').innerText = 'Select a Trade';
            switchTab('positions');
        }
        loadData();
        loadPositions();
        loadWallet();
    });
}

function rejectTrade() {
    if(!currentSymbol) return;
    fetch('/api/reject', {
        method: 'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({symbol: currentSymbol})
    }).then(r=>r.json()).then(res => {
        currentSymbol = null;
        document.getElementById('mainContent').style.display = 'none';
        document.getElementById('coinTitle').innerText = 'Select a Trade';
        loadData();
    });
}

function startScan() {
    let btn = document.getElementById('scanBtn');
    btn.innerHTML = 'Scanning...';
    btn.disabled = true;
    fetch('/api/scan', {method: 'POST'})
    .then(r=>r.json())
    .then(res => {
        setTimeout(() => {
            btn.innerHTML = '🔍 Scan Now';
            btn.disabled = false;
            loadData();
        }, 12000);
    });
}

function searchCoin() {
    let sym = document.getElementById('searchInput').value.trim().toUpperCase();
    if(!sym) return;
    
    let btn = document.getElementById('searchBtn');
    btn.innerHTML = '⏳';
    btn.disabled = true;
    
    fetch('/api/analyze_coin', {
        method: 'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({symbol: sym})
    }).then(r=>r.json()).then(res => {
        btn.innerHTML = '🔍';
        btn.disabled = false;
        
        if(res.status === 'error') {
            alert(res.msg);
            return;
        }
        
        switchTab('signals');
        loadData();
        setTimeout(() => selectCoin(res.symbol), 500);
    }).catch(() => {
        btn.innerHTML = '🔍';
        btn.disabled = false;
        alert("Error analyzing coin.");
    });
}

setInterval(loadData, 5000);
setInterval(loadPositions, 3000);
setInterval(loadWallet, 5000);
setInterval(loadPaperTrades, 3000);
loadWallet();
loadData();
loadPositions();
loadPaperTrades();
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, leverage=DEFAULT_LEVERAGE, margin=MARGIN_PER_TRADE)

@app.route('/api/data')
def get_data():
    return jsonify(pending_signals)

@app.route('/api/approve', methods=['POST'])
def approve():
    data = request.json
    sym = data['symbol']
    if sym in pending_signals:
        sig = pending_signals[sym]
        lev = int(data['leverage'])
        mar = float(data['margin'])
        custom_sl = data['sl']
        custom_tp = data['tp']
        
        try:
            b_auth_post("/fapi/v1/leverage", {"symbol": sym, "leverage": lev})
            b_auth_post("/fapi/v1/marginType", {"symbol": sym, "marginType": "ISOLATED"})
            
            ei = b_get("/fapi/v1/exchangeInfo")
            si = next((s for s in ei["symbols"] if s["symbol"] == sym), None)
            step = float(next((f for f in si["filters"] if f["filterType"] == "LOT_SIZE"), {}).get("stepSize", 1))
            
            # Format quantity properly based on stepSize to avoid precision errors
            raw_qty = mar * lev / sig['price']
            decimals = 0 if step == 1 else len(str(step).split('.')[1])
            qty = round(raw_qty - (raw_qty % step), decimals)
            qty_str = f"{qty:.{decimals}f}".rstrip('0').rstrip('.') if decimals > 0 else str(int(qty))
            
            # Determine sides based on LONG / SHORT
            side_main = "BUY" if sig['direction'] == "LONG" else "SELL"
            side_sl_tp = "SELL" if sig['direction'] == "LONG" else "BUY"
            
            # 1. Main Buy/Sell Order
            b_auth_post("/fapi/v1/order", {"symbol": sym, "side": side_main, "type": "MARKET", "quantity": qty_str})
            
            # 2. Stop Loss Order (STOP_MARKET)
            try:
                b_auth_post("/fapi/v1/order", {
                    "symbol": sym, "side": side_sl_tp, "type": "STOP_MARKET",
                    "stopPrice": str(custom_sl), "closePosition": "true", "timeInForce": "GTC"
                })
            except Exception as sle: log(f"SL error: {sle}")
                
            # 3. Take Profit Order (TAKE_PROFIT_MARKET)
            try:
                b_auth_post("/fapi/v1/order", {
                    "symbol": sym, "side": side_sl_tp, "type": "TAKE_PROFIT_MARKET",
                    "stopPrice": str(custom_tp), "closePosition": "true", "timeInForce": "GTC"
                })
            except Exception as tpe: log(f"TP error: {tpe}")
            
            dir_icon = "🟢" if sig['direction'] == "LONG" else "🔴"
            telegram(f"🚀 <b>TRADE EXECUTED VIA DASHBOARD</b>\nCoin: {dir_icon} {sym} ({sig['direction']})\nQty: {qty_str} | {lev}x | ${mar} margin\nSL: {custom_sl} | TP: {custom_tp}")
            
            # Store in active_trades to show targets in position tab
            active_trades[sym] = {
                "sl": custom_sl,
                "tp": custom_tp,
                "direction": sig['direction'],
                "details": sig['details'],
                "score": sig['score'],
                "ai": sig['ai']
            }
            
            del pending_signals[sym]
            return jsonify({"status": "success", "msg": f"Order placed for {sym} with Auto SL/TP!"})
        except Exception as e:
            return jsonify({"status": "error", "msg": str(e)})
    return jsonify({"status": "error", "msg": "Symbol not found."})

@app.route('/api/balance')
def get_balance():
    try:
        bals = b_auth_get("/fapi/v2/balance")
        usdt = next((b for b in bals if b['asset'] == 'USDT'), None)
        if usdt:
            return jsonify({"balance": float(usdt['balance']), "unrealized": float(usdt['crossUnPnl'])})
        return jsonify({"balance": 0, "unrealized": 0})
    except Exception as e:
        return jsonify({"balance": 0, "unrealized": 0})

@app.route('/api/positions')
def get_positions():
    try:
        pos = b_auth_get("/fapi/v2/positionRisk")
        active = []
        for p in pos:
            if float(p['positionAmt']) != 0:
                sym = p['symbol']
                t_info = active_trades.get(sym, {})
                ls_info = live_scores.get(sym, {})
                
                p['target_sl'] = t_info.get('sl', 'N/A')
                p['target_tp'] = t_info.get('tp', 'N/A')
                p['details'] = ls_info.get('details', t_info.get('details', {}))
                p['score'] = ls_info.get('score', t_info.get('score', 0))
                p['ai'] = t_info.get('ai', {"reason": "Trade running."})
                active.append(p)
        return jsonify(active)
    except:
        return jsonify([])

@app.route('/api/close', methods=['POST'])
def close_position():
    sym = request.json['symbol']
    try:
        # Cancel all pending SL/TP open orders for this coin
        b_auth_delete("/fapi/v1/allOpenOrders", {"symbol": sym})
        
        # Get active position size and close it
        pos = b_auth_get("/fapi/v2/positionRisk", {"symbol": sym})
        amt = float(pos[0]['positionAmt'])
        if amt != 0:
            side = "SELL" if amt > 0 else "BUY"
            qty_str = str(abs(amt))
            b_auth_post("/fapi/v1/order", {"symbol": sym, "side": side, "type": "MARKET", "quantity": qty_str})
            telegram(f"🛑 <b>POSITION CLOSED MANUALLY</b>: {sym}")
            return jsonify({"status": "success", "msg": f"Closed position and cancelled orders for {sym}"})
        return jsonify({"status": "error", "msg": "No active position found."})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)})

@app.route('/api/reject', methods=['POST'])
def reject():
    sym = request.json['symbol']
    if sym in pending_signals:
        del pending_signals[sym]
    return jsonify({"status": "success"})

@app.route('/api/scan', methods=['POST'])
def scan_now():
    threading.Thread(target=run_scan, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/api/analyze_coin', methods=['POST'])
def analyze_coin():
    sym = request.json['symbol'].upper()
    if not sym.endswith('USDT'): sym += 'USDT'
    try:
        ticker = b_get("/fapi/v1/ticker/price" if FUTURES_MODE else "/api/v3/ticker/price", {"symbol": sym})
        if 'price' not in ticker:
            return jsonify({"status": "error", "msg": "Symbol not found on Binance."})
        
        price = float(ticker['price'])
        score, direction, details, klines = get_19_point_score(sym)
        ai_data = get_ai_signal(sym, price, details, klines, direction)
        
        pending_signals[sym] = {
            "symbol": sym, "price": price, "score": score,
            "direction": direction, "details": details, "ai": ai_data, "timestamp": time.time()
        }
        
        return jsonify({
            "status": "success", "symbol": sym, "price": price, "score": score,
            "direction": direction, "details": details, "ai": ai_data
        })
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)})

@app.route('/api/dummy_data')
def get_dummy_data():
    return jsonify({
        "balance": DUMMY_WALLET,
        "positions": list(dummy_positions.values()),
        "history": dummy_history[-10:] # Last 10 trades
    })

@app.route('/api/chat', methods=['POST'])
def chat_api():
    data = request.json
    user_message = data.get('message', '')
    
    top_coins = [f"{k} (Score: {v['score']}/65)" for k, v in sorted(live_scores.items(), key=lambda item: item[1]['score'], reverse=True)[:5]]
    market_context = f"Top coins right now: {', '.join(top_coins)}." if top_coins else "No coins scanned yet."
    
    prompt = f"You are a professional Crypto Quant AI Assistant built into a trading terminal. Be concise, sharp, and use trading terminology (SMC, Liquidity, FVG). The user says: {user_message}. Context: {market_context}"
    
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}]}, timeout=15)
        resp_json = r.json()
        if "error" in resp_json:
            return jsonify({"reply": f"AI Error: {resp_json['error'].get('message', 'Unknown Error from Google API')}"})
        raw = resp_json["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"reply": raw})
    except Exception as e:
        return jsonify({"reply": f"System Error: {e}"})

if __name__ == "__main__":
    # Auto-open browser
    def open_browser():
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.gov:5000") # local
        webbrowser.open("http://127.0.0.1:5000") 
    
    threading.Thread(target=open_browser, daemon=True).start()
    print("🌐 Dashboard starting on http://127.0.0.1:5000 ...")
    app.run(host='0.0.0.0', port=5000, debug=False)
