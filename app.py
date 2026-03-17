from flask import Flask, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import time
from datetime import datetime
import threading

app = Flask(__name__)
CORS(app)

cache = { "prices": {}, "last_updated": None }
GRAM_PER_OZ = 31.1035
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

def fetch_spot():
    try:
        g = requests.get("https://api.gold-api.com/price/XAU", timeout=10).json()
        s = requests.get("https://api.gold-api.com/price/XAG", timeout=10).json()
        return { "XAU": g.get("price"), "XAG": s.get("price") }
    except Exception as e:
        print(f"Spot error: {e}")
        return None

def fetch_fx():
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?base=USD&symbols=EUR,GBP,TRY,CNY,INR,JPY,IDR,AED",
            timeout=10
        ).json()
        rates = r.get("rates", {})
        rates["USD"] = 1.0
        return rates
    except Exception as e:
        print(f"FX error: {e}")
        return None

def fetch_istanbul():
    try:
        r = requests.get(
            "https://kapali-carsi-altin-api.vercel.app/api/altin",
            timeout=10
        )
        data = r.json()
        gold_try_gram = None
        silver_try_gram = None
        for item in data:
            code = item.get("code", "").upper()
            if code == "ALTIN":
                alis = float(item.get("alis", 0) or 0)
                satis = float(item.get("satis", 0) or 0)
                if alis and satis:
                    gold_try_gram = round((alis + satis) / 2, 2)
            if code in ("GUMUS", "GÜMÜŞ"):
                alis = float(item.get("alis", 0) or 0)
                satis = float(item.get("satis", 0) or 0)
                if alis and satis:
                    silver_try_gram = round((alis + satis) / 2, 2)
        if gold_try_gram:
            return {
                "gold_try_gram": gold_try_gram,
                "silver_try_gram": silver_try_gram,
                "source": "Kapalıçarşı (kapali-carsi-altin-api)",
                "unit": "TRY/gram",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Istanbul error: {e}")
        return None

def fetch_shanghai():
    try:
        r = requests.get(
            "https://goldprice.org/gold-price-china.html",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        # Suche direkt nach dem Preis-Element
        gold_usd_oz = None
        silver_usd_oz = None
        for tag in soup.find_all(["span", "div", "td", "p"]):
            t = tag.get_text(strip=True).replace(",", "")
            try:
                val = float(t)
                if 3000 < val < 15000 and gold_usd_oz is None:
                    gold_usd_oz = val
                elif 20 < val < 200 and silver_usd_oz is None:
                    silver_usd_oz = val
            except:
                pass
        if gold_usd_oz:
            return {
                "gold_usd_oz": gold_usd_oz,
                "silver_usd_oz": silver_usd_oz,
                "source": "SGE / goldprice.org",
                "unit": "USD/oz",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Shanghai error: {e}")
        return None

def fetch_india():
    try:
        r = requests.get("https://ibjarates.com/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        gold_inr_10g = None
        silver_inr_kg = None
        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                for cell in cells[1:]:
                    val_clean = re.sub(r'[^\d.]', '', cell.get_text(strip=True))
                    try:
                        val = float(val_clean)
                    except:
                        continue
                    if ("gold" in label or "altin" in label or "sona" in label) and 50000 < val < 250000:
                        gold_inr_10g = val
                        break
                    if "silver" in label and 50000 < val < 500000:
                        silver_inr_kg = val
                        break
        if gold_inr_10g:
            return {
                "gold_inr_10g": gold_inr_10g,
                "silver_inr_kg": silver_inr_kg,
                "source": "IBJA (ibjarates.com)",
                "unit": "INR/10g",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"India error: {e}")
        return None

def fetch_japan():
    try:
        r = requests.get(
            "https://goldprice.org/gold-price-japan.html",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        gold_jpy_gram = None
        for tag in soup.find_all(["span", "div", "td"]):
            t = tag.get_text(strip=True).replace(",", "")
            try:
                val = float(t)
                if 10000 < val < 50000 and gold_jpy_gram is None:
                    gold_jpy_gram = val
            except:
                pass
        if gold_jpy_gram:
            return {
                "gold_jpy_gram": gold_jpy_gram,
                "source": "goldprice.org/japan",
                "unit": "JPY/gram",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Japan error: {e}")
        return None

def fetch_indonesia():
    try:
        # Versuche zuerst logammulia.com
        r = requests.get("https://www.logammulia.com/en", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        gold_idr_gram = None
        rows = soup.find_all("tr")
        for row in rows:
            row_text = row.get_text(strip=True).lower()
            if "1 gr" in row_text or "1gr" in row_text:
                for cell in row.find_all("td"):
                    val_clean = re.sub(r'[^\d]', '', cell.get_text(strip=True))
                    try:
                        val = float(val_clean)
                        if 1_000_000 < val < 6_000_000:
                            gold_idr_gram = val
                            break
                    except:
                        pass
                if gold_idr_gram:
                    break

        # Fallback: goldprice.org Indonesia
        if not gold_idr_gram:
            r2 = requests.get(
                "https://goldprice.org/gold-price-indonesia.html",
                headers=HEADERS, timeout=15
            )
            soup2 = BeautifulSoup(r2.text, "html.parser")
            for tag in soup2.find_all(["span", "div", "td"]):
                t = tag.get_text(strip=True).replace(",", "").replace(".", "")
                try:
                    val = float(t)
                    if 1_000_000 < val < 6_000_000 and gold_idr_gram is None:
                        gold_idr_gram = val
                except:
                    pass

        if gold_idr_gram:
            return {
                "gold_idr_gram": gold_idr_gram,
                "source": "Antam/logammulia.com",
                "unit": "IDR/gram",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Indonesia error: {e}")
        return None

def update_prices():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Update gestartet...")
    spot      = fetch_spot()
    fx        = fetch_fx()
    istanbul  = fetch_istanbul()
    shanghai  = fetch_shanghai()
    india     = fetch_india()
    japan     = fetch_japan()
    indonesia = fetch_indonesia()

    print(f"  Istanbul: {'OK' if istanbul and not istanbul.get('is_calculated') else 'FALLBACK'}")
    print(f"  Shanghai: {'OK' if shanghai and not shanghai.get('is_calculated') else 'FALLBACK'}")
    print(f"  India:    {'OK' if india and not india.get('is_calculated') else 'FALLBACK'}")
    print(f"  Japan:    {'OK' if japan and not japan.get('is_calculated') else 'FALLBACK'}")
    print(f"  Indonesia:{'OK' if indonesia and not indonesia.get('is_calculated') else 'FALLBACK'}")

    prices = {}
    if spot: prices["spot"] = spot
    if fx:   prices["fx"] = fx

    def with_fallback(result, key, fallback_fn):
        if result:
            prices[key] = result
        elif spot and fx:
            prices[key] = fallback_fn()

    with_fallback(istanbul, "istanbul", lambda: {
        "gold_try_gram": round((spot["XAU"] / GRAM_PER_OZ) * fx["TRY"], 2),
        "silver_try_gram": round((spot["XAG"] / GRAM_PER_OZ) * fx["TRY"], 2),
        "source": "berechnet (Spot × TRY)", "unit": "TRY/gram", "is_calculated": True
    })
    with_fallback(shanghai, "shanghai", lambda: {
        "gold_usd_oz": spot["XAU"], "silver_usd_oz": spot["XAG"],
        "source": "berechnet (Spot)", "unit": "USD/oz", "is_calculated": True
    })
    with_fallback(india, "india", lambda: {
        "gold_inr_10g": round((spot["XAU"] / GRAM_PER_OZ) * fx["INR"] * 10 * 1.13, 2),
        "silver_inr_kg": round((spot["XAG"] / GRAM_PER_OZ) * fx["INR"] * 1000 * 1.03, 2),
        "source": "berechnet (Spot × INR + 13% Zoll)", "unit": "INR/10g", "is_calculated": True
    })
    with_fallback(japan, "japan", lambda: {
        "gold_jpy_gram": round((spot["XAU"] / GRAM_PER_OZ) * fx["JPY"], 2),
        "source": "berechnet (Spot × JPY)", "unit": "JPY/gram", "is_calculated": True
    })
    with_fallback(indonesia, "indonesia", lambda: {
        "gold_idr_gram": round((spot["XAU"] / GRAM_PER_OZ) * fx["IDR"] * 1.05, 2),
        "source": "berechnet (Spot × IDR)", "unit": "IDR/gram", "is_calculated": True
    })

    cache["prices"] = prices
    cache["last_updated"] = datetime.now().isoformat()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fertig.")

def background_updater():
    while True:
        update_prices()
        time.sleep(15 * 60)

@app.route("/api/prices")
def get_prices():
    if not cache["prices"]:
        update_prices()
    return jsonify({ "data": cache["prices"], "last_updated": cache["last_updated"], "status": "ok" })

@app.route("/health")
def health():
    return jsonify({ "status": "ok", "last_updated": cache["last_updated"] })

@app.route("/")
def index():
    return jsonify({ "name": "Weltgold API", "endpoints": ["/api/prices", "/health"], "last_updated": cache["last_updated"] })

if __name__ == "__main__":
    update_prices()
    t = threading.Thread(target=background_updater, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=10000)
