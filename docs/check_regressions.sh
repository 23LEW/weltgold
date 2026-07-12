#!/usr/bin/env bash
# GoldPremium — Regression Check Script
# Vor jedem Commit an index.html ausführen.
# Wenn ein Check fehlschlägt: STOPP, zuerst fixen.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HTML="$REPO/index.html"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

# Einfacher Pattern-Check (eine Zeile)
check() {
  local id="$1" desc="$2" file="$3" pattern="$4"
  if grep -qE "$pattern" "$file" 2>/dev/null; then
    echo -e "${GREEN}✓${NC} $id: $desc"
    ((PASS++))
  else
    echo -e "${RED}✗${NC} $id: $desc"
    echo -e "   ${YELLOW}→ Nicht gefunden: $pattern${NC}"
    ((FAIL++))
  fi
}

# Kontext-Check: Pattern muss in den 15 Zeilen NACH einem Anker-Pattern stehen
check_context() {
  local id="$1" desc="$2" file="$3" anchor="$4" pattern="$5"
  if grep -A 15 "$anchor" "$file" 2>/dev/null | grep -qE "$pattern"; then
    echo -e "${GREEN}✓${NC} $id: $desc"
    ((PASS++))
  else
    echo -e "${RED}✗${NC} $id: $desc"
    echo -e "   ${YELLOW}→ '$pattern' nicht in 15 Zeilen nach '$anchor'${NC}"
    ((FAIL++))
  fi
}

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  GoldPremium Regression Checks"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

check "R01" "verticalGridPlugin: Zeitskalen-Support (type=time)" \
  "$HTML" "xScale\.type === 'time'"

check "R02" "verticalGridPlugin: tick.value für Zeitachse" \
  "$HTML" "xScale\.ticks\.forEach"

check "R03" "borderWidth 1.5 Standard (alle Charts)" \
  "$HTML" "borderWidth:1\.5"

check "R04" "tension:0 Standard (alle Charts)" \
  "$HTML" "tension:0"

check "R05" "Compare Modal: Flex-Column Layout" \
  "$HTML" "flex-direction:column"

check "R06" "closeChartModal: try/finally für overflow" \
  "$HTML" "finally\{.*document\.body\.style\.overflow"

check_context "R07" "closeRatioModal: _exitFS() im Funktions-Block" \
  "$HTML" "function closeRatioModal" "_exitFS"

check "R08" "Async Guard renderModalChart (kein Render nach Close)" \
  "$HTML" "chart-modal.*display.*none.*return"

check "R09" "Async Guard renderCompareChart (kein Render nach Close)" \
  "$HTML" "compare-modal.*display.*none.*return"

check "R10" "Gold Premium Feld: premium_pct (nicht gold_premium_pct)" \
  "$HTML" "'premium_pct'"

check "R11" "_autoFsOnFirstTap Race Fix (display-Check vor Fullscreen)" \
  "$HTML" "modal\.style\.display!=='none'"

check "R12" "Compare Range Buttons: ctrl-btn Klasse" \
  "$HTML" 'class="ctrl-btn[^"]*"[^>]*id="rbtn-cmp-1d"'

check "R13" "Chart-Höhe 65vh Standard (Modal + Compare)" \
  "$HTML" "65vh,760px"

check "R14" "Silver Premium Feld: silver_premium_pct" \
  "$HTML" "silver_premium_pct"

check "R15" "Compare X-Grid: immer sichtbar (display:true, nicht isShort)" \
  "$HTML" "grid:\{display:true,color:'rgba\(255,255,255,0\.2\)',lineWidth:1\}\}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$FAIL" -eq 0 ]; then
  echo -e "  ${GREEN}ALLE $PASS CHECKS BESTANDEN${NC} — Commit OK"
else
  echo -e "  ${RED}$FAIL CHECK(S) FEHLGESCHLAGEN${NC} — ${YELLOW}STOPP: erst fixen!${NC}"
  echo "  → docs/REGRESSION_CHECKS.md"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

exit $FAIL
