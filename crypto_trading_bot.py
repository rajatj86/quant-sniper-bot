#!/usr/bin/env python3
"""
=============================================================
  FREE AI CRYPTO TRADING BOT
  Binance Futures + Gemini AI + Telegram Alerts
  100% Free — No paid APIs needed
=============================================================
SETUP:
  1. pip install requests
  2. Fill CONFIG section below
  3. python crypto_trading_bot.py
=============================================================
"""

import requests, hmac, hashlib, time, json
from datetime import datetime

# ===================== CONFIG =====================
BINANCE_KEY      = "aerqxpwACAnaZPcNA9DwQc8zRIAuTgEW9PEk5yeJRq8cTFEfX4AG3lZON79Jh9zQ"
BINANCE_SECRET   = "LzF31S9uMqIWbIbpeqb9AhyjOt7NopHKwgp1ngWs17iGiJn296dbGzJzIoraz6YV"
GEMINI_KEY       = "AIzaSyA0UMYMS7e11lK2t-c-IkOydYAtWj6EuuE"        # aistudio.google.com FREE
TELEGRAM_TOKEN   = "8005708874:AAFgb-KNDWwNz03KSkQav_-WZda-cCYxAPg"    # @BotFather FREE
TELEGRAM_CHAT_ID = "687828695D"      # getUpdates se

FUTURES_MODE     = True
LEVERAGE         = 3
MARGIN_PER_TRADE = 3.0
MIN_GAIN_PCT     = 5.0
MIN_VOLUME_USD   = 3000000
MAX_OPEN_TRADES  = 3
SCAN_INTERVAL    = 1800
LOG_FILE         = "trades.log"
CONFIRM_TRADES   = True          # True = Telegram se confirm manga, False = auto trade
CONFIRM_TIMEOUT  = 300           # 5 minutes wait for confirmation
# ==================================================

FAPI = "https://fapi.binance.com"
SAPI = "https://api.binance.com"
BASE = FAPI if FUTURES_MODE else SAPI
ACTIVE_TRADES = {}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + "\n")

def telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=10
        )
    except Exception as e:
        log(f"Telegram error: {e}")

def get_last_update_id():
    """Current last update ID nikalo taaki purane messages ignore ho jayein"""
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                         params={"limit": 1, "offset": -1}, timeout=10)
        updates = r.json().get("result", [])
        if updates:
            return updates[-1]["update_id"]
    except:
        pass
    return 0

def wait_for_confirmation(symbol, price, signal_data, vol_ratio):
    """Telegram par signal bhejo aur YES/NO ka wait karo"""
    log(f"📨 Sending trade confirmation request for {symbol}...")

    # Pehle last update ID save karo taaki purane messages na padhein
    last_id = get_last_update_id()

    telegram(
        f"🔔 <b>TRADE CONFIRMATION REQUIRED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Coin: <b>{symbol}</b>\n"
        f"Entry: <b>${price:.6f}</b>\n"
        f"Stop Loss: <b>${signal_data['sl']:.6f}</b>\n"
        f"TP1: <b>${signal_data['tp1']:.6f}</b>\n"
        f"TP2: <b>${signal_data['tp2']:.6f}</b>\n"
        f"Volume Surge: <b>{vol_ratio}x</b>\n"
        f"AI Confidence: <b>{signal_data.get('confidence')}</b>\n"
        f"Reason: {signal_data.get('reason','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Reply <b>YES</b> to trade | <b>NO</b> to skip\n"
        f"⏳ {CONFIRM_TIMEOUT//60} min me reply karo, warna skip ho jayega"
    )

    log(f"⏳ Waiting {CONFIRM_TIMEOUT}s for your Telegram confirmation...")
    deadline = time.time() + CONFIRM_TIMEOUT

    while time.time() < deadline:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": last_id + 1, "timeout": 20},
                timeout=30
            )
            updates = r.json().get("result", [])
            for upd in updates:
                last_id = upd["update_id"]
                msg = upd.get("message", {}).get("text", "").strip().upper()
                chat = str(upd.get("message", {}).get("chat", {}).get("id", ""))

                if chat != str(TELEGRAM_CHAT_ID): continue  # sirf aapka message

                if msg == "YES":
                    log(f"✅ Confirmed by user! Trading {symbol}...")
                    telegram(f"✅ <b>CONFIRMED!</b> Placing trade for {symbol}...")
                    return True
                elif msg == "NO":
                    log(f"❌ Trade skipped by user for {symbol}")
                    telegram(f"❌ <b>Skipped!</b> {symbol} trade cancelled.")
                    return False
        except Exception as e:
            log(f"Confirmation poll error: {e}")
        time.sleep(3)

    # Timeout
    log(f"⏰ No reply in {CONFIRM_TIMEOUT}s. Skipping {symbol}.")
    telegram(f"⏰ <b>Timeout!</b> No reply received. {symbol} trade skipped.")
    return False

def _sign(params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def b_get(path, params=None):
    r = requests.get(f"{BASE}{path}", params=params or {}, timeout=10)
    return r.json()

def b_auth_get(path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    query = "&".join(f"{k}={v}" for k, v in p.items()) + f"&signature={_sign(p)}"
    r = requests.get(f"{BASE}{path}?{query}",
                     headers={"X-MBX-APIKEY": BINANCE_KEY}, timeout=10)
    return r.json()

def b_auth_post(path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 10000
    body = "&".join(f"{k}={v}" for k, v in p.items()) + f"&signature={_sign(p)}"
    r = requests.post(f"{BASE}{path}", data=body,
                      headers={"X-MBX-APIKEY": BINANCE_KEY,
                               "Content-Type": "application/x-www-form-urlencoded"},
                      timeout=10)
    return r.json()

def get_balance():
    try:
        return float(b_auth_get("/fapi/v2/account").get("availableBalance", 0))
    except:
        return 0.0

def get_open_positions():
    try:
        data = b_auth_get("/fapi/v2/account")
        if isinstance(data, dict):
            return [p for p in data.get("positions", [])
                    if abs(float(p.get("positionAmt", 0))) > 0]
        return []
    except:
        return []

def get_top_gainers():
    try:
        path = "/fapi/v1/ticker/24hr" if FUTURES_MODE else "/api/v3/ticker/24hr"
        data = b_get(path)
        c = [t for t in data
             if t["symbol"].endswith("USDT")
             and float(t["priceChangePercent"]) > MIN_GAIN_PCT
             and float(t["quoteVolume"]) > MIN_VOLUME_USD
             and float(t["lastPrice"]) > 0]
        c.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)
        return c[:20]
    except:
        return []

def get_klines(symbol, interval="1h", limit=24):
    try:
        path = "/fapi/v1/klines" if FUTURES_MODE else "/api/v3/klines"
        return b_get(path, {"symbol": symbol, "interval": interval, "limit": limit})
    except:
        return []

def get_volume_surge(symbol):
    klines = get_klines(symbol, "1h", 8)
    if len(klines) < 7: return 1.0
    last2  = float(klines[5][7]) + float(klines[6][7])
    prior2 = float(klines[3][7]) + float(klines[4][7])
    return round(last2 / prior2, 3) if prior2 > 0 else 1.0

def get_price(symbol):
    try:
        path = "/fapi/v1/ticker/price" if FUTURES_MODE else "/api/v3/ticker/price"
        return float(b_get(path, {"symbol": symbol})["price"])
    except:
        return 0.0

def get_lot_step(symbol):
    try:
        ei = b_get("/fapi/v1/exchangeInfo")
        si = next((s for s in ei["symbols"] if s["symbol"] == symbol), None)
        if not si: return 1.0
        lot = next((f for f in si["filters"] if f["filterType"] == "LOT_SIZE"), None)
        return float(lot["stepSize"]) if lot else 1.0
    except:
        return 1.0

def calc_atr(klines, period=14):
    trs = []
    for i in range(1, len(klines)):
        h  = float(klines[i][2])
        l  = float(klines[i][3])
        pc = float(klines[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    subset = trs[-period:]
    return sum(subset) / len(subset) if subset else 0.0

def get_ai_signal(symbol, price, change_pct, klines):
    if not klines or len(klines) < 10: return None
    closes = [float(k[4]) for k in klines[-12:]]
    highs  = [float(k[2]) for k in klines[-12:]]
    lows   = [float(k[3]) for k in klines[-12:]]
    atr    = calc_atr(klines)
    sl     = round(price - 1.5 * atr, 8)
    tp1    = round(price + 2.0 * atr, 8)
    tp2    = round(price + 3.5 * atr, 8)

    prompt = f"""You are a professional crypto futures trader with 20 years experience.
Analyze {symbol}:
Price: {price}, 24h change: {change_pct:.2f}%
Last 12 closes: {[round(c,6) for c in closes]}
12h High: {max(highs):.6f}, Low: {min(lows):.6f}
ATR(14): {atr:.6f}
Pre-calc: SL={sl}, TP1={tp1}, TP2={tp2}

Signal LONG only if strong momentum. Else SKIP.
Reply ONLY valid JSON (no markdown):
{{"signal":"LONG","confidence":"HIGH","entry":{price},"sl":{sl},"tp1":{tp1},"tp2":{tp2},"reason":"one line"}}"""

    try:
        # gemini-2.5-flash: CONFIRMED working model (verified live from API)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        r = requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}]}, timeout=30)
        data = r.json()
        if "candidates" not in data:
            log(f"Gemini API issue for {symbol}: {data.get('error', data)}")
            return None
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        clean = raw.strip().replace("```json","").replace("```","").strip()
        return json.loads(clean)
    except Exception as e:
        log(f"Gemini error {symbol}: {e}")
        return None

def set_leverage_isolated(symbol):
    try:
        b_auth_post("/fapi/v1/leverage", {"symbol": symbol, "leverage": LEVERAGE})
    except: pass
    try:
        b_auth_post("/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"})
    except: pass

def place_buy(symbol, qty):
    return b_auth_post("/fapi/v1/order",
                       {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": str(int(qty))})

def place_sell(symbol, qty):
    return b_auth_post("/fapi/v1/order",
                       {"symbol": symbol, "side": "SELL", "type": "MARKET",
                        "quantity": str(int(qty)), "reduceOnly": "true"})

def check_positions():
    for sym in list(ACTIVE_TRADES.keys()):
        t = ACTIVE_TRADES[sym]
        try:
            curr    = get_price(sym)
            if curr == 0: continue
            entry   = t["entry"]
            pnl_pct = (curr - entry) / entry * 100

            if curr <= t["sl"]:
                r = place_sell(sym, t["qty"])
                telegram(f"🛑 <b>STOP LOSS</b>\n{sym}\nEntry:{entry:.6f} → {curr:.6f}\nPnL:{pnl_pct:.2f}%")
                log(f"SL {sym} @ {curr} PnL:{pnl_pct:.2f}%")
                del ACTIVE_TRADES[sym]

            elif curr >= t["tp2"] and t.get("tp1_hit"):
                r = place_sell(sym, t["qty"])
                telegram(f"🎯 <b>TP2 FULL CLOSE</b>\n{sym}\nEntry:{entry:.6f} → {curr:.6f}\nPnL:{pnl_pct:.2f}%")
                log(f"TP2 {sym} @ {curr} PnL:{pnl_pct:.2f}%")
                del ACTIVE_TRADES[sym]

            elif curr >= t["tp1"] and not t.get("tp1_hit"):
                half = max(1, int(t["qty"] // 2))
                r = place_sell(sym, half)
                telegram(f"✅ <b>TP1 — 50% CLOSED</b>\n{sym}\n{curr:.6f} (+{pnl_pct:.2f}%)\nRemaining: {t['qty']-half}")
                log(f"TP1 {sym} @ {curr}")
                ACTIVE_TRADES[sym]["tp1_hit"] = True
                ACTIVE_TRADES[sym]["qty"] = t["qty"] - half
            else:
                log(f"HOLD {sym} @ {curr:.6f} ({pnl_pct:+.2f}%)")
        except Exception as e:
            log(f"Watcher error {sym}: {e}")

def scan_and_trade():
    log("=" * 40)
    log("🔍 Scanning market...")
    bal = get_balance()
    log(f"Balance: ${bal:.2f} USDT")

    open_pos = get_open_positions()
    if len(open_pos) >= MAX_OPEN_TRADES:
        log("Max trades open"); return

    already_in = {p["symbol"] for p in open_pos}
    gainers    = get_top_gainers()
    placed     = 0
    max_new    = MAX_OPEN_TRADES - len(open_pos)

    for ticker in gainers:
        if placed >= max_new: break
        sym    = ticker["symbol"]
        price  = float(ticker["lastPrice"])
        change = float(ticker["priceChangePercent"])

        if sym in already_in: continue

        vol_ratio = get_volume_surge(sym)
        if vol_ratio < 1.0:
            continue

        klines = get_klines(sym)
        if not klines: continue

        signal = get_ai_signal(sym, price, change, klines)
        if not signal: continue

        log(f"🎯 AI SIGNAL DETECTED: {sym} -> {signal.get('signal')} ({signal.get('confidence')})")
        log(f"   Reason: {signal.get('reason','')}")

        if signal.get("signal") == "LONG" and signal.get("confidence") in ["HIGH", "MEDIUM"]:
            step = get_lot_step(sym)
            qty  = max(step, int((MARGIN_PER_TRADE * LEVERAGE / price) / step) * step)
            if qty <= 0: continue

            # If no balance, just send alert and skip actual order
            if bal < MARGIN_PER_TRADE:
                telegram(
                    f"⚠️ <b>SIGNAL ALERT (No Funds)</b>\n"
                    f"Coin: {sym}\nEntry: {price:.6f}\n"
                    f"SL: {signal['sl']:.6f}\n"
                    f"TP1: {signal['tp1']:.6f}\n"
                    f"TP2: {signal['tp2']:.6f}\n"
                    f"Vol surge: {vol_ratio}x\n"
                    f"AI: {signal.get('confidence')} — {signal.get('reason','')}\n"
                    f"<i>(Add funds to auto-trade this next time)</i>"
                )
                log(f"⚠️ Alert Sent for {sym} (No balance to trade)")
                placed += 1
                time.sleep(1)
                continue

            # If balance exists, ask for confirmation then place real trade
            if CONFIRM_TRADES:
                confirmed = wait_for_confirmation(sym, price, signal, vol_ratio)
                if not confirmed:
                    continue  # User ne NO kaha ya timeout

            set_leverage_isolated(sym)
            time.sleep(0.3)
            result = place_buy(sym, qty)

            if result.get("orderId"):
                ACTIVE_TRADES[sym] = {
                    "entry": price, "sl": signal["sl"],
                    "tp1": signal["tp1"], "tp2": signal["tp2"],
                    "qty": qty, "tp1_hit": False
                }
                telegram(
                    f"🚀 <b>NEW TRADE</b>\n"
                    f"Coin: {sym}\nEntry: {price:.6f}\n"
                    f"SL: {signal['sl']:.6f}\n"
                    f"TP1: {signal['tp1']:.6f}\n"
                    f"TP2: {signal['tp2']:.6f}\n"
                    f"Qty: {qty} | {LEVERAGE}x | ${MARGIN_PER_TRADE} margin\n"
                    f"Vol surge: {vol_ratio}x\n"
                    f"AI: {signal.get('confidence')} — {signal.get('reason','')}"
                )
                log(f"✅ Placed {sym} qty={qty}")
                placed += 1
            else:
                log(f"❌ Failed: {result}")
        time.sleep(1)

if __name__ == "__main__":
    log("🤖 BOT STARTED")
    telegram(f"🤖 <b>Bot Started!</b>\nFutures | {LEVERAGE}x | ${MARGIN_PER_TRADE}/trade\nScan every {SCAN_INTERVAL//60} min")
    cycle = 0
    while True:
        try:
            cycle += 1
            log(f"\n--- CYCLE {cycle} ---")
            check_positions()
            scan_and_trade()
        except KeyboardInterrupt:
            log("Stopped."); telegram("🛑 Bot Stopped"); break
        except Exception as e:
            log(f"Error: {e}"); telegram(f"⚠️ Error: {e}")
        log(f"💤 Sleeping {SCAN_INTERVAL}s...")
        time.sleep(SCAN_INTERVAL)
