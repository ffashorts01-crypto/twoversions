/* Progressive enhancement: filters for the event list. The list works without JS.
   The home page ships only the newest cards; the full index (U.src, JSON) is fetched on the first
   interaction with the filters or the "show more" button. Filtering runs on the in-memory rows and
   only a small page of matching cards (STEP at a time) is ever put into the DOM, so every click
   re-renders a few dozen nodes instead of hundreds (keeps INP low). All cards stay reachable
   without JS through the date / region pages and the sitemap. */
(function(){
  var list=document.getElementById('events'); if(!list) return;
  var box=document.getElementById('filters'); if(!box) return;
  var U; try{U=JSON.parse(box.getAttribute('data-ui'))}catch(e){return}
  var STEP=30, ssr=[].slice.call(list.querySelectorAll('li.ev')), rows=null, total=U.total||ssr.length, loading=null;
  var limit=ssr.length, matches=null, shown=0, rendered=null;   // rendered: the rows currently in the DOM (null = server-rendered list)
  function el(t,a,c){var n=document.createElement(t);for(var k in a)n.setAttribute(k,a[k]);if(c)n.textContent=c;return n}
  /* The filter bar, the counter line and the "show more" button are server-rendered at their final size (CLS 0) and only
     filled in here; the old create-everything path is kept as a fallback for pages without that markup. */
  function sel(id,label,all,opts){var s=document.getElementById(id);
    if(!s){var l=el('label',{'for':id},label+': ');s=el('select',{id:id});s.appendChild(el('option',{value:''},all));var g=el('div',{'class':'fg'});g.appendChild(l);g.appendChild(s);box.appendChild(g)}
    var f=document.createDocumentFragment();opts.forEach(function(o){f.appendChild(el('option',{value:o[0]},o[1]))});s.appendChild(f);return s}
  var cov=sel('f-cov',U.cov,U.all,[['3',U.three],['2',U.two],['1',U.one]]);
  var reg=sel('f-reg',U.regionL,U.allRegions,U.regions);
  var dat=sel('f-dat',U.dateL,U.allDates,U.dates);
  var cnf=sel('f-cnf',U.confL,U.allConf,U.confs);
  var q=document.getElementById('f-q');if(!q){q=el('input',{type:'search',id:'f-q',placeholder:U.search,'aria-label':U.search});box.appendChild(q)}
  var rs=document.getElementById('f-rs');if(!rs){rs=el('button',{type:'button'},U.reset||'×');box.appendChild(rs)}rs.hidden=true;
  rs.addEventListener('click',function(){cov.value=reg.value=dat.value=cnf.value=q.value='';limit=Math.max(STEP,ssr.length);update();q.focus()});
  var cnt=document.getElementById('fcount');if(!cnt){cnt=el('p',{'class':'fcount','aria-live':'polite'});box.parentNode.insertBefore(cnt,list)}cnt.hidden=false;
  var none=el('p',{'class':'none'},U.none);none.hidden=true;list.parentNode.insertBefore(none,list.nextSibling);
  var more=document.getElementById('more'), btn=null;
  if(more&&U.src){btn=document.getElementById('showall');if(!btn){btn=el('button',{type:'button','class':'showall'});more.insertBefore(btn,more.firstChild)}
    /* prefetch the full index when the button comes near the screen, so a click appends at once (a late append would move the footer = layout shift) */
    if('IntersectionObserver' in window&&!(navigator.connection&&navigator.connection.saveData)){var pio=new IntersectionObserver(function(es){if(es[0].isIntersecting){pio.disconnect();load().catch(function(){})}},{rootMargin:'600px 0px'});pio.observe(more)}
    btn.addEventListener('pointerdown',function(){load()},{passive:true});
    btn.addEventListener('click',function(){btn.disabled=true;limit=shown+STEP;
      load().then(function(){btn.disabled=false;requestAnimationFrame(function(){update(true)})})})}
  function active(){return !!(cov.value||reg.value||dat.value||cnf.value||q.value.trim())}
  function load(){
    if(rows) return Promise.resolve();
    if(!loading) loading=fetch(U.src).then(function(r){if(!r.ok)throw r;return r.json()}).then(function(r){rows=r;total=r.length})
      .catch(function(){loading=null;if(btn)btn.disabled=false;return Promise.reject()});
    return loading;
  }
  function ok(r,t){var c=r[0],v=cov.value;
    if(v==='3'&&c!==3)return false; if(v==='2'&&c<2)return false; if(v==='1'&&c!==1)return false;
    if(reg.value&&r[1]!==reg.value)return false; if(dat.value&&r[2]!==dat.value)return false;
    if(cnf.value&&r[3]!==cnf.value)return false; if(t&&r[4].indexOf(t)<0)return false; return true}
  function href(x){var a=x.querySelector('h2 a,h3 a');return a?a.getAttribute('href'):''}
  var sameV=null;function same(){if(sameV===null){var t=html(rows.slice(0,ssr.length));var a=[].slice.call(t.children);
    sameV=a.length===ssr.length&&a.every(function(li,i){return href(li)===href(ssr[i])})}return sameV}
  function html(rs){var t=document.createElement('template');t.innerHTML=rs.map(function(r){return r[5]}).join('');return t.content}
  /* append=true: only add the next page below what is already shown (no re-render, no layout shift above) */
  function update(append){
    var t=q.value.trim().toLowerCase();
    if(!rows){                                   // index not loaded yet: filter the few server-rendered cards
      var n=0;ssr.forEach(function(li){var r=[+li.getAttribute('data-n'),li.getAttribute('data-region'),li.getAttribute('data-date'),li.getAttribute('data-conf'),li.getAttribute('data-text')];
        var y=ok(r,t);li.hidden=!y;if(y)n++});
      shown=n;finish(n,active()?n:total);return;
    }
    matches=rows.filter(function(r){return ok(r,t)});
    var want=matches.slice(0,Math.max(limit,STEP));
    if(rendered===null&&!active()&&same()){              // server list = first rows; keep those nodes, add the rest
      ssr.forEach(function(li){li.hidden=false});
      if(want.length>ssr.length)list.appendChild(html(want.slice(ssr.length)));
    }else if(append&&rendered&&rendered.length<=want.length&&rendered.every(function(r,i){return r===want[i]})){
      list.appendChild(html(want.slice(rendered.length)));
    }else{list.textContent='';list.appendChild(html(want))}
    rendered=(rendered===null&&!active()&&want.length<=ssr.length&&same())?null:want;
    shown=want.length;finish(matches.length,matches.length);
  }
  function finish(n,m){
    rs.hidden=!active();
    cnt.textContent=U.shown.replace('{n}',n).replace('{m}',total); none.hidden=n>0;
    if(btn){var left=m-shown;btn.hidden=left<=0;btn.textContent=U.more+' ('+left+')'}
  }
  function onchange(){limit=Math.max(STEP,ssr.length);if(rows)update();else{update();load().then(function(){update()})}}
  [cov,reg,dat,cnf].forEach(function(s){s.addEventListener('change',onchange)}); q.addEventListener('input',onchange);
  box.addEventListener('focusin',load,{once:true}); box.addEventListener('pointerdown',load,{once:true});
  box.hidden=false; update();
})();
