from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
from datetime import datetime
import threading
import re
import sqlite3
import os

app = Flask(__name__)
CORS(app)

cache = {"prices": {}, "last_updated": None, "yesterday": {}}
GRAM = 31.1035
DB_PATH = "/opt/goldpremium/prices.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        market TEXT NOT NULL,
        gold_usd_oz REAL,
        gold_local REAL,
        silver_local REAL,
        premium_pct REAL,
        local_currency TEXT
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_market_ts ON price_history(market, ts)')
    conn.commit()
    conn.close()
    print("DB initialized")

def save_prices(prices):
    if not prices.get("spot") or not prices.get("fx"):
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        spot_xau = prices["spot"].get("XAU")
        spot_xag = prices["spot"].get("XAG")
        fx = prices["fx"]

        def calc_premium(local_usd_oz):
            if local_usd_oz and spot_xau:
                return round((local_usd_oz - spot_xau) / spot_xau * 100, 4)
            return None

        # COMEX spot
        c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency) VALUES (?,?,?,?,?,?,?)",
            (ts, 'comex', spot_xau, spot_xau, spot_xag, 0.0, 'USD'))

        # Istanbul
        ist = prices.get("istanbul")
        if ist and not ist.get("is_calculated"):
            gold = ist.get("gold_try_gram_buy") or ist.get("gold_try_gram")
            silver = ist.get("silver_try_gram")
            usd_oz = (gold / fx.get("TRY",1)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency) VALUES (?,?,?,?,?,?,?)",
                (ts, 'istanbul', usd_oz, gold, silver, calc_premium(usd_oz), 'TRY'))

        # India
        ind = prices.get("india")
        if ind and not ind.get("is_calculated"):
            gold = ind.get("gold_inr_gram")
            usd_oz = (gold / fx.get("INR",83)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency) VALUES (?,?,?,?,?,?,?)",
                (ts, 'india', usd_oz, gold, None, calc_premium(usd_oz), 'INR'))

        # Japan
        jpn = prices.get("japan")
        if jpn and not jpn.get("is_calculated"):
            gold = jpn.get("gold_jpy_gram_bid")
            usd_oz = (gold / fx.get("JPY",150)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency) VALUES (?,?,?,?,?,?,?)",
                (ts, 'japan', usd_oz, gold, None, calc_premium(usd_oz), 'JPY'))

        # Germany
        deu = prices.get("germany")
        if deu and not deu.get("is_calculated"):
            gold = deu.get("gold_eur_oz_bid")
            usd_oz = (gold / fx.get("EUR",0.92)) if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency) VALUES (?,?,?,?,?,?,?)",
                (ts, 'germany', usd_oz, gold, None, calc_premium(usd_oz), 'EUR'))

        # Dubai
        dub = prices.get("dubai")
        if dub and not dub.get("is_calculated"):
            gold = dub.get("gold_aed_gram")
            silver = dub.get("silver_aed_kg")
            usd_oz = (gold / fx.get("AED",3.67)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency) VALUES (?,?,?,?,?,?,?)",
                (ts, 'dubai', usd_oz, gold, silver, calc_premium(usd_oz), 'AED'))

        # Russia
        rus = prices.get("russia")
        if rus and not rus.get("is_calculated"):
            gold = rus.get("gold_rub_gram")
            silver = rus.get("silver_rub_gram")
            usd_oz = (gold / fx.get("RUB",90)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency) VALUES (?,?,?,?,?,?,?)",
                (ts, 'russia', usd_oz, gold, silver, calc_premium(usd_oz), 'RUB'))

        conn.commit()
        conn.close()
        print(f"DB: saved prices at {ts}")
    except Exception as e:
        print(f"DB save error: {e}")

def fetch_fx():
    try:
        r = requests.get("https://api.frankfurter.app/latest?base=USD&symbols=EUR,GBP,TRY,CNY,INR,JPY,HKD,AED,RUB", timeout=10).json()
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

        # Silver from Kitco
        silver_bid = None
        silver_ask = None
        silver_ch = None
        silver_chp = None
        try:
            with sync_playwright() as p2:
                browser2 = p2.chromium.launch(headless=True)
                page2 = browser2.new_page()
                page2.goto("https://www.kitco.com/silver-price-today-usa/", timeout=30000)
                page2.wait_for_timeout(5000)
                content2 = page2.inner_text("body")
                browser2.close()
            lines2 = [l.strip() for l in content2.split("\n") if l.strip()]
            for i, line in enumerate(lines2):
                if line == "Bid" and i+1 < len(lines2):
                    val = parse_num(lines2[i+1])
                    if val and 0 < val < 500 and silver_bid is None:
                        silver_bid = val
                        if i+3 < len(lines2): silver_ch = parse_num(lines2[i+3])
                        if i+4 < len(lines2):
                            m2 = re.search(r'[-+]?\d+\.?\d*', lines2[i+4].replace(",",""))
                            if m2: silver_chp = float(m2.group())
                if line == "Ask" and i+1 < len(lines2):
                    val = parse_num(lines2[i+1])
                    if val and 0 < val < 500 and silver_ask is None:
                        silver_ask = val
            print(f"Kitco Silver: bid={silver_bid} ask={silver_ask} chp={silver_chp}")
        except Exception as e:
            print(f"Kitco silver error: {e}")

        if gold_bid and gold_ask:
            return {
                "XAU": round((gold_bid + gold_ask) / 2, 2),
                "XAU_bid": gold_bid,
                "XAU_ask": gold_ask,
                "XAU_ch": gold_ch,
                "XAU_chp": gold_chp,
                "XAG": round((silver_bid + silver_ask) / 2, 2) if silver_bid and silver_ask else None,
                "XAG_bid": silver_bid,
                "XAG_ask": silver_ask,
                "XAG_ch": silver_ch,
                "XAG_chp": silver_chp,
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

def fetch_india():
    try:
        r = requests.get("https://ibjarates.com/", timeout=10)
        import re as re2
        m = re2.search(r'GoldRatesCompare999[^>]*>([0-9,]+)<', r.text)
        if m:
            gold_inr_gram = float(m.group(1).replace(",", ""))
            print(f"IBJA: gold={gold_inr_gram} INR/gram")
            return {
                "gold_inr_gram": gold_inr_gram,
                "source": "ibjarates.com",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"IBJA error: {e}")
        return None

def fetch_japan():
    try:
        PROXY = "http://emvapyle:j29crz2fwh2i@142.111.67.146:5611"
        r = requests.get(
            "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
            headers={"User-Agent": "Mozilla/5.0"},
            proxies={"http": PROXY, "https": PROXY},
            timeout=15
        )
        import re as re2
        # Extract selling price (ask) and buying price (bid) for gold
        ask_m = re2.search(r'class="retail_tax">([\d,]+) yen</td>', r.text)
        bid_m = re2.search(r'class="purchase_tax">([\d,]+) yen</td>', r.text)
        if ask_m and bid_m:
            ask = float(ask_m.group(1).replace(",", ""))
            bid = float(bid_m.group(1).replace(",", ""))
            print(f"Tanaka: bid={bid} ask={ask} JPY/gram")
            return {
                "gold_jpy_gram_bid": bid,
                "gold_jpy_gram_ask": ask,
                "source": "gold.tanaka.co.jp",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Japan error: {e}")
        return None

def fetch_russia():
    try:
        r = requests.get(
            "https://www.cbr.ru/eng/hd_base/metall/metall_base_new/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        import re as re2
        # Extract last value from Gold data array
        gold_m = re2.search(r'"name":"Gold".*?"data":\[([^\]]+)\]', r.text)
        silver_m = re2.search(r'"name":"Silver".*?"data":\[([^\]]+)\]', r.text)
        if gold_m:
            gold_vals = [float(x) for x in gold_m.group(1).split(',') if x.strip()]
            silver_vals = [float(x) for x in silver_m.group(1).split(',') if x.strip()] if silver_m else []
            gold_rub_gram = gold_vals[-1] if gold_vals else None
            silver_rub_gram = silver_vals[-1] if silver_vals else None
            print(f"CBR Russia: gold={gold_rub_gram} silver={silver_rub_gram} RUB/gram")
            return {
                "gold_rub_gram": gold_rub_gram,
                "silver_rub_gram": silver_rub_gram,
                "source": "cbr.ru",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Russia error: {e}")
        return None

def fetch_dubai():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://tora.bullionview.com/", timeout=30000)
            page.wait_for_timeout(6000)
            content = page.inner_text("body")
            browser.close()

        lines = [l.strip() for l in content.split("\n") if l.strip()]
        gold_aed_gram = None
        silver_aed_kg = None
        gold_usd_bid = None
        gold_usd_ask = None
        silver_usd_bid = None
        silver_usd_ask = None

        for i, line in enumerate(lines):
            # GOLD SPOT bid/ask in USD
            if line == 'GOLD SPOT':
                # Next lines: BID, ASK, TODAY, bid_val, ask_val
                vals = []
                for j in range(i+1, min(i+8, len(lines))):
                    try:
                        val = float(lines[j].replace(',',''))
                        if 1000 < val < 10000:
                            vals.append(val)
                    except:
                        pass
                if len(vals) >= 2:
                    gold_usd_bid = vals[0]
                    gold_usd_ask = vals[1]

            # SILVER SPOT bid/ask in USD
            if line == 'SILVER SPOT':
                vals = []
                for j in range(i+1, min(i+8, len(lines))):
                    try:
                        val = float(lines[j].replace(',',''))
                        if 10 < val < 500:
                            vals.append(val)
                    except:
                        pass
                if len(vals) >= 2:
                    silver_usd_bid = vals[0]
                    silver_usd_ask = vals[1]

            # GOLD 9999 AED/gram ask price
            if 'GOLD' in line and '9999' in line:
                for j in range(i+1, min(i+5, len(lines))):
                    try:
                        val = float(lines[j].replace(',',''))
                        if 400 < val < 2000:
                            gold_aed_gram = val
                            break
                    except:
                        pass

            # SILVER AED/kg
            if line == 'SILVER' and gold_aed_gram and not silver_aed_kg:
                for j in range(i+1, min(i+5, len(lines))):
                    try:
                        val = float(lines[j].replace(',',''))
                        if 5000 < val < 20000:
                            silver_aed_kg = val
                            break
                    except:
                        pass

        if gold_aed_gram or gold_usd_bid:
            print(f"Dubai: gold_aed={gold_aed_gram} bid={gold_usd_bid} ask={gold_usd_ask} silver_bid={silver_usd_bid}")
            return {
                "gold_aed_gram": gold_aed_gram,
                "silver_aed_kg": silver_aed_kg,
                "gold_usd_bid": gold_usd_bid,
                "gold_usd_ask": gold_usd_ask,
                "silver_usd_bid": silver_usd_bid,
                "silver_usd_ask": silver_usd_ask,
                "source": "tora.bullionview.com",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Dubai error: {e}")
        return None

def fetch_germany():
    try:
        r = requests.get(
            "https://degussa.com/de/header_navigation/preise/referenzpreise/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        import re as re2
        # Find 1oz Degussa Goldbarren (geprägt) - standard bar
        idx = r.text.find('1 oz Degussa Goldbarren (geprägt)</div>')
        if idx < 0:
            idx = r.text.find('1 oz Degussa Goldbarren')
        if idx < 0:
            return None
        snippet = r.text[idx:idx+400]
        buy_m = re2.search(r'referenceListBuy">([\d\.]+,\d{2})\s*€', snippet)
        sell_m = re2.search(r'referenceListSell">([\d\.]+,\d{2})\s*€', snippet)
        if buy_m and sell_m:
            bid = float(buy_m.group(1).replace('.','').replace(',','.'))
            ask = float(sell_m.group(1).replace('.','').replace(',','.'))
            print(f"Degussa: bid={bid} ask={ask} EUR/oz")
            return {
                "gold_eur_oz_bid": bid,
                "gold_eur_oz_ask": ask,
                "source": "degussa.com",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Germany error: {e}")
        return None

def fetch_hongkong():
    try:
        from html.parser import HTMLParser
        class TDParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_td = False
                self.data = []
            def handle_starttag(self, tag, attrs):
                if tag == 'td': self.in_td = True
            def handle_endtag(self, tag):
                if tag == 'td': self.in_td = False
            def handle_data(self, data):
                if self.in_td and data.strip() and data.strip() != '\xa0':
                    self.data.append(data.strip())

        r = requests.get("https://cgse.com.hk/chines/en/latest-quotes",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        p = TDParser()
        p.feed(r.text)
        cells = p.data

        if cells and cells[0] == '99 Tael Gold':
            ask = float(cells[1].replace(",", ""))
            bid = float(cells[2].replace(",", ""))
            if bid > 1000 and ask > 1000:
                print(f"CGSE: bid={bid} ask={ask} HKD/tael")
                return {
                    "gold_hkd_tael_bid": bid,
                    "gold_hkd_tael_ask": ask,
                    "source": "cgse.com.hk",
                    "is_calculated": False
                }
        return None
    except Exception as e:
        print(f"HK error: {e}")
        return None

def update():
    print(f"[{datetime.now()}] Updating prices...")
    fx = fetch_fx()
    spot = fetch_spot()
    if not spot or not spot.get("XAU"):
        print("Kitco failed, using fallback...")
        spot = fetch_spot_fallback()
    ist = fetch_istanbul()
    india = fetch_india()
    hk = fetch_hongkong()
    japan = fetch_japan()
    germany = fetch_germany()
    russia = fetch_russia()
    dubai = fetch_dubai()

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

    if india:
        prices["india"] = india
    elif spot and fx:
        prices["india"] = {
            "gold_inr_gram": round((spot["XAU"] / GRAM) * fx["INR"] * 1.13, 2),
            "source": "calculated",
            "is_calculated": True
        }

    if hk:
        prices["hongkong"] = hk
    elif spot and fx:
        TAEL = 37.429
        prices["hongkong"] = {
            "gold_hkd_tael_bid": round((spot["XAU"] / GRAM) * TAEL * fx["HKD"], 2) if fx.get("HKD") else None,
            "gold_hkd_tael_ask": round((spot["XAU"] / GRAM) * TAEL * fx["HKD"], 2) if fx.get("HKD") else None,
            "source": "calculated",
            "is_calculated": True
        }

    if japan:
        prices["japan"] = japan
    elif spot and fx:
        prices["japan"] = {
            "gold_jpy_gram_bid": round((spot["XAU"] / GRAM) * fx["JPY"] * 1.005, 2) if fx.get("JPY") else None,
            "gold_jpy_gram_ask": round((spot["XAU"] / GRAM) * fx["JPY"] * 1.005, 2) if fx.get("JPY") else None,
            "source": "calculated",
            "is_calculated": True
        }

    if germany:
        prices["germany"] = germany
    elif spot and fx:
        prices["germany"] = {
            "gold_eur_oz_bid": round(spot["XAU"] * fx.get("EUR", 0.92) * 1.005, 2),
            "gold_eur_oz_ask": round(spot["XAU"] * fx.get("EUR", 0.92) * 1.02, 2),
            "source": "calculated",
            "is_calculated": True
        }

    if russia:
        prices["russia"] = russia
    elif spot and fx:
        prices["russia"] = {
            "gold_rub_gram": round((spot["XAU"] / GRAM) * fx.get("RUB", 90), 2) if fx.get("RUB") else None,
            "silver_rub_gram": round((spot["XAG"] / GRAM) * fx.get("RUB", 90), 2) if fx.get("RUB") and spot.get("XAG") else None,
            "source": "calculated",
            "is_calculated": True
        }

    if dubai:
        prices["dubai"] = dubai
    elif spot and fx:
        prices["dubai"] = {
            "gold_aed_gram": round((spot["XAU"] / GRAM) * fx.get("AED", 3.67), 2) if fx.get("AED") else None,
            "silver_aed_kg": round((spot["XAG"] / GRAM) * 1000 * fx.get("AED", 3.67), 2) if fx.get("AED") and spot.get("XAG") else None,
            "source": "calculated",
            "is_calculated": True
        }

    cache["prices"] = prices
    cache["last_updated"] = datetime.now().isoformat()

    # Store yesterday's closing price at end of day (UTC 22:00 = approx NY close)
    now = datetime.now()
    if now.hour == 22 and now.minute < 10:
        cache["yesterday"] = {
            "XAU": prices.get("spot", {}).get("XAU"),
            "XAG": prices.get("spot", {}).get("XAG"),
            "EUR": prices.get("fx", {}).get("EUR"),
        }
        print(f"Saved yesterday's close: {cache['yesterday']}")

    # Calculate EUR chp if we have yesterday's data
    if cache["yesterday"].get("XAU") and prices.get("spot") and prices.get("fx"):
        spot = prices["spot"]
        fx = prices["fx"]
        yest = cache["yesterday"]
        if yest.get("EUR") and fx.get("EUR"):
            xau_eur_today = spot["XAU"] * fx["EUR"]
            xau_eur_yest = yest["XAU"] * yest["EUR"]
            if xau_eur_yest:
                prices["spot"]["XAU_chp_eur"] = round((xau_eur_today - xau_eur_yest) / xau_eur_yest * 100, 2)
            if yest.get("XAG") and spot.get("XAG"):
                xag_eur_today = spot["XAG"] * fx["EUR"]
                xag_eur_yest = yest["XAG"] * yest["EUR"]
                if xag_eur_yest:
                    prices["spot"]["XAG_chp_eur"] = round((xag_eur_today - xag_eur_yest) / xag_eur_yest * 100, 2)

    print(f"[{datetime.now()}] Done: {list(prices.keys())}")
    save_prices(prices)

def background():
    while True:
        update()
        time.sleep(10 * 60)

@app.route("/api/prices")
def get_prices():
    if not cache["prices"]:
        update()
    return jsonify({"data": cache["prices"], "last_updated": cache["last_updated"], "status": "ok"})

@app.route("/api/history/<market>")
def get_history(market):
    try:
        hours = int(request.args.get('hours', 24))
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT ts, gold_usd_oz, gold_local, silver_local, premium_pct, local_currency
            FROM price_history
            WHERE market = ? AND ts >= datetime('now', ? || ' hours')
            ORDER BY ts ASC
        """, (market, f'-{hours}'))
        rows = c.fetchall()
        conn.close()
        data = [{"ts": r[0], "gold_usd_oz": r[1], "gold_local": r[2],
                 "silver_local": r[3], "premium_pct": r[4], "currency": r[5]} for r in rows]
        return jsonify({"market": market, "data": data, "count": len(data)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok", "last_updated": cache["last_updated"]})

@app.route("/")
def index():
    return jsonify({"name": "goldpremium API", "last_updated": cache["last_updated"]})

if __name__ == "__main__":
    init_db()
    update()
    t = threading.Thread(target=background, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000)
