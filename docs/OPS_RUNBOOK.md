# GoldPremium — Operations Runbook

**Last updated:** 2026-07-24 (Cron-Zeiten health_check.py korrigiert, s.u.)
**Maintainer:** Lew

Praktisches Handbuch: konkrete Befehle für wiederkehrende Aufgaben.
Copy-Paste-tauglich.

---

## SSH-Verbindungen

### Hetzner
```bash
ssh -i ~/.ssh/id_goldpremium -p 2222 root@178.104.59.147
```

### Peking
```bash
ssh -i /Users/lew/Dokumente/Business/GoldPremium/goldpremium-key-beijing.pem root@39.96.17.131
```

---

## Service Status & Restart

### Status prüfen
```bash
systemctl status goldpremium --no-pager
systemctl is-active goldpremium
ps -o pid,etime -p $(systemctl show -p MainPID goldpremium | cut -d= -f2)
```

### Restart
```bash
systemctl restart goldpremium
sleep 3
journalctl -u goldpremium --since "2 minutes ago" --no-pager | tail -30
```

### Logs live mitlesen
```bash
journalctl -u goldpremium -f
```

### Logs der letzten Stunde
```bash
journalctl -u goldpremium --since "1 hour ago" --no-pager
```

---

## Diagnose bei Hängern

### 1. API-Status (JSON-Dump zur Sichtprüfung)
```bash
curl -s -m 5 http://localhost:5000/api/prices | python3 -m json.tool | head -50
```

**Wichtig:** Daten sind unter `data`, nicht Top-Level.

### 2. Python-Stack (zeigt wo's hängt)
```bash
PID=$(systemctl show -p MainPID goldpremium | cut -d= -f2)
py-spy dump --pid $PID
```

py-spy installieren falls fehlt:
```bash
pip install py-spy --break-system-packages
```

### 3. Offene TCP-Verbindungen
```bash
PID=$(systemctl show -p MainPID goldpremium | cut -d= -f2)
ss -tnp 2>/dev/null | grep "pid=$PID"
```

### 4. Threads des Prozesses
```bash
PID=$(systemctl show -p MainPID goldpremium | cut -d= -f2)
ps -L -o pid,tid,stat,wchan:30 -p $PID
```

### 5. Subprozesse (Chromium aus Playwright)
```bash
pstree -p $(systemctl show -p MainPID goldpremium | cut -d= -f2)
```

---

## Code-Änderung (Pattern)

### Vor jeder Änderung: Backup
```bash
cd /opt/goldpremium
TS=$(date -u +%Y%m%d_%H%M%S)
cp app.py app.py.bak.${TS}_PRE_BESCHREIBUNG
ls -la app.py.bak.${TS}*
```

### Syntax-Check nach Edit
```bash
python3 -m py_compile app.py && echo "Syntax OK" || echo "SYNTAX FEHLER!"
```

### Restart und verifizieren
```bash
systemctl restart goldpremium
sleep 5
journalctl -u goldpremium --since "1 minute ago" --no-pager | tail -20
```

### Rollback wenn nötig
```bash
cd /opt/goldpremium
ls -la app.py.bak.* | tail -5  # finde gewünschtes Backup
cp app.py.bak.YYYYMMDD_HHMMSS_PRE_X app.py
systemctl restart goldpremium
```

---

## Datenbank-Inspektion (read-only)

### Tabelle einsehen
```bash
cd /opt/goldpremium
sqlite3 prices.db "SELECT COUNT(*) FROM price_history;"
sqlite3 prices.db "SELECT market, COUNT(*) FROM price_history GROUP BY market ORDER BY 2 DESC;"
```

### Letzte Einträge eines Marktes
```bash
sqlite3 prices.db -header -column \
  "SELECT ts, market, gold_usd_oz, premium_pct FROM price_history WHERE market='switzerland' ORDER BY ts DESC LIMIT 10;"
```

### Spread CCB vs SGE (heute)
```bash
sqlite3 prices.db -header -column "
  SELECT ts, market, gold_usd_oz
  FROM price_history
  WHERE market IN ('ccb', 'shanghai')
    AND ts > datetime('now', '-1 day')
  ORDER BY ts DESC LIMIT 30;
"
```

**⚠️ Niemals INSERT/UPDATE/DELETE/ALTER ohne ausdrückliches OK.**

---

## Cache-Manipulation (im Notfall)

### Cache-Status sehen
```bash
curl -s http://localhost:5000/api/prices | python3 -m json.tool | head -30
```

### Update manuell triggern
Nicht direkt möglich (kein API-Endpoint). Alternativen:
- Warten auf nächsten 10-Min-Zyklus
- `systemctl restart goldpremium` (löst Sofort-Update aus, aber 5 Min Downtime)

---

## Backup-Übersicht

### Alle Backups auflisten
```bash
ls -la /opt/goldpremium/app.py.bak.* | sort -k9
```

### Alte Backups aufräumen (vorsichtig!)
```bash
# DRY RUN — zeigt was gelöscht würde
find /opt/goldpremium -name "app.py.bak.*" -mtime +30 -ls

# Nach OK:
# find /opt/goldpremium -name "app.py.bak.*" -mtime +30 -delete
```

**Niemals ohne ausdrückliches OK ausführen!**

---

## Monitoring & Health Check

### Tägliche Health-Check-Routine (Snapshot + Confirm)

Auf dem VPS laufen über `crontab` (root) zwei Scripte:

**⚠️ Stand ab 2026-07-22 (siehe CHANGES.md):** `TZ=`-Zeilen in der Crontab werden von
Cron NICHT für die Zeitplanberechnung genutzt (entgegen `man 5 crontab`) — zwei
TZ-Versuche (Europe/Berlin 07-15, Europe/Istanbul 07-21) hatten keinen Effekt.
Fix: feste UTC-Zeiten ohne TZ-Abhängigkeit. Ziel ist 6:30 Istanbul-Zeit
(Türkei: konstant UTC+3, keine Sommerzeit) → das entspricht **03:00/03:30 UTC**,
NICHT 08:00/08:30 UTC wie in einer älteren Version dieser Tabelle stand.
Am 2026-07-24 per tatsächlich eingegangener Mail um 6:30 Istanbul-Zeit verifiziert.

| Zeit (UTC) | Script | Aufgabe |
|------------|--------|---------|
| 03:00 | `health_check.py --snapshot` | Erste Prüfung, Probleme nach `/tmp/healthcheck_snapshot.json` schreiben — KEINE Mail. |
| 03:30 | `health_check.py --confirm` | Zweite Prüfung, mit Snapshot vergleichen. Mail **nur** für Probleme, die in **beiden** Prüfungen auftreten. Filtert transiente Aussetzer einzelner Update-Zyklen raus. |
| 23:00 | `check_chp.py` | Prüft die `*_chp_*`-Wechselkurs-Anteile (eigenständig). |

**Crontab anzeigen (empfohlen: bei jeder Unklarheit live gegenchecken, nicht nur
dieser Tabelle vertrauen):**
```bash
ssh -i ~/.ssh/id_goldpremium -p 2222 root@178.104.59.147 "crontab -l"
```

**Erwartete Einträge (Stand 2026-07-22, feste UTC-Zeiten):**
```
0  3 * * *  /opt/goldpremium/venv/bin/python /opt/goldpremium/health_check.py --snapshot >> /var/log/goldpremium-health.log 2>&1
30 3 * * *  /opt/goldpremium/venv/bin/python /opt/goldpremium/health_check.py --confirm >> /var/log/goldpremium-health.log 2>&1
0 23 * * *  /usr/bin/python3 /opt/goldpremium/check_chp.py >> /var/log/goldpremium_chp.log 2>&1
```

### Logdateien

| Pfad | Inhalt |
|------|--------|
| `/var/log/goldpremium-health.log` | Lauf-Historie + Issue-Liste pro Lauf |
| `/var/log/goldpremium_chp.log` | Output von `check_chp.py` |

**Live mitlesen:**
```bash
ssh -i ~/.ssh/id_goldpremium -p 2222 root@178.104.59.147 "tail -f /var/log/goldpremium-health.log"
```

### Manueller Aufruf (Legacy-Modus, sofortige Mail)

Ohne CLI-Argumente verhält sich `health_check.py` wie das alte Script: einmal prüfen → direkt mailen. Praktisch fürs Testen nach Änderungen.

```bash
ssh -i ~/.ssh/id_goldpremium -p 2222 root@178.104.59.147 "/opt/goldpremium/venv/bin/python /opt/goldpremium/health_check.py"
```

### SMTP-Konfiguration

Liegt in `/opt/goldpremium/health_config.json` (nicht im Repo — enthält Credentials):

```json
{
  "smtp_host": "<provider>",
  "smtp_port": 587,
  "smtp_user": "<absender>",
  "smtp_pass": "<app-passwort>"
}
```

Empfänger ist in `health_check.py` als Konstante `EMAIL_TO` festgelegt.

### Was wird geprüft?

`health_check.py` prüft:
- **API erreichbar** (`http://127.0.0.1:5000/api/prices` antwortet, Status `ok`, Daten nicht älter als 15 Min).
- **Spot/FX**: XAU & XAG im Plausibilitätsband, GC/SI vorhanden, alle 9 FX-Kurse + `XAU_chp_*` da.
- **Märkte** (18 erwartete): nicht fehlend, `is_calculated` nicht True, Gold im Band 2.000–15.000 USD/oz, Silber im Band 20–200 USD/oz (nur für die Märkte mit Silber).
- **DB**: neue Einträge in den letzten 20 Min, Größe < 800 MB.
- **SSL**: Zertifikat läuft nicht in < 14 Tagen ab.

### Snapshot/Confirm-Vergleichslogik

Probleme werden über eine **Schlüssel-Funktion** verglichen (`issue_key()`), die variable Anteile (konkrete Preise, Zeitstempel) wegnormalisiert. Beispiele:

| Issue-String | Key |
|--------------|-----|
| `Markt fehlt komplett: canada` | `market_missing:canada` |
| `hongkong: Scraper ausgefallen (is_calculated=True)` | `scraper_calc:hongkong` |
| `australia: Silber fehlt oder unplausibel (None USD/oz)` | `silver_missing:australia` |
| `COMEX Gold Futures (GC) fehlen` | `comex_gc_missing` |

Nur **identische Keys** in Snapshot + Confirm-Lauf führen zur Mail. Snapshot älter als 2 h → Fail-Safe: aktuelle Probleme werden direkt gemeldet.

---

## CHANGES.md-Eintrag-Format

Nach jeder Änderung:
```bash
cd /opt/goldpremium
TS=$(date -u +"%Y-%m-%d %H:%M UTC")
cat >> CHANGES.md << EOF

## $TS — KURZBESCHREIBUNG

**Was:** Was wurde gemacht.

**Warum:** Welches Problem gelöst.

**Wie verifiziert:** Welche Checks bestätigen den Erfolg.

**Backup:** app.py.bak.TIMESTAMP_PRE_X

**Offen:** Was noch zu tun ist (optional).

EOF
```

---

## GitHub-Sync (Working Rule)

Nach erfolgreicher Code-Änderung auf VPS:
```bash
# 1. Lokal auf dem Mac das Repo aktualisieren
cd /Users/lew/PfadZumRepo
git pull

# 2. app.py vom VPS mit lokaler version vergleichen (auf Mac)
scp -i ~/.ssh/id_goldpremium -P 2222 root@178.104.59.147:/opt/goldpremium/app.py ./app.py.fromvps
diff ./app.py.fromvps ./app.py

# 3. Wenn anders: VPS-Version übernehmen, commit, push
cp ./app.py.fromvps ./app.py
git add app.py
git commit -m "Sync from VPS: BESCHREIBUNG"
git push
```

---

## Notfall: VPS antwortet nicht

### 1. Erreichbarkeit prüfen
```bash
ping -c 4 178.104.59.147
nc -zv 178.104.59.147 2222 -w 5
curl -sI -m 10 https://goldpremium.org/ | head -5
```

### 2. Falls SSH down: Hetzner Cloud Console
- https://console.hetzner.cloud → VPS auswählen → "Console" Tab
- VNC-Konsole öffnet sich im Browser
- Login mit root + Passwort (Console-Login funktioniert auch wenn SSH tot)

### 3. fail2ban prüfen (falls SSH refused trotz VPS-Aktiv)
```bash
fail2ban-client status sshd
fail2ban-client unban --all  # nur wenn nötig!
```

### 4. SSH-Daemon-Status
```bash
systemctl status ssh
ss -tnl | grep -E ':22|:2222'
```

---

## Notfall: Service zeigt absurde Werte im Frontend

### 1. Cache prüfen (direkter Aufruf bypasst Cloudflare-Cache)
```bash
curl -s http://localhost:5000/api/prices | python3 -m json.tool | head -50
```

### 2. Wenn spot/fx leer sind: update() läuft nicht
```bash
PID=$(systemctl show -p MainPID goldpremium | cut -d= -f2)
py-spy dump --pid $PID  # zeigt wo Python hängt
```

### 3. Wenn alles im Cache gefüllt, Frontend trotzdem kaputt
- Hard-Refresh: Cmd+Shift+R
- Cloudflare-Cache purgen (Cloudflare-Dashboard)

### 4. Schadensbegrenzung: Service neustarten
```bash
systemctl restart goldpremium
```

**WARTEN.** Erste Daten nach ~3 Min, vollständig nach ~5 Min.

---

## Peking-VPS Operations

### Status scraper_china.py
```bash
ssh -i ~/...goldpremium-key-beijing.pem root@39.96.17.131
ps aux | grep scraper_china | grep -v grep
```

### Logs
Pfad/Methode: zu klären — Project-Knowledge gibt Auskunft.

### Manual run zum Testen
```bash
cd /opt/goldpremium
python3 scraper_china.py
```

---

## Dieses Runbook erweitern

Wenn du eine Aufgabe ausführst, die hier fehlt: **eintragen.**
Lieber zu viele kleine Befehle als zu wenige.

Format:
```
## Aufgabe XYZ

### Beschreibung

### Befehl
```bash
...
```

### Erwartete Ausgabe
```
