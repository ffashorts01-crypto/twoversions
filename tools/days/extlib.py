"""Shared helpers for extension scripts (patch earlier day files in out/ in place; idempotent)."""
import json, re
OUT = '/workspace/compare-site-v2/tools/days/out/'
def q(i, t): return {'id': i, 'text': t}
def add(c, side, ru, uk, en, quotes=()):
    if c[side] is None:
        c[side] = {'summary': ru, 'quotes': [q(*x) for x in quotes]}; c['uk'][side] = uk; c['en'][side] = en
    else:
        c[side]['summary'] += ' ' + ru; c[side]['quotes'] += [q(*x) for x in quotes]
        c['uk'][side] += ' ' + uk; c['en'][side] += ' ' + en
def setd(c, ru, uk, en): c['diverge'] = ru; c['uk']['d'] = uk; c['en']['d'] = en
EXT = {}; NEW = {}
def ext(key, ids):
    def deco(f): EXT[key] = f; NEW[key] = set(ids); return f
    return deco
def apply(files):
    n = 0; seen = set()
    for f in files:
        a = json.load(open(OUT + f))
        for c in a:
            if c['key'] in EXT:
                seen.add(c['key'])
                if not (set(c['ids']) & NEW[c['key']]):
                    c['ids'] += sorted(NEW[c['key']]); EXT[c['key']](c); n += 1
        json.dump(a, open(OUT + f, 'w'), ensure_ascii=False, indent=1)
    assert seen == set(EXT), set(EXT) - seen
    print('extended', n, 'of', len(EXT))
