// Dev tool: full-page screenshots + layout audit via Chrome DevTools Protocol.
// Usage: node --experimental-websocket tools/shots.mjs http://localhost:8089 [outdir]
import {spawn} from 'node:child_process';
import fs from 'node:fs';
const B=process.argv[2]||'http://localhost:8089', OUT=process.argv[3]||'screenshots';
fs.mkdirSync(OUT,{recursive:true});
const port=9333+Math.floor(Math.random()*500);
const ch=spawn('google-chrome',['--headless=new','--no-sandbox','--disable-gpu','--hide-scrollbars','--remote-debugging-port='+port,'--user-data-dir=/tmp/chp'+port,'about:blank'],{stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let tabs; for(let i=0;i<50;i++){try{tabs=await (await fetch(`http://127.0.0.1:${port}/json`)).json();if(tabs.length)break}catch(e){}await sleep(200)}
const ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl); await new Promise(r=>ws.onopen=r);
let id=0; const pend={}; const evs=[];
ws.onmessage=m=>{const d=JSON.parse(m.data); if(d.id&&pend[d.id]){pend[d.id](d);delete pend[d.id]} else evs.push(d)};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}))});
const ev=async e=>{const r=await send('Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});return r.result.result.value};
await send('Page.enable');
const EV='/ru/2026-09-30-kyiv-udar-po-energetike/', EVEN='/en/2026-09-28-kharkiv-saltivka-udar/';
const pages=process.env.PAGES?JSON.parse(process.env.PAGES):[['home','/ru/'],['event',EV],['event-en',EVEN],['about','/en/about/']];
const views=[['desktop',1280,900,false],['mobile',360,740,true]];
const report=[];
for(const [pn,path] of pages) for(const [vn,w,h,mob] of views) for(const scheme of ['light','dark']){
  await send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:mob});
  await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:scheme}]});
  await send('Page.navigate',{url:B+path}); await sleep(900);
  const a=await ev(`(()=>{const de=document.documentElement;const o=[];
    document.querySelectorAll('body *').forEach(e=>{const r=e.getBoundingClientRect();if(r.width&&r.right>innerWidth+1&&getComputedStyle(e).position!=='fixed'&&!e.closest('.tw,nav:not(.langs),.top'))o.push(e.tagName+'.'+e.className+' '+Math.round(r.right))});
    return {sw:de.scrollWidth,iw:innerWidth,h:de.scrollHeight,over:o.slice(0,6),bg:getComputedStyle(document.body).backgroundColor,small:[...document.querySelectorAll('a,button,select,input')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.height&&(r.height<24||r.width<24)&&!e.closest('p,li,.links,blockquote,footer')}).length}})()`);
  const r=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:w,height:a.h,scale:1}});
  fs.writeFileSync(`${OUT}/${pn}-${vn}-${scheme}.png`,Buffer.from(r.result.data,'base64'));
  report.push(`${pn} ${vn} ${scheme}: h=${a.h} scrollW=${a.sw}/${a.iw} bg=${a.bg} overflow=${JSON.stringify(a.over)} smallTargets=${a.small}`);
}
console.log(report.join('\n')); ch.kill(); process.exit(0);
