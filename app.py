from flask import Flask, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import json
import time
from datetime import datetime
import threading

app = Flask(__name__)
CORS(app)

# Cache: Preise werden alle 15 Minuten aktualisiert
cache = {
    "prices": {},
    "last_updated": None,
    "errors": {}
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

GRAM_PER_OZ = 31.1035

# ─────────────────────────────────────────
# 1. SPOT PREIS (gold-api.com)
# ─────────────────────────────────────────
def fetch_spot():
    try:
        gold = requests.get("https://api.gold-api.com/price/XAU", timeout=10).json()
        silver = requests.get("https://api.gold-api.com/price/XAG", timeout=10).json()
        return {
            "XAU": gold.get("price"),
            "XAG": silver.get("price")
        }
    except Exception as e:
        print(f"Spot error: {e}")
        return None

# ─────────────────────────────────────────
# 2. WECHSELKURSE (frankfurter.app)
# ─────────────────────────────────────────
def fetch_fx():
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?base=USD&symbols=EUR,GBP,TRY,CNY,INR,JPY,IDR,AED",
            timeout=10
        )
        data = r.json()
        rates = data.get("rates", {})
        rates["USD"] = 1.0
        return rates
    except Exception as e:
        print(f"FX error: {e}")
        return None

# ─────────────────────────────────────────
# 3. NADIR DÖVIZ (Istanbul)
# ─────────────────────────────────────────
def fetch_nadir():
    try:
        r = requests.get("https://www.nadirdoviz.com/", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        gold_price = None
        silver_price = None

        # Nadir zeigt Preise in einer Tabelle - suche nach Has Altin (reines Gold) und Has Gümüs
        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                # Has Altin = reines Gold per gram in TRY
                if "has alt" in label or "has altin" in label or "altin" in label:
                    val = cells[-1].get_text(strip=True).replace(".", "").replace(",", ".")
                    try:
                        gold_price = float(val)
                    except:
                        pass
                # Has Gümüs = reines Silber per gram in TRY
                if "gümü" in label or "gumus" in label or "gümüş" in label:
                    val = cells[-1].get_text(strip=True).replace(".", "").replace(",", ".")
                    try:
                        silver_price = float(val)
                    except:
                        pass

        if gold_price:
            return {
                "gold_try_gram": gold_price,
                "silver_try_gram": silver_price,
                "source": "nadirdoviz.com",
                "unit": "TRY/gram"
            }
        return None
    except Exception as e:
        print(f"Nadir error: {e}")
        return None

# ─────────────────────────────────────────
# 4. SHANGHAI GOLD EXCHANGE (metalcharts.org)
# ─────────────────────────────────────────
def fetch_sge():
    try:
        # SGE Gold Au(T+D) - meistgehandelter Kontrakt
        r = requests.get(
            "https://metalcharts.org/sge",
            headers=HEADERS,
            timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")

        gold_usd_oz = None
        silver_usd_oz = None

        # metalcharts.org zeigt SGE Preise in USD/oz konvertiert
        # Suche nach dem Au(T+D) Preis
        text = soup.get_text()
        lines = text.split("\n")
        for i, line in enumerate(lines):
            line = line.strip()
            if "Au(T+D)" in line or "au(t+d)" in line.lower():
                # Preis ist meist in der Nähe
                for j in range(max(0, i-3), min(len(lines), i+5)):
                    try:
                        val = lines[j].strip().replace(",", "")
                        if val.replace(".", "").isdigit() and float(val) > 1000:
                            gold_usd_oz = float(val)
                            break
                    except:
                        pass
            if "Ag(T+D)" in line or "ag(t+d)" in line.lower():
                for j in range(max(0, i-3), min(len(lines), i+5)):
                    try:
                        val = lines[j].strip().replace(",", "")
                        if val.replace(".", "").isdigit() and float(val) > 10:
                            silver_usd_oz = float(val)
                            break
                    except:
                        pass

        # Fallback: direkte SGE Seite
        if not gold_usd_oz:
            r2 = requests.get(
                "https://metalcharts.org/sge/gold",
                headers=HEADERS,
                timeout=15
            )
            soup2 = BeautifulSoup(r2.text, "html.parser")
            # Suche nach Preis-Pattern
            import re
            prices = re.findall(r'\$?([\d,]+\.?\d*)\s*/\s*oz', soup2.get_text())
            for p in prices:
                try:
                    val = float(p.replace(",", ""))
                    if val > 1000:
                        gold_usd_oz = val
                        break
                except:
                    pass

        if gold_usd_oz:
            return {
                "gold_usd_oz": gold_usd_oz,
                "silver_usd_oz": silver_usd_oz,
                "source": "metalcharts.org/sge",
                "unit": "USD/oz"
            }
        return None
    except Exception as e:
        print(f"SGE error: {e}")
        return None

# ─────────────────────────────────────────
# 5. INDIEN - IBJA (ibjarates.com)
# ─────────────────────────────────────────
def fetch_ibja():
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
                if "gold 999" in label or "999" in label and "gold" in label:
                    val = cells[-1].get_text(strip=True).replace(",", "").replace("₹", "").strip()
                    try:
                        gold_inr_10g = float(val)
                    except:
                        pass
                if "silver 999" in label or "silver" in label:
                    val = cells[-1].get_text(strip=True).replace(",", "").replace("₹", "").strip()
                    try:
                        silver_inr_kg = float(val)
                    except:
                        pass

        if gold_inr_10g:
            return {
                "gold_inr_10g": gold_inr_10g,
                "silver_inr_kg": silver_inr_kg,
                "source": "ibjarates.com",
                "unit": "INR/10g (Gold), INR/kg (Silver)"
            }
        return None
    except Exception as e:
        print(f"IBJA error: {e}")
        return None

# ─────────────────────────────────────────
# 6. JAPAN - TOCOM (metalcharts.org)
# ─────────────────────────────────────────
def fetch_tocom():
    try:
        r = requests.get(
            "https://www.tocom.or.jp/eng/market/gold.html",
            headers=HEADERS,
            timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")

        gold_jpy_gram = None

        # TOCOM quotiert in JPY/gram
        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                if "spot" in label or "gold" in label:
                    val = cells[-1].get_text(strip=True).replace(",", "").strip()
                    try:
                        v = float(val)
                        if v > 1000:  # JPY/gram ist typisch ~15000-20000
                            gold_jpy_gram = v
                            break
                    except:
                        pass

        # Fallback: goldprice.org Japan
        if not gold_jpy_gram:
            r2 = requests.get(
                "https://goldprice.org/gold-price-japan.html",
                headers=HEADERS,
                timeout=15
            )
            soup2 = BeautifulSoup(r2.text, "html.parser")
            import re
            # Suche nach JPY Preis pro Gramm
            text = soup2.get_text()
            matches = re.findall(r'([\d,]+)\s*(?:JPY|¥)', text)
            for m in matches:
                try:
                    val = float(m.replace(",", ""))
                    if 10000 < val < 50000:  # plausible JPY/gram range
                        gold_jpy_gram = val
                        break
                except:
                    pass

        if gold_jpy_gram:
            return {
                "gold_jpy_gram": gold_jpy_gram,
                "source": "tocom.or.jp / goldprice.org",
                "unit": "JPY/gram"
            }
        return None
    except Exception as e:
        print(f"TOCOM error: {e}")
        return None

# ─────────────────────────────────────────
# 7. INDONESIEN - ANTAM (logammulia.com)
# ─────────────────────────────────────────
def fetch_antam():
    try:
        r = requests.get(
            "https://www.logammulia.com/en",
            headers=HEADERS,
            timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")

        gold_idr_gram = None

        # Antam zeigt Preis in IDR/gram
        rows = soup.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True)
                if "1 gr" in label.lower() or "1gr" in label.lower():
                    val = cells[-1].get_text(strip=True).replace(".", "").replace(",", "").replace("Rp", "").strip()
                    try:
                        gold_idr_gram = float(val)
                        break
                    except:
                        pass

        # Fallback: suche direkt nach IDR Preis
        if not gold_idr_gram:
            import re
            text = soup.get_text()
            matches = re.findall(r'Rp\s*([\d.]+)', text)
            for m in matches:
                try:
                    val = float(m.replace(".", ""))
                    if 1_000_000 < val < 5_000_000:  # plausible IDR/gram
                        gold_idr_gram = val
                        break
                except:
                    pass

        if gold_idr_gram:
            return {
                "gold_idr_gram": gold_idr_gram,
                "source": "logammulia.com",
                "unit": "IDR/gram"
            }
        return None
    except Exception as e:
        print(f"Antam error: {e}")
        return None

# ─────────────────────────────────────────
# HAUPT-UPDATE FUNKTION
# ─────────────────────────────────────────
def update_prices():
    print(f"[{datetime.now()}] Aktualisiere Preise...")

    spot = fetch_spot()
    fx = fetch_fx()
    nadir = fetch_nadir()
    sge = fetch_sge()
    ibja = fetch_ibja()
    tocom = fetch_tocom()
    antam = fetch_antam()

    prices = {}

    if spot:
        prices["spot"] = spot

    if fx:
        prices["fx"] = fx

    if nadir:
        prices["istanbul"] = nadir
    else:
        # Fallback: berechnet
        if spot and fx:
            prices["istanbul"] = {
                "gold_try_gram": round((spot["XAU"] / GRAM_PER_OZ) * fx["TRY"], 2),
                "silver_try_gram": round((spot["XAG"] / GRAM_PER_OZ) * fx["TRY"], 2),
                "source": "berechnet (Spot × TRY)",
                "unit": "TRY/gram",
                "is_calculated": True
            }

    if sge:
        prices["shanghai"] = sge
    else:
        if spot and fx:
            prices["shanghai"] = {
                "gold_usd_oz": spot["XAU"],
                "silver_usd_oz": spot["XAG"],
                "source": "berechnet (Spot × CNY)",
                "unit": "USD/oz",
                "is_calculated": True
            }

    if ibja:
        prices["india"] = ibja
    else:
        if spot and fx:
            prices["india"] = {
                "gold_inr_10g": round((spot["XAU"] / GRAM_PER_OZ) * fx["INR"] * 10 * 1.13, 2),
                "silver_inr_kg": round((spot["XAG"] / GRAM_PER_OZ) * fx["INR"] * 1000 * 1.03, 2),
                "source": "berechnet (Spot × INR + Zoll)",
                "unit": "INR/10g",
                "is_calculated": True
            }

    if tocom:
        prices["japan"] = tocom
    else:
        if spot and fx:
            prices["japan"] = {
                "gold_jpy_gram": round((spot["XAU"] / GRAM_PER_OZ) * fx["JPY"], 2),
                "source": "berechnet (Spot × JPY)",
                "unit": "JPY/gram",
                "is_calculated": True
            }

    if antam:
        prices["indonesia"] = antam
    else:
        if spot and fx:
            prices["indonesia"] = {
                "gold_idr_gram": round((spot["XAU"] / GRAM_PER_OZ) * fx["IDR"] * 1.05, 2),
                "source": "berechnet (Spot × IDR + 5%)",
                "unit": "IDR/gram",
                "is_calculated": True
            }

    cache["prices"] = prices
    cache["last_updated"] = datetime.now().isoformat()
    print(f"[{datetime.now()}] Fertig. Märkte geladen: {list(prices.keys())}")

# ─────────────────────────────────────────
# BACKGROUND THREAD: alle 15 Minuten
# ─────────────────────────────────────────
def background_updater():
    while True:
        update_prices()
        time.sleep(15 * 60)

# ─────────────────────────────────────────
# API ENDPOINTS
# ─────────────────────────────────────────
@app.route("/api/prices")
def get_prices():
    if not cache["prices"]:
        update_prices()
    return jsonify({
        "data": cache["prices"],
        "last_updated": cache["last_updated"],
        "status": "ok"
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok", "last_updated": cache["last_updated"]})

@app.route("/")
def index():
    return jsonify({
        "name": "Weltgold API",
        "endpoints": ["/api/prices", "/health"],
        "last_updated": cache["last_updated"]
    })

# ─────────────────────────────────────────
# START
# ─────────────────────────────────────────
if __name__ == "__main__":
    # Einmal sofort laden
    update_prices()
    # Background Thread starten
    t = threading.Thread(target=background_updater, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=10000)
