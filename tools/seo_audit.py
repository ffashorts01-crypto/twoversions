#!/usr/bin/env python3
"""Offline SEO audit of the built site (public/): JSON-LD syntax + types, canonical, hreflang reciprocity, robots,
title/description uniqueness + length, sitemap consistency. No network. Usage: python3 tools/seo_audit.py [public_dir]"""
import os, re, sys, json, html, collections
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'public')
BASE = 'https://twoversions.com'
def url_of(rel):
    p = '/' + rel.replace(os.sep, '/')
    return BASE + (p[:-len('index.html')] if p.endswith('index.html') else p)
pages = {}
for d, _, fs in os.walk(OUT):
    for f in fs:
        if f.endswith('.html'): pages[url_of(os.path.relpath(os.path.join(d, f), OUT))] = os.path.join(d, f)
stats = collections.Counter(); probs = collections.defaultdict(list); titles = collections.defaultdict(list); descs = collections.defaultdict(list)
alts = {}; robots = {}; types = collections.Counter()
for u, fp in pages.items():
    h = open(fp, encoding='utf-8').read()
    if u.endswith('/404.html'): continue
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
        try:
            obj = json.loads(m.group(1)); stats['jsonld_blocks'] += 1
            for o in (obj if isinstance(obj, list) else [obj]):
                t = o.get('@type'); types[t if isinstance(t, str) else str(t)] += 1
        except ValueError as ex: probs['jsonld_invalid'].append(u)
    if '<script type="application/ld+json">' in h: stats['pages_with_jsonld'] += 1
    c = re.findall(r'<link rel="canonical" href="([^"]+)">', h)
    if len(c) != 1: probs['canonical_count'].append(u)
    elif html.unescape(c[0]) != u: probs['canonical_not_self'].append((u, c[0]))
    r = re.search(r'<meta name="robots" content="([^"]+)">', h); robots[u] = r.group(1) if r else ''
    a = dict((l, html.unescape(x)) for l, x in re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', h)); alts[u] = a
    t = re.search(r'<title>(.*?)</title>', h, re.S); d = re.search(r'<meta name="description" content="([^"]*)">', h)
    if t: titles[html.unescape(t.group(1))].append(u)
    else: probs['no_title'].append(u)
    if d: descs[html.unescape(d.group(1))].append(u)
    elif 'noindex' not in robots[u]: probs['no_desc'].append(u)
    tl = len(html.unescape(t.group(1))) if t else 0
    if 'noindex' not in robots[u]:
        if tl > 70: stats['title_gt70'] += 1
        if tl < 20: probs['title_lt20'].append(u)
        if d and not (50 <= len(html.unescape(d.group(1))) <= 170): probs['desc_len'].append((u, len(d.group(1))))
for u, a in alts.items():
    if robots.get(u, '').startswith('noindex') or not a: 
        if not a and not u.endswith('/404.html') and u != BASE + '/': stats['no_hreflang'] += 1; probs['no_hreflang'].append(u)
        continue
    if set(a) != {'ru', 'uk', 'en', 'x-default'}: probs['hreflang_set'].append((u, sorted(a)))
    if u not in a.values(): probs['hreflang_no_self'].append(u)
    for l, v in a.items():
        if l == 'x-default': continue
        if v not in pages: probs['hreflang_target_missing'].append((u, v)); continue
        if u not in alts.get(v, {}).values(): probs['hreflang_not_reciprocal'].append((u, v))
for t, us in titles.items():
    if len(us) > 1: probs['dup_title'].append((t, us[:3]))
for t, us in descs.items():
    if len(us) > 1: probs['dup_desc'].append((t[:60], us[:3]))
sm = os.path.join(OUT, 'sitemap.xml')
if os.path.exists(sm):
    locs = re.findall(r'<loc>([^<]+)</loc>', open(sm, encoding='utf-8').read())
    stats['sitemap_urls'] = len(locs)
    for l in locs:
        l = html.unescape(l)
        if l not in pages: probs['sitemap_missing_page'].append(l)
        elif 'noindex' in robots.get(l, ''): probs['sitemap_noindex'].append(l)
    idx = {u for u, r in robots.items() if 'noindex' not in r and not u.endswith('404.html')}
    stats['indexable_not_in_sitemap'] = len(idx - set(html.unescape(x) for x in locs)); probs['indexable_not_in_sitemap'] = sorted(idx - set(html.unescape(x) for x in locs))[:20]
print('pages:', len(pages)); print('stats:', dict(stats)); print('jsonld types:', dict(types.most_common()))
for k, v in probs.items():
    if v: print('PROBLEM %s: %d e.g. %s' % (k, len(v), v[:3]))
