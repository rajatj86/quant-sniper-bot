import os, time, json, threading, math
from datetime import datetime
import requests
from flask import Flask, jsonify, request, render_template_string

# ==============================================================================
# 👑 INSTITUTIONAL QUANT BOT (v3.0) - PROFESSIONAL MULTI-TAB SCALPER
# Features: Multi-Key Rotation, Hindi AI Analysis, TradingView integration
# ==============================================================================

app = Flask(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
TRADE_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quant_paper_trades.json")

# --- Multi-API Key Rotation ---
GEMINI_KEYS = [
    "AIzaSyA0UMYMS7e11lK2t-c-IkOydYAtWj6EuuE",
    "AIzaSyDcALFI95JYAHWFfu9EmbSCobl91lbsjKI",
    "AIzaSyCsUsK4zluODK81hPXS30lXUM0OJx-EtCs"
]
current_key_idx = 0

def get_gemini_key():
    return GEMINI_KEYS[current_key_idx]

def rotate_gemini_key():
    global current_key_idx
    current_key_idx = (current_key_idx + 1) % len(GEMINI_KEYS)
    print(f"Rotated Gemini API Key. Now using key index: {current_key_idx}")

# --- Professional Intraday Settings ---
INITIAL_BALANCE = 50.0
MARGIN_PER_TRADE = 15.0   # Small position: $15 margin per trade
DEFAULT_LEVERAGE = 10     # 10x lev = $150 position size
MAX_CONCURRENT = 3        # Max 3 trades open = $45 used, $5 buffer
MAX_RISK_PER_TRADE = 3.0  # Max $3 loss per trade
DAILY_LOSS_LIMIT = 10.0   # HARD STOP
MIN_WIN_PROBABILITY = 60  # Reduced from 75 to 60 for more aggressive scalping

# Global State
paper_wallet = INITIAL_BALANCE
active_trades = {}
trade_history = []
live_radar = {}
daily_pnl = 0.0
daily_reset_date = datetime.now().strftime("%Y-%m-%d")
bot_running = True # Control switch

# --- Persistence ---
def load_data():
    global paper_wallet, active_trades, trade_history, daily_pnl, daily_reset_date
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE, 'r') as f:
                data = json.load(f)
                paper_wallet = data.get('wallet', INITIAL_BALANCE)
                active_trades = data.get('active', {})
                trade_history = data.get('history', [])
                daily_pnl = data.get('daily_pnl', 0.0)
                daily_reset_date = data.get('daily_reset_date', datetime.now().strftime("%Y-%m-%d"))
        except: pass

def save_data():
    with open(TRADE_LOG_FILE, 'w') as f:
        json.dump({
            "wallet": paper_wallet,
            "active": active_trades,
            "history": trade_history,
            "daily_pnl": daily_pnl,
            "daily_reset_date": daily_reset_date
        }, f, indent=4)

load_data()

# --- Core API ---
def b_get(endpoint, params=None):
    try:
        r = requests.get(BINANCE_FAPI + endpoint, params=params, timeout=5)
        return r.json()
    except Exception as e:
        return []

# --- Core Technical Indicators ---
def calc_atr(klines, period=14):
    if len(klines) < period + 1: return 0
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def calc_ema_values(closes, period):
    if len(closes) < period: return []
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    emas = [ema]
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
        emas.append(ema)
    return emas

def calc_ema(klines, period=200):
    if len(klines) < period: return 0
    closes = [float(k[4]) for k in klines]
    vals = calc_ema_values(closes, period)
    return vals[-1] if vals else 0

def calc_rsi(klines, period=14):
    if len(klines) < period + 2: return 50
    closes = [float(k[4]) for k in klines]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period: return 50
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_volume_spike(klines, lookback=20):
    if len(klines) < lookback + 1: return 1.0
    volumes = [float(k[5]) for k in klines]
    avg_vol = sum(volumes[-(lookback+1):-1]) / lookback
    curr_vol = volumes[-1]
    if avg_vol == 0: return 1.0
    return curr_vol / avg_vol

def detect_ema_cross(klines):
    closes = [float(k[4]) for k in klines]
    if len(closes) < 22: return None
    ema9 = calc_ema_values(closes, 9)
    ema21 = calc_ema_values(closes, 21)
    if len(ema9) < 3 or len(ema21) < 3: return None
    offset = len(ema9) - len(ema21)
    if offset < 0: return None
    curr_9, prev_9 = ema9[-1], ema9[-2]
    curr_21, prev_21 = ema21[-1], ema21[-2]
    if prev_9 <= prev_21 and curr_9 > curr_21: return "LONG"
    if prev_9 >= prev_21 and curr_9 < curr_21: return "SHORT"
    return None

def detect_rsi_signal(klines):
    rsi = calc_rsi(klines, 14)
    rsi_prev = calc_rsi(klines[:-1], 14)
    if rsi_prev < 30 and rsi >= 30: return "LONG", rsi
    if rsi_prev > 70 and rsi <= 70: return "SHORT", rsi
    return None, rsi

def detect_volume_breakout(klines):
    if len(klines) < 25: return None
    vol_ratio = calc_volume_spike(klines, 20)
    if vol_ratio < 2.0: return None
    closes = [float(k[4]) for k in klines[-4:]]
    opens = [float(k[1]) for k in klines[-4:]]
    bullish = sum(1 for i in range(len(closes)) if closes[i] > opens[i])
    bearish = sum(1 for i in range(len(closes)) if closes[i] < opens[i])
    if bullish >= 3: return "LONG"
    if bearish >= 3: return "SHORT"
    return None

def calc_bollinger_bands(klines, period=20, std_dev=2):
    if len(klines) < period: return 0, 0, 0
    closes = [float(k[4]) for k in klines]
    recent_closes = closes[-period:]
    sma = sum(recent_closes) / period
    variance = sum((x - sma) ** 2 for x in recent_closes) / period
    std = variance ** 0.5
    upper_band = sma + (std_dev * std)
    lower_band = sma - (std_dev * std)
    return upper_band, sma, lower_band

def detect_bb_rsi_combo(klines, rsi_val):
    if len(klines) < 20: return None
    upper, sma, lower = calc_bollinger_bands(klines, 20, 2)
    if upper == 0 and lower == 0: return None
    
    last_candle = klines[-2] # using previous closed candle for reliable signal
    close = float(last_candle[4])
    
    # LONG: Price closes below lower band, RSI is oversold
    if close < lower and rsi_val < 35:
        return "LONG"
    # SHORT: Price closes above upper band, RSI is overbought
    if close > upper and rsi_val > 65:
        return "SHORT"
    return None

# --- Scanner Thread (Multi-Strategy, Zero AI tokens) ---
def market_scanner():
    global daily_pnl, daily_reset_date
    print("=" * 60)
    print("🚀 MULTI-STRATEGY SCALPER v4.1 STARTED")
    print("   Strategies: RSI | EMA Cross | Volume | BB+RSI Combo")
    print("   Timeframe: 5m | Scan: 30s | Top 50 Volume Coins")
    print("=" * 60)
    scan_count = 0
    while True:
        try:
            if not bot_running:
                time.sleep(5)
                continue
            today = datetime.now().strftime("%Y-%m-%d")
            if today != daily_reset_date:
                print(f"\n{'='*50}")
                print(f"📅 NEW DAY: {today} | Yesterday PNL: ${daily_pnl:.2f}")
                print(f"{'='*50}\n")
                daily_pnl = 0.0
                daily_reset_date = today
                save_data()
            if daily_pnl <= -DAILY_LOSS_LIMIT:
                print(f"🛑 DAILY LOSS LIMIT (${daily_pnl:.2f}). Paused.")
                time.sleep(300)
                continue
            if len(active_trades) >= MAX_CONCURRENT:
                time.sleep(10)
                continue
            scan_count += 1
            tickers = b_get("/fapi/v1/ticker/24hr")
            if not tickers or not isinstance(tickers, list):
                print(f"[Scan #{scan_count}] ⚠️ No Binance data. Retrying...")
                time.sleep(10)
                continue
            # Filter for liquidity, then sort by highest volatility (Price Change % magnitude) to catch surging coins
            valid_coins = [t for t in tickers if t.get("symbol","").endswith("USDT") and float(t.get("quoteVolume", 0)) > 15000000]
            valid_coins.sort(key=lambda x: abs(float(x.get("priceChangePercent", 0))), reverse=True)
            signals_found = 0
            for t in valid_coins[:50]:
                sym = t["symbol"]
                if sym in active_trades: continue
                if len(active_trades) >= MAX_CONCURRENT: break
                k_5m = b_get("/fapi/v1/klines", {"symbol": sym, "interval": "5m", "limit": 100})
                if not k_5m or not isinstance(k_5m, list) or len(k_5m) < 30: continue
                price = float(k_5m[-1][4])
                if price == 0: continue
                direction = None
                strategy = ""
                reason = ""
                # STRATEGY 1: RSI Reversal
                rsi_sig, rsi_val = detect_rsi_signal(k_5m)
                if rsi_sig:
                    direction = rsi_sig
                    strategy = "RSI_REVERSAL"
                    reason = f"RSI {rsi_val:.0f} bounce"
                # STRATEGY 2: EMA 9/21 Cross
                if not direction:
                    ema_sig = detect_ema_cross(k_5m)
                    if ema_sig:
                        direction = ema_sig
                        strategy = "EMA_CROSS"
                        reason = f"EMA 9/21 {ema_sig}"
                # STRATEGY 3: Volume Breakout
                if not direction:
                    vol_sig = detect_volume_breakout(k_5m)
                    if vol_sig:
                        direction = vol_sig
                        strategy = "VOL_BREAKOUT"
                        reason = f"Volume {calc_volume_spike(k_5m,20):.1f}x spike"
                # STRATEGY 4: BB + RSI Combo
                if not direction:
                    bb_rsi_sig = detect_bb_rsi_combo(k_5m, rsi_val)
                    if bb_rsi_sig:
                        direction = bb_rsi_sig
                        strategy = "BB_RSI_COMBO"
                        reason = f"BB Breakout + RSI {rsi_val:.0f}"
                if direction:
                    signals_found += 1
                    atr = calc_atr(k_5m)
                    if atr == 0: continue
                    max_sl_pct = MAX_RISK_PER_TRADE / (MARGIN_PER_TRADE * DEFAULT_LEVERAGE)
                    sl_dist = min(2.0 * atr, price * max_sl_pct)
                    tp_dist = sl_dist * 1.5
                    if sl_dist == 0: continue
                    if direction == "LONG":
                        sl, tp = price - sl_dist, price + tp_dist
                    else:
                        sl, tp = price + sl_dist, price - tp_dist
                        
                    # --- AI VERIFICATION (The "Best Indicator") ---
                    ai_prompt = f"Analyze {sym} 5m timeframe. Technicals show {strategy} signal for {direction} ({reason}). Price: {price}. As an expert quant, is this a safe scalp? Reply with EXACTLY 'YES' or 'NO' and 1 short reason."
                    print(f"🧠 Asking AI for Trade Confirmation on {sym}...")
                    ai_response = call_gemini(ai_prompt, retries=1)
                    
                    if "YES" not in ai_response.upper() and "yes" not in ai_response.lower():
                        print(f"🚫 AI REJECTED {sym} {direction}: {ai_response.strip()}")
                        continue
                        
                    print(f"✅ AI APPROVED {sym} {direction}!")
                    ai_final_reason = ai_response.replace('YES', '').replace('yes', '').strip()[:40]
                    
                    trade_id = f"TRD_{int(time.time())}_{sym[:4]}"
                    active_trades[sym] = {
                        "id": trade_id, "symbol": sym, "side": direction,
                        "entry": price, "sl": sl, "tp": tp,
                        "margin": MARGIN_PER_TRADE, "leverage": DEFAULT_LEVERAGE,
                        "prob": 0, "ai_reason": f"[{strategy}] {ai_final_reason}",
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    save_data()
                    print(f"🎯 ENTRY: {direction} {sym} @ {price:.4f} | {strategy} | {ai_final_reason}")
                live_radar[sym] = {"sweep": strategy or "SCANNING", "delta": 0, "prob": 0}
            if scan_count % 5 == 0:
                print(f"[Scan #{scan_count}] Signals: {signals_found} | Active: {len(active_trades)}/{MAX_CONCURRENT}")
        except Exception as e:
            print(f"Scanner Error: {e}")
        time.sleep(30)

threading.Thread(target=market_scanner, daemon=True).start()


def call_gemini(prompt, retries=3):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key="
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {'Content-Type': 'application/json'}
    for attempt in range(retries):
        try:
            r = requests.post(url + get_gemini_key(), json=payload, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data['candidates'][0]['content']['parts'][0]['text']
            else:
                print(f"⚠️ Gemini API Error (Status {r.status_code}): {r.text.strip()}")
                rotate_gemini_key()
                time.sleep(2)
        except Exception as e:
            print(f"⚠️ Gemini Exception: {e}")
            time.sleep(2)
    return "Error: AI Service Unavailable."

def get_hindi_analysis(trade_data):
    prompt = f"""Aap ek professional crypto trading mentor hain. Ek beginner trader ko HINDI (roman english script) mein samjhao ki yeh trade kyun li gayi.
    Trade Details:
    - Coin: {trade_data['symbol']}
    - Type: {trade_data['side']}
    - Entry Price: {trade_data['entry']}
    - Stop Loss: {trade_data['sl']}
    - AI ka Reason: {trade_data.get('ai_reason', 'N/A')}
    
    Reply mein simple language use karna. Maximum 3-4 lines."""
    return call_gemini(prompt, retries=2)

# --- Trade Manager Thread ---
def trade_manager():
    global paper_wallet, daily_pnl
    while True:
        try:
            if not active_trades:
                time.sleep(5)
                continue
                
            # Fetch all prices in one single call (Weight: 2) to avoid rate limits
            tickers = b_get("/fapi/v1/ticker/price")
            if not tickers or not isinstance(tickers, list):
                time.sleep(5)
                continue
                
            price_map = {t['symbol']: float(t['price']) for t in tickers if 'symbol' in t and 'price' in t}
            
            for sym in list(active_trades.keys()):
                pos = active_trades[sym]
                
                if sym not in price_map:
                    # Fallback single fetch to verify if the coin still exists
                    single = b_get("/fapi/v1/ticker/price", {"symbol": sym})
                    if not single or 'price' not in single:
                        print(f"⚠️ {sym} not found on Binance API. Removing stuck trade to free slot.")
                        del active_trades[sym]
                        save_data()
                        continue
                    curr_price = float(single['price'])
                else:
                    curr_price = price_map[sym]
                
                diff = (curr_price - pos['entry']) / pos['entry']
                if pos['side'] == "SHORT": diff = -diff
                
                unrealized_pnl = pos['margin'] * pos['leverage'] * diff
                pos['curr_price'] = curr_price
                pos['unrealized_pnl'] = unrealized_pnl
                
                closed = False
                reason = ""
                exit_price = curr_price
                
                if pos['side'] == "LONG":
                    if curr_price <= pos['sl']: 
                        closed = True; reason = "STOP_LOSS"; exit_price = pos['sl']
                    elif curr_price >= pos['tp']: 
                        closed = True; reason = "TAKE_PROFIT"; exit_price = pos['tp']
                else:
                    if curr_price >= pos['sl']: 
                        closed = True; reason = "STOP_LOSS"; exit_price = pos['sl']
                    elif curr_price <= pos['tp']: 
                        closed = True; reason = "TAKE_PROFIT"; exit_price = pos['tp']
                    
                if closed:
                    # Recalculate exact PNL using the limit execution price (prevents massive fake slippage)
                    final_diff = (exit_price - pos['entry']) / pos['entry']
                    if pos['side'] == "SHORT": final_diff = -final_diff
                    final_pnl = pos['margin'] * pos['leverage'] * final_diff
                    
                    paper_wallet += final_pnl
                    daily_pnl += final_pnl
                    pos['exit_price'] = exit_price
                    pos['pnl'] = final_pnl
                    pos['reason'] = reason
                    pos['exit_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    trade_history.insert(0, pos)
                    del active_trades[sym]
                    save_data()
                    print(f"TRADE CLOSED: {sym} | PNL: ${final_pnl:.2f} | Reason: {reason}")
                    
        except Exception as e:
            print(f"Manager Error: {e}")
        time.sleep(5)

threading.Thread(target=trade_manager, daemon=True).start()

# ==============================================================================
# 🌐 PROFESSIONAL FRONTEND (HTML, JS, TradingView)
# ==============================================================================
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Sniper Terminal v3.0</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; }
        body { background-color: #0b0e14; color: #c9d1d9; font-family: 'Inter', 'Consolas', monospace; font-size: 13px; }
        .panel { background-color: #151a23; border: 1px solid #21262d; border-radius: 8px; padding: 15px; margin-bottom: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .header-bar { border-bottom: 2px solid #2962ff; padding-bottom: 10px; margin-bottom: 20px; }
        .text-up { color: #00e676 !important; }
        .text-down { color: #ff1744 !important; }
        .nav-tabs { border-bottom: 1px solid #21262d; margin-bottom: 20px; }
        .nav-tabs .nav-link { color: #888; border: none; border-bottom: 2px solid transparent; border-radius: 0; padding: 10px 20px; font-size: 15px; }
        .nav-tabs .nav-link:hover { color: #fff; border-color: transparent; }
        .nav-tabs .nav-link.active { color: #2962ff; background-color: transparent; border-bottom: 2px solid #2962ff; font-weight: bold; }
        
        /* TABLE - no white borders */
        table { color: #c9d1d9 !important; background-color: #151a23 !important; border-collapse: collapse !important; width: 100%; }
        thead, tbody, tr, td, th { background-color: #151a23 !important; }
        thead tr { border-bottom: 1px solid #21262d !important; }
        tbody tr { border-bottom: 1px solid #161b22 !important; }
        tbody tr:hover, tbody tr:hover td { background-color: #1c2333 !important; }
        th { color: #6e7681 !important; font-weight: 600 !important; border: none !important; border-bottom: 1px solid #21262d !important; text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; padding: 8px 6px !important; }
        td { vertical-align: middle !important; border: none !important; padding: 8px 6px !important; color: #e6edf3 !important; }
        
        /* Color Classes */
        .col-red { color: #ff4757 !important; font-weight: 600; }
        .col-green { color: #00e676 !important; font-weight: 600; }
        .col-yellow { color: #ffc107 !important; font-weight: 600; }
        .col-blue { color: #60a5fa !important; font-weight: 600; }
        .col-white { color: #ffffff !important; font-weight: 500; }
        .col-muted { color: #6e7681 !important; font-size: 11px; }
        
        .btn-ai { background: linear-gradient(45deg, #2962ff, #7c4dff); color: white; border: none; font-size: 11px; padding: 4px 10px; border-radius: 4px; font-weight: bold; cursor: pointer; }
        .btn-ai:hover { opacity: 0.8; color: white; }
        .stat-box { border-right: 1px solid #21262d; padding-right: 20px; }
        .stat-value { font-size: 22px; font-weight: bold; color: #fff; font-family: 'Inter', monospace; }
        
        #aiModal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); z-index: 1000; align-items: center; justify-content: center; backdrop-filter: blur(4px); }
        .ai-modal-content { background: #151a23; border: 1px solid #2962ff; border-radius: 8px; padding: 20px; width: 90%; max-width: 500px; color: #fff; box-shadow: 0 0 20px rgba(41, 98, 255, 0.2); }
        
        /* Switch */
        .switch { position: relative; display: inline-block; width: 50px; height: 24px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ff3366; transition: .4s; border-radius: 24px; }
        .slider:before { position: absolute; content: ""; height: 16px; width: 16px; left: 4px; bottom: 4px; background-color: white; transition: .4s; border-radius: 50%; }
        input:checked + .slider { background-color: #00ff88; }
        input:checked + .slider:before { transform: translateX(26px); }
    </style>
</head>
<body class="p-3">

<div class="container-fluid">
    <!-- Header -->
    <div class="d-flex flex-wrap justify-content-between align-items-end header-bar gap-3">
        <div>
            <h3 class="m-0 text-white"><i class="fa-solid fa-robot" style="color: #2962ff;"></i> Institutional Quant Bot</h3>
            <small class="text-muted">v3.0 | Multi-Tab Pro Scalper | Status: <span id="botStatus" class="text-up font-weight-bold">ACTIVE</span></small>
        </div>
        <div class="d-flex gap-4 flex-wrap">
            <div class="stat-box">
                <div class="text-muted small">WALLET ($50 Base)</div>
                <div class="stat-value" id="walletBal">$50.00</div>
            </div>
            <div class="stat-box">
                <div class="text-muted small">DAILY PNL</div>
                <div class="stat-value" id="dailyPnl">$0.00</div>
            </div>
            <div class="stat-box">
                <div class="text-muted small">RISK USED</div>
                <div class="stat-value" id="riskUsed">$0.0/$10</div>
            </div>
            <div>
                <div class="text-muted small">WIN RATE</div>
                <div class="stat-value" id="winRate">0.0%</div>
            </div>
        </div>
    </div>

    <!-- Tabs -->
    <ul class="nav nav-tabs" id="myTab" role="tablist">
        <li class="nav-item" role="presentation">
            <button class="nav-link active" id="dashboard-tab" data-bs-toggle="tab" data-bs-target="#dashboard" type="button" role="tab"><i class="fa-solid fa-chart-line"></i> Dashboard</button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="research-tab" data-bs-toggle="tab" data-bs-target="#research" type="button" role="tab"><i class="fa-solid fa-magnifying-glass-chart"></i> Research Hub</button>
        </li>
        <li class="nav-item" role="presentation">
            <button class="nav-link" id="settings-tab" data-bs-toggle="tab" data-bs-target="#settings" type="button" role="tab"><i class="fa-solid fa-gear"></i> Settings</button>
        </li>
    </ul>

    <div class="tab-content" id="myTabContent">
        <!-- DASHBOARD TAB -->
        <div class="tab-pane fade show active" id="dashboard" role="tabpanel">
            <div class="row">
                <div class="col-12 mb-3">
                    <div class="panel">
                        <h6 class="text-white mb-3"><i class="fa-solid fa-crosshairs text-warning"></i> ACTIVE SNIPER POSITIONS</h6>
                        <div class="table-responsive">
                            <table class="table table-borderless table-sm">
                                <thead>
                                    <tr>
                                        <th>TIME</th>
                                        <th>SYMBOL</th>
                                        <th>SIDE</th>
                                        <th>ENTRY</th>
                                        <th>MARKET</th>
                                        <th>MARGIN</th>
                                        <th>LEVERAGE</th>
                                        <th>SL</th>
                                        <th>TP</th>
                                        <th>LIVE PNL</th>
                                        <th>ACTIONS</th>
                                    </tr>
                                </thead>
                                <tbody id="activeTable">
                                    <tr><td colspan="11" class="text-center text-muted py-3">🔍 Scanning for high-probability 5m setups...</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <div class="col-12">
                    <div class="panel">
                        <h6 class="text-white mb-3"><i class="fa-solid fa-book text-info"></i> 24H TRADE LEDGER</h6>
                        <div class="table-responsive">
                            <table class="table table-borderless table-sm">
                                <thead>
                                    <tr>
                                        <th>EXIT TIME</th>
                                        <th>SYMBOL</th>
                                        <th>SIDE</th>
                                        <th>ENTRY</th>
                                        <th>EXIT</th>
                                        <th>REASON</th>
                                        <th>FINAL PNL</th>
                                        <th>ACTIONS</th>
                                    </tr>
                                </thead>
                                <tbody id="historyTable">
                                    <tr><td colspan="8" class="text-center text-muted py-3">No closed trades yet.</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- RESEARCH TAB -->
        <div class="tab-pane fade" id="research" role="tabpanel">
            <div class="panel">
                <div class="d-flex flex-wrap justify-content-between align-items-center mb-3 gap-2">
                    <h6 class="text-white m-0"><i class="fa-brands fa-searchengin text-success"></i> LIVE CHART & AI ANALYSIS</h6>
                    <div class="d-flex gap-2">
                        <input type="text" id="tvSymbol" list="coinList" class="form-control form-control-sm bg-dark text-white border-secondary" placeholder="e.g. BTCUSDT" value="BTCUSDT" style="min-width:130px;">
                        <datalist id="coinList"></datalist>
                        <button class="btn btn-sm btn-outline-info" onclick="changeChart()"><i class="fa-solid fa-search"></i> Chart</button>
                        <button class="btn btn-sm btn-ai" onclick="analyzeAnyCoin()"><i class="fa-solid fa-robot"></i> AI Analysis</button>
                    </div>
                </div>
                <!-- TradingView Widget -->
                <div class="tradingview-widget-container" style="height: 50vh; border: 1px solid #2a3241; border-radius: 4px; overflow: hidden;">
                  <div id="tradingview_chart" style="height: 100%;"></div>
                </div>
            </div>
            <!-- AI Coin Analysis Result Panel -->
            <div class="panel mt-3" id="coinAnalysisPanel" style="display:none;">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <h6 class="text-white m-0"><i class="fa-solid fa-brain text-warning"></i> AI COIN REPORT <span id="coinAnalysisSymbol" class="text-info"></span></h6>
                    <button class="btn btn-sm btn-outline-secondary" onclick="document.getElementById('coinAnalysisPanel').style.display='none'"><i class="fa-solid fa-times"></i></button>
                </div>
                <div id="coinAnalysisBody" style="font-size:14px; line-height:1.7; color:#ddd; white-space: pre-line;">Loading...</div>
            </div>
        </div>

        <!-- SETTINGS TAB -->
        <div class="tab-pane fade" id="settings" role="tabpanel">
            <div class="panel" style="max-width: 500px;">
                <h6 class="text-white mb-4"><i class="fa-solid fa-sliders text-secondary"></i> BOT CONTROLS</h6>
                
                <div class="d-flex justify-content-between align-items-center mb-3 pb-3 border-bottom border-secondary">
                    <span><i class="fa-solid fa-power-off"></i> Algorithm Engine (Run/Pause)</span>
                    <label class="switch">
                        <input type="checkbox" id="botToggle" checked onchange="toggleBot()">
                        <span class="slider"></span>
                    </label>
                </div>
                
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <span>Target Daily PNL</span>
                    <span class="text-up">+$10.00</span>
                </div>
                
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <span>Daily Drawdown Limit</span>
                    <span class="text-down">-$10.00</span>
                </div>
                
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <span>Risk Per Trade</span>
                    <span class="text-warning">Max $3.00</span>
                </div>
                
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <span>Active API Keys (Gemini)</span>
                    <span class="text-info">3 Keys (Auto-Rotating)</span>
                </div>
                
                <div class="mt-4 pt-3 border-top border-secondary text-muted small">
                    <i class="fa-solid fa-cloud"></i> <b>Cloud Hosting:</b> You can deploy this on a free VPS (Oracle Cloud/AWS) or Render. Access this dashboard 24/7 on your mobile phone to monitor and stop/start the bot.
                </div>
            </div>
        </div>
    </div>
</div>

<!-- AI Analysis Modal -->
<div id="aiModal">
    <div class="ai-modal-content">
        <div class="d-flex justify-content-between align-items-center mb-3 pb-2 border-bottom border-secondary">
            <h5 class="m-0"><i class="fa-solid fa-robot text-primary"></i> AI Mentor (Hindi)</h5>
            <button class="btn btn-sm btn-outline-danger" onclick="closeAiModal()"><i class="fa-solid fa-times"></i></button>
        </div>
        <div id="aiModalBody" style="font-size: 14px; line-height: 1.6; color: #ddd;">
            <div class="text-center py-4"><i class="fa-solid fa-circle-notch fa-spin fa-2x text-primary"></i><br><br>Gemini is analyzing the trade...</div>
        </div>
    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
<script>
    let globalTrades = {}; 
    let tvWidget = null;

    function initChart(symbol) {
        if(tvWidget) {
            document.getElementById('tradingview_chart').innerHTML = ''; // clear old
        }
        tvWidget = new TradingView.widget({
            "autosize": true,
            "symbol": "BINANCE:" + symbol + "PERP",
            "interval": "15",
            "timezone": "Etc/UTC",
            "theme": "dark",
            "style": "1",
            "locale": "en",
            "enable_publishing": false,
            "backgroundColor": "#151a23",
            "gridColor": "#2a3241",
            "hide_top_toolbar": false,
            "hide_legend": false,
            "save_image": false,
            "container_id": "tradingview_chart"
        });
    }
    
    function changeChart() {
        let sym = document.getElementById('tvSymbol').value.toUpperCase().trim();
        if(!sym.endsWith('USDT') && sym.length > 0) sym += 'USDT';
        if(sym) initChart(sym);
    }
    
    // Init default chart
    initChart("BTCUSDT");

    async function toggleBot() {
        try {
            const res = await fetch('/api/toggle_bot', { method: 'POST' });
            const data = await res.json();
            const statusEl = document.getElementById('botStatus');
            if (data.bot_running) {
                statusEl.innerText = "ACTIVE";
                statusEl.className = "text-up font-weight-bold";
            } else {
                statusEl.innerText = "PAUSED";
                statusEl.className = "text-warning font-weight-bold";
            }
        } catch (e) { console.error(e); }
    }

    async function analyzeTrade(tradeId) {
        const trade = globalTrades[tradeId];
        if(!trade) return;
        
        const modal = document.getElementById('aiModal');
        const body = document.getElementById('aiModalBody');
        
        modal.style.display = 'flex';
        body.innerHTML = '<div class="text-center py-4"><i class="fa-solid fa-circle-notch fa-spin fa-2x text-primary"></i><br><br><b>Gemini 2.5 Flash</b> is writing analysis in Hindi...</div>';
        
        try {
            const res = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ trade: trade })
            });
            const data = await res.json();
            body.innerHTML = data.analysis.replace(/\\n/g, '<br>');
        } catch (e) {
            body.innerHTML = '<div class="text-danger">Error fetching AI analysis. API limit might be reached.</div>';
        }
    }
    
    function closeAiModal() {
        document.getElementById('aiModal').style.display = 'none';
    }

    async function analyzeAnyCoin() {
        let sym = document.getElementById('tvSymbol').value.toUpperCase().trim();
        if(!sym) { alert('Pehle coin name dalo!'); return; }
        if(!sym.endsWith('USDT')) sym += 'USDT';
        
        const panel = document.getElementById('coinAnalysisPanel');
        const body = document.getElementById('coinAnalysisBody');
        const symLabel = document.getElementById('coinAnalysisSymbol');
        
        panel.style.display = 'block';
        symLabel.innerText = sym;
        body.innerHTML = '<div class="text-center py-3"><i class="fa-solid fa-circle-notch fa-spin fa-2x text-primary"></i><br><br><b>Gemini AI</b> analyzing ' + sym + ' in Hindi...</div>';
        
        try {
            const res = await fetch('/api/analyze_coin', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: sym })
            });
            const data = await res.json();
            body.innerText = data.analysis;
        } catch (e) {
            body.innerHTML = '<span class="text-danger">Error: AI service unavailable. Try again.</span>';
        }
    }

    async function fetchData() {
        try {
            const res = await fetch('/api/data');
            const data = await res.json();
            
            // Sync Toggle State
            document.getElementById('botToggle').checked = data.bot_running;
            
            // Update Stats
            document.getElementById('walletBal').innerText = '$' + data.wallet.toFixed(2);
            
            let dp = data.daily_pnl || 0;
            document.getElementById('dailyPnl').innerText = (dp >= 0 ? '+$' : '-$') + Math.abs(dp).toFixed(2);
            document.getElementById('dailyPnl').className = 'stat-value ' + (dp >= 0 ? 'text-up' : 'text-down');
            
            let riskUsed = dp < 0 ? Math.abs(dp) : 0;
            document.getElementById('riskUsed').innerText = '$' + riskUsed.toFixed(1) + '/$10';
            document.getElementById('riskUsed').className = 'stat-value ' + (riskUsed >= 7 ? 'text-down' : riskUsed >= 4 ? 'text-warning' : 'text-up');
            
            let totalPnl = 0;
            let wins = 0;
            data.history.forEach(t => {
                totalPnl += t.pnl;
                if(t.pnl > 0) wins++;
            });
            
            if(data.history.length > 0) {
                let wr = (wins / data.history.length) * 100;
                document.getElementById('winRate').innerText = wr.toFixed(1) + '%';
            }

            // Update Active
            let activeHtml = '';
            for(let sym in data.active) {
                let t = data.active[sym];
                globalTrades[t.id] = t; 
                
                let sideClass = t.side === 'LONG' ? 'text-up' : 'text-down';
                let currPrice = t.curr_price ? t.curr_price.toFixed(4) : '---';
                let pnl = t.unrealized_pnl ? t.unrealized_pnl : 0;
                let pnlClass = pnl >= 0 ? 'text-up' : 'text-down';
                let pnlStr = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
                
                activeHtml += `<tr>
                    <td class="col-muted">${t.time.split(' ')[1]}</td>
                    <td><a href="javascript:void(0)" onclick="document.getElementById('tvSymbol').value='${t.symbol}'; changeChart(); var tab = new bootstrap.Tab(document.getElementById('research-tab')); tab.show();" class="text-decoration-none col-blue fw-bold"><i class="fa-solid fa-chart-simple"></i> ${t.symbol}</a></td>
                    <td class="${t.side === 'LONG' ? 'col-green' : 'col-red'} fw-bold">${t.side}</td>
                    <td class="col-white">${t.entry.toFixed(4)}</td>
                    <td class="col-white">${currPrice}</td>
                    <td class="col-yellow">$${(t.margin || 15).toFixed(2)}</td>
                    <td class="col-blue">${t.leverage || 10}x</td>
                    <td class="col-red">${t.sl.toFixed(4)}</td>
                    <td class="col-green">${t.tp.toFixed(4)}</td>
                    <td class="${pnl >= 0 ? 'col-green' : 'col-red'} fw-bold">$${pnlStr}</td>
                    <td>
                        <button class="btn-ai" onclick="analyzeTrade('${t.id}')" style="margin-right: 4px;"><i class="fa-solid fa-robot"></i> AI</button>
                        <button class="btn-ai" onclick="document.getElementById('tvSymbol').value='${t.symbol}'; changeChart(); var tab = new bootstrap.Tab(document.getElementById('research-tab')); tab.show();" style="background: linear-gradient(45deg, #00b0ff, #00e5ff);"><i class="fa-solid fa-chart-line"></i> Chart</button>
                    </td>
                </tr>`;
            }
            if(activeHtml) document.getElementById('activeTable').innerHTML = activeHtml;
            else document.getElementById('activeTable').innerHTML = '<tr><td colspan="11" class="text-center text-muted py-3">🔍 Scanning for high-probability 5m setups...</td></tr>';
            
            // Update History
            let histHtml = '';
            data.history.forEach(t => {
                globalTrades[t.id] = t;
                let pnlClass = t.pnl >= 0 ? 'col-green' : 'col-red';
                let pnlStr = (t.pnl >= 0 ? '+' : '') + t.pnl.toFixed(2);
                let reasonClass = t.reason === 'TAKE_PROFIT' ? 'col-green' : 'col-red';
                histHtml += `<tr>
                    <td class="col-muted">${t.exit_time.split(' ')[1]}</td>
                    <td class="col-white fw-bold">${t.symbol}</td>
                    <td class="${t.side === 'LONG' ? 'col-green' : 'col-red'}">${t.side}</td>
                    <td class="col-white">${t.entry.toFixed(4)}</td>
                    <td class="col-white">${t.exit_price.toFixed(4)}</td>
                    <td class="${reasonClass} col-muted">${t.reason}</td>
                    <td class="${pnlClass} fw-bold">$${pnlStr}</td>
                    <td>
                        <button class="btn-ai" onclick="analyzeTrade('${t.id}')" style="margin-right: 4px;"><i class="fa-solid fa-robot"></i> AI</button>
                        <button class="btn-ai" onclick="document.getElementById('tvSymbol').value='${t.symbol}'; changeChart(); var tab = new bootstrap.Tab(document.getElementById('research-tab')); tab.show();" style="background: linear-gradient(45deg, #00b0ff, #00e5ff);"><i class="fa-solid fa-chart-line"></i> Chart</button>
                    </td>
                </tr>`;
            });
            if(histHtml) document.getElementById('historyTable').innerHTML = histHtml;

        } catch (e) { console.error(e); }
    }

    async function loadSymbols() {
        try {
            const res = await fetch('/api/symbols');
            const symbols = await res.json();
            let html = '';
            symbols.forEach(sym => {
                html += `<option value="${sym}">`;
            });
            document.getElementById('coinList').innerHTML = html;
        } catch (e) { console.error("Error loading symbols:", e); }
    }

    setInterval(fetchData, 3000);
    fetchData();
    loadSymbols();
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/data')
def api_data():
    return jsonify({
        "wallet": paper_wallet,
        "active": active_trades,
        "history": trade_history,
        "radar": live_radar,
        "daily_pnl": daily_pnl,
    })

@app.route('/api/symbols')
def api_symbols():
    try:
        tickers = b_get("/fapi/v1/ticker/24hr")
        if not tickers: return jsonify([])
        symbols = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
        symbols.sort()
        return jsonify(symbols)
    except Exception as e:
        print(f"Error fetching symbols list: {e}")
        return jsonify([])

@app.route('/api/toggle_bot', methods=['POST'])
def toggle_bot():
    global bot_running
    bot_running = not bot_running
    return jsonify({"bot_running": bot_running})

@app.route('/api/analyze', methods=['POST'])
def analyze_trade():
    data = request.json
    trade = data.get('trade')
    analysis = get_hindi_analysis(trade)
    return jsonify({"analysis": analysis})

@app.route('/api/analyze_coin', methods=['POST'])
def analyze_coin():
    data = request.json
    symbol = data.get('symbol', 'BTCUSDT').upper()
    if not symbol.endswith('USDT'): symbol += 'USDT'
    try:
        ticker = b_get('/fapi/v1/ticker/24hr', {'symbol': symbol})
        klines = b_get('/fapi/v1/klines', {'symbol': symbol, 'interval': '15m', 'limit': 100})
        
        if not ticker or not klines or 'lastPrice' not in ticker:
            return jsonify({'analysis': f'❌ Error: {symbol} Binance Futures par nahi mila. Kripya check karein ki coin ka naam sahi hai ya nahi.', 'symbol': symbol})
            
        price = float(ticker.get('lastPrice', 0))
        change_pct = float(ticker.get('priceChangePercent', 0))
        volume = float(ticker.get('quoteVolume', 0))
        high24 = float(ticker.get('highPrice', 0))
        low24 = float(ticker.get('lowPrice', 0))
        
        ema21 = calc_ema(klines, min(21, len(klines)-1)) if len(klines) > 21 else 0
        atr = calc_atr(klines) if len(klines) > 14 else 0
        flow = calc_order_flow_delta(klines) if len(klines) > 10 else 0
        trend = 'BULLISH (price > EMA21)' if price > ema21 else 'BEARISH (price < EMA21)'
        
        prompt = f"""Aap ek expert crypto analyst ho. Ek trader ko HINDI (roman english script) mein yeh coin analyze karke batao. Simple language mein samjhao jisse beginner bhi samajh sake.

Coin: {symbol}
Current Price: ${price}
24h Change: {change_pct:.2f}%
24h High/Low: ${high24} / ${low24}
24h Volume: ${volume/1000000:.1f}M USDT
EMA 21 Trend: {trend}
ATR (Volatility): {atr:.4f}
Order Flow: {'Buyers strong' if flow > 0 else 'Sellers strong'}

Batao:
1. Abhi is coin ka trend kya hai (bullish/bearish)?
2. Kya abhi trade karna safe hai ya wait kare?
3. Agar trade kare toh LONG ya SHORT aur approximate entry/SL/TP
4. Risk level kya hai (Low/Medium/High)?

Maximum 8-10 lines mein jawab do."""
        analysis = call_gemini(prompt, retries=2)
        return jsonify({'analysis': analysis, 'symbol': symbol})
    except Exception as e:
        return jsonify({'analysis': f'Error analyzing {symbol}: {str(e)}', 'symbol': symbol})

if __name__ == "__main__":
    print("Starting Institutional Pro Server on port 5001...")
    app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False)
