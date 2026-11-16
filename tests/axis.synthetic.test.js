// Test 1 (deterministisch, ohne Netz): Die Spot-Charts der index.html im Repo-Stamm werden mit synthetischen
// Daten in drei Dichten (Monat/Tag/Stunde, wie auf der Live-Seite) gerendert.
// Geprueft wird: 1M/1Y/5Y/Max = echte Zeitachse mit Pixelabstand proportional zur Zeit, 1D/5D unveraendert Kategorieachse,
// Zoom laedt Detaildaten, keine neuen Seitenfehler.
// Regel: Jeder gefundene Fehler wird hier als zusaetzlicher Test festgeschrieben.
const fs = require('fs');
const path = require('path');
const { loadPlaywright, launchOptions, readModuleFile, pearson, READ_AXIS, NOISE } = require('./helpers');
const { chromium } = loadPlaywright();

// Die Seite prueft ihre fremden Skripte per integrity (SRI). Der Test liefert eigene Dateien aus (node_modules, andere Version),
// daher werden die integrity-Attribute hier entfernt; die echten Pruefsummen prueft der Live-Test gegen die ausgelieferte Seite.
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8').replace(/ integrity="[^"]*"/g, '');
const chartjs = readModuleFile('chart.js/dist/chart.umd.js');
const adapter = readModuleFile('chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.js');

function makeHist(){
  const rows = []; const iso = t => new Date(t).toISOString();
  for(let y=1960; y<2000; y++) for(let m=0; m<12; m++){ const t = Date.UTC(y, m, 15); rows.push({date: iso(t).slice(0,10), gold: 35 + (y-1960)*8 + m}); }
  for(let t=Date.UTC(2000,0,3); t<Date.UTC(2026,2,1); t+=86400000){ const dow=new Date(t).getUTCDay(); if(dow===0||dow===6) continue; rows.push({date: iso(t).slice(0,10), gold: 300 + (t-Date.UTC(2000,0,1))/86400000*0.3}); }
  const end = Date.now();
  for(let t=Date.UTC(2026,2,1); t<end; t+=3600000){ const d=new Date(t); const dow=d.getUTCDay(); if(dow===6||(dow===0&&d.getUTCHours()<23)) continue; rows.push({date: iso(t).slice(0,10), ts: iso(t).slice(0,19)+'Z', gold: 4000 + Math.sin(t/3e8)*200}); }
  return rows;
}

(async()=>{
  const browser = await chromium.launch(launchOptions());
  // Service Worker blocken: sein Skript (sw.js) wuerde sonst am Mocking vorbei ins echte Netz gehen (in GitHub: 403)
  const context = await browser.newContext({ viewport:{width:1400,height:1000}, serviceWorkers:'block' });
  const page = await context.newPage();
  const errs = [];
  page.on('pageerror', e=>errs.push('PAGEERROR: '+String(e).slice(0,200)));
  page.on('console', m=>{ if(m.type()==='error') errs.push('console: '+m.text().slice(0,200)); });
  const detail = [];
  const t0 = Date.now()-6*86400000;
  for(let t=t0; t<t0+86400000*4; t+=600000) detail.push([new Date(t).toISOString().slice(0,19), 4000+Math.sin(t/1e7)*30]);
  await page.route('**/*', async route=>{
    const u = route.request().url();
    if(u.includes('chart.umd.min.js')) return route.fulfill({contentType:'application/javascript', body: chartjs});
    if(u.includes('chartjs-adapter-date-fns')) return route.fulfill({contentType:'application/javascript', body: adapter});
    if(u==='https://goldpremium.org/') return route.fulfill({contentType:'text/html', body: html});
    if(u.includes('/api/long-history') && u.includes('&from=')) return route.fulfill({json:{data: detail}});
    if(u.includes('/api/prices')) return route.fulfill({json:{status:'ok',data:{spot:{XAU:4015.6,XAG:54.2}}}});
    if(u.startsWith('https://api.goldpremium.org') || u.includes('frankfurter') || u.includes('gold-api.com')) return route.fulfill({json:{data:[]}});
    return route.fulfill({status:200, contentType:'application/javascript', body:''});
  });
  await page.goto('https://goldpremium.org/', {waitUntil:'domcontentloaded'});
  await page.waitForTimeout(4000);
  const hist = makeHist();
  let failed = 0;
  const check = (ok, msg, extra) => { console.log(`${ok?'OK  ':'FAIL'} ${msg}`); if(!ok){ failed++; if(extra) console.log('     ', extra); } };

  for(const metalCode of ['XAU','XAG']){
    for(const range of ['1M','1Y','5Y','MAX']){
      const r = await page.evaluate(({hist, range, metalCode, fn})=>{
        histData = hist.map(d=>({...d, silver: d.gold/80})); currentRange = range; metal = metalCode; renderSpotChart();
        return (new Function('return ('+fn+')()'))();
      }, {hist, range, metalCode, fn: READ_AXIS.toString()});
      const pts = r.ticks.filter(t=>t.ts!=null);
      const corr = pts.length>2 ? pearson(pts.map(p=>p.ts), pts.map(p=>p.px)) : NaN;
      check(r.type==='time' && corr>0.999, `${metalCode} ${range}: Zeitachse, Beschriftungen=${pts.length}, Korrelation Pixel~Zeit=${isNaN(corr)?'n/a':corr.toFixed(4)} (Soll > 0.999)`,
            r.ticks.map(p=>p.label+'@'+Math.round(p.px)+'px').join(' | '));
    }
  }
  for(const range of ['1D','5D']){
    const r = await page.evaluate(({hist, range})=>{ histData=hist; currentRange=range; metal='XAU'; renderSpotChart(); return { type: chartSpot.scales.x.type, n: chartSpot.data.labels.length }; }, {hist, range});
    check(r.type==='category' && r.n>0, `${range}: Kategorieachse unveraendert (Typ ${r.type}, ${r.n} Punkte)`);
  }
  const zoom = await page.evaluate(async (hist)=>{
    histData = hist; currentRange='1Y'; metal='XAU'; renderSpotChart();
    const now = Date.now();
    window._spotZoomBusy = false; window._spotZoomData = null;
    try { spotZoomComplete({ scales:{ x:{ min: now-8*86400000, max: now-3*86400000 } } }, getFilteredHistory()); } catch(e){ return {error:String(e)}; }
    await new Promise(r=>setTimeout(r,1500));
    return { loaded: Array.isArray(window._spotZoomData) && window._spotZoomData.length>0 };
  }, hist);
  check(zoom.loaded && !zoom.error, `Zoom auf 5-Tage-Fenster laedt Detaildaten ${JSON.stringify(zoom)}`);
  const real = errs.filter(e=>!NOISE.test(e));
  check(real.length===0, `Keine Seitenfehler (ohne bekannte Nebengeraeusche): ${real.length}`, real.slice(0,5).join('\n      '));
  await browser.close();
  console.log(failed ? `\n${failed} Pruefung(en) FEHLGESCHLAGEN` : '\nAlle Pruefungen bestanden');
  process.exit(failed?1:0);
})();
