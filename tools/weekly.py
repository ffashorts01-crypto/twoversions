#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Weekly review "Разбор недели": for a closed ISO week, one short neutral article (ru, qwen3.7-plus) about where the sides' versions
diverged most, based on published cards labelled 'contradict' / 'details'; translated to uk/en through pipeline/translate.py
(same code checks: numbers, script, place names). Cached in data/weekly.json -- each week is generated ONCE.
  python3 tools/weekly.py --auto            # last closed week if missing (auto_run calls this; no-op most runs)
  python3 tools/weekly.py --weeks 2026-W46  # explicit weeks
Cost ~ $0.01-0.02 per week; hard cap WEEKLY_MAX_USD (default 0.05) on top of the normal run/day budget caps."""
import os, sys, json, re, argparse, datetime as dt, fcntl
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline import common
from pipeline.common import CFG, log
from pipeline.llm import LLM, BudgetExceeded
from pipeline import translate, checks
OUT = os.path.join(ROOT, 'data', 'weekly.json')
MAX_CARDS = 8
MIN_CARDS = 3
MAX_USD = float(os.environ.get('WEEKLY_MAX_USD', '0.05'))
SYSTEM = """Ты — редактор нейтрального сайта, который сравнивает, как одно событие описывают российские, украинские и независимые СМИ.
Напиши короткий обзор недели на русском языке: где версии сторон расходились сильнее всего. Используй ТОЛЬКО данные карточек ниже.
Правила: нейтральный тон; каждое утверждение приписывай источнику («российские СМИ сообщают», «по данным украинской стороны», «независимые издания пишут»);
не делай собственных выводов о том, кто прав, и не оценивай достоверность сверх того, что сказано в карточках; не добавляй фактов, чисел, имён и дат, которых нет в карточках;
не используй оценочные слова («ложь», «пропаганда», «героически») от своего имени. Ссылайся на карточки по их id.
Верни ТОЛЬКО JSON: {"title": "заголовок до 90 знаков, без слов «обзор недели»", "lede": "2 предложения: о чём неделя и в чём главные расхождения",
"sections": [{"h": "подзаголовок до 70 знаков", "text": "2–4 предложения: что говорит каждая сторона и в чём расхождение", "cards": [id, ...]}, ...] (3–5 секций),
"outro": "1 предложение: какие темы остались без подтверждения независимыми источниками (если это следует из карточек), иначе пустая строка"}"""

def iso_week(d): y, w, _ = d.isocalendar(); return '%d-W%02d' % (y, w)
def week_range(wk):
    y, w = int(wk[:4]), int(wk[6:]); a = dt.date.fromisocalendar(y, w, 1); return a, a + dt.timedelta(days=6)

def load_cards():
    d = json.load(open(os.path.join(ROOT, 'data', 'cards_auto.json'), encoding='utf-8'))
    try: cache = json.load(open(os.path.join(ROOT, 'data', 'divergence_cache.json'), encoding='utf-8'))
    except (OSError, ValueError): cache = {}
    out = []
    for r in d['cards']:
        if r.get('status') != 'published': continue
        c = r['card']; lab = c.get('divergence') or (cache.get('a%d' % r['event_id']) or cache.get(str(r['event_id'])) or {}).get('label')
        out.append(dict(id=r['event_id'], slug=r['slug'], start=dt.date.fromisoformat(c['start']), div=lab, title=c['title'], place=c.get('place') or '',
                        d=c['diverge'], ru=c['sides']['ru']['summary'], ua=c['sides']['ua']['summary'], ind=c['sides']['ind']['summary'],
                        n_src=(r.get('confidence') or {}).get('features', {}).get('n_sources', 0), conf=(r.get('confidence') or {}).get('label')))
    return out

def pick(cards, wk):
    a, b = week_range(wk)
    cs = [c for c in cards if a <= c['start'] <= b and c['div'] in ('contradict', 'details') and c['ru'] and c['ua']]
    cs.sort(key=lambda c: (c['div'] != 'contradict', -c['n_src'], c['start'].isoformat()))
    return cs[:MAX_CARDS]

def trunc(s, n): s = re.sub(r'\s+', ' ', s or '').strip(); return s if len(s) <= n else s[:n].rsplit(' ', 1)[0] + '…'

def generate(llm, wk, cs):
    spent0 = llm.run_spend
    a, b = week_range(wk)
    items = [dict(id=c['id'], date=c['start'].isoformat(), place=c['place'], title=c['title'], divergence=('противоречат' if c['div'] == 'contradict' else 'расходятся в деталях'),
                  how_differ=trunc(c['d'], 600), russian_media=trunc(c['ru'], 450), ukrainian_media=trunc(c['ua'], 450), independent=trunc(c['ind'], 300) if c['ind'] else None) for c in cs]
    user = 'Неделя %s (%s — %s). Карточки:\n%s' % (wk, a.isoformat(), b.isoformat(), json.dumps(items, ensure_ascii=False))
    src_text = user
    ids = {c['id'] for c in cs}
    obj = None
    for attempt in range(2):
        obj, _ = llm.call('weekly', CFG['MODEL_HARD'], SYSTEM, user, max_out=2500)
        probs = []
        if not isinstance(obj, dict) or not obj.get('title') or not isinstance(obj.get('sections'), list) or not obj['sections']: probs.append('bad structure')
        else:
            txt = ' '.join([obj['title'], obj.get('lede', ''), obj.get('outro', '')] + [s.get('h', '') + ' ' + s.get('text', '') for s in obj['sections']])
            bad = checks.ungrounded_numbers(txt, [src_text])
            if bad: probs.append('numbers not in the cards: %s' % ', '.join(bad[:5]))
            for s in obj['sections']:
                s['cards'] = [int(x) for x in s.get('cards', []) if str(x).isdigit() and int(x) in ids]
            if not any(s['cards'] for s in obj['sections']): probs.append('no valid card ids')
        if not probs: break
        log.warning('weekly %s rejected: %s', wk, '; '.join(probs)); user += '\n\nПредыдущий ответ отклонён: %s. Исправь.' % '; '.join(probs); obj = None
        if llm.run_spend - spent0 > MAX_USD: break
    if not obj: return None
    flat = dict(t=obj['title'], lede=obj.get('lede', ''), outro=obj.get('outro') or None)
    for i, s in enumerate(obj['sections']): flat['h%d' % i] = s.get('h', ''); flat['x%d' % i] = s.get('text', '')
    flat = {k: v for k, v in flat.items() if v is not None}
    res = dict(week=wk, cards=[c['id'] for c in cs], sections_cards=[s['cards'] for s in obj['sections']], ru=flat, generated=common.now_iso(), model=CFG['MODEL_HARD'])
    uk = None
    for lang in ('uk', 'en'):
        if llm.run_spend - spent0 > MAX_USD: log.warning('weekly %s: WEEKLY_MAX_USD reached before %s translation', wk, lang); return None
        tr, path = translate.translate(llm, flat, lang, bridge=uk)
        if not tr: log.warning('weekly %s: %s translation withheld (%s)', wk, lang, path); return None
        res[lang] = tr
        if lang == 'uk': uk = tr
    res['cost'] = round(llm.run_spend - spent0, 5)
    return res

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--auto', action='store_true'); ap.add_argument('--weeks', default='')
    a = ap.parse_args()
    common.get_logger(to_file=True)
    lock = open(os.path.join(common.DATA, 'update.lock'), 'w')
    if os.environ.get('UPDATE_LOCK_HELD') != '1':
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: print('weekly: lock busy'); return 1
    try: data = json.load(open(OUT, encoding='utf-8'))
    except (OSError, ValueError): data = {}
    today = common.now_local().date()
    if a.weeks: weeks = [w.strip().upper() for w in a.weeks.split(',') if w.strip()]
    else: weeks = [iso_week(today - dt.timedelta(days=7))]          # the last closed week
    cards = load_cards()
    todo = []
    for wk in weeks:
        if wk in data: continue
        if week_range(wk)[1] >= today: print('weekly %s: week not closed yet' % wk); continue
        cs = pick(cards, wk)
        if len(cs) < MIN_CARDS: print('weekly %s: only %d divergent cards -- skipped' % (wk, len(cs))); continue
        todo.append((wk, cs))
    if not todo: print('weekly: nothing to do; LLM spend this run: $0.0000 (0 calls)'); return 0
    db = common.connect()
    llm = LLM(db, 'weekly-' + common.now_local().strftime('%Y%m%d-%H%M%S'))
    try:
        for wk, cs in todo:
            r = generate(llm, wk, cs)
            if r: data[wk] = r; json.dump(data, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1); print('weekly %s: ok, %d cards, $%.4f' % (wk, len(cs), r['cost']))
            else: print('weekly %s: failed (will retry next run)' % wk)
    except BudgetExceeded as e: print('weekly: budget cap: %s' % e)
    print('weekly: LLM spend this run: $%.4f (%d calls)' % (llm.run_spend, llm.calls))
    return 0

if __name__ == '__main__':
    sys.exit(main())
