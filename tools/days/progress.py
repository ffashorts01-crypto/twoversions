import json,glob,os,sys
nxt=sys.argv[1] if len(sys.argv)>1 else ''
lines=['# Editor progress (manual cards, no OpenAI API)','','| day | cards |','|---|---|']
tot=0
for p in sorted(glob.glob('/workspace/compare-site-v2/data/editor/2026-*.json')):
    n=len(json.load(open(p))); tot+=n; lines.append('| %s | %d |'%(os.path.basename(p)[:10],n))
lines+=['| total | %d |'%tot,'','Workflow: tools/days/mkDDx.py -> tools/days/out/cDDx.json -> `python3 tools/days/merge.py DD` -> `python3 tools/editor_import.py --check` then `--quiet`.',
'Next step: '+nxt,'API spend: $0 this session; $0.9030 total recorded (all before the switch).']
open('/workspace/compare-site-v2/data/editor/PROGRESS.md','w').write('\n'.join(lines)+'\n'); print(tot)
