# -*- coding: utf-8 -*-
"""Daily duplicate pass over PUBLISHED cards (point 5 of the Nov 21 pipeline improvements).

The clustering merge pass (cluster.run_merge) works on event titles before cards exist; it misses splits such as
"morning attack" + "aftermath in the afternoon" that were both published. Once a day this pass:
  1. finds candidate pairs deterministically (published cards, start dates <= 1 day apart, same region, similar
     title/place words; no LLM),
  2. confirms each pair with ONE cheap bulk-model call (qwen3.7-flash, ~$0.0001 per pair),
  3. merges confirmed pairs: the loser's articles move to the main card (more relevant articles; tie -> older),
     the loser's card becomes 'merged'; its URLs are redirected (301) to the main card (cards_auto.json 'redirects'
     -> build.py writes public/assets/redirects.json, applied by functions/_middleware.js on 404); the sitemap only
     lists published cards, so it stays consistent. The main card is rewritten in the same run (new input).
Editor-written pairs are never merged automatically; roundup cards are never merged."""
import re, os, json, datetime as dt
from .common import CFG, log, now_iso, BERLIN, DATA, load_json, save_json
from .llm import BudgetExceeded, ContentFiltered

STATE = os.path.join(DATA, 'dedup_state.json')
STOP = set('в во на по и с со из за для под над при от до к о об у а что как это его ее их после около возле рядом также ранее'.split())
TYPE_WORDS = {'дрон': 'drone', 'бпла': 'drone', 'беспил': 'drone', 'ракет': 'missile', 'обстр': 'shelling', 'удар': 'strike', 'атак': 'strike',
              'взрыв': 'blast', 'пожар': 'fire', 'обмен': 'exchange', 'плен': 'exchange', 'переговор': 'talks', 'санкц': 'sanctions'}

SYSTEM = """You compare two published news cards about the war in Ukraine and decide whether they describe the SAME concrete incident (same strike / attack / event at the same place and time window, e.g. a night attack and its aftermath reported later the same day) and should be merged into one card. Different strikes on the same city on different days or at different times, a general overview vs one specific incident, or related-but-separate developments are NOT the same. When in doubt, answer false.
Return ONLY JSON: {"same": true|false, "why": "<= 15 words"}"""

def _words(s):
    out = set()
    for w in re.findall(r'[А-Яа-яЁёA-Za-z]{3,}', (s or '').lower()):
        if w in STOP: continue
        out.add(w[:5])
    return out

GENERIC = {'бпла', 'пво', 'рф', 'сбу', 'всу', 'зсу', 'миноб', 'минис', 'генша', 'росси', 'украи', 'губер', 'мэр', 'ова', 'ога', 'мчс', 'гснс', 'нато',
           'атака', 'удар', 'удары', 'сбити', 'обстр', 'ракет', 'взрыв', 'повре', 'пожар', 'погиб', 'ранен', 'постр', 'работ', 'нейтр', 'паден', 'росси', 'росс',
           'росси', 'росто', 'запад', 'ночна', 'масси', 'комби', 'данны', 'версии', 'верси', 'сообщ', 'заявл', 'данн'}
def _proper(s):
    """Stems (5 letters) of capitalised words that are not sentence-initial generic words: mostly place and person names."""
    out = set()
    toks = re.findall(r'[А-ЯЁA-Z][а-яёa-z]{2,}', s or '')
    for w in toks:
        k = w.lower()[:5]
        if k in GENERIC or k[:4] in {x[:4] for x in GENERIC if len(x) >= 4}: continue
        out.add(k)
    return out

def _types(s):
    s = (s or '').lower()
    return {v for k, v in TYPE_WORDS.items() if k in s}

def _due(force=False):
    st = load_json(STATE, {})
    last = st.get('last_run', '')
    every = float(os.environ.get('DEDUP_EVERY_HOURS', '24'))
    return force or not last or last < (dt.datetime.now(BERLIN) - dt.timedelta(hours=every - 0.25)).isoformat(timespec='seconds')

def candidates(db, days=3):
    since = (dt.datetime.now(BERLIN).date() - dt.timedelta(days=days)).isoformat()
    rows = db.execute("""select c.event_id, c.card, c.model_path, c.first_published, (select count(*) from event_articles ea where ea.event_id=c.event_id and ea.rel='yes') n
                         from cards c join events e on e.id=c.event_id
                         where c.status='published' and e.merged_into is null and e.status!='roundup'""").fetchall()
    cards = []
    for r in rows:
        try: c = json.loads(r['card'])['card']
        except (ValueError, KeyError, TypeError): continue
        if not c or (c.get('end') or c.get('start') or '') < since or c.get('roundup'): continue
        cards.append(dict(eid=r['event_id'], c=c, editor=r['model_path'] == 'editor', fp=r['first_published'] or '', n=r['n'],
                          w=_words(c['title'] + ' ' + (c.get('place') or '')), pw=_words(c.get('place') or ''), ty=_types(c['title'] + ' ' + c['diverge'][:200]),
                          pn=_proper(c['title'] + ' ' + (c.get('place') or ''))))
    pairs = []
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            a, b = cards[i], cards[j]
            if a['editor'] and b['editor']: continue
            if a['c'].get('region') != b['c'].get('region'): continue
            try:
                gap = abs((dt.date.fromisoformat(a['c']['start']) - dt.date.fromisoformat(b['c']['start'])).days)
            except (KeyError, ValueError): continue
            if gap > 1: continue
            if a['ty'] and b['ty'] and not (a['ty'] & b['ty']): continue
            jac = len(a['w'] & b['w']) / max(1, len(a['w'] | b['w']))
            same_place = bool(a['pw']) and a['pw'] == b['pw']
            shared = a['pn'] & b['pn']                      # a shared proper name (place / person) is required
            if shared and (jac >= 0.2 or same_place or len(shared) >= 2):
                pairs.append((jac + (0.2 if same_place else 0) + 0.1 * len(shared), a, b))
    pairs.sort(key=lambda x: -x[0])
    return pairs

def _brief(x):
    c = x['c']
    sides = ' | '.join('%s: %s' % (g, (c['sides'][g]['summary'] or '')[:350]) for g in ('ru', 'ua', 'ind') if c['sides'][g]['summary'])
    return 'TITLE: %s\nPLACE: %s\nDATES: %s..%s\nDIVERGE: %s\n%s' % (c['title'], c.get('place'), c.get('start'), c.get('end'), c['diverge'][:400], sides)

def merge_pair(db, win, lose):
    from .cluster import _touch
    db.execute('update event_articles set event_id=? where event_id=?', (win, lose))
    db.execute("update events set merged_into=?, status='merged' where id=?", (win, lose))
    db.execute("update cards set status='merged' where event_id=?", (lose,))
    _touch(db, win)
    db.commit()

def run_dedup(db, llm, dry=False, force=False, max_pairs=None):
    stats = dict(pairs=0, checked=0, merged=0, calls=0)
    if not force and not _due(): stats['skipped'] = 'not due'; return stats
    pairs = candidates(db)
    stats['pairs'] = len(pairs)
    if dry:
        stats['sample'] = [(round(s, 2), a['eid'], a['c']['title'][:60], b['eid'], b['c']['title'][:60]) for s, a, b in pairs[:15]]
        return stats
    done = set(); mp = max_pairs or int(os.environ.get('DEDUP_MAX_PAIRS', '30'))
    try:
        for sc, a, b in pairs[:mp]:
            if a['eid'] in done or b['eid'] in done: continue
            stats['checked'] += 1
            try:
                obj, _ = llm.call('dedup', CFG['MODEL_BULK'], SYSTEM, 'CARD A:\n%s\n\nCARD B:\n%s' % (_brief(a), _brief(b)), max_out=200, filter_raise=True)
            except ContentFiltered:
                continue
            stats['calls'] += 1
            if not isinstance(obj, dict) or obj.get('same') is not True: continue
            # main card: editor card first, then more relevant articles, then the older one
            win, lose = sorted([a, b], key=lambda x: (not x['editor'], -x['n'], x['fp'] or '9'))
            merge_pair(db, win['eid'], lose['eid'])
            done |= {a['eid'], b['eid']}; stats['merged'] += 1
            log.info('dedup: merged card %d «%s» into %d «%s» (%s)', lose['eid'], lose['c']['title'][:60], win['eid'], win['c']['title'][:60], str(obj.get('why', ''))[:80])
    except BudgetExceeded as ex:
        log.warning('dedup stopped: %s', ex)
    st = load_json(STATE, {}); st['last_run'] = now_iso(); st.setdefault('history', []).append(dict(ts=now_iso(), **{k: v for k, v in stats.items() if k != 'sample'}))
    st['history'] = st['history'][-60:]
    save_json(STATE, st)
    return stats
