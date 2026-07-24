# GoldPremium — Data Sources

**Last updated:** 2026-05-20
**Maintainer:** Lew

Detaillierte Übersicht aller Datenquellen, die GoldPremium nutzt.
Für jeden Markt: Quelle, URL, Code-Position, Eigenheiten, Bid/Ask-Konvention.

---

## Konvention: Bid vs. Ask

- **Ask** = Verkaufspreis (was der Händler verlangt, was der Kunde zahlt)
- **Bid** = Ankaufspreis (was der Händler zahlt, was der Verkäufer bekommt)
- **Spread** = Ask − Bid (typisch 1-3% bei Edelmetallhändlern, bei Banken größer)

**GoldPremium-Norm: Ask** (was zahlt der Käufer für 1 oz).
Ausnahme: `hongkong` nutzt Bid (historisch gewachsen).

Siehe `NORM_TABLE` in `app.py` Zeile ~1700-1720.

---

## Spot/Benchmark

### COMEX (`market='comex'`)
- **Quelle:** Yahoo Finance `query1.finance.yahoo.com/v8/finance/chart/GC=F`
- **Fallback:** Stooq `stooq.com/q/l/?s=gc.f`
- **Code:** `fetch_comex_futures()` ab Zeile ~605
- **Update:** in `update()` synchron mit anderen Märkten

### LBMA PM Fix (`market='lbma'`)
- **Quelle:** `prices.lbma.org.uk/json/gold_pm.json` und `silver.json`
- **Code:** `fetch_lbma()` ab Zeile ~2000
- **Update:** Tagesfixing, ändert sich nur einmal pro Tag

### Kitco (Live-Spot intern)
- **Quelle:** `kitco.com/charts/gold` und `/charts/silver`
- **Methode:** Playwright (JS-Render erforderlich)
- **Code:** `fetch_spot()` ab Zeile ~480
- **Liefert:** XAU/XAG bid+ask, Change in USD und %

### ⭐ WICHTIG — Berechnungsbasis für `premium_pct` / `silver_premium_pct`
**Alle Premium-Werte in `price_history` werden ausschließlich gegen Kitco berechnet,
nicht gegen COMEX oder LBMA.**
- `spot_xau` / `spot_xag` = Kitco-Mittelwert aus Bid+Ask (`app.py` Zeile ~678/683:
  `(gold_bid + gold_ask) / 2`)
- `premium_pct = (gold_usd_oz - spot_xau) / spot_xau * 100` (`app.py` Zeile 2686/2687,
  identische Formel an allen anderen Insert-Stellen)
- COMEX (`market='comex'`) ist selbst nur eine weitere Vergleichszeile gegen dieselbe
  Kitco-Basis (`basis_pct = (gc - spot_xau) / spot_xau`, Zeile 158-160) — keine zweite
  Berechnungsgrundlage.
- Konsequenz: Wenn irgendwo (Chart, Post, Frontend-Text) von "Premium über COMEX" oder
  "Premium über LBMA" die Rede ist, ist das umgangssprachlich gemeint (COMEX/LBMA liegen
  meist nah an Kitco) — technisch korrekt heißt es immer "Premium über Kitco World Spot Price".
- Bei neuen Charts/Posts: Achse/Titel entsprechend präzise beschriften ("vs. Kitco spot"),
  nicht "vs. COMEX", außer es wird tatsächlich die COMEX-Zeile selbst referenziert.

---

## Westliche Märkte (Hetzner-Pipeline)

### USA (`market='usa'`)
- **Quelle:** BGASC.com (online bullion dealer)
- **URLs:**
  - Gold-Listing: `www.bgasc.com/gold/gold-bars/1-kilo-gold-bars/`
  - Silver-Listing: `www.bgasc.com/product/kilo-silver-bars-secondary-market-random-assorted`
  - Sell-Page: `www.bgasc.com/sell-silver-and-gold`
- **Code:** `fetch_usa()` ab Zeile ~1370
- **Methode:** Playwright + Text-Parsing
- **Sortenstrategie:** Drei Sorten in Priorität (siehe `KNOWN_ISSUES.md` Bug 2)
- **Liefert:** gold_usd_kg_ask, silver_usd_kg_ask, silver_usd_oz_bid

### Switzerland (`market='switzerland'`)
- **Quelle:** philoro.ch (Philoro Schweiz AG)
- **URLs:**
  - Gold: `www.philoro.ch/shop/goldbarren`
  - Silver: `www.philoro.ch/shop/silberbarren`
- **Code:** `fetch_philoro()` ab Zeile ~1022
- **Methode:** requests + BeautifulSoup (Vue SSR, statisches HTML)
- **Sucht:** Karten mit `data-testid='productCard'`, Pattern "1 oz" / "1000 g"
- **Liefert:** gold_chf_oz_ask, gold_chf_oz_bid, silver_chf_kg_ask, silver_chf_kg_bid
- **⚠️ Bug:** DB-Insert nutzt bid statt ask (siehe `KNOWN_ISSUES.md` Bug 1)

### Germany (`market='germany'`)
- **Quelle:** Degussa
- **URL:** `degussa.com/de-de/header_navigation/preise/preisliste/`
- **Code:** ab Zeile ~1070 (`page.goto` bei 1075)
- **Methode:** Playwright
- **⚠️ Bug:** DB-Insert nutzt vermutlich bid statt ask (siehe `KNOWN_ISSUES.md` Bug 1)

### UK (`market='uk_royalmint'`)
- **Quelle:** Royal Mint (Llantrisant, Wales — Government Mint)
- **URL:** Über `royalmint.com` Produkt-Seiten
- **Liefert:** gold_gbp_oz_ask, silver_gbp_oz_ask

### Australia (`market='australia'`)
- **Quelle:** Perth Mint (Government Mint)
- **URL:** Über `perthmint.com`
- **Liefert:** gold_aud_kg_ask, gold_aud_kg_bid, silver_aud_kg

### Canada (`market='canada'`)
- **Quelle:** Canadian Bullion (canadianbullion.ca)
- **URLs:**
  - Gold: `canadianbullion.ca/1kg-pure-gold-assorted.html`
  - Silver: `canadianbullion.ca/silver/bars.html`
- **Code:** `fetch_canada()` ab Zeile ~1460
- **Methode:** Playwright

---

## Asien / Pazifik

### Hongkong (`market='hongkong'`)
- **Quelle:** Hang Seng Bank (Bullion Dealer)
- **URL:** `hangseng.com/en-hk/personal/banking/rates/gold-prices/`
- **Code:** `fetch_hongkong()` ab Zeile ~1130
- **Methode:** Playwright
- **Liefert:** gold_hkd_oz_bid (Achtung: BID — historisch gewachsen!)

### Hong Kong Gold Exchange (`market='hkgx'`)
- **Quelle:** HKGX (Hong Kong Gold Exchange)
- **URL:** `hkgx.com.hk/en/marketdata/latestquotes`
- **Code:** `fetch_hkgx()` ab Zeile ~1280
- **Methode:** Playwright
- **Liefert:** gold_usd_oz_ask

### Japan (`market='japan'`)
- **Quelle:** Tanaka Kikinzoku (Tokyo, Precious Metals)
- **URL:** `gold.tanaka.co.jp/commodity/souba/english/index.php`
- **Code:** `fetch_japan()` ab Zeile ~810
- **Methode:** requests
- **Liefert:** gold_jpy_gram_ask

### India — IBJA (`market='india'`)
- **Quelle:** India Bullion and Jewellers Association
- **URL:** `ibjarates.com/`
- **Code:** ab Zeile ~788
- **Methode:** requests
- **Liefert:** gold_inr_gram, silver_inr_kg

### India — GJC (`market='india_gjc'`)
- **Quelle:** Gem & Jewellery Council, Mumbai
- **URL:** `www.gjc.org.in/`
- **Code:** `fetch_gjc()` ab Zeile ~700
- **Methode:** Playwright
- **Liefert:** gold_inr_gram_ask, silver_inr_kg_ask

### India — Augmont (`market='india_mumbai'`)
- **Quelle:** Augmont (Mumbai, Live Spot)
- **URL:** `spot.augmont.com`
- **Liefert:** gold INR/10g

---

## Russland

### Russia — CBR (`market='russia'`)
- **Quelle:** Central Bank of Russia
- **URLs:**
  - `cbr.ru/scripts/XML_daily.asp` (FX)
  - `cbr.ru/eng/hd_base/metall/metall_base_new/` (Gold/Silber)
- **Code:** ab Zeile ~837
- **Methode:** requests + XML/HTML-Parsing
- **Liefert:** gold_rub_gram, silver_rub_gram

### Russia — Dealer (`market='russia_dealer'`)
- **Quelle:** Region-Zoloto (Moscow Online Dealer)
- **URLs:**
  - Gold: `shop.region-zoloto.ru/affinazh/slitki/zolotoj-mernoj-slitok-999-proby-1000-gr`
  - Silver: `shop.region-zoloto.ru/affinazh/serebro-granyli-999`
- **Code:** ab Zeile ~865
- **Methode:** Playwright

---

## Türkei

### Istanbul (`market='istanbul'`)
- **Quelle:** Nadir (Grand Bazaar / Kapalı Çarşı)
- **URL:** `www.nadirdoviz.com/`
- **Code:** `fetch_istanbul()` ab Zeile ~645
- **Methode:** Playwright
- **Liefert:** gold_try_gram, silver_try_gram

---

## Dubai

### Dubai (`market='dubai'`)
- **Quelle:** GVS Trading (Gold & Diamond Park)
- **URLs:**
  - Listing: `www.gvs-trading.ae/buy/gold-bars.html` und `silver-bars.html`
  - Sell-Page: `sell.gvs-trading.ae/1-kg-gold-bar-various-manufacturers.html`
- **Code:** ab Zeile ~925
- **Methode:** Playwright + requests
- **Liefert:** gold_eur_kg_ask, gold_eur_kg_bid, silver_eur_kg_ask

---

## China (Peking-Pipeline)

### SGE (`market='shanghai'`)
- **Doppelpipeline** — siehe `ARCHITECTURE.md`

**Pipeline A — Hetzner direkt:**
- Quelle: metalcharts.org (Aggregator-Mirror) + sge.com.cn (für Silber)
- URLs: `metalcharts.org/shanghai/xau`, `sge.com.cn/sjzx/shanghaiAgAuto`
- Code: `fetch_shanghai()` ab Zeile ~1206
- Methode: Playwright (Gold) + requests+BeautifulSoup (Silber)
- Source-Marker: `"metalcharts.org/sge"`

**Pipeline B — Peking direkt:**
- Quelle: sge.com.cn direkt
- URLs:
  - Gold: `sge.com.cn/sjzx/shanghaiAuAuto?start_date={today}&end_date={today}`
  - Silber: `sge.com.cn/sjzx/shanghaiAgAuto?start_date={today}&end_date={today}`
- Code: `get_sge_price()` in `scraper_china.py`
- Methode: Playwright von Peking-VPS aus
- POST: `api.goldpremium.org/ingest` mit market='shanghai'

### CCB Paper Gold (`market='ccb'`)
- **Quelle:** China Construction Bank, Account Gold (账户黄金), NGJS01
- **URL:** `gold1.ccb.com/chn/home/gold_new/hqzs/index.shtml`
- **Code:** `get_ccb_gold()` in `scraper_china.py`
- **Methode:** Playwright von Peking-VPS aus (CCB erfordert SSL Legacy Renegotiation)
- **POST:** `api.goldpremium.org/ingest` mit market='ccb'
- **Liefert:** gold_cny_gram (Verkaufspreis Bank → Kunde), gold_cny_gram_bid (Ankaufspreis)
- **Charakter:** 24/7 Bank-Retail-Preis, eigene Dynamik (kein SGE-Derivat)

---

## NORM_TABLE-Übersicht

Aus `app.py` ab Zeile ~1700. Diese Tabelle definiert, welches Feld pro Markt
in USD/oz umgerechnet wird:

```python
NORM_TABLE = {
    "istanbul":      ("gold_try_gram",     "TRY", "gram", ...),
    "india_gjc":     ("gold_inr_gram_ask", "INR", "gram", ...),
    "hongkong":      ("gold_hkd_oz_bid",   "HKD", "oz",   ...),  # ⚠️ BID!
    "lbma":          ("gold_usd_oz",       "USD", "oz",   ...),
    "hkgx":          ("gold_usd_oz_ask",   "USD", "oz",   ...),
    "australia":     ("gold_aud_kg_ask",   "AUD", "kg",   ...),
    "usa":           ("gold_usd_oz_ask",   "USD", "oz",   ...),
    "canada":        ("gold_cad_kg_ask",   "CAD", "kg",   ...),
    "switzerland":   ("gold_chf_oz_ask",   "CHF", "oz",   ...),
    "germany":       ("gold_eur_oz_ask",   "EUR", "oz",   ...),
    "russia":        ("gold_rub_gram",     "RUB", "gram", ...),
    "russia_dealer": ("gold_rub_gram",     "RUB", "gram", ...),
    "dubai":         ("gold_eur_kg_ask",   "EUR", "kg",   ...),
    "india":         ("gold_inr_gram",     "INR", "gram", ...),
    "japan":         ("gold_jpy_gram_ask", "JPY", "gram", ...),
    "shanghai":      ("gold_cny_gram",     "CNY", "gram", ...),
    "uk_royalmint":  ("gold_gbp_oz_ask",   "GBP", "oz",   ...),
}
```

Alle nutzen `ask` außer `hongkong` (`bid`) und solche ohne explizite Bid/Ask-Trennung.

---

## FX-Quellen

### Frankfurter API (primär)
- URL: `api.frankfurter.dev/v1/latest?base=USD&symbols=EUR,GBP,...`
- Quelle: ECB-Tagesfixings
- Code: `fetch_fx()` ab Zeile ~395

### Yahoo Finance (sekundär, live)
- URL: `query1.finance.yahoo.com/v8/finance/chart/EURUSD=X` etc.
- Liefert: Live-Kurse
- Code: `fetch_fx_yahoo()` ab Zeile ~460

### Backup: yesterday_fx.json
- Pfad: `/opt/goldpremium/yesterday_fx.json`
- Wird täglich (22:05 UTC) aktualisiert
- Fallback wenn beide Online-Quellen ausfallen
