// Gemeinsame Hilfsfunktionen fuer die Tests.
const fs = require('fs');
const path = require('path');

function loadPlaywright(){
  try { return require('playwright'); } catch(e) { return require('playwright-core'); }
}

// CHROMIUM_PATH nur setzen, wenn ein vorinstallierter Browser verwendet werden soll (z.B. lokale Entwicklung).
function launchOptions(){
  const o = { args: ['--no-sandbox'] };
  if (process.env.CHROMIUM_PATH) o.executablePath = process.env.CHROMIUM_PATH;
  return o;
}

function readModuleFile(rel){
  return fs.readFileSync(path.join(__dirname, '..', 'node_modules', rel), 'utf8');
}

function pearson(xs, ys){
  const n = xs.length, mx = xs.reduce((a,b)=>a+b,0)/n, my = ys.reduce((a,b)=>a+b,0)/n;
  let sxy=0, sxx=0, syy=0;
  for(let i=0;i<n;i++){ sxy+=(xs[i]-mx)*(ys[i]-my); sxx+=(xs[i]-mx)**2; syy+=(ys[i]-my)**2; }
  return sxy/Math.sqrt(sxx*syy);
}

// Liest die sichtbaren Achsenbeschriftungen des Spot-Charts: Zeitstempel (ms) und Pixelposition.
// Gilt fuer eine Zeitachse (type 'time'); bei anderen Achsentypen kommt ts=null zurueck.
const READ_AXIS = () => {
  const sc = chartSpot.scales.x;
  const ticks = [];
  sc.ticks.forEach((t,i)=>{
    if(t.label==null || t.label==='') return;
    ticks.push({ label: String(t.label), ts: sc.type==='time' ? t.value : null, px: sc.getPixelForTick(i) });
  });
  return { type: sc.type, ticks };
};

// Zaehlt Seitenfehler, ignoriert bekannte Nebengeraeusche (Netzwerk, Drittanbieter-Skripte).
const NOISE = /Failed to load resource|net::ERR|parser-blocking|An unknown error occurred when fetching the script/;

module.exports = { loadPlaywright, launchOptions, readModuleFile, pearson, READ_AXIS, NOISE };
