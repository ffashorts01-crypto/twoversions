#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validation of the generated site: well-formed HTML (tag balance, unique ids), internal link / asset / fragment
resolution, SEO tags (title, description, canonical, hreflang reciprocity, h1, JSON-LD), sitemap completeness.
Usage:  python3 tools/check.py [public_dir] [base_url]        (also called by build.py)
        python3 tools/check.py --http http://localhost:8089   (crawl a running server from /, following internal links)"""
import os, sys, re, json, collections
from html.parser import HTMLParser
from urllib.parse import urlparse, urldefrag, unquote
import xml.etree.ElementTree as ET

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}

class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.ids, self.links, self.h, self.title = [], [], [], [], [], ''
        self.meta, self.alts, self.canon, self.ld, self.lang, self._cap = {}, {}, None, [], None, None
        self.dup_ids = set(); self.doctype = False
    def handle_decl(self, d): self.doctype = d.lower().startswith('doctype html')
    def handle_starttag(self, tag, attrs):
        a = dict(attrs); ln = self.getpos()[0]
        if 'id' in a:
            if a['id'] in self.ids: self.dup_ids.add(a['id'])
            self.ids.append(a['id'])
        if tag == 'html': self.lang = a.get('lang')
        if tag == 'a' and 'href' in a: self.links.append(('a', a['href'], ln))
        if tag == 'link' and 'href' in a:
            rel = a.get('rel', '')
            if rel == 'canonical': self.canon = a['href']
            elif rel == 'alternate' and 'hreflang' in a: self.alts[a['hreflang']] = a['href']
            else: self.links.append(('link', a['href'], ln))
        if tag in ('script', 'img') and 'src' in a: self.links.append((tag, a['src'], ln))
        if tag == 'meta':
            k = a.get('name') or a.get('property')
            if k: self.meta[k] = a.get('content', '')
            if k == 'og:image' or k == 'og:url': self.links.append(('meta', a.get('content', ''), ln))
        if re.fullmatch(r'h[1-6]', tag): self.h.append(int(tag[1])); self._cap = 'h'
        if tag == 'title': self._cap = 'title'
        if tag == 'script' and a.get('type') == 'application/ld+json': self._cap = 'ld'; self.ld.append('')
        if tag not in VOID: self.stack.append((tag, ln))
    def handle_endtag(self, tag):
        if tag in VOID: self.errors.append('stray end tag </%s> line %d' % (tag, self.getpos()[0])); return
        if not self.stack or self.stack[-1][0] != tag:
            self.errors.append('mismatched </%s> line %d (open: %s)' % (tag, self.getpos()[0], self.stack[-1] if self.stack else None))
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag: del self.stack[i:]; break
        else: self.stack.pop()
        if tag == 'title' or tag == 'script' or re.fullmatch(r'h[1-6]', tag): self._cap = None
    def handle_data(self, d):
        if self._cap == 'title': self.title += d
        elif self._cap == 'ld': self.ld[-1] += d
    def close(self):
        super().close()
        for t, ln in self.stack: self.errors.append('unclosed <%s> from line %d' % (t, ln))

def local_path(out, base, url):
    """Map an internal URL to a file path (None if external)."""
    if url.startswith(base): url = url[len(base):] or '/'
    u = urlparse(url)
    if u.scheme in ('mailto', 'tel', 'javascript'): return None
    if u.scheme or u.netloc: return None
    p = unquote(u.path)
    if not p.startswith('/'): return 'REL'
    fp = os.path.join(out, p.lstrip('/'))
    if p.endswith('/'): fp = os.path.join(fp, 'index.html')
    return fp

def run(out, base):
    out = os.path.abspath(out); base = base.rstrip('/')
    errors, warns = [], []
    files = []
    for dp, _, fs in os.walk(out):
        for f in fs:
            if f.endswith('.html'): files.append(os.path.join(dp, f))
    parsed = {}
    for fp in sorted(files):
        rel = '/' + os.path.relpath(fp, out)
        p = P(); p.feed(open(fp, encoding='utf-8').read()); p.close(); parsed[rel] = p
        for x in p.errors: errors.append('%s: HTML: %s' % (rel, x))
        for i in p.dup_ids: errors.append('%s: duplicate id %s' % (rel, i))
    # --- content rules: anonymity (only the CONTACT address may appear), event pages carry the badge, error link and automatic-summary note
    allowed_mail = {os.environ.get('CONTACT_EMAIL', 'twoversions@proton.me').lower()}
    import urllib.parse as _up
    for fp in files:
        rel = '/' + os.path.relpath(fp, out); raw = open(fp, encoding='utf-8').read()
        for m in re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', _up.unquote(raw)):
            if m.lower() not in allowed_mail and not m.lower().endswith(('.png', '.svg')): errors.append('%s: unexpected e-mail address %s' % (rel, m))
        if re.match(r'^/(ru|uk|en)/\d{4}-\d\d-\d\d-', rel):
            if 'class="conf c-' not in raw: errors.append('%s: event page without confidence badge' % rel)
            if 'mailto:' not in raw or 'ID%3A' not in raw and 'ID:' not in _up.unquote(raw): errors.append('%s: event page without error-report link' % rel)
            if '"additionalProperty"' not in raw: errors.append('%s: JSON-LD without confidence property' % rel)
            if 'class="auto"' not in raw: errors.append('%s: no automatic-summary marker' % rel)
    nlinks = 0; ext = set(); bad = []
    titles, descs = collections.defaultdict(list), collections.defaultdict(list)
    for rel, p in parsed.items():
        if not p.doctype: errors.append('%s: missing doctype' % rel)
        if not p.lang: errors.append('%s: missing <html lang>' % rel)
        if rel == '/404.html': continue
        if not p.title.strip(): errors.append('%s: empty <title>' % rel)
        if p.h.count(1) != 1: errors.append('%s: %d <h1>' % (rel, p.h.count(1)))
        for a, b in zip(p.h, p.h[1:]):
            if b > a + 1: errors.append('%s: heading level jump h%d -> h%d' % (rel, a, b)); break
        titles[p.title.strip()].append(rel); descs[p.meta.get('description', '')].append(rel)
        if not p.meta.get('description'): errors.append('%s: missing meta description' % rel)
        for k in ('og:title', 'og:description', 'og:url', 'og:image', 'twitter:card'):
            if not p.meta.get(k): errors.append('%s: missing %s' % (rel, k))
        if not p.canon: errors.append('%s: missing canonical')
        else:
            want = base + ('/' if rel == '/index.html' else rel[:-len('index.html')])
            if p.canon != want: errors.append('%s: canonical %s != %s' % (rel, p.canon, want))
            if p.meta.get('og:url') != p.canon: errors.append('%s: og:url != canonical' % rel)
        # hreflang: complete + reciprocal
        if set(p.alts) != {'ru', 'uk', 'en', 'x-default'}: errors.append('%s: hreflang set %s' % (rel, sorted(p.alts)))
        for l, href in p.alts.items():
            if l == 'x-default': continue
            lp = local_path(out, base, href)
            q = parsed.get(os.sep + os.path.relpath(lp, out)) if lp and lp != 'REL' else None
            if not q: errors.append('%s: hreflang %s -> %s not found' % (rel, l, href)); continue
            if q.alts.get(l) != href or q.canon != href: errors.append('%s: hreflang %s not reciprocal/self-canonical' % (rel, l))
        for raw in p.ld:
            try:
                j = json.loads(raw)
                if '@context' not in j: errors.append('%s: JSON-LD without @context' % rel)
            except Exception as ex: errors.append('%s: bad JSON-LD: %s' % (rel, ex))
        for kind, href, ln in p.links:
            if not href or href.startswith('#') and False: pass
            u, frag = urldefrag(href)
            nlinks += 1
            fp = local_path(out, base, u) if u or not frag else out + rel
            if fp is None:
                if href.startswith('mailto:'): continue
                ext.add(href); continue
            if fp == 'REL':
                if href.startswith('#'): fp = os.path.join(out, rel.lstrip('/'))
                else: bad.append('%s:%d relative URL %s' % (rel, ln, href)); continue
            if not os.path.isfile(fp):
                bad.append('%s:%d BROKEN %s %s' % (rel, ln, kind, href)); continue
            if frag and fp.endswith('.html'):
                tgt = parsed.get('/' + os.path.relpath(fp, out))
                if tgt is not None and frag not in tgt.ids: bad.append('%s:%d missing fragment #%s in %s' % (rel, ln, frag, href))
    for k, v in titles.items():
        if len(v) > 1: warns.append('duplicate <title> %r on %s' % (k[:60], v[:4]))
    for k, v in descs.items():
        if len(v) > 1 and k: warns.append('duplicate meta description %r on %s' % (k[:60], v[:4]))
    errors += bad
    # sitemap
    sm = os.path.join(out, 'sitemap.xml'); n_sm = 0
    if not os.path.isfile(sm): errors.append('sitemap.xml missing')
    else:
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9', 'x': 'http://www.w3.org/1999/xhtml'}
        root = ET.parse(sm).getroot(); locs = set()
        for u in root.findall('s:url', ns):
            loc = u.find('s:loc', ns).text; locs.add(loc); n_sm += 1
            if len(u.findall('x:link', ns)) != 4: errors.append('sitemap %s: expected 4 xhtml:link' % loc)
            lp = local_path(out, base, loc)
            if not lp or not os.path.isfile(lp): errors.append('sitemap URL has no file: %s' % loc)
        for rel, p in parsed.items():
            if rel == '/404.html': continue
            if p.canon not in locs and 'noindex' not in p.meta.get('robots', ''): errors.append('page not in sitemap: %s' % p.canon)
            if p.canon in locs and 'noindex' in p.meta.get('robots', ''): errors.append('noindex page in sitemap: %s' % p.canon)
        if len(locs) != n_sm: errors.append('duplicate sitemap URLs')
    if not os.path.isfile(os.path.join(out, 'robots.txt')) or 'Sitemap:' not in open(os.path.join(out, 'robots.txt')).read(): errors.append('robots.txt missing/without Sitemap')
    print('CHECK: %d HTML files parsed, %d link/asset references checked (%d external, not fetched), %d sitemap URLs, %d problems, %d warnings' % (len(parsed), nlinks, len(ext), n_sm, len(errors), len(warns)))
    for x in errors[:60]: print('  PROBLEM', x)
    if len(errors) > 60: print('  ... %d more' % (len(errors) - 60))
    for x in warns[:40]: print('  WARNING', x)
    if len(warns) > 40: print('  ... %d more warnings' % (len(warns) - 40))
    return 1 if errors else 0

def crawl_http(root):
    import urllib.request
    root = root.rstrip('/'); seen, todo, bad = set(), ['/'], []
    while todo:
        path = todo.pop()
        if path in seen: continue
        seen.add(path)
        try:
            r = urllib.request.urlopen(root + path, timeout=20); body = r.read(); ctype = r.headers.get('Content-Type', '')
        except Exception as ex:
            bad.append((path, str(ex))); continue
        if 'html' in ctype:
            p = P(); p.feed(body.decode('utf-8'))
            for kind, href, ln in p.links:
                if kind == 'meta' or href.startswith(('mailto:', 'http://', 'https://')): continue
                u = urldefrag(href)[0]
                if u.startswith('/') : todo.append(u)
    print('HTTP CRAWL %s: %d URLs fetched, %d failures' % (root, len(seen), len(bad)))
    for b in bad[:30]: print('  FAIL', b)
    return 1 if bad else 0

if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--http': sys.exit(crawl_http(sys.argv[2]))
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, '..', 'public')
    base = sys.argv[2] if len(sys.argv) > 2 else 'http://localhost:8089'
    sys.exit(run(out, base))
