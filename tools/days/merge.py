import json, glob, sys
d = sys.argv[1]
a = []
for p in sorted(glob.glob('/workspace/compare-site-v2/tools/days/out/c%s?.json' % d)): a += json.load(open(p))
sys.path.insert(0,'/workspace/compare-site-v2/tools/days'); from fixtags import fix
for c in a: c['tags'] = fix(c['tags'])
json.dump(a, open('/workspace/compare-site-v2/data/editor/2026-10-%s.json' % d, 'w'), ensure_ascii=False, indent=1)
print('merged', d, len(a))
