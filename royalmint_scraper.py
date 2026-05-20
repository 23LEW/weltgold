"""
Royal Mint (UK) Scraper
=======================
GOLD: aus der 1kg-Goldbarren-Produktseite (Verkaufspreis pro kg -> GBP/oz).
      ask = Barren-Produktpreis (Kunde kauft; ueber Spot, VAT-frei).
      bid = dataLayer "sell".gold (Rueckkaufpreis der Royal Mint; nahe/unter Spot).

SILBER: vorerst aus dem dataLayer derselben Seite (Spot-nahe Werte).
      ask = dataLayer "buy".silver, bid = dataLayer "sell".silver.
      HINWEIS: Das ist NICHT der echte 1kg-Silberbarren-Verkaufspreis. UK-Silber
      enthaelt 20% VAT und hat eine komplexe Preisstruktur (Hauptseite != Warenkorb).
      1kg-Silber mit VAT-Entscheidung ist als eigener Schritt offen (siehe OPEN_TASKS).

Frueher (vor 2026-05): Scraper las /invest/ (Rueckkauf-Spot) faelschlich als
Gold-Produktpreis -> Gold zeigte negatives Premium. Behoben durch Umstieg auf
die 1kg-Barren-Produktseite.
"""

import re
import json
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}

GOLD_URL = "https://www.royalmint.com/invest/bullion/bullion-bars/gold-bars/bvmgbb-royal-mint-1kg-gold-bullion-bar/"
OZ_PER_KG = 32.1507  # troy ounces per kilogram


def fetch_uk_royalmint():
    """Returns dict mit gold/silver gbp_oz ask+bid, oder None bei Fehler."""
    try:
        r = requests.get(GOLD_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        html = r.text

        # Gold-Barren-Produktpreis: groesster £-Wert >= 50000 (= der 1kg-Goldpreis)
        candidates = []
        for s in re.findall(r'£\s*([\d,]+(?:\.\d{2})?)', html):
            try:
                v = float(s.replace(",", ""))
                if v >= 50000:
                    candidates.append(v)
            except ValueError:
                pass
        gold_kg = max(candidates) if candidates else None

        # dataLayer: Rueckkauf-Spot fuer gold (bid) + silber (ask/bid vorlaeufig)
        market = None
        m = re.search(r'"market"\s*:\s*(\{"buy":\{[^}]+\},"sell":\{[^}]+\}\})', html)
        if m:
            try:
                market = json.loads(m.group(1))
            except Exception:
                market = None

        if not gold_kg:
            print("RoyalMint: 1kg-Goldbarren-Preis nicht gefunden")
            return None

        result = {
            "gold_gbp_oz_ask": round(gold_kg / OZ_PER_KG, 2),
            "source": "royalmint.com",
            "is_calculated": False,
        }
        if market:
            sell = market.get("sell", {})
            buy = market.get("buy", {})
            if sell.get("gold"):
                result["gold_gbp_oz_bid"] = round(float(sell["gold"]), 2)
            # Silber vorerst aus dataLayer (Spot-nah)
            if buy.get("silver"):
                result["silver_gbp_oz_ask"] = round(float(buy["silver"]), 2)
            if sell.get("silver"):
                result["silver_gbp_oz_bid"] = round(float(sell["silver"]), 2)

        # Plausibilitaets-Check: ask sollte ueber bid liegen
        if result.get("gold_gbp_oz_bid") and result["gold_gbp_oz_ask"] < result["gold_gbp_oz_bid"]:
            print(f"RoyalMint WARN: gold ask {result['gold_gbp_oz_ask']} < bid {result['gold_gbp_oz_bid']} "
                  f"-> Produktpreis-Extraktion pruefen!")

        print(f"RoyalMint 1kg: gold ask={result.get('gold_gbp_oz_ask')} bid={result.get('gold_gbp_oz_bid')} | "
              f"silver(dataLayer) ask={result.get('silver_gbp_oz_ask')} bid={result.get('silver_gbp_oz_bid')} GBP/oz")
        return result
    except Exception as e:
        print(f"RoyalMint error: {e}")
        return None


if __name__ == "__main__":
    print(json.dumps(fetch_uk_royalmint(), indent=2))
