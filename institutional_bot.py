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

# --- Advanced Institutional Logic ---
def calc_atr(klines, period=14):
    if len(klines) < period: return 0
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def calc_ema(klines, period=200):
    if len(klines) < period: return 0
    closes = [float(k[4]) for k in klines]
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def detect_liquidity_sweep(klines):
    if len(klines) < 20: return None
    # Look back 15 candles (1 hour 15 mins on 5m chart)
    recent_lows = [float(k[3]) for k in klines[-15:-2]]
    recent_highs = [float(k[2]) for k in klines[-15:-2]]
    
    major_support = min(recent_lows)
    major_resistance = max(recent_highs)
    
    last_candle = klines[-2]
    lc_low, lc_high, lc_close, lc_open = float(last_candle[3]), float(last_candle[2]), float(last_candle[4]), float(last_candle[1])
    
    # Check for strong rejection (long wick)
    body = abs(lc_close - lc_open)
    lower_wick = min(lc_open, lc_close) - lc_low
    upper_wick = lc_high - max(lc_open, lc_close)
    
    if lc_low < major_support and lc_close > major_support and lower_wick > body * 1.5:
        return "LONG_SWEEP"
    if lc_high > major_resistance and lc_close < major_resistance and upper_wick > body * 1.5:
        return "SHORT_SWEEP"
    return None

def calc_order_flow_delta(klines):
    if len(klines) < 10: return 0
    delta = 0
    for k in klines[-10:]:
        o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        if h == l: continue
        buying_pressure = (c - l) / (h - l)
        selling_pressure = (h - c) / (h - l)
        delta += (buying_pressure - selling_pressure) * v
    return delta

def knn_probability(klines, pattern_length=10, lookforward=5, k=5):
    if len(klines) < 100: return 0.0
    closes = [float(candle[4]) for candle in klines]
    curr_slice = closes[-pattern_length:]
    curr_base = curr_slice[0]
    if curr_base == 0: return 0.0
    
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
        future_move_pct = (future_price - end_of_pattern_price) / end_of_pattern_price * 100
        
        distances.append((dist, future_move_pct))
    
    distances.sort(key=lambda x: x[0])
    top_k = distances[:k]
    if not top_k: return 0.0
    avg_future_move = sum(match[1] for match in top_k) / k
    if avg_future_move > 0:
        return (sum(1 for match in top_k if match[1] > 0) / k) * 100
    else:
        return -(sum(1 for match in top_k if match[1] < 0) / k) * 100

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
            elif r.status_code == 429 or r.status_code == 403:
                print("Gemini API Limit hit. Rotating key...")
                rotate_gemini_key()
                time.sleep(2)
            else:
                print(f"Gemini API Error {r.status_code}: {r.text}")
                rotate_gemini_key()
                time.sleep(2)
        except Exception as e:
            print(f"Gemini Request Exception: {e}")
            time.sleep(2)
    return "Error: AI Service Unavailable."

def ai_risk_assessment(symbol, sweep, flow_delta, prob, price, sl, tp):
    prompt = f"""You are an Aggressive AI Scalper. A quantitative algo found a 5-minute setup for {symbol}.
    Setup Details:
    - Liquidity Trap: {sweep}
    - Order Flow Delta: {flow_delta:.2f}
    - Statistical Win Prob: {prob:.2f}%
    - Entry: {price}, SL: {sl}, TP: {tp}
    
    We need high-frequency trades. As long as Order Flow matches the Sweep direction, APPROVE IT.
    Reply strictly in JSON format: {{"trade_approved": boolean, "reason": "1 short sentence reason"}}"""
    
    resp_text = call_gemini(prompt, retries=3)
    if "Error" in resp_text:
        return {"trade_approved": True, "reason": "Auto-Approved (API Retry Limit)"}
    
    try:
        clean_text = resp_text.replace('```json', '').replace('```', '').strip()
        data = json.loads(clean_text)
        return data
    except Exception as e:
        return {"trade_approved": True, "reason": "Auto-Approved (Parse Error)"}

def get_hindi_analysis(trade_data):
    prompt = f"""Aap ek professional crypto trading mentor hain. Ek beginner trader ko HINDI (roman english script) mein samjhao ki yeh trade kyun li gayi.
    Trade Details:
    - Coin: {trade_data['symbol']}
    - Type: {trade_data['side']}
    - Entry Price: {trade_data['entry']}
    - Stop Loss: {trade_data['sl']}
    - AI ka Reason: {trade_data['ai_reason']}
    
    Reply mein simple language use karna. Technical terms ko asaan bhasha mein samjhao jisse beginner bhi samajh sake. Maximum 3-4 lines."""
    return call_gemini(prompt, retries=2)

# --- Scanner Thread ---
def market_scanner():
    global daily_pnl, daily_reset_date
    print("Quant Scanner Started...")
    while True:
        try:
            if not bot_running:
                time.sleep(5)
                continue
                
            # Daily Reset Check
            today = datetime.now().strftime("%Y-%m-%d")
            if today != daily_reset_date:
                print(f"\n{'='*50}")
                print(f"📅 NEW TRADING DAY: {today} | Yesterday PNL: ${daily_pnl:.2f}")
                print(f"{'='*50}\n")
                daily_pnl = 0.0
                daily_reset_date = today
                save_data()
            
            # CIRCUIT BREAKER
            if daily_pnl <= -DAILY_LOSS_LIMIT:
                print(f"🛑 DAILY LOSS LIMIT HIT (${daily_pnl:.2f}). No more trades today.")
                time.sleep(300)
                continue
            
            if len(active_trades) >= MAX_CONCURRENT:
                time.sleep(10)
                continue
            
            tickers = b_get("/fapi/v1/ticker/24hr")
            valid_coins = [t for t in tickers if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > 50000000]
            valid_coins.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
            
            for t in valid_coins[:50]:
                sym = t["symbol"]
                if sym in active_trades: continue
                if len(active_trades) >= MAX_CONCURRENT: break
                
                # CHANGED to 5m timeframe for frequent trades
                k_5m = b_get("/fapi/v1/klines", {"symbol": sym, "interval": "5m", "limit": 200})
                if not k_5m or len(k_5m) < 50: continue
                
                sweep = detect_liquidity_sweep(k_5m)
                if not sweep: continue
                
                flow_delta = calc_order_flow_delta(k_5m)
                prob = knn_probability(k_5m)
                
                price = float(k_5m[-1][4])
                
                # Fast Intraday Trend Filter: EMA 21
                ema_fast = calc_ema(k_5m, min(21, len(k_5m) - 1))
                
                direction = None
                if sweep == "LONG_SWEEP" and flow_delta > 0 and prob >= MIN_WIN_PROBABILITY and price > ema_fast:
                    direction = "LONG"
                elif sweep == "SHORT_SWEEP" and flow_delta < 0 and prob <= -MIN_WIN_PROBABILITY and price < ema_fast:
                    direction = "SHORT"
                
                if direction:
                    atr = calc_atr(k_5m)
                    
                    max_sl_pct = MAX_RISK_PER_TRADE / (MARGIN_PER_TRADE * DEFAULT_LEVERAGE)
                    sl_dist = min(2.5 * atr, price * max_sl_pct)
                    tp_dist = sl_dist * 1.2
                    
                    if direction == "LONG":
                        sl, tp = price - sl_dist, price + tp_dist
                    else:
                        sl, tp = price + sl_dist, price - tp_dist
                        
                    print(f"[{sym}] Asking AI Risk Manager...")
                    ai_check = ai_risk_assessment(sym, sweep, flow_delta, prob, price, sl, tp)
                    if not ai_check.get("trade_approved", True):
                        continue
                        
                    trade_id = f"TRD_{int(time.time())}"
                    active_trades[sym] = {
                        "id": trade_id, "symbol": sym, "side": direction,
                        "entry": price, "sl": sl, "tp": tp,
                        "margin": MARGIN_PER_TRADE, "leverage": DEFAULT_LEVERAGE,
                        "prob": abs(prob), "ai_reason": ai_check.get('reason', 'Approved'),
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    save_data()
                    print(f"SNIPER ENTRY: {direction} {sym}")
                    
                live_radar[sym] = {"sweep": sweep, "delta": flow_delta, "prob": abs(prob)}
                
        except Exception as e:
            print(f"Scanner Error: {e}")
        time.sleep(45)

threading.Thread(target=market_scanner, daemon=True).start()

# --- Trade Manager Thread ---
def trade_manager():
    global paper_wallet, daily_pnl
    while True:
        try:
            for sym in list(active_trades.keys()):
                pos = active_trades[sym]
                ticker = b_get("/fapi/v1/ticker/price", {"symbol": sym})
                if not ticker or 'price' not in ticker: continue
                
                curr_price = float(ticker['price'])
                diff = (curr_price - pos['entry']) / pos['entry']
                if pos['side'] == "SHORT": diff = -diff
                
                unrealized_pnl = pos['margin'] * pos['leverage'] * diff
                pos['curr_price'] = curr_price
                pos['unrealized_pnl'] = unrealized_pnl
                
                closed = False
                reason = ""
                if pos['side'] == "LONG":
                    if curr_price <= pos['sl']: closed = True; reason = "STOP_LOSS"
                    elif curr_price >= pos['tp']: closed = True; reason = "TAKE_PROFIT"
                else:
                    if curr_price >= pos['sl']: closed = True; reason = "STOP_LOSS"
                    elif curr_price <= pos['tp']: closed = True; reason = "TAKE_PROFIT"
                    
                if closed:
                    paper_wallet += unrealized_pnl
                    daily_pnl += unrealized_pnl
                    pos['exit_price'] = curr_price
                    pos['pnl'] = unrealized_pnl
                    pos['reason'] = reason
                    pos['exit_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    trade_history.insert(0, pos)
                    del active_trades[sym]
                    save_data()
                    print(f"TRADE CLOSED: {sym} | PNL: ${unrealized_pnl:.2f}")
                    
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
                        <input type="text" id="tvSymbol" class="form-control form-control-sm bg-dark text-white border-secondary" placeholder="e.g. BTCUSDT" value="BTCUSDT" style="min-width:130px;">
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

    setInterval(fetchData, 3000);
    fetchData();
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
        "bot_running": bot_running
    })

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
