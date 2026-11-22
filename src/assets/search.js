(function(){
var root=document.getElementById('search');if(!root)return;
var inp=document.getElementById('q'),out=document.getElementById('results'),st=document.getElementById('sstatus'),U=JSON.parse(root.getAttribute('data-ui')),idx=null,loading=null;
function norm(s){return (s||'').toLowerCase().replace(/ё/g,'е').replace(/ї/g,'і').replace(/[’'ʼ`]/g,'').replace(/[^0-9a-zа-яіє]+/g,' ').trim()}
function stem(w){return w.length>5?w.slice(0,Math.max(4,w.length-2)):w}
function load(){if(idx)return Promise.resolve(idx);if(loading)return loading;st.textContent=U.loading;
loading=fetch(root.getAttribute('data-index')).then(function(r){return r.json()}).then(function(j){idx=j.map(function(x){return {x:x,t:' '+norm(x[1]+' '+x[2])+' ',b:' '+norm(x[1]+' '+x[2]+' '+x[4]+' '+x[5]+' '+x[3])+' '}});return idx});return loading}
function esc(s){return s.replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function run(){var q=norm(inp.value);var u=new URL(location.href);if(q)u.searchParams.set('q',inp.value);else u.searchParams.delete('q');history.replaceState(null,'',u);
if(!q){out.innerHTML='';st.textContent='';return}
load().then(function(ix){var ws=q.split(' ').filter(Boolean).map(stem),res=[];
ix.forEach(function(r){var sc=0;for(var i=0;i<ws.length;i++){var w=ws[i],k=r.b.indexOf(' '+w);if(k<0){k=r.b.indexOf(w);if(k<0||w.length<4)return;sc+=1}else sc+=2;if(r.t.indexOf(' '+w)>=0)sc+=3}res.push([sc,r.x])});
res.sort(function(a,b){return b[0]-a[0]||(a[1][3]<b[1][3]?1:-1)});
st.textContent=res.length?U.found.replace('{n}',res.length):U.none;
out.innerHTML=res.slice(0,60).map(function(r){var x=r[1];return '<li class="ev"><h2><a href="'+esc(x[0])+'">'+esc(x[1])+'</a></h2><p class="chips"><time class="chip d" datetime="'+x[3]+'">'+esc(x[6])+'</time><span class="chip p">'+esc(x[2])+'</span></p><p class="lead">'+esc(x[5])+'</p></li>'}).join('')})}
var tm;inp.addEventListener('input',function(){clearTimeout(tm);tm=setTimeout(run,150)});inp.addEventListener('focus',load,{once:true});
document.getElementById('sform').addEventListener('submit',function(ev){ev.preventDefault();run()});
var q0=new URL(location.href).searchParams.get('q');if(q0){inp.value=q0;run()}
})();
