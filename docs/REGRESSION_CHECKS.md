# GoldPremium — Regression Checks

**Zweck:** Diese Datei listet alle kritischen Fixes in `index.html`, die nach jeder
Änderung noch vorhanden sein MÜSSEN. Vor jedem Commit an `index.html` MUSS
`docs/check_regressions.sh` ausgeführt werden.

**Für Claude:** Dieses Dokument MUSS am Anfang jeder Session, die index.html anfasst,
gelesen werden. Vor jedem Commit: Skript ausführen, Fehler = STOPP.

---

## Aktive Checks

| # | Fix | Grep-Pattern | Datei |
|---|-----|-------------|-------|
| R01 | verticalGridPlugin: Zeitskalen-Support | `xScale.type === 'time'` | index.html |
| R02 | verticalGridPlugin: tick.value für Zeitachse | `xScale.ticks.forEach` | index.html |
| R03 | borderWidth 1.5 Standard (alle Charts) | `borderWidth:1.5` | index.html |
| R04 | tension:0 Standard (alle Charts) | `tension:0` | index.html |
| R05 | Compare Modal: Flex-Column Layout | `flex-direction:column` | index.html |
| R06 | closeChartModal: try/finally für overflow | `finally{ document.body.style.overflow` | index.html |
| R07 | closeRatioModal: _exitFS() Aufruf | `closeRatioModal` | index.html (+ _exitFS im selben Block) |
| R08 | Async Guard renderModalChart | `chart-modal.*display.*none.*return\|display.*none.*return.*renderModal` | index.html |
| R09 | Async Guard renderCompareChart | `compare-modal.*display.*none.*return\|display.*none.*return.*renderCompare` | index.html |
| R10 | Premium-Feld korrekt: premium_pct (nicht gold_premium_pct) | `premium_pct` | index.html |
| R11 | _autoFsOnFirstTap Race Fix | `modal.style.display!==` | index.html |
| R12 | Chart-Buttons: ctrl-btn (nicht rbtn) | `ctrl-btn.*rbtn-cmp-1d\|id="rbtn-cmp-1d".*ctrl-btn` | index.html |
| R13 | Chart-Höhe 65vh (Modal + Compare) | `65vh,760px` | index.html |
| R14 | Silver Premium Feld: silver_premium_pct | `silver_premium_pct` | index.html |
| R15 | GVS Dubai Silver: AED-Parsing | `AED.*6000.*20000\|6000.*_sval.*20000` | app.py |

---

## Wie zu benutzen

```bash
# Vor jedem Commit:
bash docs/check_regressions.sh
```

Wenn alle Checks grün: committen.
Wenn ein Check rot: STOPP — zuerst fixen, dann committen.

---

## Neue Fixes eintragen

Immer wenn ein Bug zum zweiten Mal aufgetaucht ist (d.h. ein Fix wurde versehentlich
revertiert), MUSS ein neuer Eintrag in diese Tabelle. Format:

```
| R## | Kurzbeschreibung | `grep-pattern` | Datei |
```

Dann `check_regressions.sh` entsprechend ergänzen.

---

## Bekannte harmlose Fehlalarme

*(noch keine)*
