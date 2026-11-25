/* Events map: loads Leaflet (self-hosted) and the points JSON only on the map page. No front lines, no claims about control. */
/* Perf: the map box has a fixed height in CSS (no layout shift); Leaflet, its CSS and the data are fetched only when the box
   is (nearly) on screen, after the page has painted (IntersectionObserver + idle callback), and tiles are drawn into <canvas>
   elements, which are not LCP candidates, so slow third-party tiles cannot become the page's Largest Contentful Paint. */
(function () {
  var el = document.getElementById('map'); if (!el) return;
  var cfg = JSON.parse(el.getAttribute('data-cfg')), started = false;
  function load(src, cb) { var s = document.createElement('script'); s.src = src; s.onload = cb; document.head.appendChild(s); }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function idle(fn) { if (window.requestIdleCallback) requestIdleCallback(fn, {timeout: 1200}); else setTimeout(fn, 200); }
  function start() { if (started) return; started = true; idle(init); }
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) { if (es.some(function (x) { return x.isIntersecting; })) { io.disconnect(); start(); } }, {rootMargin: '300px 0px'});
    io.observe(el);
  } else start();
  function init() {
  var css = document.createElement('link'); css.rel = 'stylesheet'; css.href = cfg.css; document.head.appendChild(css);
  load(cfg.leaflet, function () {
    fetch(cfg.data).then(function (r) { return r.json(); }).then(function (D) {
      el.classList.add('on');
      var map = L.map(el, {scrollWheelZoom: false, worldCopyJump: true}).setView([49.5, 34], 5);
      /* same raster tiles as L.tileLayer, painted into a canvas (identical look; a cross-origin image only taints the canvas, which is never read) */
      var CanvasTiles = L.GridLayer.extend({createTile: function (c, done) {
        var t = document.createElement('canvas'), sz = this.getTileSize(), img = new Image();
        t.width = sz.x; t.height = sz.y; img.decoding = 'async';
        img.onload = function () { try { t.getContext('2d').drawImage(img, 0, 0, sz.x, sz.y); } catch (e) {} done(null, t); };
        img.onerror = function (e) { done(e, t); };
        img.src = L.Util.template(this.options.url, {z: c.z, x: c.x, y: c.y});
        return t;
      }});
      /* Esri World Light/Dark Gray Canvas (raster, no key, attribution required); OSM's own tile servers block unidentified heavy use and CARTO now needs an API key */
      var root = document.documentElement, mq = window.matchMedia && matchMedia('(prefers-color-scheme: dark)');
      function isDark() { var t = root.getAttribute('data-theme'); return t ? t === 'dark' : !!(mq && mq.matches); }
      var attr = 'Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Esri, HERE, Garmin, FAO, NOAA, USGS';
      var tiles = null, cur = null;
      function setTiles() { var st = isDark() ? 'World_Dark_Gray_Base' : 'World_Light_Gray_Base'; if (st === cur) return; cur = st; if (tiles) map.removeLayer(tiles);
        tiles = new CanvasTiles({url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/' + st + '/MapServer/tile/{z}/{y}/{x}', maxZoom: 12, minZoom: 3, attribution: attr}).addTo(map); }
      setTiles();
      new MutationObserver(setTiles).observe(root, {attributes: true, attributeFilter: ['data-theme']});
      if (mq && mq.addEventListener) mq.addEventListener('change', setTiles);
      map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');   /* neutral: no flag in the attribution */
      var layer = L.layerGroup().addTo(map);
      var last = new Date(D.last + 'T23:59:59');
      function draw(days) {
        layer.clearLayers();
        var cut = days ? new Date(last.getTime() - days * 864e5) : null, shown = 0;
        D.p.forEach(function (pt) {
          var cs = pt[3].map(function (i) { return D.c[i]; }).filter(function (c) { return !cut || new Date(c[0] + 'T12:00:00') >= cut; });
          if (!cs.length) return;
          shown += cs.length;
          var r = Math.min(26, 5 + Math.sqrt(cs.length) * 3);
          var m = L.circleMarker([pt[0], pt[1]], {radius: r, weight: 1.5, color: '#3d4a5c', fillColor: '#5b7083', fillOpacity: .55});
          var h = '<strong>' + esc(pt[2]) + '</strong> <span class="mc">' + esc(cfg.count.replace('{n}', cs.length)) + '</span><ul class="mp">';
          cs.slice(0, 8).forEach(function (c) { h += '<li><a href="' + esc(c[2]) + '">' + esc(c[1]) + '</a> <small>' + esc(c[3]) + '</small></li>'; });
          h += '</ul>' + (cs.length > 8 ? '<small>' + esc(cfg.more.replace('{n}', cs.length - 8)) + '</small>' : '');
          m.bindPopup(h, {maxWidth: 320}); m.bindTooltip(pt[2] + ' · ' + cs.length);
          layer.addLayer(m);
        });
        var st = document.getElementById('map-n'); if (st) st.textContent = cfg.shown.replace('{n}', shown);
      }
      var btns = document.querySelectorAll('[data-days]');
      btns.forEach(function (b) {
        b.hidden = false;
        b.addEventListener('click', function () { btns.forEach(function (x) { x.setAttribute('aria-pressed', 'false'); }); b.setAttribute('aria-pressed', 'true'); draw(+b.getAttribute('data-days')); });
      });
      draw(0);
    });
  });
  }
})();
