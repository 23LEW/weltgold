// Test 2 (gegen die echte Live-Seite mit echten Daten): laedt https://goldpremium.org/, schaltet alle Zeitbereiche
// und beide Metalle durch und prueft die Achsen des Spot-Charts. Laeuft nach jedem Deploy und taeglich.
// Aufruf: node tests/live.smoke.test.js [URL]
const { loadPlaywright, launchOptions, pearson, READ_AXIS, NOISE } = require('./helpers');
const { chromium } = loadPlaywright();
const URL_ = process.argv[2] || process.env.SITE_URL || 'https://goldpremium.org/';

(async()=>{
  const browser = await chromium.launch(launchOptions());
  const page = await browser.newPage({ viewport:{width:1400,height:1000} });
  const errs = [];
  page.on('pageerror', e=>errs.push('PAGEERROR: '+String(e).slice(0,200)));
  page.on('console', m=>{ if(m.type()==='error') errs.push('console: '+m.text().slice(0,200)); });
  let failed = 0;
  const check = (ok, msg, extra) => { console.log(`${ok?'OK  ':'FAIL'} ${msg}`); if(!ok){ failed++; if(extra) console.log('     ', extra); } };

  const resp = await page.goto(URL_, {waitUntil:'domcontentloaded', timeout:60000});
  check(resp && resp.status()===200, `Seite erreichbar (HTTP ${resp && resp.status()})`);
  await page.waitForTimeout(8000);
  const ready = await page.evaluate(()=> typeof chartSpot!=='undefined' && !!chartSpot && chartSpot.data && chartSpot.data.labels.length>0);
  check(ready, 'Spot-Chart ist vorhanden und enthaelt Daten');

  if(ready){
    for(const metalCode of ['XAU','XAG']){
      await page.evaluate((m)=>{ metal = m; }, metalCode);
      for(const range of ['1D','5D','1M','1Y','5Y','MAX']){
        await page.evaluate(({range})=>{ const b=[...document.querySelectorAll('.range-btns .rbtn')].find(x=>x.getAttribute('onclick')&&x.getAttribute('onclick').includes("'"+range+"'")); setRange(range, b); }, {range});
        await page.waitForTimeout(2500);
        const r = await page.evaluate(`(${READ_AXIS.toString()})()`);
        const n = await page.evaluate(()=>chartSpot.data.labels.length);
        if(range==='1D' || range==='5D'){
          check(n>0, `${metalCode} ${range}: Chart hat ${n} Punkte`);
        } else {
          const pts = r.ticks.filter(t=>t.ts!=null);
          const corr = pts.length>2 ? pearson(pts.map(p=>p.ts), pts.map(p=>p.px)) : NaN;
          check(r.type==='time' && n>0 && corr>0.999, `${metalCode} ${range}: Zeitachse, ${n} Punkte, Korrelation Pixel~Zeit=${isNaN(corr)?'n/a':corr.toFixed(4)} (Soll > 0.999)`,
                r.ticks.map(p=>p.label+'@'+Math.round(p.px)+'px').join(' | '));
        }
      }
    }
  }
  const real = errs.filter(e=>!NOISE.test(e));
  check(real.length===0, `Keine Seitenfehler im Browser: ${real.length}`, real.slice(0,5).join('\n      '));
  await browser.close();
  console.log(failed ? `\n${failed} Pruefung(en) FEHLGESCHLAGEN` : '\nAlle Pruefungen bestanden');
  process.exit(failed?1:0);
})();
