from flask import Flask, jsonify
from flask_cors import CORS
import requests
import time
from datetime import datetime
import threading
import re

app = Flask(__name__)
CORS(app)

cache = {"prices": {}, "last_updated": None}
GRAM = 31.1035

def fetch_fx():
    try:
        r = requests.get("https://api.frankfurter.app/latest?base=USD&symbols=EUR,GBP,TRY,CNY,INR,JPY,IDR,AED", timeout=10).json()
        rates = r.get("rates", {})
        rates["USD"] = 1.0
        return rates
    except Exception as e:
        print(f"FX error: {e}")
        return None

def parse_num(val):
    try:
        return float(str(val).replace(",", ""))
    except:
        return None

def fetch_spot():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://www.kitco.com/gold-price-today-usa/", timeout=30000)
            page.wait_for_timeout(5000)
            content = page.inner_text("body")
            browser.close()

        lines = [l.strip() for l in content.split("\n") if l.strip()]
        gold_bid = None
        gold_ask = None
        gold_ch = None
        gold_chp = None

        for i, line in enumerate(lines):
            if line == "Bid" and i+1 < len(lines):
                val = parse_num(lines[i+1])
                if val and val > 1000 and gold_bid is None:
                    gold_bid = val
                    # Kitco layout: bid, USD, ch_abs, ch_pct e.g. (-2.69%)
                    if i+3 < len(lines):
                        gold_ch = parse_num(lines[i+3])
                    if i+4 < len(lines):
                        m = re.search(r'[-+]?\d+\.?\d*', lines[i+4].replace(",",""))
                        if m:
                            gold_chp = float(m.group())
            if line == "Ask" and i+1 < len(lines):
                val = parse_num(lines[i+1])
                if val and val > 1000 and gold_ask is None:
                    gold_ask = val

        if gold_bid and gold_ask:
            print(f"Kitco Gold: bid={gold_bid} ask={gold_ask} ch={gold_ch} chp={gold_chp}")
            try:
                silver = requests.get("https://api.gold-api.com/price/XAG", timeout=10).json()
                price_xag = silver.get("price")
            except:
                price_xag = None
            return {
                "XAU": round((gold_bid + gold_ask) / 2, 2),
                "XAU_bid": gold_bid,
                "XAU_ask": gold_ask,
                "XAU_ch": gold_ch,
                "XAU_chp": gold_chp,
                "XAG": price_xag,
                "XAG_bid": round(price_xag * 0.9999, 2) if price_xag else None,
                "XAG_ask": round(price_xag * 1.0001, 2) if price_xag else None,
                "XAG_ch": None,
                "XAG_chp": None,
            }
        return None
    except Exception as e:
        print(f"Kitco error: {e}")
        return None

def fetch_spot_fallback():
    try:
        gold = requests.get("https://api.gold-api.com/price/XAU", timeout=10).json()
        silver = requests.get("https://api.gold-api.com/price/XAG", timeout=10).json()
        price_xau = gold.get("price")
        price_xag = silver.get("price")
        return {
            "XAU": price_xau,
            "XAU_bid": round(price_xau * 0.9999, 2) if price_xau else None,
            "XAU_ask": round(price_xau * 1.0001, 2) if price_xau else None,
            "XAU_ch": None, "XAU_chp": None,
            "XAG": price_xag,
            "XAG_bid": round(price_xag * 0.9999, 2) if price_xag else None,
            "XAG_ask": round(price_xag * 1.0001, 2) if price_xag else None,
            "XAG_ch": None, "XAG_chp": None,
        }
    except Exception as e:
        print(f"Fallback spot error: {e}")
        return None

def fetch_istanbul():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://www.nadirdoviz.com/", timeout=30000)
            page.wait_for_timeout(4000)
            content = page.inner_text("body")
            browser.close()

        gold_buy = None
        gold_sell = None
        silver_sell = None
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == "Altın/TL":
                for j in range(i+1, min(i+6, len(lines))):
                    val_text = lines[j].strip().replace(".", "").replace(",", ".")
                    try:
                        val = float(val_text)
                        if val > 1000:
                            if gold_buy is None:
                                gold_buy = val
                            elif gold_sell is None:
                                gold_sell = val
                                break
                    except:
                        pass
            if "GümüşKG/TL" in line.strip():
                for j in range(i+1, min(i+6, len(lines))):
                    val_text = lines[j].strip().replace(".", "").replace(",", ".")
                    try:
                        val = float(val_text)
                        if val > 10000:
                            silver_sell = round(val / 1000, 4)
                            break
                    except:
                        pass

        if gold_buy and gold_sell:
            gold = round((gold_buy + gold_sell) / 2, 2)
            print(f"Nadir: buy={gold_buy} sell={gold_sell}")
            return {
                "gold_try_gram": gold,
                "gold_try_gram_buy": gold_buy,
                "gold_try_gram_sell": gold_sell,
                "silver_try_gram": silver_sell,
                "source": "nadirdoviz.com",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Istanbul error: {e}")
        return None

def update():
    print(f"[{datetime.now()}] Updating prices...")
    fx = fetch_fx()
    spot = fetch_spot()
    if not spot or not spot.get("XAU"):
        print("Kitco failed, using fallback...")
        spot = fetch_spot_fallback()
    ist = fetch_istanbul()

    prices = {}
    if spot: prices["spot"] = spot
    if fx: prices["fx"] = fx

    if ist:
        prices["istanbul"] = ist
    elif spot and fx:
        prices["istanbul"] = {
            "gold_try_gram": round((spot["XAU"] / GRAM) * fx["TRY"], 2),
            "silver_try_gram": round((spot["XAG"] / GRAM) * fx["TRY"], 2) if spot.get("XAG") else None,
            "source": "calculated",
            "is_calculated": True
        }

    cache["prices"] = prices
    cache["last_updated"] = datetime.now().isoformat()
    print(f"[{datetime.now()}] Done: {list(prices.keys())}")

def background():
    while True:
        update()
        time.sleep(10 * 60)

@app.route("/api/prices")
def get_prices():
    if not cache["prices"]:
        update()
    return jsonify({"data": cache["prices"], "last_updated": cache["last_updated"], "status": "ok"})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "last_updated": cache["last_updated"]})

@app.route("/")
def index():
    return jsonify({"name": "goldpremium API", "last_updated": cache["last_updated"]})

if __name__ == "__main__":
    update()
    t = threading.Thread(target=background, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000)
