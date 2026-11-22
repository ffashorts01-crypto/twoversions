/* Theme toggle (progressive enhancement). Follows the OS until the visitor picks a theme; the choice is saved in localStorage['theme']. */
(function(){
  var b=document.getElementById('theme-toggle'); if(!b) return;
  var root=document.documentElement, mq=window.matchMedia&&matchMedia('(prefers-color-scheme: dark)');
  function dark(){var t=root.getAttribute('data-theme');return t?t==='dark':!!(mq&&mq.matches)}
  function sync(){b.setAttribute('aria-pressed',dark()?'true':'false')}
  b.hidden=false; sync();
  b.addEventListener('click',function(){
    var t=dark()?'light':'dark'; root.setAttribute('data-theme',t);
    try{localStorage.setItem('theme',t)}catch(e){} sync();
  });
  if(mq&&mq.addEventListener) mq.addEventListener('change',sync);
})();
/* Header nav: horizontal scroll on narrow screens -- fade hints at the scrollable edges, current item scrolled into view. */
(function(){
  var n=document.querySelector('.top nav:not(.langs)'); if(!n) return;
  function upd(){var o=n.scrollWidth-n.clientWidth>2;n.classList.toggle('sc',o);n.classList.toggle('at-s',n.scrollLeft<=2);n.classList.toggle('at-e',n.scrollLeft>=n.scrollWidth-n.clientWidth-2)}
  var c=n.querySelector('[aria-current]'); if(c&&n.scrollWidth>n.clientWidth) n.scrollLeft=Math.max(0,c.offsetLeft-n.clientWidth/2+c.offsetWidth/2);
  n.addEventListener('scroll',upd,{passive:true}); window.addEventListener('resize',upd); upd();
})();
