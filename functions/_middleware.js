// Canonical host: the site lives at https://twoversions.com (see <link rel="canonical">).
// The Pages default host twoversions-site.pages.dev, its deployment / branch aliases (*.twoversions-site.pages.dev)
// and www.twoversions.com answer with a permanent redirect to the same path + query on the apex domain.
// Merged duplicate cards: when a page is missing (404) and /assets/redirects.json (written by build.py) maps its path to the
// card it was merged into, answer with a 301 to that card.
const CANON = 'twoversions.com';
export async function onRequest(ctx) {
  const url = new URL(ctx.request.url);
  const h = url.hostname.toLowerCase();
  if (h === 'www.' + CANON || h === 'twoversions-site.pages.dev' || h.endsWith('.twoversions-site.pages.dev')) {
    return new Response(null, { status: 301, headers: { Location: 'https://' + CANON + url.pathname + url.search, 'Cache-Control': 'public, max-age=86400' } });
  }
  const res = await ctx.next();
  if (res.status === 404 && (ctx.request.method === 'GET' || ctx.request.method === 'HEAD')) {
    try {
      const r = await ctx.env.ASSETS.fetch(new URL('/assets/redirects.json', url.origin));
      if (r.ok) {
        const map = await r.json();
        const p = url.pathname.endsWith('/') ? url.pathname : url.pathname + '/';
        const to = map[p];
        if (to) return new Response(null, { status: 301, headers: { Location: to + url.search, 'Cache-Control': 'public, max-age=3600' } });
      }
    } catch (e) { /* fall through to the 404 */ }
  }
  return res;
}
