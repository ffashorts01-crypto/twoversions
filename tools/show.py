#!/usr/bin/env python3
"""Editor helper: print article texts.  python3 tools/show.py 123 456 [--n 1500]"""
import sys, os, re, sqlite3
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = [x for x in sys.argv[1:]]; n = 1500
if '--n' in args: i = args.index('--n'); n = int(args[i + 1]); del args[i:i + 2]
db = sqlite3.connect(os.path.join(HERE, 'data', 'articles.db'))
for x in args:
    r = db.execute('select id,source,grp,lang,date_local,title,text,url from articles where id=?', (int(x),)).fetchone()
    if not r: print('## %s: not found' % x); continue
    print('## %d | %s | %s | %s | %s\n# %s\n%s\n' % (r[0], r[1], r[2], r[3], r[4][:16], r[5], re.sub(r'[ \t]+', ' ', r[6])[:n]))
