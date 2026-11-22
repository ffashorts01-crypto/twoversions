#!/usr/bin/env python3
"""Structural RSS 2.0 validation of every rss.xml under public/ (or a given dir): well-formed XML, required channel/item elements,
RFC 822 dates, absolute links, unique guids, atom:link self."""
import os, sys, email.utils, xml.etree.ElementTree as ET
def check(fp):
    errs = []
    try: root = ET.parse(fp).getroot()
    except ET.ParseError as ex: return ['not well-formed: %s' % ex]
    if root.tag != 'rss' or root.get('version') != '2.0': errs.append('root is not <rss version="2.0">')
    ch = root.find('channel')
    if ch is None: return errs + ['no <channel>']
    for t in ('title', 'link', 'description'):
        if not (ch.findtext(t) or '').strip(): errs.append('channel <%s> missing' % t)
    if ch.find('{http://www.w3.org/2005/Atom}link') is None: errs.append('no atom:link rel=self')
    items = ch.findall('item'); guids = set()
    if not items: errs.append('no items')
    for it in items:
        if not (it.findtext('title') or it.findtext('description')): errs.append('item without title/description')
        lk = it.findtext('link') or ''
        if not lk.startswith('https://'): errs.append('item link not absolute: %r' % lk)
        g = it.findtext('guid')
        if g in guids: errs.append('duplicate guid %s' % g)
        guids.add(g)
        pd = it.findtext('pubDate')
        try: email.utils.parsedate_to_datetime(pd)
        except Exception: errs.append('bad pubDate %r' % pd)
    return errs
if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'public')
    n = bad = 0
    for d, _, fs in os.walk(out):
        for f in fs:
            if f == 'rss.xml':
                n += 1; errs = check(os.path.join(d, f))
                if errs: bad += 1; print(os.path.join(d, f), errs[:5])
    print('RSS: %d feeds checked, %d with problems' % (n, bad))
    sys.exit(1 if bad or not n else 0)
