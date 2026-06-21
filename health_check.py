"""Daily health check for goldpremium.org.

Modi (per CLI-Argument):
  --snapshot   : Checks ausfuehren, Probleme in /tmp/healthcheck_snapshot.json schreiben, KEINE Mail.
  --confirm    : Checks ausfuehren, mit Snapshot vergleichen, nur Probleme melden, die in BEIDEN auftreten.
  (ohne Argument): Wie frueher — direkte Mail bei jedem Problem. Backwards-compat fuer manuelle Aufrufe.

Cron-Setup:
  0 8  * * *  python health_check.py --snapshot
  30 8 * * *  python health_check.py --confirm
"""
import json, requests, sqlite3, smtplib, sys, os, re
from datetime import datetime
from email.mime.text import MIMEText

CONFIG_PATH    = "/opt/goldpremium/health_config.json"
API_URL        = "http://127.0.0.1:5000/api/prices"
DB_PATH        = "/opt/goldpremium/prices.db"
EMAIL_TO       = "larsenwolff@posteo.de"
SNAPSHOT_PATH  = "/tmp/healthcheck_snapshot.json"
SNAPSHOT_MAX_AGE_SEC = 7200  # 2h - aelter -> ignorieren (z.B. nach Server-Neustart)

EXPECTED_MARKETS = [
    'lbma','shanghai','india','india_gjc','india_mumbai',
    'istanbul','japan','germany','switzerland','uk_royalmint',
    'dubai','hongkong','hkgx',
    'australia','canada','usa','russia','russia_dealer'
]
SILVER_MARKETS = [
    'india','india_gjc','india_mumbai','istanbul','shanghai',
    'australia','germany','switzerland','uk_royalmint',
    'dubai','usa','canada','russia','russia_dealer','lbma'
]
FX_CURRENCIES = ['EUR','GBP','JPY','CNY','TRY','INR','AUD','HKD','CHF']
GOLD_MIN,  GOLD_MAX   = 2000, 15000
SILVER_MIN,SILVER_MAX = 20,   200

def check_api():
    issues = []
    try:
        resp = requests.get(API_URL, timeout=15)
        data = resp.json()
    except Exception as e:
        return [f"API nicht erreichbar: {e}"]

    if data.get('status') != 'ok':
        return [f"API Status: {data.get('status')} — noch am Laden oder abgestürzt"]

    try:
        lu  = datetime.fromisoformat(data['last_updated'])
        age = (datetime.now() - lu).total_seconds() / 60
        if age > 15:
            issues.append(f"Daten veraltet: letzte Aktualisierung vor {age:.0f} Minuten")
    except:
        issues.append("last_updated nicht lesbar")

    markets = data.get('data', {})
    spot    = markets.get('spot', {})
    fx      = markets.get('fx',   {})

    xau = spot.get('XAU')
    xag = spot.get('XAG')
    if not xau or not (GOLD_MIN < xau < GOLD_MAX):
        issues.append(f"XAU Preis unplausibel: {xau} USD/oz")
    if not xag or not (SILVER_MIN < xag < SILVER_MAX):
        issues.append(f"XAG Preis unplausibel: {xag} USD/oz (erwartet {SILVER_MIN}–{SILVER_MAX})")

    if not spot.get('GC'):
        issues.append("COMEX Gold Futures (GC) fehlen")
    if not spot.get('SI'):
        issues.append("COMEX Silber Futures (SI) fehlen")

    for cur in FX_CURRENCIES:
        if spot.get(f"XAU_chp_{cur.lower()}") is None:
            issues.append(f"Währungs-% fehlt: XAU_chp_{cur.lower()}")
            break  # nur einmal melden wenn generell kaputt

    for cur in FX_CURRENCIES:
        if not fx.get(cur):
            issues.append(f"FX Rate fehlt: {cur}")

    for market in EXPECTED_MARKETS:
        if market not in markets:
            issues.append(f"Markt fehlt komplett: {market}")
            continue
        m    = markets[market]
        gold = m.get('gold_usd_oz')
        if m.get('is_calculated'):
            issues.append(f"{market}: Scraper ausgefallen (is_calculated=True)")
        elif not gold or not (GOLD_MIN < gold < GOLD_MAX):
            issues.append(f"{market}: Gold-Preis unplausibel ({gold} USD/oz)")

    for market in SILVER_MARKETS:
        if market not in markets:
            continue
        silver = markets[market].get('silver_usd_oz')
        if not silver or not (SILVER_MIN < silver < SILVER_MAX):
            issues.append(f"{market}: Silber fehlt oder unplausibel ({silver} USD/oz)")

    return issues

def check_db():
    issues = []
    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("SELECT COUNT(*) FROM price_history WHERE ts >= datetime('now','-20 minutes')")
        if c.fetchone()[0] == 0:
            issues.append("Keine neuen DB-Einträge in 20 Minuten — Scraper-Loop gestoppt?")
        c.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
        row = c.fetchone()
        if row:
            mb = row[0] / 1024 / 1024
            if mb > 800:
                issues.append(f"DB-Größe kritisch: {mb:.0f} MB")
        conn.close()
    except Exception as e:
        issues.append(f"DB nicht erreichbar: {e}")
    return issues

def check_ssl():
    import ssl, socket
    issues = []
    try:
        ctx  = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname='goldpremium.org') as s:
            s.settimeout(10)
            s.connect(('goldpremium.org', 443))
            cert    = s.getpeercert()
            expires = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
            days    = (expires - datetime.utcnow()).days
            if days < 14:
                issues.append(f"SSL-Zertifikat läuft in {days} Tagen ab!")
    except Exception as e:
        issues.append(f"SSL-Check fehlgeschlagen: {e}")
    return issues

def issue_key(issue):
    """Extrahiert einen vergleichbaren Schluessel aus einem Issue-String.

    Damit aendernde Detailwerte (z.B. konkrete Preise) den Vergleich
    Snapshot vs. Confirm nicht stoeren — wir vergleichen die Problem-Kategorie
    + den betroffenen Markt/Wert, nicht den vollen Text.
    """
    if issue.startswith("Markt fehlt komplett: "):
        return "market_missing:" + issue.split(": ", 1)[1].strip()
    if ": Scraper ausgefallen" in issue:
        return "scraper_calc:" + issue.split(":", 1)[0].strip()
    if ": Gold-Preis unplausibel" in issue:
        return "gold_implausible:" + issue.split(":", 1)[0].strip()
    if ": Silber fehlt oder unplausibel" in issue:
        return "silver_missing:" + issue.split(":", 1)[0].strip()
    if issue.startswith("COMEX Gold Futures"):
        return "comex_gc_missing"
    if issue.startswith("COMEX Silber Futures"):
        return "comex_si_missing"
    if issue.startswith("Daten veraltet"):
        return "data_stale"
    if issue.startswith("XAU Preis unplausibel"):
        return "xau_implausible"
    if issue.startswith("XAG Preis unplausibel"):
        return "xag_implausible"
    if issue.startswith("FX Rate fehlt: "):
        return "fx_missing:" + issue.split(": ", 1)[1].strip()
    if issue.startswith("Währungs-% fehlt:"):
        return "chp_missing"
    if "SSL" in issue:
        return "ssl_issue"
    if "DB" in issue or "Scraper-Loop" in issue:
        return "db_issue"
    if issue.startswith("API"):
        return "api_unreachable"
    return "other:" + issue.strip()[:50]

def write_snapshot(issues):
    snap = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "issues": issues,
    }
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"Snapshot geschrieben: {SNAPSHOT_PATH} ({len(issues)} Issue(s))")

def read_snapshot():
    if not os.path.exists(SNAPSHOT_PATH):
        return None, "kein Snapshot vorhanden"
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        ts = datetime.fromisoformat(snap["ts"].rstrip("Z"))
        age = (datetime.utcnow() - ts).total_seconds()
        if age > SNAPSHOT_MAX_AGE_SEC:
            return None, f"Snapshot zu alt ({age/60:.0f} Min)"
        return snap, None
    except Exception as e:
        return None, f"Snapshot nicht lesbar: {e}"

def send_email(issues, ok=False, mode_hint=""):
    try:
        cfg = json.load(open(CONFIG_PATH))
    except Exception as e:
        print(f"Config nicht lesbar: {e}")
        return

    if ok:
        body    = "Goldpremium.org Health Check — Alle Checks bestanden ✓\n\n"
        body   += f"Märkte geprüft: {len(EXPECTED_MARKETS)}\n"
        body   += f"FX-Kurse geprüft: {len(FX_CURRENCIES)}\n"
        if mode_hint:
            body += f"Modus: {mode_hint}\n"
        body   += f"\nZeitstempel: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        body   += "\nAPI: http://178.104.59.147:5000/api/prices"
        subject = "[Goldpremium] ✅ Alle Checks OK"
    else:
        body  = f"Goldpremium.org Health Check — {len(issues)} Problem(e) gefunden:\n\n"
        body += "\n".join(f"  {i+1}. {p}" for i,p in enumerate(issues))
        if mode_hint:
            body += f"\n\nModus: {mode_hint}"
        body += f"\n\nZeitstempel: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        body += "\n\nAPI: http://178.104.59.147:5000/api/prices"
        subject = f"[Goldpremium] ⚠️ {len(issues)} Problem(e) gefunden"

    msg             = MIMEText(body, "plain", "utf-8")
    msg["Subject"]  = subject
    msg["From"]     = cfg["smtp_user"]
    msg["To"]       = EMAIL_TO

    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as s:
            s.starttls()
            s.login(cfg["smtp_user"], cfg["smtp_pass"])
            s.sendmail(cfg["smtp_user"], [EMAIL_TO], msg.as_string())
        print("Email gesendet.")
    except Exception as e:
        print(f"Email-Fehler: {e}")
        with open("/var/log/goldpremium-health.log", "a") as f:
            f.write(f"\n[{datetime.now()}] EMAIL FEHLGESCHLAGEN: {e}\n{body}\n")

def run_checks():
    """Alle Checks ausfuehren, Issue-Liste zurueckgeben."""
    return check_api() + check_db() + check_ssl()

if __name__ == "__main__":
    mode = None
    for arg in sys.argv[1:]:
        if arg in ("--snapshot", "--confirm"):
            mode = arg

    print(f"[{datetime.now()}] Health Check startet (mode={mode or 'legacy'})...")
    issues = run_checks()

    if mode == "--snapshot":
        # Snapshot speichern, KEINE Mail
        if issues:
            print(f"Snapshot: {len(issues)} Issue(s) gefunden — werden in 30 Min nochmal geprueft.")
            for p in issues: print(f"  - {p}")
        else:
            print("Snapshot: Keine Issues.")
        write_snapshot(issues)
        with open("/var/log/goldpremium-health.log", "a") as f:
            f.write(f"[{datetime.now()}] SNAPSHOT: {len(issues)} issue(s)\n")
        sys.exit(0)

    if mode == "--confirm":
        snap, snap_err = read_snapshot()
        if snap is None:
            # Kein gueltiger Snapshot -> Fail-Safe: aktuelle Issues direkt melden
            print(f"Confirm: kein gueltiger Snapshot ({snap_err}) -> Fallback auf direkten Alarm")
            if issues:
                send_email(issues, mode_hint=f"Confirm-Fallback ({snap_err})")
            else:
                send_email([], ok=True, mode_hint=f"Confirm-Fallback ({snap_err})")
            sys.exit(0)
        snap_keys     = {issue_key(p) for p in snap["issues"]}
        current_keys  = {issue_key(p) for p in issues}
        persistent    = snap_keys & current_keys
        transient_snap = snap_keys - current_keys
        transient_now  = current_keys - snap_keys
        # Persistente Issues: die aktuelle Auspraegung melden (nicht die alte)
        persistent_issues = [p for p in issues if issue_key(p) in persistent]
        print(f"Confirm: Snapshot={len(snap_keys)} jetzt={len(current_keys)} persistent={len(persistent)}")
        if transient_snap:
            print(f"  -> {len(transient_snap)} Issue(s) inzwischen verschwunden (transient): {sorted(transient_snap)}")
        if transient_now:
            print(f"  -> {len(transient_now)} Issue(s) jetzt neu (kein Alarm, da nicht im Snapshot): {sorted(transient_now)}")
        if persistent_issues:
            print(f"  -> {len(persistent_issues)} Issue(s) bestaetigt -> Mail")
            for p in persistent_issues: print(f"    - {p}")
            send_email(persistent_issues, mode_hint="Confirm (Snapshot + Recheck nach 30 Min)")
        else:
            print("  -> Keine bestaetigten Issues -> OK-Mail")
            send_email([], ok=True, mode_hint="Confirm (Snapshot + Recheck nach 30 Min)")
        with open("/var/log/goldpremium-health.log", "a") as f:
            f.write(f"[{datetime.now()}] CONFIRM: persistent={len(persistent_issues)} transient_snap={len(transient_snap)} transient_now={len(transient_now)}\n")
        sys.exit(0)

    # Legacy: keine Flag -> direkt mailen wie frueher
    if issues:
        print(f"PROBLEME ({len(issues)}):")
        for p in issues: print(f"  - {p}")
        send_email(issues)
    else:
        print("Alle Checks OK ✓")
        send_email([], ok=True)
        with open("/var/log/goldpremium-health.log", "a") as f:
            f.write(f"[{datetime.now()}] OK\n")
