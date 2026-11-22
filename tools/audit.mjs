// Dev tool: WCAG AA text-contrast audit (light + dark) via Chrome DevTools Protocol.
// Usage: node --experimental-websocket tools/audit.mjs http://localhost:8089
import {spawn} from 'node:child_process';
const B=process.argv[2]||'http://localhost:8089', port=9833+Math.floor(Math.random()*100);
const ch=spawn('google-chrome',['--headless=new','--no-sandbox','--disable-gpu','--remote-debugging-port='+port,'--user-data-dir=/tmp/cha'+port,'about:blank'],{stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let tabs; for(let i=0;i<50;i++){try{tabs=await (await fetch(`http://127.0.0.1:${port}/json`)).json();if(tabs.length)break}catch(e){}await sleep(200)}
const ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl); await new Promise(r=>ws.onopen=r);
let id=0; const pend={}; ws.onmessage=m=>{const d=JSON.parse(m.data); if(d.id&&pend[d.id]){pend[d.id](d);delete pend[d.id]}};
const send=(method,params={})=>new Promise(r=>{const i=++id;pend[i]=r;ws.send(JSON.stringify({id:i,method,params}))});
const FN=`(()=>{
 const P=s=>{const m=s.match(/[\\d.]+/g).map(Number);return {r:m[0],g:m[1],b:m[2],a:m.length>3?m[3]:1}};
 const L=c=>{const f=v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)};return .2126*f(c.r)+.7152*f(c.g)+.0722*f(c.b)};
 const over=(f,b)=>({r:f.r*f.a+b.r*(1-f.a),g:f.g*f.a+b.g*(1-f.a),b:f.b*f.a+b.b*(1-f.a),a:1});
 const bgOf=e=>{const st=[];for(let n=e;n;n=n.parentElement){const c=P(getComputedStyle(n).backgroundColor);if(c.a>0)st.push(c);if(c.a===1)break}
   let b=st.length&&st[st.length-1].a===1?st.pop():{r:255,g:255,b:255,a:1};while(st.length)b=over(st.pop(),b);return b};
 const bad={};
 const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
 for(let t;t=w.nextNode();){if(!t.nodeValue.trim())continue;const e=t.parentElement;if(e.closest('[hidden]'))continue;const cs=getComputedStyle(e);if(cs.visibility==='hidden'||e.closest('.vh,.skip,script,style'))continue;
  const r=e.getBoundingClientRect();if(!r.width)continue;
  let fg=P(cs.color);const bg=bgOf(e);fg=over(fg,bg);const l1=L(fg),l2=L(bg);const cr=(Math.max(l1,l2)+.05)/(Math.min(l1,l2)+.05);
  const px=parseFloat(cs.fontSize),big=px>=24||(px>=18.66&&+cs.fontWeight>=700);
  if(cr<(big?3:4.5)){const k=e.tagName+'.'+e.className+' '+cs.color+' on rgb('+[bg.r,bg.g,bg.b].map(Math.round)+') '+cr.toFixed(2);bad[k]=(bad[k]||0)+1}}
 return bad})()`;
const out=[];
for(const p of ['/ru/','/en/2026-09-30-kyiv-udar-po-energetike/','/uk/2026-09-28-kharkiv-saltivka-udar/','/en/about/','/en/region/','/en/date/'])
 for(const scheme of ['light','dark']){
  await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-color-scheme',value:scheme}]});
  await send('Page.navigate',{url:B+p}); await sleep(700);
  const r=await send('Runtime.evaluate',{expression:FN,returnByValue:true});
  out.push(p+' '+scheme+' -> '+(r.result.result? JSON.stringify(r.result.result.value):JSON.stringify(r.result)));
 }
console.log(out.join('\n')); ch.kill(); process.exit(0);
