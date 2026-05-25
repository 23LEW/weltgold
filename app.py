from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import time
from datetime import datetime
import threading
import re
import sqlite3
import os
import json
import smtplib
from email.mime.text import MIMEText
from augmont_scraper import fetch_india_augmont
from royalmint_scraper import fetch_uk_royalmint

app = Flask(__name__)
CORS(app)

cache = {"prices": {}, "last_updated": None, "yesterday": {}, "last_gc": None, "last_si": None, "lbma_ratio": None, "lbma_ratio_ts": 0, "alerts_sent": set()}
YESTERDAY_FX_FILE = '/opt/goldpremium/yesterday_fx.json'

ALERT_EMAIL = "larsenwolff@posteo.de"
SMTP_HOST = "posteo.de"
SMTP_PORT = 587

def _smtp_pass():
    p = os.environ.get("SMTP_PASS", "")
    if not p:
        try:
            with open("/opt/goldpremium/.smtp_pass") as f:
                p = f.read().strip()
        except Exception:
            pass
    return p

def send_alert_email(subject, body):
    pw = _smtp_pass()
    if not pw:
        print(f"[ALERT] No SMTP password — skipping: {subject}")
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = ALERT_EMAIL
        msg["To"] = ALERT_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(ALERT_EMAIL, pw)
            s.send_message(msg)
        print(f"[ALERT] Email sent: {subject}")
    except Exception as e:
        print(f"[ALERT] Email failed: {e}")

def check_price_alerts():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT market,
                (SELECT gold_usd_oz FROM price_history p2
                 WHERE p2.market=p.market AND p2.ts>=datetime('now','-24 hours')
                 AND p2.gold_usd_oz IS NOT NULL ORDER BY p2.ts ASC LIMIT 1) AS first_g,
                (SELECT gold_usd_oz FROM price_history p2
                 WHERE p2.market=p.market AND p2.ts>=datetime('now','-24 hours')
                 AND p2.gold_usd_oz IS NOT NULL ORDER BY p2.ts DESC LIMIT 1) AS last_g,
                (SELECT silver_usd_oz FROM price_history p2
                 WHERE p2.market=p.market AND p2.ts>=datetime('now','-24 hours')
                 AND p2.silver_usd_oz IS NOT NULL ORDER BY p2.ts ASC LIMIT 1) AS first_s,
                (SELECT silver_usd_oz FROM price_history p2
                 WHERE p2.market=p.market AND p2.ts>=datetime('now','-24 hours')
                 AND p2.silver_usd_oz IS NOT NULL ORDER BY p2.ts DESC LIMIT 1) AS last_s
            FROM price_history p
            WHERE ts>=datetime('now','-24 hours')
            GROUP BY market HAVING COUNT(*)>=3
        """)
        rows = c.fetchall()
        conn.close()
        today = datetime.utcnow().strftime('%Y-%m-%d')
        # Clear stale alert keys from previous days
        cache["alerts_sent"] = {k for k in cache["alerts_sent"] if k[2] == today}
        alerts = []
        for market, fg, lg, fs, ls in rows:
            if fg and lg and fg > 0:
                pct = (lg - fg) / fg * 100
                key = (market, 'gold', today)
                if abs(pct) >= 25 and key not in cache["alerts_sent"]:
                    alerts.append(f"GOLD  {market:12s}  {fg:,.2f} → {lg:,.2f} USD/oz  ({pct:+.1f}%)")
                    cache["alerts_sent"].add(key)
            if fs and ls and fs > 0:
                pct = (ls - fs) / fs * 100
                key = (market, 'silver', today)
                if abs(pct) >= 25 and key not in cache["alerts_sent"]:
                    alerts.append(f"SILVER {market:12s}  {fs:,.4f} → {ls:,.4f} USD/oz  ({pct:+.1f}%)")
                    cache["alerts_sent"].add(key)
        if alerts:
            body = (
                "GoldPremium.org — Price Alert\n"
                "══════════════════════════════\n\n"
                "The following markets moved more than 25% in the last 24 hours:\n\n"
                + "\n".join(f"  • {a}" for a in alerts)
                + f"\n\nChecked: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                + "\nhttps://goldpremium.org"
            )
            send_alert_email(f"⚠️ GoldPremium Alert: {len(alerts)} market(s) moved >25%", body)
    except Exception as e:
        print(f"[ALERT] check_price_alerts failed: {e}")
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
        silver_usd_oz REAL,
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

        # COMEX Futures Front Month (GC/SI from stooq/CME)
        gc = prices.get("spot", {}).get("GC") or spot_xau
        si = prices.get("spot", {}).get("SI") or spot_xag
        basis_pct = round((gc - spot_xau) / spot_xau * 100, 4) if spot_xau and gc else 0.0
        c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, 'comex', gc, gc, si, si, basis_pct, 'USD', 'oz', 'oz'))

        # Istanbul
        ist = prices.get("istanbul")
        if ist and not ist.get("is_calculated"):
            gold = ist.get("gold_try_gram_buy") or ist.get("gold_try_gram")
            silver = ist.get("silver_try_gram")
            silver_bid = ist.get("silver_try_gram_buy")
            usd_oz = (gold / fx.get("TRY",1)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit,silver_local_bid) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, 'istanbul', usd_oz, gold, silver, calc_premium(usd_oz), 'TRY', 'gram', 'gram', silver_bid))

        # India GJC
        gjc = prices.get("india_gjc")
        if gjc and not gjc.get("is_calculated"):
            gold_ask = gjc.get("gold_inr_gram_ask")
            gold_bid = gjc.get("gold_inr_gram_bid")
            silv_ask = gjc.get("silver_inr_kg_ask")
            silv_bid = gjc.get("silver_inr_kg_bid")
            usd_oz_ask = (gold_ask / fx.get("INR", 83)) * GRAM if gold_ask else None
            usd_oz_bid = (gold_bid / fx.get("INR", 83)) * GRAM if gold_bid else None
            silver_usd = (silv_ask / 32.1507 / fx.get("INR", 83)) if silv_ask else None
            c.execute(
                "INSERT INTO price_history "
                "(ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,"
                "premium_pct,local_currency,gold_local_unit,silver_local_unit,"
                "bid_usd_oz,bid_premium_pct,gold_local_bid,silver_local_bid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, 'india_gjc',
                 usd_oz_ask, gold_ask, silv_ask, silver_usd,
                 calc_premium(usd_oz_ask), 'INR', 'gram', 'kg',
                 usd_oz_bid, calc_premium(usd_oz_bid),
                 gold_bid, silv_bid)
            )

        # India Mumbai (Augmont) — mit Bid/Ask
        indm = prices.get("india_mumbai")
        if indm and not indm.get("is_calculated"):
            gold_ask  = indm.get("gold_inr_10g_ask")
            gold_bid  = indm.get("gold_inr_10g_bid")
            silv_ask  = indm.get("silver_inr_kg_ask")
            silv_bid  = indm.get("silver_inr_kg_bid")
            usd_oz_ask = indm.get("gold_usd_oz_ask")
            usd_oz_bid = indm.get("gold_usd_oz_bid")
            silv_usd_oz_ask = indm.get("silver_usd_oz_ask")
            c.execute(
                "INSERT INTO price_history "
                "(ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,"
                "premium_pct,local_currency,gold_local_unit,silver_local_unit,"
                "bid_usd_oz,bid_premium_pct,gold_local_bid,silver_local_bid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, 'india_mumbai',
                 usd_oz_ask, gold_ask, silv_ask, silv_usd_oz_ask,
                 calc_premium(usd_oz_ask), 'INR', '10g', 'kg',
                 usd_oz_bid, calc_premium(usd_oz_bid),
                 gold_bid, silv_bid))

        # Japan
        jpn = prices.get("japan")
        if jpn and not jpn.get("is_calculated"):
            gold = jpn.get("gold_jpy_gram_bid")
            usd_oz = (gold / fx.get("JPY",150)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, 'japan', usd_oz, gold, None, calc_premium(usd_oz), 'JPY', 'gram', None))

        # Switzerland Philoro
        swi = prices.get("switzerland")
        if swi and not swi.get("is_calculated"):
            gold = swi.get("gold_chf_oz_ask")
            usd_oz = (gold / fx.get("CHF", 0.89)) if gold else None
            silver_kg = swi.get("silver_chf_kg_ask")
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, 'switzerland', usd_oz, gold, silver_kg, calc_premium(usd_oz), 'CHF', 'oz', 'kg'))

        # Germany
        deu = prices.get("germany")
        if deu and not deu.get("is_calculated"):
            gold = deu.get("gold_eur_oz_ask")
            usd_oz = (gold / fx.get("EUR",0.92)) if gold else None
            silver_kg = deu.get("silver_eur_kg_ask")
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, 'germany', usd_oz, gold, silver_kg, calc_premium(usd_oz), 'EUR', 'oz', 'kg'))

        # Dubai
        dub = prices.get("dubai")
        if dub and not dub.get("is_calculated"):
            gold_ask = dub.get("gold_eur_kg_ask")
            gold_bid  = dub.get("gold_eur_kg_bid")
            silver = dub.get("silver_eur_kg_ask")
            silver_bid = dub.get("silver_eur_kg_bid")
            eur_rate = fx.get("EUR", 0.85)
            usd_oz_ask = (gold_ask / 32.1507 / eur_rate) if gold_ask else None
            usd_oz_bid = (gold_bid  / 32.1507 / eur_rate) if gold_bid  else None
            silver_usd = (silver / 32.1507 / eur_rate) if silver else None
            c.execute(
                "INSERT INTO price_history "
                "(ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,"
                "premium_pct,local_currency,gold_local_unit,silver_local_unit,"
                "bid_usd_oz,bid_premium_pct,gold_local_bid,silver_local_bid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, 'dubai',
                 usd_oz_ask, gold_ask, silver, silver_usd,
                 calc_premium(usd_oz_ask), 'EUR', 'kg', 'kg',
                 usd_oz_bid, calc_premium(usd_oz_bid),
                 gold_bid, silver_bid)
            )

        # Russia
        rus = prices.get("russia")
        if rus and not rus.get("is_calculated"):
            gold = rus.get("gold_rub_gram")
            silver = rus.get("silver_rub_gram")
            usd_oz = (gold / fx.get("RUB",90)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, 'russia', usd_oz, gold, silver, calc_premium(usd_oz), 'RUB', 'gram', 'gram'))

        # Hong Kong Hang Seng
        hk = prices.get("hongkong")
        if hk and not hk.get("is_calculated"):
            gold = hk.get("gold_hkd_oz_ask") or hk.get("gold_hkd_oz_bid")
            gold_bid = hk.get("gold_hkd_oz_bid")
            usd_oz = (gold / fx.get("HKD",7.83)) if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit,gold_local_bid) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, 'hongkong', usd_oz, gold, None, calc_premium(usd_oz), 'HKD', 'oz', None, gold_bid))

        # Russia Dealer
        rd = prices.get("russia_dealer")
        if rd and not rd.get("is_calculated"):
            gold = rd.get("gold_rub_gram")
            silver = rd.get("silver_rub_gram")
            usd_oz = (gold / fx.get("RUB",90)) * GRAM if gold else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, 'russia_dealer', usd_oz, gold, silver, calc_premium(usd_oz), 'RUB', 'gram', 'gram'))

        # LBMA
        lbma = prices.get("lbma")
        if lbma and not lbma.get("is_calculated"):
            gold = lbma.get("gold_usd_oz")
            silver = lbma.get("silver_usd_oz")
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, 'lbma', gold, gold, silver, silver, calc_premium(gold), 'USD', 'oz', 'oz'))

        # HKGX
        hkgx = prices.get("hkgx")
        if hkgx and not hkgx.get("is_calculated"):
            gold = hkgx.get("gold_usd_oz_bid") or hkgx.get("gold_usd_oz_ask")
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, 'hkgx', gold, gold, None, calc_premium(gold), 'USD', 'oz', None))

        # Shanghai SGE
        sge = prices.get("shanghai")
        if sge and not sge.get("is_calculated"):
            gold_cny_gram = sge.get("gold_cny_gram")
            silver_cny_kg = sge.get("silver_cny_kg")
            usd_oz = (gold_cny_gram / fx.get("CNY",7.1)) * GRAM if gold_cny_gram else None
            silver_usd = (silver_cny_kg / 32.1507 / fx.get("CNY", 7.1)) if silver_cny_kg else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, 'shanghai', usd_oz, gold_cny_gram, silver_cny_kg, silver_usd, calc_premium(usd_oz), 'CNY', 'gram', 'kg'))

        # China Gold (中国黄金) — Investment-Barren mit Bid/Ask (CNY/gram)
        cg = prices.get("chinagold")
        if cg and not cg.get("is_calculated"):
            gold_ask = cg.get("gold_cny_gram_ask")
            gold_bid = cg.get("gold_cny_gram_bid")
            usd_oz_ask = (gold_ask / fx.get("CNY", 7.1)) * GRAM if gold_ask else None
            usd_oz_bid = (gold_bid / fx.get("CNY", 7.1)) * GRAM if gold_bid else None
            c.execute(
                "INSERT INTO price_history "
                "(ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,"
                "premium_pct,local_currency,gold_local_unit,silver_local_unit,"
                "bid_usd_oz,bid_premium_pct,gold_local_bid,silver_local_bid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, 'chinagold',
                 usd_oz_ask, gold_ask, None, None,
                 calc_premium(usd_oz_ask), 'CNY', 'gram', None,
                 usd_oz_bid, calc_premium(usd_oz_bid),
                 gold_bid, None))

        # Australia Perth Mint
        au = prices.get("australia")
        if au and not au.get("is_calculated"):
            gold_ask = au.get("gold_aud_kg_ask")
            gold_bid = au.get("gold_aud_kg_bid")
            silver = au.get("silver_aud_kg")
            aud_rate = fx.get("AUD", 1.55)
            usd_oz_ask = (gold_ask / 32.1507 / aud_rate) if gold_ask else None
            usd_oz_bid = (gold_bid / 32.1507 / aud_rate) if gold_bid else None
            silver_usd = (silver / 32.1507 / aud_rate) if silver else None
            c.execute(
                "INSERT INTO price_history "
                "(ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,"
                "premium_pct,local_currency,gold_local_unit,silver_local_unit,"
                "bid_usd_oz,bid_premium_pct,gold_local_bid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, 'australia',
                 usd_oz_ask, gold_ask, silver, silver_usd,
                 calc_premium(usd_oz_ask), 'AUD', 'kg', 'kg',
                 usd_oz_bid, calc_premium(usd_oz_bid),
                 gold_bid)
            )

        # USA BGASC
        usa = prices.get("usa")
        if usa and not usa.get("is_calculated"):
            gold = usa.get("gold_usd_oz_ask")
            silver = usa.get("silver_usd_kg_ask")  # USD/kg
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, 'usa', gold, gold, silver, calc_premium(gold), 'USD', 'kg', 'kg'))

        # Canada CB Metals
        ca = prices.get("canada")
        if ca and not ca.get("is_calculated"):
            gold_cad_kg = ca.get("gold_cad_kg_ask")
            gold = (gold_cad_kg / 32.1507) / fx.get("CAD", 1.36) if gold_cad_kg else None
            silver_cad_kg = ca.get("silver_cad_kg_ask")
            silver = (silver_cad_kg / 32.1507) / fx.get("CAD", 1.36) if silver_cad_kg else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, 'canada', gold, gold_cad_kg, silver_cad_kg, calc_premium(gold), 'CAD', 'kg', 'kg'))

        # UK Royal Mint
        ukrm = prices.get("uk_royalmint")
        if ukrm and not ukrm.get("is_calculated"):
            gold_ask   = ukrm.get("gold_gbp_oz_ask")
            gold_bid   = ukrm.get("gold_gbp_oz_bid")
            silv_ask   = ukrm.get("silver_gbp_oz_ask")
            silv_bid   = ukrm.get("silver_gbp_oz_bid")
            gbp_rate   = fx.get("GBP", 0.79)
            usd_oz_ask = round(gold_ask / gbp_rate, 4) if gold_ask and gbp_rate else None
            usd_oz_bid = round(gold_bid / gbp_rate, 4) if gold_bid and gbp_rate else None
            silv_usd   = round(silv_ask / gbp_rate, 4) if silv_ask and gbp_rate else None
            c.execute(
                "INSERT INTO price_history "
                "(ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,"
                "premium_pct,local_currency,gold_local_unit,silver_local_unit,"
                "bid_usd_oz,bid_premium_pct,gold_local_bid,silver_local_bid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, 'uk_royalmint',
                 usd_oz_ask, gold_ask, silv_ask, silv_usd,
                 calc_premium(usd_oz_ask), 'GBP', 'oz', 'oz',
                 usd_oz_bid, calc_premium(usd_oz_bid),
                 gold_bid, silv_bid))

        # India IBJA
        ibja = prices.get("india")
        if ibja and not ibja.get("is_calculated"):
            gold = ibja.get("gold_inr_gram")
            silver = ibja.get("silver_inr_kg")
            usd_oz = (gold / fx.get("INR", 83)) * GRAM if gold else None
            silver_usd = (silver / 32.1507 / fx.get("INR", 83)) if silver else None
            c.execute("INSERT INTO price_history (ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,premium_pct,local_currency,gold_local_unit,silver_local_unit) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, 'india', usd_oz, gold, silver, silver_usd, calc_premium(usd_oz), 'INR', 'gram', 'kg'))

        conn.commit()
        conn.close()
        print(f"DB: saved prices at {ts}")
    except Exception as e:
        print(f"DB save error: {e}")

def _prev_frankfurter_day(date_str):
    from datetime import date as _d, timedelta as _td
    d = _d.fromisoformat(date_str) - _td(days=1)
    while d.weekday() >= 5:
        d -= _td(days=1)
    return d.isoformat()

def fetch_fx():
    """Returns (today_rates, yest_rates).
    Always fetches two different Frankfurter trading days — handles weekends,
    public holidays, and early-morning Mondays automatically."""
    try:
        SYM = "EUR,GBP,TRY,CNY,INR,JPY,HKD,AED,AUD,CAD,CHF"
        r = requests.get(
            f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={SYM}",
            timeout=(5, 10)).json()
        fx_date = r.get("date")
        rates = r.get("rates", {})
        rates["USD"] = 1.0

        # RUB von CBR holen (Frankfurter hat kein RUB seit 2022)
        try:
            import re as re_rub
            r_cbr = requests.get(
                "https://www.cbr.ru/scripts/XML_daily.asp",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 10))
            usd_rub = re_rub.search(r"USD.*?<Value>([0-9,]+)<", r_cbr.text, re_rub.DOTALL)
            if usd_rub:
                rub_rate = float(usd_rub.group(1).replace(",", "."))
                rates["RUB"] = rub_rate
                print(f"CBR RUB/USD: {rub_rate}")
        except Exception as rub_e:
            print(f"RUB fetch error: {rub_e}")

        # Fetch previous Frankfurter trading day (always different)
        yest_rates = {}
        if fx_date:
            prev_date = _prev_frankfurter_day(fx_date)
            try:
                r2 = requests.get(
                    f"https://api.frankfurter.dev/v1/{prev_date}?base=USD&symbols={SYM}",
                    timeout=(5, 10)).json()
                yest_rates = r2.get("rates", {})
                yest_rates["USD"] = 1.0
                print(f"FX: latest={fx_date} prev={prev_date}  "
                      f"EUR today={rates.get('EUR')} EUR yest={yest_rates.get('EUR')}")
            except Exception as e2:
                print(f"FX prev-day error: {e2}")

        return rates, yest_rates
    except Exception as e:
        print(f"FX error: {e}")
        return None, {}


def fetch_fx_yahoo():
    """Fetch live FX rates from Yahoo Finance v8 chart API.
    Returns rates as {EUR: 0.85, JPY: 156.7, ...} — same format as Frankfurter.
    Updates continuously (not once per day like ECB/Frankfurter).
    Used for per-currency chp to match Kitco's live reference period."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    SYMBOLS = {
        'EUR': ('EURUSD=X', True),   # invert: Yahoo gives USD per EUR
        'GBP': ('GBPUSD=X', True),
        'JPY': ('USDJPY=X', False),  # direct: USD per JPY
        'CNY': ('USDCNY=X', False),
        'TRY': ('USDTRY=X', False),
        'INR': ('USDINR=X', False),
        'AUD': ('AUDUSD=X', True),
        'HKD': ('USDHKD=X', False),
        'CHF': ('USDCHF=X', False),
    }
    UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

    def _fetch_one(sym):
        r = requests.get(
            f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d',
            headers={'User-Agent': UA}, timeout=(5, 10)
        ).json()
        meta = r.get('chart', {}).get('result', [{}])[0].get('meta', {})
        return meta.get('regularMarketPrice')

    try:
        rates = {'USD': 1.0}
        with ThreadPoolExecutor(max_workers=9) as ex:
            futures = {ex.submit(_fetch_one, sym): (cur, invert)
                       for cur, (sym, invert) in SYMBOLS.items()}
            for fut in as_completed(futures, timeout=15):
                cur, invert = futures[fut]
                try:
                    price = fut.result()
                    if price and price > 0:
                        rates[cur] = round(1.0 / price, 6) if invert else round(price, 6)
                except Exception as fe:
                    print(f"Yahoo FX {cur} error: {fe}")
        if rates.get('EUR'):
            print(f"Yahoo FX live: EUR={rates.get('EUR')} JPY={rates.get('JPY')} TRY={rates.get('TRY')}")
            return rates
        print("Yahoo FX: no EUR rate returned")
        return {}
    except Exception as e:
        print(f"Yahoo FX error: {e}")
        return {}

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
                            val = float(m.group())
                            if abs(val) <= 20:
                                gold_chp = val
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
                            if m2:
                                val2 = float(m2.group())
                                if abs(val2) <= 20:
                                    silver_chp = val2
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

def fetch_comex_futures():
    """COMEX front month futures: stooq.com (CME data), Yahoo Finance fallback."""
    def _stooq(symbol):
        r = requests.get(f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 10))
        parts = r.text.strip().split(',')
        # format: symbol,date,time,open,high,low,close,volume
        return float(parts[6]) if len(parts) >= 7 and parts[6] else None

    try:
        gc_price = _stooq("gc.f")
        si_raw = _stooq("si.f")
        # COMEX silver is quoted in cents/troy oz — convert to USD/oz
        si_price = round(si_raw / 100, 4) if si_raw and si_raw > 1000 else si_raw
        if gc_price and gc_price > 100:
            print(f"COMEX Futures (stooq/CME): GC={gc_price} SI={si_price}")
            return {"GC": round(gc_price, 2), "SI": si_price, "source": "cmegroup.com"}
        return None
    except Exception as e:
        print(f"COMEX stooq error: {e} — trying Yahoo Finance fallback")
    try:
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1m&range=1d",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 10))
        gc_price = r.json()['chart']['result'][0]['meta'].get('regularMarketPrice')
        r2 = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/SI=F?interval=1m&range=1d",
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 10))
        si_price = r2.json()['chart']['result'][0]['meta'].get('regularMarketPrice')
        if gc_price and gc_price > 100:
            print(f"COMEX Futures (Yahoo fallback): GC={gc_price} SI={si_price}")
            return {"GC": round(gc_price, 2), "SI": round(si_price, 4) if si_price else None, "source": "cmegroup.com"}
        return None
    except Exception as e2:
        print(f"COMEX Futures error: {e2}")
        return None

def fetch_sp500():
    """S&P 500 current index value via Yahoo Finance.
    Returns the regularMarketPrice as float, or None on error.
    Historischer Backfill liegt bereits in der Tabelle `sp500_history` (Yahoo
    range=10y/1d + range=max/1mo)."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=1d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=(5, 10)
        )
        meta = r.json()['chart']['result'][0]['meta']
        price = meta.get('regularMarketPrice')
        if price and price > 100:
            print(f"S&P 500 (Yahoo): {price}")
            return float(price)
        return None
    except Exception as e:
        print(f"S&P 500 error: {e}")
        return None

def fetch_spot_fallback():
    try:
        gold = requests.get("https://api.gold-api.com/price/XAU", timeout=(5, 10)).json()
        silver = requests.get("https://api.gold-api.com/price/XAG", timeout=(5, 10)).json()
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
        silver_buy = None
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
                # Seite liefert zwei Werte: Alis (Ankauf/bid), Satis (Verkauf/ask) - in KG/TL
                svals = []
                for j in range(i+1, min(i+6, len(lines))):
                    val_text = lines[j].strip().replace(".", "").replace(",", ".")
                    try:
                        val = float(val_text)
                        if val > 10000:
                            svals.append(val)
                            if len(svals) >= 2:
                                break
                    except:
                        pass
                if len(svals) >= 2:
                    silver_buy = round(svals[0] / 1000, 4)    # Alis = Ankauf (bid)
                    silver_sell = round(svals[1] / 1000, 4)   # Satis = Verkauf (ask)
                elif len(svals) == 1:
                    silver_sell = round(svals[0] / 1000, 4)

        if gold_buy and gold_sell:
            gold = round((gold_buy + gold_sell) / 2, 2)
            print(f"Nadir: gold buy={gold_buy} sell={gold_sell}  silver buy={silver_buy} sell={silver_sell}")
            return {
                "gold_try_gram": gold,
                "gold_try_gram_buy": gold_buy,
                "gold_try_gram_sell": gold_sell,
                "silver_try_gram": silver_sell,
                "silver_try_gram_buy": silver_buy,
                "silver_try_gram_sell": silver_sell,
                "source": "nadirdoviz.com",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"Istanbul error: {e}")
        return None

def fetch_gjc():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://www.gjc.org.in/", timeout=30000)
            page.wait_for_timeout(6000)
            content = page.inner_text("body")
            browser.close()

        lines = [l.strip() for l in content.splitlines() if l.strip()]
        gold_ask_10g = gold_bid_10g = silver_ask_kg = silver_bid_kg = None

        for i, line in enumerate(lines):
            if 'Standard Rate Selling' in line and gold_ask_10g is None:
                parts = line.split('\t')
                for part in parts[1:]:
                    try:
                        val = float(part.replace(',', '').replace('\xa0', '').strip())
                        if 100000 < val < 500000:
                            gold_ask_10g = val
                            break
                    except: pass
                if gold_ask_10g is None and i+1 < len(lines):
                    try:
                        val = float(lines[i+1].replace(',','').replace('\xa0','').strip())
                        if 100000 < val < 500000:
                            gold_ask_10g = val
                    except: pass
            elif 'Standard Rate Buying' in line and gold_bid_10g is None:
                parts = line.split('\t')
                for part in parts[1:]:
                    try:
                        val = float(part.replace(',', '').replace('\xa0', '').strip())
                        if 100000 < val < 500000:
                            gold_bid_10g = val
                            break
                    except: pass
                if gold_bid_10g is None and i+1 < len(lines):
                    try:
                        val = float(lines[i+1].replace(',','').replace('\xa0','').strip())
                        if 100000 < val < 500000:
                            gold_bid_10g = val
                    except: pass
            elif 'Silver Sale Rate' in line and silver_ask_kg is None:
                for j in range(i+1, min(i+3, len(lines))):
                    try:
                        val = float(lines[j].replace(',','').replace('\xa0','').strip())
                        if 100000 < val < 1500000:
                            silver_ask_kg = val
                            break
                    except: pass
            elif 'Silver Purchase Rate' in line and silver_bid_kg is None:
                for j in range(i+1, min(i+3, len(lines))):
                    try:
                        val = float(lines[j].replace(',','').replace('\xa0','').strip())
                        if 100000 < val < 1500000:
                            silver_bid_kg = val
                            break
                    except: pass

        if gold_ask_10g:
            gold_inr_gram_ask = round(gold_ask_10g / 10, 2)
            gold_inr_gram_bid = round(gold_bid_10g / 10, 2) if gold_bid_10g else None
            print(f"GJC: gold ask={gold_ask_10g}/10g bid={gold_bid_10g}/10g silver ask={silver_ask_kg} bid={silver_bid_kg} INR/kg")
            result = {
                "gold_inr_10g_ask": gold_ask_10g,
                "gold_inr_gram_ask": gold_inr_gram_ask,
                "source": "gjc.org.in",
                "is_calculated": False
            }
            if gold_inr_gram_bid:
                result["gold_inr_10g_bid"] = gold_bid_10g
                result["gold_inr_gram_bid"] = gold_inr_gram_bid
            if silver_ask_kg:
                result["silver_inr_kg_ask"] = silver_ask_kg
            if silver_bid_kg:
                result["silver_inr_kg_bid"] = silver_bid_kg
            return result
        return None
    except Exception as e:
        print(f"GJC error: {e}")
        return None


def fetch_india():
    try:
        import html as htmllib, json, re as re2
        r = requests.get("https://ibjarates.com/", timeout=(5, 10))
        m = re2.search(r'GoldRatesCompare999[^>]*>([0-9,]+)<', r.text)
        gold_inr_gram = float(m.group(1).replace(",", "")) if m else None
        silver_inr_kg = None
        s = re2.search(r'id="HdnSilver"[^>]*value="([^"]*)"', r.text)
        if s:
            data = json.loads(htmllib.unescape(s.group(1)))
            rates = data.get('silverRate', [])
            if rates:
                silver_inr_kg = float(rates[-1])
        if gold_inr_gram:
            return {"gold_inr_gram": gold_inr_gram, "gold_inr_gram_ask": gold_inr_gram,
                    "gold_inr_gram_bid": gold_inr_gram, "gold_inr_10g": gold_inr_gram * 10,
                    "silver_inr_kg": silver_inr_kg, "silver_inr_kg_ask": silver_inr_kg,
                    "silver_inr_kg_bid": silver_inr_kg, "source": "ibjarates.com", "is_calculated": False}
        return None
    except Exception as e:
        print(f"IBJA error: {e}")
        return None

def fetch_japan():
    try:
        r = requests.get(
            "https://gold.tanaka.co.jp/commodity/souba/english/index.php",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=(5, 15)
        )
        import re as re2
        text = r.content.decode("utf-8")
        # Extract selling price (ask) and buying price (bid) for gold
        ask_m = re2.search(r'class="retail_tax">([\d,]+) yen</td>', text)
        bid_m = re2.search(r'class="purchase_tax">([\d,]+) yen</td>', text)
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
            timeout=(5, 15)
        )
        import re as re2
        # Extract last value from Gold data array
        metals = {}
        for m2 in re2.finditer(r'"data":\[([^\]]+)\],"color":"[^"]+","id":"[^"]+","name":"(\w+)"', r.text):
            vals = [float(x) for x in m2.group(1).split(',') if x.strip().replace('.','').replace('-','').isdigit()]
            if vals: metals[m2.group(2)] = vals[-1]
        gold_rub_gram = metals.get('Gold')
        silver_rub_gram = metals.get('Silver')
        if gold_rub_gram:
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

def fetch_russia_dealer():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://shop.region-zoloto.ru/affinazh/slitki/zolotoj-mernoj-slitok-999-proby-1000-gr", timeout=30000)
            page.wait_for_timeout(5000)
            gold_content = page.inner_text("body")
            page.goto("https://shop.region-zoloto.ru/affinazh/serebro-granyli-999", timeout=30000)
            page.wait_for_timeout(5000)
            silver_content = page.inner_text("body")
            browser.close()
        lines = [l.strip() for l in gold_content.split("\n") if l.strip()]
        gold_rub_kg = None
        for line in lines:
            try:
                clean = line.replace("\xa0","").replace("\u20bd","").replace("₽","").replace(",",".").replace(" ","").strip()
                val = float(clean)
                if 5000000 < val < 30000000:
                    gold_rub_kg = val
                    break
            except:
                pass
        # Silver
        # Silver: Preis ist RUB/gram direkt (z.B. 248 ₽/gram ab 1000g)
        silver_rub_gram = None
        slines = [l.strip() for l in silver_content.split("\n") if l.strip()]
        found_1000g = False
        for i, line in enumerate(slines):
            if "от 1000 г" in line:
                found_1000g = True
                # Nächste Zeile ist der Preis
                for j in range(i+1, min(i+3, len(slines))):
                    try:
                        clean = slines[j].replace("\xa0","").replace("\u20bd","").replace("₽","").replace(",",".").replace(" ","").strip()
                        val = float(clean)
                        if 10 < val < 10000:
                            silver_rub_gram = val
                            break
                    except: pass
                break
        if gold_rub_kg:
            gold_rub_kg_net = round(gold_rub_kg / 1.22, 2)
            gold_rub_gram = round(gold_rub_kg_net / 1000, 2)
            result = {"gold_rub_kg": gold_rub_kg, "gold_rub_gram": gold_rub_gram,
                    "source": "region-zoloto.ru", "is_calculated": False}
            if silver_rub_gram:
                result["silver_rub_gram"] = silver_rub_gram
                print(f"Region Zoloto: gold={gold_rub_gram} silver={silver_rub_gram} RUB/gram")
            else:
                print(f"Region Zoloto: gold={gold_rub_gram} RUB/gram (no silver)")
            return result
        return None
    except Exception as e:
        print(f"Russia dealer error: {e}")
        return None


def fetch_dubai():
    try:
        import re as re2
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Gold bars
            page.goto("https://www.gvs-trading.ae/buy/gold-bars.html", timeout=30000)
            page.wait_for_timeout(5000)
            gold_text = page.inner_text("body")
            # Silver bars
            page.goto("https://www.gvs-trading.ae/buy/silver-bars.html", timeout=30000)
            page.wait_for_timeout(5000)
            silver_text = page.inner_text("body")
            browser.close()

        # Parse 1kg gold ask price in EUR (line-based)
        gold_eur_kg = None
        gold_lines = gold_text.split('\n')
        for i, line in enumerate(gold_lines):
            if '1 kg Gold Bar' in line:
                for j in range(i+1, min(i+5, len(gold_lines))):
                    clean = gold_lines[j].strip().replace('\xa0', '').replace('\u00a0', '').replace('€', '').replace(',', '').strip()
                    try:
                        val = float(clean)
                        if 50000 < val < 500000:
                            gold_eur_kg = val
                            break
                    except: pass
                if gold_eur_kg: break
        if gold_eur_kg:
            print(f"GVS Dubai: gold_eur_kg={gold_eur_kg}")

        # Parse 1kg silver ask price in EUR (line-based)
        silver_eur_kg = None
        silver_lines = silver_text.split('\n')
        for i, line in enumerate(silver_lines):
            if '1 kg Silver Bar' in line:
                for j in range(i+1, min(i+5, len(silver_lines))):
                    clean = silver_lines[j].strip().replace('\xa0', '').replace('\u00a0', '').replace('€', '').replace(',', '').strip()
                    try:
                        val = float(clean)
                        if 500 < val < 50000:
                            silver_eur_kg = val
                            break
                    except: pass
                if silver_eur_kg: break
        if silver_eur_kg:
            print(f"GVS Dubai: silver_eur_kg={silver_eur_kg}")

        # Bid prices from sell subdomain (static JSON-LD, no JS needed)
        import re as _re
        gold_eur_kg_bid = None
        silver_eur_kg_bid = None
        try:
            sell_gold = requests.get(
                "https://sell.gvs-trading.ae/1-kg-gold-bar-various-manufacturers.html",
                timeout=(5, 10), headers={"User-Agent": "Mozilla/5.0"}
            ).text
            mg = _re.search(r'"price"\s*:\s*"?([\d.]+)"?.*?"priceCurrency"\s*:\s*"EUR"', sell_gold)
            if mg:
                bid_val = float(mg.group(1))
                if 50000 < bid_val < 500000:
                    gold_eur_kg_bid = bid_val
        except Exception as be:
            print(f"GVS gold bid error: {be}")
        try:
            sell_silver = requests.get(
                "https://sell.gvs-trading.ae/1-kg-silver-bar-diverse-manufacturers.html",
                timeout=(5, 10), headers={"User-Agent": "Mozilla/5.0"}
            ).text
            ms = _re.search(r'"price"\s*:\s*"?([\d.]+)"?.*?"priceCurrency"\s*:\s*"EUR"', sell_silver)
            if ms:
                bid_val = float(ms.group(1))
                if 500 < bid_val < 50000:
                    silver_eur_kg_bid = bid_val
        except Exception as be:
            print(f"GVS silver bid error: {be}")

        if gold_eur_kg:
            result = {
                "gold_eur_kg_ask": gold_eur_kg,
                "gold_eur_oz_ask": round(gold_eur_kg / 32.1507, 2),
                "source": "gvs-trading.ae",
                "is_calculated": False
            }
            if gold_eur_kg_bid:
                result["gold_eur_kg_bid"] = gold_eur_kg_bid
                print(f"GVS Dubai: gold_eur_kg ask={gold_eur_kg} bid={gold_eur_kg_bid}")
            if silver_eur_kg:
                result["silver_eur_kg_ask"] = silver_eur_kg
            if silver_eur_kg_bid:
                result["silver_eur_kg_bid"] = silver_eur_kg_bid
                print(f"GVS Dubai: silver_eur_kg ask={silver_eur_kg} bid={silver_eur_kg_bid}")
            return result
        return None
    except Exception as e:
        print(f"Dubai error: {e}")
        return None
def fetch_philoro():
    """Scrape live gold/silver prices from philoro.ch (Philoro Schweiz AG).
    Returns gold 1oz ask/bid in CHF and silver 1kg ask/bid in CHF.
    Uses requests+BeautifulSoup on static Vue SSR HTML."""
    import re
    from bs4 import BeautifulSoup

    UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

    def parse_chf(s):
        return float(s.replace('.', '').replace(',', '.'))

    def scrape(url, title_pattern):
        r = requests.get(url, headers={'User-Agent': UA}, timeout=(5, 20))
        soup = BeautifulSoup(r.text, 'html.parser')
        for card in soup.find_all(attrs={'data-testid': 'productCard'}):
            txt = card.get_text(' ', strip=True)
            if re.search(title_pattern, txt):
                m_ask = re.search(r'Kaufen:\s*([\d.,]+)\s*CHF', txt)
                m_bid = re.search(r'Verkaufen:\s*([\d.,]+)\s*CHF', txt)
                if m_ask and m_bid:
                    return parse_chf(m_ask.group(1)), parse_chf(m_bid.group(1))
        return None, None

    try:
        gold_ask, gold_bid = scrape('https://www.philoro.ch/shop/goldbarren', r'Goldbarren 1 oz[ -]')
        silver_ask, silver_bid = scrape('https://www.philoro.ch/shop/silberbarren', r'Silberbarren 1000 g ')
        if not gold_ask:
            print("Philoro: gold 1oz not found")
            return None
        result = {
            "gold_chf_oz_ask": round(gold_ask, 2),
            "gold_chf_oz_bid": round(gold_bid, 2),
            "source": "philoro.ch",
            "is_calculated": False
        }
        if silver_ask:
            result["silver_chf_kg_ask"] = round(silver_ask, 2)
            result["silver_chf_kg_bid"] = round(silver_bid, 2)
            print(f"Philoro: gold ask={gold_ask} bid={gold_bid} CHF/oz  silver ask={silver_ask} bid={silver_bid} CHF/kg")
        else:
            print(f"Philoro: gold ask={gold_ask} bid={gold_bid} CHF/oz  (no silver)")
        return result
    except Exception as e:
        print(f"Philoro error: {e}")
        return None

def fetch_germany():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://degussa.com/de-de/header_navigation/preise/preisliste/", timeout=30000)
            page.wait_for_timeout(6000)
            text = page.inner_text("body")
            browser.close()
        lines = [l.strip() for l in text.splitlines()]
        def parse_eur(s):
            return float(s.replace('\xa0','').replace('€','').strip().replace('.','').replace(',','.'))
        gold_bid_kg = gold_ask_kg = None
        silver_bid_kg = silver_ask_kg = None
        for i, line in enumerate(lines):
            if '1000 g Degussa Goldbarren' in line and gold_bid_kg is None and i+2 < len(lines):
                try:
                    gold_bid_kg = parse_eur(lines[i+1])
                    gold_ask_kg = parse_eur(lines[i+2])
                except Exception:
                    pass
            elif '1000 g Degussa Silberbarren' in line and silver_bid_kg is None and i+2 < len(lines):
                try:
                    silver_bid_kg = parse_eur(lines[i+1])
                    silver_ask_kg = parse_eur(lines[i+2])
                except Exception:
                    pass
        if not gold_bid_kg:
            print("Degussa: 1000g gold not found on preisliste")
            return None
        bid_eur_oz = gold_bid_kg / 32.1507
        ask_eur_oz = gold_ask_kg / 32.1507
        print(f"Degussa: bid={bid_eur_oz:.2f} ask={ask_eur_oz:.2f} EUR/oz (1kg from preisliste)")
        result = {
            "gold_eur_oz_bid": round(bid_eur_oz, 2),
            "gold_eur_oz_ask": round(ask_eur_oz, 2),
            "source": "degussa.com",
            "is_calculated": False
        }
        if silver_bid_kg:
            result["silver_eur_kg_bid"] = round(silver_bid_kg, 2)
            result["silver_eur_kg_ask"] = round(silver_ask_kg, 2)
            print(f"Degussa Silver: bid={silver_bid_kg} ask={silver_ask_kg} EUR/kg")
        return result
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

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://www.hangseng.com/en-hk/personal/banking/rates/gold-prices/", timeout=30000)
            page.wait_for_timeout(8000)
            content = page.inner_text("body")
            browser.close()
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        bid_oz = None
        ask_oz = None
        for line in lines:
            if "Hang Seng Logo Gold Bar (per 1 Ounce Troy)" in line:
                # Next non-empty line with numbers
                idx = lines.index(line)
                for j in range(idx+1, min(idx+5, len(lines))):
                    parts = [p.strip() for p in lines[j].split("\t")]
                    nums = []
                    for part in parts:
                        try:
                            val = float(part.replace(",",""))
                            if val > 10000:
                                nums.append(val)
                        except: pass
                    if len(nums) >= 2:
                        bid_oz = nums[0]
                        ask_oz = nums[1]
                        break
                if bid_oz: break
        if bid_oz and ask_oz:
            print(f"Hang Seng: bid={bid_oz} ask={ask_oz} HKD/oz")
            return {
                "gold_hkd_oz_bid": bid_oz,
                "gold_hkd_oz_ask": ask_oz,
                "source": "hangseng.com",
                "is_calculated": False
            }
        return None
    except Exception as e:
        print(f"HK error: {e}")
        return None

def fetch_lbma():
    try:
        import requests as req2
        r3 = req2.get("https://prices.lbma.org.uk/json/gold_pm.json",
                      headers={"User-Agent": "Mozilla/5.0",
                               "Referer": "https://www.lbma.org.uk/"}, timeout=10)
        data = r3.json()
        # Format: {"d": "2026-04-27", "v": [am_usd, am_gbp, am_eur]}
        # v[0] = AM fix USD/oz
        latest = data[-1]
        price = float(latest["v"][0])
        if price > 100:
            print(f"LBMA gold PM: {price} USD/oz")
            result = {"gold_usd_oz": price, "source": "lbma.org.uk", "is_calculated": False}
            try:
                rs = req2.get("https://prices.lbma.org.uk/json/silver.json",
                              headers={"User-Agent": "Mozilla/5.0",
                                       "Referer": "https://www.lbma.org.uk/"}, timeout=10)
                sdata = rs.json()
                silver = float(sdata[-1]["v"][0])
                if silver > 1:
                    result["silver_usd_oz"] = silver
                    print(f"LBMA silver: {silver} USD/oz")
            except: pass
            return result
        return None
    except Exception as e:
        print(f"LBMA error: {e}")
        return None


def fetch_shanghai():
    try:
        from playwright.sync_api import sync_playwright
        import json as _json

        sge_data = {}
        def handle_response(response):
            if 'api/shanghai' in response.url and 'symbols=XAU' in response.url and response.status == 200:
                try:
                    sge_data['gold'] = response.json()
                except: pass
            if 'api/shanghai' in response.url and 'symbols=XAG' in response.url and response.status == 200:
                try:
                    sge_data['silver'] = response.json()
                except: pass

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on('response', handle_response)
            page.goto("https://metalcharts.org/shanghai/xau", timeout=30000)
            page.wait_for_timeout(6000)
            browser.close()

        gold_data = sge_data.get('gold', {}).get('data', {}).get('XAU', {})
        silver_data = sge_data.get('silver', {}).get('data', {}).get('XAG', {})

        gold_cny_gram = gold_data.get('priceCNY')
        gold_usd_oz = gold_data.get('price')
        silver_cny_kg = None  # Silber kommt von shanghaiAgAuto (separate Quelle)

        if gold_cny_gram and gold_cny_gram > 100:
            print(f"SGE Gold (metalcharts): {gold_cny_gram} CNY/gram = {gold_usd_oz} USD/oz")
            result = {
                "gold_cny_gram": round(gold_cny_gram, 4),
                "gold_usd_oz": round(gold_usd_oz, 4) if gold_usd_oz else None,
                "source": "metalcharts.org/sge",
                "is_calculated": False
            }
            # Silber via shanghaiAgAuto
            try:
                import requests
                from bs4 import BeautifulSoup
                from datetime import date, timedelta
                headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sge.com.cn/sjzx/mrhqsj"}
                for d in [date.today().isoformat()] + [(date.today()-timedelta(days=i)).isoformat() for i in range(1,6)]:
                    ag_url = f"https://www.sge.com.cn/sjzx/shanghaiAgAuto?start_date={d}&end_date={d}"
                    r = requests.get(ag_url, timeout=(5, 10), headers=headers)
                    soup = BeautifulSoup(r.text, 'html.parser')
                    last_price = None
                    for row in soup.find_all('tr'):
                        cells = row.find_all('td')
                        if len(cells) >= 6:
                            cont = cells[2].get_text(strip=True)
                            price_text = cells[5].get_text(strip=True)
                            if 'SHAG' in cont or 'Ag' in cont:
                                try:
                                    p2 = float(price_text.replace(',',''))
                                    if p2 > 1000: last_price = p2
                                except: pass
                    if last_price:
                        silver_cny_kg = last_price
                        print(f"SGE SHAG silver ({d}): {silver_cny_kg} CNY/kg")
                        result['silver_cny_kg'] = silver_cny_kg
                        break
            except Exception as se:
                print(f"SGE silver error: {se}")
            return result

        return None
    except Exception as e:
        print(f"SGE error: {e}")
        return None

def fetch_chinagold():
    """中国黄金 (China Gold) Investment-Barren via Bright Data Web Unlocker.
    Quelle: chnau99999.com (offizielle Terminal-Preisseite).
    零售价 = Verkauf (ask), 回购价 = Rueckkauf (bid), 基础金价 = Basis (Referenz). Alle CNY/Gramm.
    Token + Zone aus den Umgebungsvariablen BRIGHTDATA_API_TOKEN / BRIGHTDATA_ZONE."""
    import os, re
    token = os.environ.get("BRIGHTDATA_API_TOKEN")
    zone  = os.environ.get("BRIGHTDATA_ZONE")
    if not token or not zone:
        print("ChinaGold: BRIGHTDATA_API_TOKEN/ZONE fehlt -> uebersprungen")
        return None
    try:
        r = requests.post(
            "https://api.brightdata.com/request",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"zone": zone, "url": "https://www.chnau99999.com/page/goldPrice",
                  "format": "raw", "data_format": "markdown"},
            timeout=(10, 60))
        r.raise_for_status()
        text = r.text

        def grab(label):
            m = re.search(label + r"[^0-9]{0,15}([0-9]{3,5}\.[0-9]{2})", text)
            return float(m.group(1)) if m else None

        ask  = grab("零售价")
        bid  = grab("回购价")
        base = grab("基础金价")
        if not ask:
            print("ChinaGold: 零售价 nicht gefunden -> Parser/Quelle pruefen")
            return None
        print(f"ChinaGold: ask={ask} bid={bid} base={base} CNY/g")
        return {
            "gold_cny_gram_ask":  round(ask, 2),
            "gold_cny_gram_bid":  round(bid, 2) if bid else None,
            "gold_cny_gram_base": round(base, 2) if base else None,
            "source": "chnau99999.com",
            "is_calculated": False,
        }
    except Exception as e:
        print(f"ChinaGold error: {e}")
        return None

def fetch_hkgx():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://hkgx.com.hk/en/marketdata/latestquotes", timeout=30000)
            page.wait_for_timeout(5000)
            content = page.inner_text("body")
            browser.close()
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        for line in lines:
            if "Loco London Gold 100 Ounces" in line:
                parts = [p.strip() for p in line.split("\t")]
                if len(parts) >= 3:
                    try:
                        bid = float(parts[1].replace(",",""))
                        ask = float(parts[2].replace(",",""))
                        if 1000 < bid < 20000 and 1000 < ask < 20000:
                            print(f"HKGX: bid={bid} ask={ask} USD/oz")
                            return {"gold_usd_oz_bid": bid, "gold_usd_oz_ask": ask,
                                    "source": "hkgx.com.hk", "is_calculated": False}
                    except: pass
        return None
    except Exception as e:
        print(f"HKGX error: {e}")
        return None


def fetch_australia():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(
                "https://www.perthmint.com/invest/information-for-investors/metal-prices/",
                timeout=30000
            )
            page.wait_for_timeout(7000)
            data = page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('.table__row:not(.table__row--header)');
                    let goldAsk = null, goldBid = null, silverAsk = null, silverBid = null;
                    for (const row of rows) {
                        const cells = row.querySelectorAll('.table__cell.mx-md-1');
                        if (cells.length < 3) continue;
                        const size = cells[0].innerText.trim();
                        if (size !== '1 kilo') continue;
                        const ask = parseFloat(cells[1].innerText.replace(/[$, ]/g, ''));
                        const bid = parseFloat(cells[2].innerText.replace(/[$, ]/g, ''));
                        if (isNaN(ask) || isNaN(bid)) continue;
                        // Gold 1kg bar: 100k-400k AUD — take the lowest ask (cast bar < coin)
                        if (ask > 100000 && ask < 400000) {
                            if (goldAsk === null || ask < goldAsk) {
                                goldAsk = ask; goldBid = bid;
                            }
                        }
                        // Silver 1kg bar: 1k-30k AUD
                        if (ask > 1000 && ask < 30000 && silverAsk === null) {
                            silverAsk = ask; silverBid = bid;
                        }
                    }
                    return {goldAsk, goldBid, silverAsk, silverBid};
                }
            """)
            browser.close()
        if data and data.get("goldAsk"):
            print(f"Perth Mint: gold_kg ask={data['goldAsk']} bid={data['goldBid']} silver_kg ask={data.get('silverAsk')} AUD")
            result = {
                "gold_aud_kg_ask": data["goldAsk"],
                "gold_aud_kg_bid": data["goldBid"],
                "source": "perthmint.com",
                "is_calculated": False
            }
            if data.get("silverAsk"):
                result["silver_aud_kg"] = data["silverAsk"]
                result["silver_aud_kg_bid"] = data["silverBid"]
            return result
        return None
    except Exception as e:
        print(f"Australia error: {e}")
        return None
def fetch_usa():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            browser2 = p.chromium.launch(headless=True)
            page2 = browser2.new_page()
            page2.goto("https://www.bgasc.com/gold/gold-bars/1-kilo-gold-bars/", timeout=30000)
            page2.wait_for_timeout(6000)
            gold_content = page2.inner_text("body")
            page2.goto("https://www.bgasc.com/product/kilo-silver-bars-secondary-market-random-assorted", timeout=30000)
            page2.wait_for_timeout(6000)
            silver_content = page2.inner_text("body")
            page2.goto("https://www.bgasc.com/sell-silver-and-gold", timeout=30000)
            page2.wait_for_timeout(6000)
            sell_content = page2.inner_text("body")
            browser2.close()
        lines = [l.strip() for l in gold_content.split(chr(10)) if l.strip()]
        silver_lines = [l.strip() for l in silver_content.split(chr(10)) if l.strip()]
        ask_usd_kg = None
        # Sortenliste in Prioritaetsreihenfolge:
        # 1. Varied Condition (Sekundaermarkt, billigster) â Original
        # 2. Valcambi Cast â Fallback wenn Varied ausverkauft
        # 3. Perth Mint Cast â Fallback wenn auch Valcambi ausverkauft
        gold_titles = [
            "1 Kilo Gold Bar (Varied Condition",
            "1 Kilo Valcambi Cast Gold Bar",
            "1 Kilo Perth Mint Cast Gold Bar",
        ]
        used_title = None
        for title in gold_titles:
            for i, line in enumerate(lines):
                if title in line:
                    for j in range(i+1, min(i+4, len(lines))):
                        try:
                            val = float(lines[j].replace("$","").replace(",",""))
                            if 100000 < val < 300000:
                                ask_usd_kg = val
                                used_title = title
                                break
                        except: pass
                    if ask_usd_kg:
                        break
            if ask_usd_kg:
                break
        if used_title and used_title != "1 Kilo Gold Bar (Varied Condition":
            print(f"BGASC USA Fallback aktiv: {used_title}")
        # Silver: suche ersten Preis zwischen 1000-5000 USD
        silver_usd_kg = None
        for line2 in silver_lines:
            if line2.startswith("$"):
                try:
                    val2 = float(line2.replace("$","").replace(",",""))
                    if 1000 < val2 < 5000:
                        silver_usd_kg = val2
                        break
                except: pass
        # Silver bid: parse sell page for "100 oz Silver Bars" price → per-oz
        import re as _re
        silver_usd_oz_bid = None
        try:
            for line in sell_content.splitlines():
                m100 = _re.search(r'100 oz Silver Bar.*?\$([0-9,]+\.?\d*)', line)
                if m100:
                    val = float(m100.group(1).replace(',',''))
                    if 3000 < val < 20000:
                        silver_usd_oz_bid = round(val / 100, 2)
                        print(f"BGASC silver bid: 100oz bar=${val} → ${silver_usd_oz_bid}/oz")
                        break
        except Exception as se:
            print(f"BGASC silver bid error: {se}")

        if ask_usd_kg:
            G = 31.1035
            ask_oz = ask_usd_kg / 1000 * G
            bid_kg = round(ask_usd_kg * 0.98, 2)
            bid_oz = bid_kg / 1000 * G
            print(f"BGASC USA: gold_ask={ask_usd_kg} silver_ask={silver_usd_kg} USD/kg")
            result = {"gold_usd_oz_ask": round(ask_oz,2), "gold_usd_oz_bid": round(bid_oz,2),
                    "gold_usd_kg_ask": ask_usd_kg, "gold_usd_kg_bid": bid_kg,
                    "source": "bgasc.com", "is_calculated": False}
            if silver_usd_kg:
                silver_oz = silver_usd_kg / 1000 * G
                result["silver_usd_kg_ask"] = silver_usd_kg
                result["silver_usd_oz_ask"] = round(silver_oz, 2)
            if silver_usd_oz_bid:
                result["silver_usd_oz_bid"] = silver_usd_oz_bid
                result["silver_usd_kg_bid"] = round(silver_usd_oz_bid / G * 1000, 2)
            return result
        return None
    except Exception as e:
        print(f"USA error: {e}")
        return None


def fetch_canada():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # Gold page
            page.goto("https://canadianbullion.ca/1kg-pure-gold-assorted.html", timeout=30000)
            page.wait_for_timeout(5000)
            gold_content = page.inner_text("body")
            # Silver page
            page.goto("https://canadianbullion.ca/silver/bars.html", timeout=30000)
            page.wait_for_timeout(5000)
            silver_content = page.inner_text("body")
            browser.close()
        # Gold
        lines = [l.strip() for l in gold_content.split("\n") if l.strip()]
        gold_cad_kg = None
        for line in lines:
            if line.startswith("CA$") and gold_cad_kg is None:
                try:
                    val = float(line.replace("CA$","").replace(",","").strip())
                    if 100000 < val < 1000000:
                        gold_cad_kg = val
                        break
                except: pass
        # Silver - suche 1kg Preis auf Silber-Seite (1kg = 2000-10000 CAD)
        silver_lines = [l.strip() for l in silver_content.split("\n") if l.strip()]
        silver_cad_kg = None
        for line in silver_lines:
            if line.startswith("CA$"):
                try:
                    val = float(line.replace("CA$","").replace(",","").strip())
                    if 2000 < val < 10000:
                        silver_cad_kg = val
                        break
                except: pass
        if gold_cad_kg:
            print(f"Canada CB: gold_cad_kg={gold_cad_kg} silver_cad_kg={silver_cad_kg}")
            result = {"gold_cad_kg_ask": gold_cad_kg, "source": "canadianbullion.ca",
                    "is_calculated": False}
            if silver_cad_kg:
                result["silver_cad_kg_ask"] = silver_cad_kg
            return result
        return None
    except Exception as e:
        print(f"Canada error: {e}")
        return None


def update():
    print(f"[{datetime.now()}] Updating prices...")
    fx, fx_yest = fetch_fx()
    fx_yahoo = fetch_fx_yahoo()
    spot = fetch_spot()
    if not spot or not spot.get("XAU"):
        print("Kitco failed, using fallback...")
        spot = fetch_spot_fallback()
    # If XAU_ch missing (Kitco blocked), compute from DB yesterday price
    if spot and spot.get("XAU") and spot.get("XAU_ch") is None:
        try:
            _conn = sqlite3.connect(DB_PATH)
            _c = _conn.cursor()
            _c.execute("""SELECT gold_usd_oz FROM price_history
                          WHERE market IN ('lbma','comex')
                            AND gold_usd_oz IS NOT NULL
                            AND ts < datetime('now','-12 hours')
                          ORDER BY ts DESC LIMIT 1""")
            _row = _c.fetchone()
            _conn.close()
            if _row and _row[0]:
                spot["XAU_ch"] = round(spot["XAU"] - _row[0], 2)
                print(f"XAU_ch from DB: {spot['XAU_ch']} (today={spot['XAU']:.2f} yest={_row[0]:.2f})")
        except Exception as _e:
            print(f"XAU_ch DB fallback error: {_e}")
    ist = fetch_istanbul()
    india_gjc = fetch_gjc()
    india_mumbai = fetch_india_augmont()
    uk_royalmint = fetch_uk_royalmint()
    india = fetch_india()
    hk = fetch_hongkong()
    lbma = fetch_lbma()
    sge = fetch_shanghai()
    # China Gold nur alle 30 Min abfragen (Preis traege, spart Bright-Data-Kosten).
    # An fetch-freien Zyklen bleibt der letzte Wert ueber die DB-Injection in /api/prices sichtbar.
    chinagold = None
    if datetime.now().timestamp() - cache.get("chinagold_last_ts", 0) >= 1800:
        chinagold = fetch_chinagold()
        if chinagold:
            cache["chinagold_last_ts"] = datetime.now().timestamp()
    hkgx = fetch_hkgx()
    australia = fetch_australia()
    usa = fetch_usa()
    canada = fetch_canada()
    japan = fetch_japan()
    philoro = fetch_philoro()
    germany = fetch_germany()
    russia = fetch_russia()
    russia_dealer = fetch_russia_dealer()
    dubai = fetch_dubai()

    prices = {}
    if spot: prices["spot"] = spot

    # COMEX Futures GC=F / SI=F von stooq/Yahoo Finance
    comex_futures = fetch_comex_futures()
    if comex_futures and prices.get("spot"):
        cache["last_gc"] = comex_futures.get("GC")
        cache["last_si"] = comex_futures.get("SI")
        print(f"COMEX Futures: GC={comex_futures.get('GC')} SI={comex_futures.get('SI')} USD/oz")
    if prices.get("spot") and cache.get("last_gc"):
        prices["spot"]["GC"] = cache["last_gc"]
        prices["spot"]["SI"] = cache["last_si"]

    # S&P 500 — wird separat in sp500_history Tabelle gespeichert (fuer Gold/SP500 Ratio)
    sp500_value = fetch_sp500()
    if sp500_value:
        try:
            _conn = sqlite3.connect(DB_PATH)
            _c = _conn.cursor()
            _ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            _c.execute("INSERT OR IGNORE INTO sp500_history (ts, value) VALUES (?, ?)", (_ts, sp500_value))
            _conn.commit()
            _conn.close()
        except Exception as _e:
            print(f"S&P 500 DB save error: {_e}")

    if fx: prices["fx"] = fx
    if fx_yest: prices["fx_yest"] = fx_yest
    if fx_yahoo: prices["fx_yahoo"] = fx_yahoo

    if ist:
        prices["istanbul"] = ist
    elif spot and fx:
        prices["istanbul"] = {
            "gold_try_gram": round((spot["XAU"] / GRAM) * fx["TRY"], 2),
            "silver_try_gram": round((spot["XAG"] / GRAM) * fx["TRY"], 2) if spot.get("XAG") else None,
            "source": "calculated",
            "is_calculated": True
        }

    if india_gjc:
        prices["india_gjc"] = india_gjc
    elif spot and fx:
        inr_gram = round((spot["XAU"] / GRAM) * fx.get("INR", 83) * 1.13, 2)
        prices["india_gjc"] = {
            "gold_inr_gram_ask": inr_gram,
            "gold_inr_gram_bid": round(inr_gram * (1 - 0.033), 2),
            "source": "calculated",
            "is_calculated": True
        }

    if india_mumbai:
        prices["india_mumbai"] = india_mumbai
        # Direkt USD/oz setzen (Augmont-eigener USD/INR-Kurs, NICHT NORM_TABLE)
        prices["india_mumbai"]["gold_usd_oz"] = india_mumbai["gold_usd_oz_ask"]
        prices["india_mumbai"]["silver_usd_oz"] = india_mumbai["silver_usd_oz_ask"]

    if india:
        prices["india"] = india

    if hk:
        prices["hongkong"] = hk
    if lbma:
        prices["lbma"] = lbma
    if sge:
        prices["shanghai"] = sge
    if chinagold:
        prices["chinagold"] = chinagold
    if hkgx:
        prices["hkgx"] = hkgx
    if australia:
        prices["australia"] = australia
    if usa:
        prices["usa"] = usa
    if canada:
        prices["canada"] = canada
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

    if philoro:
        prices["switzerland"] = philoro
    elif spot and fx:
        prices["switzerland"] = {
            "gold_chf_oz_bid": round(spot["XAU"] * fx.get("CHF", 0.89) * 1.005, 2),
            "gold_chf_oz_ask": round(spot["XAU"] * fx.get("CHF", 0.89) * 1.02, 2),
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
    if russia_dealer:
        prices["russia_dealer"] = russia_dealer
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

    if uk_royalmint:
        prices["uk_royalmint"] = uk_royalmint
    elif spot and fx:
        prices["uk_royalmint"] = {
            "gold_gbp_oz_ask": round(spot["XAU"] * fx.get("GBP", 0.79) * 1.01, 2),
            "gold_gbp_oz_bid": round(spot["XAU"] * fx.get("GBP", 0.79) * 0.995, 2),
            "silver_gbp_oz_ask": round(spot["XAG"] * fx.get("GBP", 0.79) * 1.01, 2) if spot.get("XAG") else None,
            "silver_gbp_oz_bid": round(spot["XAG"] * fx.get("GBP", 0.79) * 0.995, 2) if spot.get("XAG") else None,
            "source": "calculated",
            "is_calculated": True
        }


    # ============================================================
    # NORMALISIERUNG: gold_usd_oz + silver_usd_oz fuer jeden Markt
    # ============================================================
    _spot = prices.get("spot", {})
    _fx = prices.get("fx", {})
    _G = 31.1035

    def to_usd_oz(val, currency, unit):
        if not val: return None
        # Fallbacks fuer Waehrungen die nicht in Frankfurter API sind
        FALLBACKS = {"RUB": 75.0, "AED": 3.6725, "AUD": 1.55, "CAD": 1.36, "CHF": 0.89}
        rate = (_fx.get(currency) or FALLBACKS.get(currency, 1.0)) if currency != "USD" else 1.0
        if unit == "gram": return round(val * _G / rate, 4)
        if unit == "oz":   return round(val / rate, 4)
        if unit == "kg":   return round(val / 32.1507 / rate, 4)
        return None

    NORM_TABLE = {
        "istanbul":      ("gold_try_gram",     "TRY", "gram", "silver_try_gram",    "TRY", "gram"),
        "india_gjc":     ("gold_inr_gram_ask",  "INR", "gram", "silver_inr_kg_ask",  "INR", "kg"),
        "hongkong":      ("gold_hkd_oz_ask",   "HKD", "oz",   None,                 None,  None),
        "lbma":          ("gold_usd_oz",       "USD", "oz",   "silver_usd_oz",      "USD", "oz"),
        "hkgx":          ("gold_usd_oz_ask",   "USD", "oz",   None,                 None,  None),
        "australia":     ("gold_aud_kg_ask",   "AUD", "kg",   "silver_aud_kg",      "AUD", "kg"),
        "usa":           ("gold_usd_oz_ask",   "USD", "oz",   "silver_usd_kg_ask",  "USD", "kg"),
        "canada":        ("gold_cad_kg_ask",   "CAD", "kg",   "silver_cad_kg_ask",  "CAD", "kg"),
        "switzerland":   ("gold_chf_oz_ask",   "CHF", "oz",   "silver_chf_kg_ask",  "CHF", "kg"),
        "germany":       ("gold_eur_oz_ask",   "EUR", "oz",   "silver_eur_kg_ask",  "EUR", "kg"),
        "russia":        ("gold_rub_gram",     "RUB", "gram", "silver_rub_gram",    "RUB", "gram"),
        "russia_dealer": ("gold_rub_gram",     "RUB", "gram", "silver_rub_gram",    "RUB", "gram"),
        "dubai":         ("gold_eur_kg_ask",   "EUR", "kg",   "silver_eur_kg_ask",  "EUR", "kg"),
        "india":         ("gold_inr_gram",     "INR", "gram", "silver_inr_kg",      "INR", "kg"),
        "japan":         ("gold_jpy_gram_ask", "JPY", "gram", None,                 None,  None),
        "shanghai":      ("gold_cny_gram",     "CNY", "gram", "silver_cny_kg",      "CNY", "kg"),
        "chinagold":     ("gold_cny_gram_ask", "CNY", "gram", None,                 None,  None),
        "uk_royalmint":  ("gold_gbp_oz_ask",   "GBP", "oz",   "silver_gbp_oz_ask",  "GBP", "oz"),
    }

    for market, norm in NORM_TABLE.items():
        gold_key, gold_cur, gold_unit, silver_key, silver_cur, silver_unit = norm
        m = prices.get(market)
        if not m: continue
        gold_val = m.get(gold_key)
        gold_usd = to_usd_oz(gold_val, gold_cur, gold_unit)
        if gold_usd:
            prices[market]["gold_usd_oz"] = gold_usd
        if silver_key:
            silver_val = m.get(silver_key)
            silver_usd = to_usd_oz(silver_val, silver_cur, silver_unit)
            if silver_usd:
                prices[market]["silver_usd_oz"] = silver_usd

    # China Gold: zusaetzlich Rueckkauf (bid) in USD/oz fuers Frontend
    _cg = prices.get("chinagold")
    if _cg and _cg.get("gold_cny_gram_bid"):
        _cg["gold_usd_oz_bid"] = to_usd_oz(_cg["gold_cny_gram_bid"], "CNY", "gram")

    print(f"Normalized gold/silver usd_oz: { {k: (prices[k].get('gold_usd_oz'), prices[k].get('silver_usd_oz')) for k in NORM_TABLE if k in prices} }")

    cache["prices"] = prices
    cache["last_updated"] = datetime.now().isoformat()

    FX_CURRENCIES = ['EUR','GBP','JPY','CNY','TRY','INR','RUB','AUD','HKD','CHF']

    # --- 22:00 UTC snapshot (Yahoo FX) — aligns with Kitco's 17:00 ET reference ---
    now = datetime.now()
    if now.hour == 22 and now.minute < 10:
        snap = prices.get("fx_yahoo", {})
        if snap.get("EUR"):
            try:
                with open(YESTERDAY_FX_FILE, 'w') as _f:
                    json.dump({'ts': now.isoformat(), 'rates': snap, 'source': 'yahoo'}, _f)
                print(f"Saved 22:00 Yahoo FX snapshot: EUR={snap.get('EUR')} JPY={snap.get('JPY')}")
            except Exception as _e:
                print(f"FX snapshot write error: {_e}")

    # --- Per-currency chp ---
    # Primary: Yahoo live (today) + 22:00 UTC Yahoo snapshot (yesterday ≈ Kitco 17:00 ET)
    # Fallback: Frankfurter two-day approach (always gives different values, slightly off-period)
    fx_chp_today = {}
    fx_chp_yest = {}

    fx_yahoo_live = prices.get("fx_yahoo", {})
    if fx_yahoo_live.get("EUR"):
        try:
            with open(YESTERDAY_FX_FILE) as _f:
                _snap = json.load(_f)
            fx_chp_yest = _snap.get("rates", {})
        except Exception:
            pass
        if fx_chp_yest.get("EUR"):
            fx_chp_today = fx_yahoo_live
            print(f"chp: Yahoo live + 22:00 snapshot  EUR today={fx_chp_today.get('EUR')} yest={fx_chp_yest.get('EUR')}")
        else:
            fx_chp_today = prices.get("fx", {})
            fx_chp_yest = prices.get("fx_yest", {})
            print("chp: no 22:00 snapshot yet — Frankfurter two-day fallback")
    else:
        fx_chp_today = prices.get("fx", {})
        fx_chp_yest = prices.get("fx_yest", {})
        print("chp: Yahoo FX unavailable — Frankfurter two-day fallback")

    if fx_chp_yest.get("EUR") and prices.get("spot"):
        spot_d = prices["spot"]
        xau_today = spot_d.get("XAU")
        xau_ch = spot_d.get("XAU_ch")
        xag_today = spot_d.get("XAG")
        xag_ch = spot_d.get("XAG_ch")
        xau_yest_val = (xau_today - xau_ch) if (xau_today and xau_ch is not None) else None
        xag_yest_val = (xag_today - xag_ch) if (xag_today and xag_ch is not None) else None
        for cur in FX_CURRENCIES:
            if cur == "RUB":
                continue
            cur_low = cur.lower()
            fx_t = fx_chp_today.get(cur)
            fx_y = fx_chp_yest.get(cur)
            if not (fx_t and fx_y and xau_today and xau_yest_val):
                continue
            val_today = xau_today * fx_t
            val_yest  = xau_yest_val * fx_y
            if val_yest:
                prices["spot"][f"XAU_chp_{cur_low}"] = round((val_today - val_yest) / val_yest * 100, 2)
            if xag_today and xag_yest_val:
                ag_today = xag_today * fx_t
                ag_yest  = xag_yest_val * fx_y
                if ag_yest:
                    prices["spot"][f"XAG_chp_{cur_low}"] = round((ag_today - ag_yest) / ag_yest * 100, 2)

    print(f"[{datetime.now()}] Done: {list(prices.keys())}")
    save_prices(prices)

def init_yesterday_from_db():
    """Seed cache['yesterday'] FX rates at startup.
    Tries the 22:00-UTC file snapshot first (survives restarts),
    then falls back to Frankfurter historical as last resort."""
    # 1. Try saved file snapshot (written every day at 22:00 UTC)
    try:
        with open(YESTERDAY_FX_FILE) as _f:
            data = json.load(_f)
        rates = data.get('rates', {})
        if rates.get('EUR'):
            cache["yesterday"].update(rates)
            print(f"init_yesterday_from_db: loaded from file (ts={data.get('ts')}): {rates}")
            return
    except Exception as _e:
        print(f"init_yesterday_from_db: no file ({_e}), falling back to Frankfurter")
    # 2. Fallback: Frankfurter historical (less reliable on weekends/early Mon)
    try:
        import datetime as dt
        yest = dt.date.today() - dt.timedelta(days=1)
        while yest.weekday() >= 5:
            yest -= dt.timedelta(days=1)
        r = requests.get(
            f"https://api.frankfurter.dev/v1/{yest.isoformat()}?base=USD&symbols=EUR,GBP,TRY,CNY,INR,JPY,HKD,AUD,CHF",
            timeout=(5, 10)
        )
        r.raise_for_status()
        fx_yest = r.json().get("rates", {})
        if fx_yest:
            cache["yesterday"].update(fx_yest)
            print(f"init_yesterday_from_db: Frankfurter fallback for {yest}: {fx_yest}")
    except Exception as e:
        print(f"init_yesterday_from_db error: {e}")


def background():
    import traceback
    while True:
        try:
            update()
            check_price_alerts()
        except Exception as _e:
            print(f"[BACKGROUND] crashed: {type(_e).__name__}: {_e}", flush=True)
            traceback.print_exc()
            print("[BACKGROUND] Continuing after error", flush=True)
        time.sleep(10 * 60)

@app.route("/api/prices")
def get_prices():
    if not cache["prices"]:
        return jsonify({"status": "loading", "data": {}}), 202
    data = dict(cache["prices"])
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT MAX(gold_usd_oz), MIN(gold_usd_oz),
                   MAX(silver_usd_oz), MIN(silver_usd_oz)
            FROM price_history
            WHERE market='comex' AND ts >= datetime('now', '-24 hours')
              AND gold_usd_oz IS NOT NULL
        """)
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            spot = dict(data.get("spot") or {})
            spot["XAU_high"] = round(row[0], 2)
            spot["XAU_low"] = round(row[1], 2)
            if row[2]:
                spot["XAG_high"] = round(row[2], 4)
                spot["XAG_low"] = round(row[3], 4)
            data["spot"] = spot
    except Exception as e:
        print(f"High/Low DB error: {e}")
    # China Gold: letzter Wert aus DB (Fetch ist auf 30 Min gedrosselt -> Cache leer auf Zwischen-Zyklen)
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT gold_local, gold_local_bid, gold_usd_oz, bid_usd_oz, premium_pct, bid_premium_pct, ts
            FROM price_history
            WHERE market='chinagold' AND ts >= datetime('now', '-12 hours')
              AND gold_local IS NOT NULL
            ORDER BY ts DESC LIMIT 1
        """)
        row = c.fetchone()
        conn.close()
        if row:
            data["chinagold"] = {
                "gold_cny_gram_ask": row[0],
                "gold_cny_gram_bid": row[1],
                "gold_usd_oz":       row[2],
                "gold_usd_oz_bid":   row[3],
                "premium_pct":       row[4],
                "bid_premium_pct":   row[5],
                "ts":                row[6],
                "source":            "chnau99999.com",
                "is_calculated":     False,
            }
    except Exception as e:
        print(f"ChinaGold DB error: {e}")
    return jsonify({"data": data, "last_updated": cache["last_updated"], "status": "ok"})

@app.route("/api/history/<market>")
def get_history(market):
    try:
        hours = int(request.args.get('hours', 24))
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT ts, gold_usd_oz, gold_local, silver_local, silver_usd_oz, premium_pct, local_currency, silver_local_unit,
                   bid_usd_oz, gold_local_bid, silver_local_bid, bid_premium_pct
            FROM price_history
            WHERE market = ? AND ts >= datetime('now', ? || ' hours')
            ORDER BY ts ASC
        """, (market, f'-{hours}'))
        rows = c.fetchall()
        conn.close()
        data = [{"ts": r[0], "gold_usd_oz": r[1], "gold_local": r[2],
                 "silver_local": r[3], "silver_usd_oz": r[4], "premium_pct": r[5], "currency": r[6], "silver_unit": r[7],
                 "bid_usd_oz": r[8], "gold_local_bid": r[9], "silver_local_bid": r[10], "bid_premium_pct": r[11]} for r in rows]
        return jsonify({"market": market, "data": data, "count": len(data)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/premium-history")
def get_premium_history():
    try:
        days = int(request.args.get('days', 30))
        metal = request.args.get('metal', 'XAU')
        GRAM = 31.1035
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        if metal == 'XAG':
            result = {}
            for market in ['istanbul', 'dubai', 'switzerland', 'germany', 'usa', 'australia', 'canada', 'russia_dealer', 'shanghai']:
                dow_filter = "AND strftime('%w', ph.ts) NOT IN ('1','2')" if market in ('canada', 'dubai') else ""
                c.execute(f"""
                    SELECT date(ph.ts), AVG(ph.silver_local), ph.local_currency, AVG(cx.silver_local), ph.silver_local_unit
                    FROM price_history ph
                    JOIN price_history cx ON date(ph.ts)=date(cx.ts) AND cx.market='comex'
                    WHERE ph.market=? AND ph.silver_local IS NOT NULL AND cx.silver_local IS NOT NULL
                    AND ph.ts >= datetime('now', ? || ' days')
                    {{dow_filter}}
                    GROUP BY date(ph.ts), ph.local_currency, ph.silver_local_unit ORDER BY date(ph.ts) ASC
                """.format(dow_filter=dow_filter), (market, f'-{days}'))
                rows = c.fetchall()
                fx = cache.get("prices",{}).get("fx",{})
                points = []
                for day, avg_sl, cur, avg_spot, sl_unit in rows:
                    if not avg_sl or not avg_spot or avg_spot==0: continue
                    # Dynamische Einheitsumrechnung aus DB
                    FALLBACKS = {"RUB":75,"TRY":45,"INR":85,"AUD":1.55,"CAD":1.36,"EUR":0.85,"HKD":7.8,"AED":3.6725,"JPY":150}
                    rate = fx.get(cur, FALLBACKS.get(cur, 1.0)) if cur != 'USD' else 1.0
                    unit = sl_unit or ('gram' if market == 'istanbul' else 'oz')
                    if unit == 'gram': usd_oz = (avg_sl / rate) * GRAM
                    elif unit == 'kg': usd_oz = (avg_sl / rate) / 32.1507
                    elif unit == 'oz': usd_oz = avg_sl / rate
                    else: continue
                    pct=round((usd_oz-avg_spot)/avg_spot*100,4)
                    points.append({"date":day,"premium":pct})
                if points: result[market]=points
        else:
            result = {}
            for market in ['istanbul','india_gjc','japan','switzerland','germany','dubai','russia','usa','hongkong','australia','canada','shanghai']:
                c.execute("""
                    SELECT date(ts), AVG(premium_pct) FROM price_history
                    WHERE market=? AND premium_pct IS NOT NULL AND ts>=datetime('now',? || ' days')
                    GROUP BY date(ts) ORDER BY date(ts) ASC
                """, (market, f'-{days}'))
                rows = c.fetchall()
                if rows: result[market]=[{"date":r[0],"premium":r[1]} for r in rows]
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ratio-history")
def get_ratio_history():
    range_ = request.args.get('range', '30d')
    type_  = request.args.get('type', 'gold_silver')  # 'gold_silver' (default) oder 'gold_sp500'
    try:
        # ------- Gold / S&P 500 -------
        if type_ == 'gold_sp500':
            if range_ in ('1d', '5d', '30d'):
                hours = {'1d': 24, '5d': 120, '30d': 720}[range_]
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("""
                    SELECT
                        sp.ts,
                        sp.value AS sp500,
                        (SELECT gold_usd_oz FROM price_history
                         WHERE market='comex' AND gold_usd_oz IS NOT NULL
                         AND ts <= sp.ts ORDER BY ts DESC LIMIT 1) AS gold
                    FROM sp500_history sp
                    WHERE sp.ts >= datetime('now', ? || ' hours')
                    ORDER BY sp.ts ASC
                """, (f'-{hours}',))
                rows = c.fetchall()
                conn.close()
                result = [{"ts": ts, "ratio": round(sp / g, 4)} for ts, sp, g in rows if g and g > 0]
                return jsonify(result)
            else:
                # Lange Zeitraeume: SP500 aus sp500_history + LBMA gold per Tag matchen
                import time as _time
                now = _time.time()
                gold_map = cache.get("lbma_gold_map")
                gold_map_ts = cache.get("lbma_gold_map_ts", 0)
                if not gold_map or (now - gold_map_ts) >= 3600:
                    gr = requests.get("https://prices.lbma.org.uk/json/gold_pm.json",
                                      headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.lbma.org.uk/"}, timeout=(5, 20))
                    gold_map = {e['d']: float(e['v'][0])
                                for e in gr.json()
                                if e.get('v') and e['v'][0] and float(e['v'][0]) > 100}
                    cache["lbma_gold_map"] = gold_map
                    cache["lbma_gold_map_ts"] = now
                from datetime import timedelta
                years = {'1y': 1, '10y': 10, 'max': 200}.get(range_, 200)
                cutoff = (datetime.now() - timedelta(days=years * 365)).strftime('%Y-%m-%d')
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT ts, value FROM sp500_history WHERE ts >= ? ORDER BY ts ASC",
                          (cutoff + "T00:00:00Z",))
                rows = c.fetchall()
                conn.close()
                result = []
                for ts, value in rows:
                    date_str = ts[:10]
                    if date_str in gold_map:
                        result.append({"ts": date_str, "ratio": round(value / gold_map[date_str], 4)})
                return jsonify(result)

        # ------- Gold / Silver (bestehende Logik) -------
        if range_ in ('1d', '5d', '30d'):
            hours = {'1d': 24, '5d': 120, '30d': 720}[range_]
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                SELECT ts, gold_usd_oz, silver_usd_oz
                FROM price_history
                WHERE market='comex' AND gold_usd_oz IS NOT NULL AND silver_usd_oz IS NOT NULL
                AND ts >= datetime('now', ? || ' hours')
                ORDER BY ts ASC
            """, (f'-{hours}',))
            rows = c.fetchall()
            conn.close()
            result = [{"ts": ts, "ratio": round(g/s, 4)} for ts, g, s in rows if s and s > 0]
            return jsonify(result)
        else:
            import time as _time
            now = _time.time()
            if cache["lbma_ratio"] and (now - cache["lbma_ratio_ts"]) < 3600:
                lbma_data = cache["lbma_ratio"]
            else:
                gr = requests.get("https://prices.lbma.org.uk/json/gold_pm.json",
                                  headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.lbma.org.uk/"}, timeout=(5, 20))
                sr = requests.get("https://prices.lbma.org.uk/json/silver.json",
                                  headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.lbma.org.uk/"}, timeout=(5, 20))
                gold_map = {e['d']: float(e['v'][0]) for e in gr.json() if e.get('v') and e['v'][0] and float(e['v'][0]) > 0}
                silv_map = {e['d']: float(e['v'][0]) for e in sr.json() if e.get('v') and e['v'][0] and float(e['v'][0]) > 0}
                lbma_data = sorted([
                    {"ts": d, "ratio": round(gold_map[d] / silv_map[d], 4)}
                    for d in gold_map if d in silv_map and silv_map[d] > 0 and gold_map[d] > 100
                ], key=lambda x: x["ts"])
                cache["lbma_ratio"] = lbma_data
                cache["lbma_ratio_ts"] = now
            from datetime import timedelta
            years = {'1y': 1, '10y': 10, 'max': 200}.get(range_, 200)
            cutoff = (datetime.now() - timedelta(days=years * 365)).strftime('%Y-%m-%d')
            return jsonify([p for p in lbma_data if p["ts"] >= cutoff])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok", "last_updated": cache["last_updated"]})

@app.route("/api/fx")
def api_fx():
    try:
        fx = cache["prices"].get("fx", {})
        return jsonify({**fx, "USD": 1})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def index():
    return jsonify({"name": "goldpremium API", "last_updated": cache["last_updated"]})

INGEST_API_KEY  = os.environ.get("INGEST_API_KEY", "GP_BEIJING_INGEST_2026")
INGEST_MARKETS  = {"shanghai"}

@app.route("/ingest", methods=["POST"])
def ingest_price():
    data = request.get_json(silent=True)
    if not data or data.get("api_key") != INGEST_API_KEY:
        return jsonify({"error": "unauthorized"}), 401
    market = data.get("market")
    if market not in INGEST_MARKETS:
        return jsonify({"error": f"unknown market: {market}"}), 400
    gold_cny_gram     = data.get("gold_cny_gram")
    gold_cny_gram_bid = data.get("gold_cny_gram_bid")
    silver_cny_kg     = data.get("silver_cny_kg")
    if not gold_cny_gram and not silver_cny_kg:
        return jsonify({"error": "no price data"}), 400
    spot     = (cache.get("prices") or {}).get("spot") or {}
    fx       = (cache.get("prices") or {}).get("fx")   or {}
    spot_xau = spot.get("XAU")
    cny_rate = fx.get("CNY", 7.1)
    gold_usd_oz     = round((gold_cny_gram / cny_rate) * GRAM, 4) if gold_cny_gram else None
    bid_usd_oz      = round((gold_cny_gram_bid / cny_rate) * GRAM, 4) if gold_cny_gram_bid else None
    silver_usd      = round(silver_cny_kg / 32.1507 / cny_rate, 4) if silver_cny_kg else None
    premium_pct     = round((gold_usd_oz - spot_xau) / spot_xau * 100, 4) if gold_usd_oz and spot_xau else None
    bid_premium_pct = round((bid_usd_oz - spot_xau) / spot_xau * 100, 4) if bid_usd_oz and spot_xau else None
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO price_history "
            "(ts,market,gold_usd_oz,gold_local,silver_local,silver_usd_oz,"
            "premium_pct,local_currency,gold_local_unit,silver_local_unit,"
            "bid_usd_oz,bid_premium_pct,gold_local_bid) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, market, gold_usd_oz, gold_cny_gram, silver_cny_kg,
             silver_usd, premium_pct, 'CNY', 'gram', 'kg',
             bid_usd_oz, bid_premium_pct, gold_cny_gram_bid)
        )
        conn.commit()
        conn.close()
        print(f"/ingest [{market}] gold={gold_cny_gram} bid={gold_cny_gram_bid} silver={silver_cny_kg} prem={premium_pct}%")
        return jsonify({"status": "ok", "ts": ts, "premium_pct": premium_pct,
                        "bid_premium_pct": bid_premium_pct}), 200
    except Exception as e:
        print(f"/ingest DB error: {e}")
        return jsonify({"error": "db error"}), 500

if __name__ == "__main__":
    init_db()
    init_yesterday_from_db()
    t = threading.Thread(target=background, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000)
