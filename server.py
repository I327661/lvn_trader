"""
Render-compatible wrapper for LVN alert.py.
- Runs trading loop in a background thread
- Exposes /token to update Fyers token daily
- Exposes /levels to edit zones each day
- Exposes /prices for chart data (price history, zones, events, trade state)
- Exposes / for the dark-theme dashboard with live chart
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from flask import Flask, request, redirect, jsonify

LEVELS_FILE  = Path(__file__).resolve().parent / "levels" / "lvn_levels.json"
TOKEN_FILE   = Path(__file__).resolve().parent / ".fyers_token"

def _load_persisted_token() -> str:
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text().strip()
        if t:
            print("[INFO] Loaded token from .fyers_token")
            return t
    return ""

# ── dashboard HTML ─────────────────────────────────────────────────────────────
DASHBOARD_HTML = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LVN Trader — CRUDEOIL</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#090c12;--bg2:#0d1117;
  --border:#1e2a38;--border2:#2a3a50;
  --text:#e2e8f0;--dim:#6e8098;--dim2:#435060;
  --green:#3fd68a;--red:#ff5c5c;--amber:#f2b33d;--blue:#5aa9ff;--cyan:#4fd1c5;
  --font:"Inter","Segoe UI",system-ui,sans-serif;
  --mono:"JetBrains Mono","Cascadia Code",Consolas,monospace;
}
html{color-scheme:dark}
body{background:var(--bg);color:var(--text);font-family:var(--font);height:100vh;display:flex;flex-direction:column;overflow:hidden;font-variant-numeric:tabular-nums;font-size:14px}
.bg-glow{position:fixed;inset:0;z-index:-1;pointer-events:none;background:radial-gradient(ellipse 1000px 500px at 15% -5%,rgba(90,169,255,.07),transparent 55%),radial-gradient(ellipse 800px 600px at 90% 110%,rgba(160,120,255,.05),transparent 55%),var(--bg)}

/* header */
header{display:flex;align-items:center;gap:14px;padding:7px 16px;background:linear-gradient(180deg,#111a24,#0c1119);border-bottom:1px solid var(--border);flex-shrink:0}
.brand{font-weight:800;letter-spacing:2px;font-size:12px;white-space:nowrap;background:linear-gradient(90deg,var(--blue),var(--cyan));-webkit-background-clip:text;background-clip:text;color:transparent}
.price-box{display:flex;align-items:baseline;gap:7px}
#price-val{font-size:24px;font-weight:800;letter-spacing:-.5px;line-height:1}
#price-chg{font-size:12px;font-weight:600;transition:color .3s}
.up{color:var(--green)}.down{color:var(--red)}
.tbadge{padding:3px 11px;border-radius:6px;font-size:11px;font-weight:800;letter-spacing:1px;transition:all .3s}
.tbadge.flat{background:#141d28;color:var(--dim);border:1px solid var(--border)}
.tbadge.ce{background:#0d211a;color:var(--green);border:1px solid var(--green);box-shadow:0 0 14px rgba(63,214,138,.25);animation:pgce 2s infinite}
.tbadge.pe{background:#221215;color:var(--red);border:1px solid var(--red);box-shadow:0 0 14px rgba(255,92,92,.25);animation:pgpe 2s infinite}
@keyframes pgce{0%,100%{box-shadow:0 0 8px rgba(63,214,138,.2)}50%{box-shadow:0 0 22px rgba(63,214,138,.45)}}
@keyframes pgpe{0%,100%{box-shadow:0 0 8px rgba(255,92,92,.2)}50%{box-shadow:0 0 22px rgba(255,92,92,.45)}}
#hdr-time{font-size:11px;color:var(--dim);font-family:var(--mono)}
.feed-dot{width:7px;height:7px;border-radius:50%;background:var(--dim2);margin-left:auto}
.feed-dot.live{background:var(--green);animation:fdp 1.8s infinite}
.feed-dot.dead{background:var(--red)}
.feed-dot.stale{background:var(--amber)}
@keyframes fdp{0%{box-shadow:0 0 0 0 rgba(63,214,138,.6)}100%{box-shadow:0 0 0 7px rgba(63,214,138,0)}}

/* layout */
main{flex:1;display:flex;min-height:0}
.chart-pane{flex:1;position:relative;min-width:0}
#chart{width:100%;height:100%}
.side{width:288px;flex-shrink:0;border-left:1px solid var(--border);display:flex;flex-direction:column;overflow-y:auto;background:var(--bg2)}

/* cards */
.card{border-bottom:1px solid var(--border);padding:11px 13px}
.ctitle{font-size:10px;font-weight:800;letter-spacing:1.2px;color:var(--dim);text-transform:uppercase;margin-bottom:8px}
.cdim{font-size:12px;color:var(--dim)}

/* trade card */
.tcard-ce{background:#0a1c14}.tcard-pe{background:#1c0b0d}
.tgrid{display:grid;grid-template-columns:66px 1fr;gap:4px 0;font-size:12px}
.tlabel{color:var(--dim);font-size:11px}
.tval{font-weight:600}
.tgreen{color:var(--green)}.tred{color:var(--red)}.tamber{color:var(--amber)}
.lbadge{display:inline-block;font-size:9px;padding:1px 6px;border-radius:4px;font-weight:800;letter-spacing:.4px}
.lbadge.bk{background:#0d211a;color:var(--green);border:1px solid var(--green)}
.lbadge.op{background:#141d28;color:var(--dim);border:1px solid var(--border)}

/* zones */
.zrow{display:flex;align-items:center;gap:7px;padding:6px 0;border-bottom:1px solid #131c26;font-size:11px}
.zrow:last-child{border-bottom:none}
.ztype{font-size:9px;font-weight:800;padding:2px 5px;border-radius:4px;letter-spacing:.5px;min-width:46px;text-align:center}
.ztype.supply{background:#251315;color:var(--red);border:1px solid #421a1e}
.ztype.demand{background:#0c2118;color:var(--green);border:1px solid #174832}
.ztype.neutral{background:#141d28;color:var(--dim);border:1px solid var(--border)}
.ztype.breached{background:#12181e;color:var(--dim2);border:1px solid var(--border);text-decoration:line-through}
.zprice{font-weight:700;font-family:var(--mono);font-size:11px;flex:1}
.zcount{font-size:10px;color:var(--dim)}
.zcount.maxed{color:var(--amber)}

/* log */
.log-wrap{flex:1;min-height:120px;display:flex;flex-direction:column}
.log-inner{flex:1;overflow-y:auto;padding:6px 13px}
.ll{font-size:10px;font-family:var(--mono);color:var(--dim);padding:2px 0;border-bottom:1px solid #111;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ll.entry{color:#7dd5a8}.ll.exit{color:#7ab8ff}.ll.trade{color:var(--amber)}

/* settings */
.settings{padding:10px 13px}
.settings details summary{font-size:10px;font-weight:800;color:var(--dim);letter-spacing:1.2px;cursor:pointer;text-transform:uppercase;user-select:none}
.settings details summary:hover{color:var(--text)}
.sform{display:flex;flex-direction:column;gap:7px;margin-top:9px}
.sform input{width:100%;background:#111824;color:var(--text);border:1px solid var(--border2);border-radius:5px;padding:5px 7px;font-size:11px;font-family:var(--font)}
.sform input:focus{outline:none;border-color:var(--blue)}
.sbtn{width:100%;background:none;border:1px solid var(--green);color:var(--green);padding:5px 0;border-radius:5px;font-size:11px;font-weight:700;cursor:pointer;font-family:var(--font);transition:background .2s}
.sbtn:hover{background:#0d211a}
.slnk{display:block;text-align:center;font-size:11px;color:var(--blue);text-decoration:none;padding:4px 0;margin-top:4px}
.slnk:hover{text-decoration:underline}
.lbtn{background:none;border:1px solid var(--border2);color:var(--dim);padding:2px 9px;border-radius:4px;font-size:10px;font-weight:700;cursor:pointer;font-family:var(--font);letter-spacing:.5px;transition:all .2s}
.lbtn:hover{border-color:var(--blue);color:var(--blue)}
.lbtn.active{border-color:var(--green);color:var(--green)}
.tab-btn{background:none;border:none;color:var(--dim2);font-size:10px;font-weight:800;letter-spacing:1.2px;cursor:pointer;font-family:var(--font);padding:3px 6px;border-bottom:2px solid transparent;transition:all .2s}
.tab-btn.active{color:var(--text);border-bottom-color:var(--blue)}
.tab-btn:hover{color:var(--text)}
.trow{font-size:10px;font-family:var(--mono);padding:5px 0;border-bottom:1px solid #111;display:grid;grid-template-columns:46px 28px 1fr auto;gap:4px;align-items:center}
.trow .tr-dir{font-weight:800}.trow .ce{color:var(--green)}.trow .pe{color:var(--red)}
.trow .tr-pnl{font-weight:700;text-align:right}.trow .pnl-pos{color:var(--green)}.trow .pnl-neg{color:var(--red)}
</style>
</head>
<body>
<div class="bg-glow"></div>
<header>
  <div class="brand">◆ LVN TRADER</div>
  <div class="price-box">
    <span id="price-val">—</span>
    <span id="price-chg"></span>
  </div>
  <div class="tbadge flat" id="tbadge">FLAT</div>
  <span id="hdr-time"></span>
  <div class="feed-dot" id="feed-dot"></div>
</header>
<main>
  <div class="chart-pane">
    <div id="chart"></div>
    <div id="no-data" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;pointer-events:none">
      <div style="text-align:center;color:var(--dim)">
        <div style="font-size:22px;margin-bottom:8px">📡</div>
        <div style="font-size:12px;font-weight:700;letter-spacing:1px">WAITING FOR LIVE PRICES</div>
        <div style="font-size:10px;margin-top:4px">Set token in ⚙ Settings to start</div>
      </div>
    </div>
    <button class="lbtn active" id="live-btn" style="position:absolute;top:8px;right:8px;z-index:10" onclick="goLive()">● LIVE</button>
    <div style="position:absolute;top:8px;right:74px;z-index:10;display:flex;gap:4px">
      <button class="lbtn" onclick="zoomIn()">＋</button>
      <button class="lbtn" onclick="zoomOut()">－</button>
    </div>
  </div>
  <div class="side">
    <div class="card" id="trade-card">
      <div class="ctitle">POSITION</div>
      <div class="cdim">Flat — watching zones</div>
    </div>
    <div class="card">
      <div class="ctitle">ZONES</div>
      <div id="zones-list"></div>
    </div>
    <div class="log-wrap">
      <div class="card" style="border-bottom:none;padding-bottom:4px;display:flex;align-items:center;gap:8px">
        <button class="tab-btn active" id="tab-log" onclick="switchTab('log')">LOG</button>
        <button class="tab-btn" id="tab-trades" onclick="switchTab('trades')">TRADES</button>
      </div>
      <div class="log-inner" id="log-inner"></div>
      <div class="log-inner" id="trades-inner" style="display:none"></div>
    </div>
    <div class="settings">
      <details>
        <summary>⚙ Settings</summary>
        <form class="sform" onsubmit="doExchange(event)">
          <div style="font-size:10px;color:var(--dim);margin-bottom:2px">Step 1 — Login to Fyers:</div>
          <a href="/fyers-login" target="_blank" class="sbtn" style="display:block;text-align:center;text-decoration:none;padding:5px 0">🔑 Open Fyers Login →</a>
          <div style="font-size:10px;color:var(--dim);margin-top:6px;margin-bottom:2px">Step 2 — Paste auth code from redirect URL:</div>
          <input id="authcode" placeholder="Paste auth code JWT here…" autocomplete="off">
          <button class="sbtn" type="submit" id="exc-btn">Exchange &amp; Start Trading</button>
        </form>
        <a href="/levels" class="slnk">✏ Edit Zones &amp; Levels →</a>
      </details>
    </div>
  </div>
</main>
<script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
<script>
// ── chart init ─────────────────────────────────────────────────────────────────
const el=document.getElementById('chart');
const chart=LightweightCharts.createChart(el,{
  layout:{background:{color:'#090c12'},textColor:'#8b98a8'},
  grid:{vertLines:{color:'#131c28'},horzLines:{color:'#131c28'}},
  timeScale:{borderColor:'#1e2a38',timeVisible:true,secondsVisible:false,rightOffset:8},
  rightPriceScale:{borderColor:'#1e2a38'},
  crosshair:{mode:1},
});
const ser=chart.addCandlestickSeries({
  upColor:'#3fd68a',downColor:'#ff5c5c',
  wickUpColor:'#3fd68a',wickDownColor:'#ff5c5c',
  borderVisible:false,
  lastValueVisible:true,priceLineVisible:false,
});
new ResizeObserver(()=>chart.applyOptions({width:el.offsetWidth,height:el.offsetHeight})).observe(el);

// ── zone and trade price lines ─────────────────────────────────────────────────
let zLines=[],tLines=[],zKey='',tKey='',lastBarTime=null,lastP=null,userPanned=false;

chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{
  userPanned=true;
  document.getElementById('live-btn').className='lbtn';
});

function goLive(){
  userPanned=false;
  chart.timeScale().scrollToRealTime();
  document.getElementById('live-btn').className='lbtn active';
}

function zoomIn(){
  const r=chart.timeScale().getVisibleLogicalRange();
  if(!r)return;
  const c=(r.from+r.to)/2, h=(r.to-r.from)/2*0.6;
  chart.timeScale().setVisibleLogicalRange({from:c-h,to:c+h});
}

function zoomOut(){
  const r=chart.timeScale().getVisibleLogicalRange();
  if(!r)return;
  const c=(r.from+r.to)/2, h=(r.to-r.from)/2*1.6;
  chart.timeScale().setVisibleLogicalRange({from:c-h,to:c+h});
}
const ZC={supply:'#ff5c5c',demand:'#3fd68a',neutral:'#6a8090'};
const LS=LightweightCharts.LineStyle;

function drawZones(zones,breached){
  const k=JSON.stringify(zones)+JSON.stringify(breached);
  if(k===zKey)return;zKey=k;
  zLines.forEach(l=>{try{ser.removePriceLine(l)}catch(e){}});zLines=[];
  zones.forEach(z=>{
    const isBr=breached[z.id]===true;
    const base=ZC[isBr?'neutral':z.type]||ZC.neutral;
    const a=isBr?'44':'99';
    zLines.push(ser.createPriceLine({price:z.upper,color:base+a,lineWidth:1,lineStyle:LS.Solid,axisLabelVisible:true,title:z.id+'▲'}));
    zLines.push(ser.createPriceLine({price:z.lower,color:base+a,lineWidth:1,lineStyle:LS.Dashed,axisLabelVisible:false,title:''}));
  });
}

function drawTrade(trade){
  const k=JSON.stringify(trade?[trade.sl,trade.target,trade.lot1_hit,trade.lot1_target]:null);
  if(k===tKey)return;tKey=k;
  tLines.forEach(l=>{try{ser.removePriceLine(l)}catch(e){}});tLines=[];
  if(!trade)return;
  tLines.push(ser.createPriceLine({price:trade.sl,color:'#ff5c5ccc',lineWidth:1,lineStyle:LS.Dotted,axisLabelVisible:true,title:'SL'}));
  tLines.push(ser.createPriceLine({price:trade.target,color:'#3fd68acc',lineWidth:1,lineStyle:LS.Dotted,axisLabelVisible:true,title:'T'}));
  if(!trade.lot1_hit&&trade.lot1_target)
    tLines.push(ser.createPriceLine({price:trade.lot1_target,color:'#f2b33dcc',lineWidth:1,lineStyle:LS.Dotted,axisLabelVisible:true,title:'L1'}));
}

// ── markers ────────────────────────────────────────────────────────────────────
function setMarkers(events){
  if(!events.length){ser.setMarkers([]);return;}
  ser.setMarkers([...events].sort((a,b)=>a.time-b.time).map(e=>({
    time:e.time,
    position:e.direction==='CE'?'belowBar':'aboveBar',
    color:e.type==='exit'?'#5aa9ff':e.direction==='CE'?'#3fd68a':'#ff5c5c',
    shape:e.type==='entry'?(e.direction==='CE'?'arrowUp':'arrowDown'):e.type==='lot1'?'circle':'square',
    text:e.text,size:e.type==='entry'?2:1,
  })));
}

// ── UI render ──────────────────────────────────────────────────────────────────
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function render(d){
  const price=d.price;

  // header price + change
  if(price!=null){
    document.getElementById('price-val').textContent=price.toFixed(2);
    if(lastP!=null){
      const diff=price-lastP;
      const el2=document.getElementById('price-chg');
      el2.textContent=(diff>=0?'+':'')+diff.toFixed(2);
      el2.className=diff>=0?'up':'down';
    }
    lastP=price;
  }

  // IST clock
  document.getElementById('hdr-time').textContent=
    new Date().toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit'});

  // trade badge
  const badge=document.getElementById('tbadge');
  const tr=d.trade;
  if(tr){
    badge.textContent=(tr.direction==='CE'?'▲ ':'▼ ')+tr.direction;
    badge.className='tbadge '+tr.direction.toLowerCase();
  }else{badge.textContent='FLAT';badge.className='tbadge flat';}

  // trade card
  const card=document.getElementById('trade-card');
  if(tr&&price!=null){
    const pnl=tr.direction==='CE'?price-tr.entry:tr.entry-price;
    const pc=pnl>=0?'tgreen':'tred';
    const l1=tr.lot1_hit
      ?'<span class="lbadge bk">LOT1 BOOKED &#9733;</span>'
      :'<span class="lbadge op">LOT1 OPEN</span>';
    card.className='card '+(tr.direction==='CE'?'tcard-ce':'tcard-pe');
    card.innerHTML=`<div class="ctitle">POSITION &mdash; ${tr.direction==='CE'?'&#9650; CE':'&#9660; PE'}</div>
<div class="tgrid">
<div class="tlabel">Entry</div><div class="tval">${tr.entry.toFixed(2)}</div>
<div class="tlabel">SL</div><div class="tval tred">${tr.sl.toFixed(2)}</div>
<div class="tlabel">Target</div><div class="tval tgreen">${tr.target.toFixed(2)}</div>
<div class="tlabel">Lot1 T</div><div class="tval tamber">${(tr.lot1_target||0).toFixed(2)}</div>
<div class="tlabel">Live P&amp;L</div><div class="tval ${pc}">${pnl>=0?'+':''}${pnl.toFixed(1)} pts</div>
<div class="tlabel">Lot1</div><div>${l1}</div>
</div>`;
  }else{
    card.className='card';
    card.innerHTML='<div class="ctitle">POSITION</div><div class="cdim">Flat &mdash; watching zones</div>';
  }

  // zones panel
  const zones=d.zones_cfg||[],zt=d.zone_trades||{},zb=d.zone_breached||{};
  document.getElementById('zones-list').innerHTML=zones.slice().reverse().map(z=>{
    const cnt=zt[z.id]||0,br=zb[z.id]===true;
    const tl=br?'NEUTRAL':z.type.toUpperCase(),tc=br?'breached':z.type;
    return`<div class="zrow"><span class="ztype ${tc}">${tl}</span><span class="zprice">${z.upper.toFixed(0)}/${z.lower.toFixed(0)}</span><span class="zcount${cnt>=2?' maxed':''}">${cnt}/2</span></div>`;
  }).join('');

  // log panel
  const log=d.log||[],li=document.getElementById('log-inner');
  const atBot=li.scrollHeight-li.scrollTop<=li.clientHeight+30;
  li.innerHTML=log.slice(-60).map(l=>{
    const cls=(l.includes('ENTER')||l.includes('★ ENTER'))?'entry':
              (l.includes('SL_HIT')||l.includes('TARGET_HIT')||l.includes('✓')||l.includes('✗')||l.includes('EOD'))?'exit':
              (l.includes('TAPPED')||l.includes('LOT1'))?'trade':'';
    return`<div class="ll ${cls}">${esc(l)}</div>`;
  }).join('');
  if(atBot)li.scrollTop=li.scrollHeight;

  // trades panel
  const trades=d.trades||[],ti=document.getElementById('trades-inner');
  if(trades.length===0){
    ti.innerHTML='<div class="ll" style="padding:8px 0;color:var(--dim2)">No trades today</div>';
  }else{
    ti.innerHTML=[...trades].reverse().map(t=>{
      const dir=t.direction||'';
      const pnl=parseFloat(t.fut_pnl_pts||0);
      const pc=pnl>=0?'pnl-pos':'pnl-neg';
      return`<div class="trow"><span class="ll" style="font-size:10px">${t.exit_time||''}</span><span class="tr-dir ${dir.toLowerCase()}">${dir}</span><span style="color:var(--dim)">${t.exit_reason||''} L${t.lot||''}</span><span class="tr-pnl ${pc}">${pnl>=0?'+':''}${pnl}pts</span></div>`;
    }).join('');
  }

  document.getElementById('feed-dot').className='feed-dot live';
}

function switchTab(tab){
  document.getElementById('log-inner').style.display=tab==='log'?'':'none';
  document.getElementById('trades-inner').style.display=tab==='trades'?'':'none';
  document.getElementById('tab-log').className='tab-btn'+(tab==='log'?' active':'');
  document.getElementById('tab-trades').className='tab-btn'+(tab==='trades'?' active':'');
}

// ── data polling ───────────────────────────────────────────────────────────────
function updateChart(candles){
  if(!candles||!candles.length)return;
  const last=candles[candles.length-1];
  if(lastBarTime===null||last.time<lastBarTime){
    // first paint or day reset → full reload, fit view
    ser.setData(candles);
    chart.timeScale().fitContent();
    lastBarTime=last.time;
    setTimeout(()=>{userPanned=false;},500);
  }else{
    // incremental — only update new/forming candle(s)
    candles.forEach(c=>{if(c.time>=lastBarTime)ser.update(c);});
    lastBarTime=last.time;
    if(!userPanned)chart.timeScale().scrollToRealTime();
  }
}

async function refresh(){
  try{
    const r=await fetch('/prices');
    if(!r.ok)throw 0;
    const d=await r.json();
    const nd=document.getElementById('no-data');
    if(d.candles&&d.candles.length){
      nd.style.display='none';
      updateChart(d.candles);
    }else{
      nd.style.display='';
    }
    drawZones(d.zones_cfg||[],d.zone_breached||{});
    drawTrade(d.trade);
    setMarkers(d.events||[]);
    render(d);
  }catch(e){
    document.getElementById('feed-dot').className='feed-dot dead';
  }
}

async function doExchange(e){
  e.preventDefault();
  const code=document.getElementById('authcode').value.trim();
  if(!code)return;
  const btn=document.getElementById('exc-btn');
  btn.textContent='Exchanging…';btn.disabled=true;
  try{
    const r=await fetch('/exchange',{method:'POST',body:new URLSearchParams({auth_code:code})});
    const d=await r.json();
    if(d.ok){
      document.getElementById('authcode').value='';
      btn.textContent='✓ '+d.msg;
      btn.style.borderColor='var(--green)';btn.style.color='var(--green)';
      refresh();
    }else{
      btn.textContent='✗ '+d.msg;
      btn.style.borderColor='var(--red)';btn.style.color='var(--red)';
    }
  }catch(ex){
    btn.textContent='✗ Network error';
    btn.style.borderColor='var(--red)';btn.style.color='var(--red)';
  }
  setTimeout(()=>{btn.textContent='Exchange & Start Trading';btn.disabled=false;btn.style.borderColor='';btn.style.color='';},5000);
}

refresh();
setInterval(refresh,30000);
</script>
</body>
</html>'''

# ── Flask app ──────────────────────────────────────────────────────────────────
app = Flask(__name__)

state: dict = {
    "app_id":         os.getenv("FYERS_APP_ID", "POGBT26YG3-100"),
    "token":          os.getenv("FYERS_ACCESS_TOKEN", "") or _load_persisted_token(),
    "token_updated":  False,
    "levels_updated": False,
    "price":          None,
    "trade":          None,
    "log":            [],    # last 60 status lines
    "candles":        [],    # [{time, open, high, low, close}] 1-min OHLC — chart data
    "events":         [],    # [{time, type, direction, price, text}] — chart markers
    "zones_cfg":      [],    # [{id, upper, lower, type, session}] — zone lines
    "zone_trades":    {},    # {zone_id: count} — mirrored from alert.py
    "zone_breached":  {},    # {zone_id: bool}  — mirrored from alert.py
    "trades":         [],    # completed trades today [{exit_time, direction, exit_reason, fut_pnl_pts, lot}]
}


# ── web endpoints ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return DASHBOARD_HTML


@app.route("/prices")
def api_prices():
    return jsonify({
        "price":         state["price"],
        "trade":         state["trade"],
        "log":           state["log"],
        "candles":       state["candles"],
        "events":        state["events"],
        "zones_cfg":     state["zones_cfg"],
        "zone_trades":   state["zone_trades"],
        "zone_breached": state["zone_breached"],
        "trades":        state["trades"],
    })


@app.route("/fyers-login")
def fyers_login():
    from fyers_apiv3 import fyersModel
    session = fyersModel.SessionModel(
        client_id=state["app_id"],
        secret_key=os.getenv("FYERS_SECRET_KEY", "O105V1TQJ1"),
        redirect_uri="https://trade.fyers.in/api-login/redirect-uri/index.html",
        response_type="code",
        grant_type="authorization_code",
    )
    return redirect(session.generate_authcode())


@app.route("/exchange", methods=["POST"])
def exchange_token():
    auth_code = request.form.get("auth_code", "").strip()
    if not auth_code:
        return jsonify({"ok": False, "msg": "No auth code provided"}), 400
    try:
        from fyers_apiv3 import fyersModel
        session = fyersModel.SessionModel(
            client_id=state["app_id"],
            secret_key=os.getenv("FYERS_SECRET_KEY", "O105V1TQJ1"),
            redirect_uri="https://trade.fyers.in/api-login/redirect-uri/index.html",
            response_type="code",
            grant_type="authorization_code",
        )
        session.set_token(auth_code)
        result = session.generate_token()
        if result.get("s") != "ok":
            return jsonify({"ok": False, "msg": result.get("message", "Token exchange failed")}), 400
        access_token = result["access_token"]
    except Exception as ex:
        return jsonify({"ok": False, "msg": str(ex)}), 500

    state["token"] = access_token
    state["token_updated"] = True
    try:
        TOKEN_FILE.write_text(access_token)
    except Exception:
        pass
    return jsonify({"ok": True, "msg": "Logged in ✓ Trading started"})


@app.route("/token", methods=["POST"])
def update_token():
    token = request.form.get("token", "").strip()
    if not token:
        return jsonify({"ok": False, "msg": "No token provided"}), 400
    try:
        from fyers_apiv3 import fyersModel
        fyers_test = fyersModel.FyersModel(
            client_id=state["app_id"],
            token=token,
            log_path="",
        )
        result = fyers_test.get_profile()
        if result.get("s") != "ok":
            return jsonify({"ok": False, "msg": result.get("message", "Fyers API rejected token")}), 400
    except Exception as ex:
        return jsonify({"ok": False, "msg": str(ex)}), 500
    state["token"] = token
    state["token_updated"] = True
    try:
        TOKEN_FILE.write_text(token)
    except Exception:
        pass
    return jsonify({"ok": True, "msg": f"Token valid ✓  logged in as {result.get('data', {}).get('fy_id', '')}"})



@app.route("/levels", methods=["GET", "POST"])
def levels():
    if request.method == "POST":
        try:
            cfg = _parse_levels_form(request.form)
            LEVELS_FILE.write_text(json.dumps(cfg, indent=2))
            state["levels_updated"] = True
            return redirect("/")
        except Exception as e:
            cfg = _read_levels_cfg()
            return _levels_page(cfg, str(e))
    return _levels_page(_read_levels_cfg())


def _read_levels_cfg() -> dict:
    try:
        return json.loads(LEVELS_FILE.read_text()) if LEVELS_FILE.exists() else {}
    except Exception:
        return {}


def _parse_levels_form(form) -> dict:
    def parse_zones(session):
        zones, i = [], 0
        while f"{session}_upper_{i}" in form:
            upper = float(form[f"{session}_upper_{i}"])
            lower = float(form[f"{session}_lower_{i}"])
            ztype = form.get(f"{session}_type_{i}", "neutral")
            zones.append({"id": f"z{i+1}", "upper": upper, "lower": lower, "type": ztype})
            i += 1
        return zones
    return {
        "comment": "Zone types: supply=bearish, demand=bullish, neutral=both",
        "crude_key":    form.get("crude_key", "MCX_FO|573422"),
        "cooldown_min": int(form.get("cooldown_min", 30)),
        "bounce_buffer": float(form.get("bounce_buffer", 5)),
        "max_sl_pts":   float(form.get("max_sl_pts", 40)),
        "reversal_pts": float(form.get("reversal_pts", 20)),
        "breach_pts":   float(form.get("breach_pts", 50)),
        "d2": {"date": form.get("d2_date", ""), "zones": parse_zones("d2")},
        "d1": {"date": form.get("d1_date", ""), "zones": parse_zones("d1")},
    }


def _levels_page(cfg: dict, error: str = "") -> str:
    def zone_rows(session, zones):
        rows = ""
        for i, z in enumerate(zones):
            s = z.get("type", "neutral")
            rows += f"""<tr><td><input type="number" name="{session}_upper_{i}" value="{z['upper']}" step="0.5" required></td>
<td><input type="number" name="{session}_lower_{i}" value="{z['lower']}" step="0.5" required></td>
<td><select name="{session}_type_{i}">
  <option value="supply"{"  selected" if s=="supply" else ""}>Supply (Bearish)</option>
  <option value="demand"{"  selected" if s=="demand" else ""}>Demand (Bullish)</option>
  <option value="neutral"{"  selected" if s=="neutral" else ""}>Neutral (Both)</option>
</select></td>
<td><button type="button" onclick="this.closest('tr').remove()" class="del">✕</button></td></tr>"""
        return rows

    d2 = cfg.get("d2", {}); d1 = cfg.get("d1", {})
    err = f'<div class="err">{error}</div>' if error else ""
    return f"""<!doctype html><html><head><title>LVN — Edit Levels</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Inter","Segoe UI",sans-serif;background:#090c12;color:#e2e8f0;padding:16px;font-size:13px}}
h2{{color:#5aa9ff;margin-bottom:14px;font-size:15px;font-weight:800}}
.card{{background:#0d1117;border:1px solid #1e2a38;border-radius:8px;padding:14px;margin-bottom:14px}}
.stitle{{font-size:10px;font-weight:800;letter-spacing:1.2px;color:#6e8098;text-transform:uppercase;margin-bottom:10px}}
table{{width:100%;border-collapse:collapse;margin-bottom:6px}}
th{{font-size:10px;color:#6e8098;text-align:left;padding:5px 8px;border-bottom:1px solid #1e2a38;font-weight:600}}
td{{padding:4px 6px}}
input,select{{background:#111824;color:#e2e8f0;border:1px solid #2a3a50;border-radius:4px;padding:5px 8px;font-size:12px;width:100%;font-family:inherit}}
input:focus,select:focus{{outline:none;border-color:#5aa9ff}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:6px}}
label{{font-size:10px;color:#6e8098;display:block;margin-bottom:3px;letter-spacing:.3px}}
.date-row{{display:flex;align-items:center;gap:8px;margin-bottom:10px}}
.date-row label{{white-space:nowrap;margin:0}}
.date-row input{{max-width:110px}}
.save{{background:none;border:1px solid #3fd68a;color:#3fd68a;padding:8px 24px;border-radius:5px;font-size:13px;font-weight:700;cursor:pointer;font-family:inherit}}
.save:hover{{background:#0d211a}}
.add{{background:none;border:1px dashed #2a3a50;color:#6e8098;padding:6px;font-size:11px;border-radius:4px;cursor:pointer;width:100%;transition:all .2s;font-family:inherit}}
.add:hover{{border-color:#5aa9ff;color:#5aa9ff}}
.del{{background:none;border:1px solid #ff5c5c44;color:#ff5c5c;padding:2px 7px;font-size:11px;border-radius:4px;cursor:pointer}}
.del:hover{{background:#221215;border-color:#ff5c5c}}
.err{{color:#ff5c5c;background:#221215;padding:8px 12px;border-radius:5px;margin-bottom:12px;font-size:12px}}
a{{color:#5aa9ff;font-size:12px;text-decoration:none}}
</style></head><body>
<p style="margin-bottom:12px"><a href="/">← Dashboard</a></p>
<h2>Edit Levels</h2>{err}
<form method="post">
<input type="hidden" name="crude_key" value="{cfg.get('crude_key','MCX_FO|573422')}">
<div class="card">
  <div class="stitle">D2 Zones</div>
  <div class="date-row"><label>Session Date:</label>
    <input type="text" name="d2_date" value="{d2.get('date','')}" placeholder="06 Nov" style="max-width:100px"></div>
  <table><thead><tr><th>Upper</th><th>Lower</th><th>Type</th><th></th></tr></thead>
  <tbody id="d2_body">{zone_rows('d2', d2.get('zones',[]))}</tbody></table>
  <button type="button" class="add" onclick="addRow('d2')">+ Add Zone</button>
</div>
<div class="card">
  <div class="stitle">D1 Zones</div>
  <div class="date-row"><label>Session Date:</label>
    <input type="text" name="d1_date" value="{d1.get('date','')}" placeholder="05 Nov" style="max-width:100px"></div>
  <table><thead><tr><th>Upper</th><th>Lower</th><th>Type</th><th></th></tr></thead>
  <tbody id="d1_body">{zone_rows('d1', d1.get('zones',[]))}</tbody></table>
  <button type="button" class="add" onclick="addRow('d1')">+ Add Zone</button>
</div>
<div class="card">
  <div class="stitle">Parameters</div>
  <div class="grid">
    <div><label>Cooldown (min)</label><input type="number" name="cooldown_min" value="{cfg.get('cooldown_min',30)}" min="1"></div>
    <div><label>Bounce Buffer (pts)</label><input type="number" name="bounce_buffer" value="{cfg.get('bounce_buffer',5)}" step="0.5" min="0"></div>
    <div><label>Max SL (pts)</label><input type="number" name="max_sl_pts" value="{cfg.get('max_sl_pts',40)}" min="1"></div>
    <div><label>Reversal (pts)</label><input type="number" name="reversal_pts" value="{cfg.get('reversal_pts',20)}" min="1"></div>
    <div><label>Breach (pts)</label><input type="number" name="breach_pts" value="{cfg.get('breach_pts',50)}" min="1"></div>
  </div>
</div>
<button type="submit" class="save">Save &amp; Apply</button>
</form>
<script>
const counts={{d2:{len(d2.get('zones',[]))},d1:{len(d1.get('zones',[]))}}};
function addRow(s){{
  const i=counts[s]++;
  const tr=document.createElement('tr');
  tr.innerHTML=`<td><input type="number" name="${{s}}_upper_${{i}}" step="0.5" placeholder="9200" required></td><td><input type="number" name="${{s}}_lower_${{i}}" step="0.5" placeholder="9160" required></td><td><select name="${{s}}_type_${{i}}"><option value="supply">Supply (Bearish)</option><option value="demand">Demand (Bullish)</option><option value="neutral">Neutral (Both)</option></select></td><td><button type="button" onclick="this.closest('tr').remove()" class="del">✕</button></td>`;
  document.getElementById(s+'_body').appendChild(tr);
}}
</script></body></html>"""


@app.route("/status")
def status():
    return jsonify({"price": state["price"], "trade": state["trade"]})


@app.route("/health")
def health():
    return "ok"


# ── trading thread ─────────────────────────────────────────────────────────────
def _run_trading():
    import time as _t
    while True:
        try:
            from alert import run
            asyncio.run(run(state=state))
        except SystemExit:
            pass
        except Exception as e:
            print(f"[ERROR] Trading loop crashed: {e} — restarting in 5s")
        _t.sleep(5)


def start_trading():
    t = threading.Thread(target=_run_trading, daemon=True)
    t.start()


# ── entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not state["token"]:
        print("[WARN] FYERS_ACCESS_TOKEN env var not set — update via /token after deploy")
    start_trading()
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
