#!/usr/bin/env python3
"""Submit all sitemap URLs to IndexNow (Bing, Yandex, etc.). Usage: python3 tools/indexnow.py [--base https://twoversions.com]"""
import json, os, re, sys, urllib.request
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
base = sys.argv[sys.argv.index('--base')+1] if '--base' in sys.argv else 'https://twoversions.com'
key = open(os.path.join(HERE, 'data', 'indexnow.key')).read().strip()
urls = re.findall(r'<loc>([^<]+)</loc>', open(os.path.join(HERE, 'public', 'sitemap.xml')).read())
host = base.split('//')[1]
for ep in ('https://api.indexnow.org/indexnow', 'https://yandex.com/indexnow'):
    for i in range(0, len(urls), 9000):
        body = json.dumps({'host': host, 'key': key, 'keyLocation': '%s/%s.txt' % (base, key), 'urlList': urls[i:i+9000]}).encode()
        req = urllib.request.Request(ep, data=body, headers={'Content-Type': 'application/json; charset=utf-8'})
        try:
            r = urllib.request.urlopen(req, timeout=60); print(ep, len(urls[i:i+9000]), 'urls ->', r.status)
        except urllib.error.HTTPError as e:
            print(ep, 'HTTP', e.code, e.read()[:200])
