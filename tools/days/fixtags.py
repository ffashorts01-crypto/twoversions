import json, glob, sys
sys.path.insert(0, '/workspace/compare-site-v2/src'); import ui
M = {'не проверено независимо': 'не проверено', 'атрибуция': 'расхождение: атрибуция', 'расхождение: атрибуция атаки': 'расхождение: атрибуция',
     'расхождение: характер целей': 'расхождение: характер объектов', 'расхождение: характер цели': 'расхождение: характер объекта',
     'расхождение: ответственность': 'расхождение: кто виноват', 'расхождение: факты': 'расхождение: подтверждение',
     'расхождение: соблюдение перемирия': 'расхождение: подтверждение', 'расхождение: сумма': 'расхождение: числа',
     'расхождение: оценка потерь': 'расхождение: числа', 'расхождение: оценка ущерба': 'расхождение: последствия',
     'нет независимых источников': 'одна из групп не освещает', 'стороны совпадают': None}
def fix(tags):
    out = []
    for t in tags:
        t = M.get(t, t) if t in M else (t if t in ui.TAGS else 'расхождение: формулировки')
        if t and t not in out: out.append(t)
    return out[:3]
if __name__ != "__main__": paths = []
else: paths = glob.glob('/workspace/compare-site-v2/data/editor/2026-10-*.json') + glob.glob('/workspace/compare-site-v2/tools/days/out/c*.json')
n = 0
for p in paths:
    a = json.load(open(p))
    for c in a:
        nt = fix(c['tags'])
        if nt != c['tags']: n += 1; c['tags'] = nt
    json.dump(a, open(p, 'w'), ensure_ascii=False, indent=1)
print('fixed', n)
