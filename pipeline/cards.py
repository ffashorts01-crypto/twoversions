# -*- coding: utf-8 -*-
"""Build, gate, score and store event cards; export data/cards_auto.json; maintain the corrections log."""
import re, json, hashlib, os, sys, datetime as dt
from concurrent.futures import ThreadPoolExecutor
from .common import CFG, ROOT, DATA, log, now_iso, load_json, save_json, BERLIN, DBLOCK
from .llm import BudgetExceeded
from . import summarize, translate, confidence, checks, relevance, importance, roundup

TR = str.maketrans({'а':'a','б':'b','в':'v','г':'h','ґ':'g','д':'d','е':'e','є':'ie','ж':'zh','з':'z','и':'y','і':'i','ї':'i','й':'i','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'iu','я':'ia','ё':'e'})
def slugify(title, n=6):
    t = title.lower().translate(TR)
    words = re.findall(r'[a-z0-9]+', t)
    return '-'.join(words[:n])[:60].strip('-') or 'event'

WITHHOLD_TEXT = {  # reason code -> human text (English, for logs and the public transparency counts)
    'few_articles': 'fewer than 2 relevant articles', 'few_sources': 'fewer than 2 distinct sources among relevant articles',
    'relevance_low': 'relevance filter did not confirm enough of the clustered articles', 'quote_check': 'a side has a summary but no verified verbatim quote',
    'numbers_ungrounded': 'several numbers in the summary are not found in the source texts', 'llm_output_invalid': 'model output invalid or incomplete',
    'translation': 'translation checks failed', 'model_uncertain': 'model reported high uncertainty', 'budget': 'budget cap reached before the card was built',
    'merged': 'merged into another event', 'sol_cap': 'per-run cap of expensive-model events reached',
    'roundup_member': 'included in a daily roundup card',
}
HOLD_HOURS = float(os.environ.get('ONE_SIDED_HOLD_HOURS', '6'))          # one-sided new events wait this long for the other side
ROUNDUP_REFRESH_HOURS = float(os.environ.get('ROUNDUP_REFRESH_HOURS', '6'))

def one_sided(grps):
    """Exactly one of the two war sides (ru / ua) is present (independent outlets do not count as a side)."""
    return ('ru' in grps) != ('ua' in grps)

def event_state(db, eid):
  if roundup.is_roundup(db, eid): return roundup.member_rows(db, eid)
  with DBLOCK:
    rows = [dict(r) for r in db.execute('select a.*, ea.rel from event_articles ea join articles a on a.id=ea.article_id where ea.event_id=? order by a.date_utc', (eid,))]
    return rows

def input_hash(ids):
    return hashlib.sha1((summarize.PROMPT_VERSION + '|' + ','.join(str(i) for i in sorted(ids))).encode()).hexdigest()[:16]

def process_event(db, llm, eid, sol_budget):
    with DBLOCK:
        e = db.execute('select * from events where id=?', (eid,)).fetchone()
        old = db.execute('select * from cards where event_id=?', (eid,)).fetchone()
    rows = event_state(db, eid)
    judged = [r for r in rows if r['rel']]
    rel = [r for r in rows if r['rel'] == 'yes']
    agree = (len(rel) / len(judged)) if judged else None
    h = input_hash([r['id'] for r in rel])
    editor_old = bool(old and old['model_path'] == 'editor')
    if editor_old and old['input_hash'] in (h + '-ed', h + '-edkeep'):
        return 'unchanged'                                  # editor-written card, no new articles -> never rewritten
    if old and old['input_hash'] == h and old['status'] in ('published', 'withheld') and not any(x in (old['reasons'] or '') for x in ('budget', 'sol_cap', 'llm_output_invalid')):
        return 'unchanged'
    if old and old['input_hash'] == h and old['built'] and old['built'] > (dt.datetime.now(BERLIN) - dt.timedelta(hours=6)).isoformat(timespec='seconds'):
        return 'unchanged'                                  # failed recently with the same input: retry at most every 6 hours
    reasons = []
    srcs = {r['source'] for r in rel}
    if len(rel) < CFG['MIN_REL_ARTICLES']: reasons.append('few_articles')
    elif len(srcs) < CFG['MIN_REL_SOURCES']: reasons.append('few_sources')
    if agree is not None and agree < CFG['MIN_REL_SHARE']: reasons.append('relevance_low')
    if reasons:
        _store(db, eid, h, 'withheld', reasons, None, None, '', e)
        return 'withheld'
    rup = roundup.is_roundup(db, eid)
    now = dt.datetime.now(BERLIN)
    # --- point 1: a NEW event reported by only one war side waits up to HOLD_HOURS for the other side (nothing stored, re-checked every run)
    if not rup and not editor_old and not (old and old['first_published']) and one_sided({r['grp'] for r in rel}):
        try: created = dt.datetime.fromisoformat(e['created'])
        except (TypeError, ValueError): created = now - dt.timedelta(hours=HOLD_HOURS)
        if now - created < dt.timedelta(hours=HOLD_HOURS):
            log.info('event %d held (one-sided %s, %.1f h of %.0f h): %s', eid, '+'.join(sorted({r['grp'] for r in rel})), (now - created).total_seconds() / 3600, HOLD_HOURS, (e['title'] or '')[:70])
            return 'held'
    # --- point 3: roundup cards are refreshed at most every ROUNDUP_REFRESH_HOURS while their day is open
    if rup and old and old['status'] == 'published' and old['built'] and rup['day'] >= now.date().isoformat() and \
            old['built'] > (now - dt.timedelta(hours=ROUNDUP_REFRESH_HOURS)).isoformat(timespec='seconds'):
        return 'unchanged'
    prev = None
    if editor_old:
        try: prev = json.loads(old['card'])['card']
        except (ValueError, KeyError, TypeError): prev = None
    old_card = None                                     # previously published card (any path): its updates are carried over; new articles are marked NEW
    if old and old['status'] == 'published' and old['card']:
        try: old_card = json.loads(old['card'])['card']
        except (ValueError, KeyError, TypeError): old_card = None
    old_ids = set((old_card or {}).get('article_ids') or [])
    new_ids = {r['id'] for r in rel if r['id'] not in old_ids} if old_ids else set()
    rinfo = roundup.kind_info(rup['kind']) if rup else None
    res = summarize.summarize_event(llm, e['title'], rel, prev=prev, new_ids=new_ids, roundup=rinfo[3] if rinfo else None, force_bulk=bool(rup))
    if prev and res.get('card') is not None and not _keeps_detail(prev, res['card']):
        _editor_link_only(db, eid, h, rel, 'rewrite shorter than the editor card'); return 'extended'
    card = res.get('card')
    if card is None:
        _store(db, eid, h, 'withheld', ['llm_output_invalid'], None, None, '+'.join(res['path']), e); return 'withheld'
    fl = res['flags']; probs = res['problems']
    if rup:                                              # deterministic roundup title / place / region
        card['title'] = e['title']; card['place'] = rinfo[1]; card['region'] = rinfo[2]
        with DBLOCK: mids = [r[0] for r in db.execute('select event_id from roundup_members where roundup_id=? order by event_id', (eid,))]
        card['roundup'] = dict(kind=rup['kind'], day=rup['day'], members=mids)
        probs = [p for p in probs if p != 'bad title']
    # --- point 4: numbers the sources do not give (incl. casualty figures out of context). The model already got one retry with
    # feedback inside summarize_event; if some remain, drop the sentences that carry them (deterministic) when enough text is left.
    if fl['ungrounded'] >= CFG.get('MAX_UNGROUNDED', 1) and fl.get('ungrounded_by_field'):
        _strip_ungrounded(eid, card, fl, res.get('sel') or {}, rel)
        if fl['ungrounded'] == 0: probs = [p for p in probs if 'numbers not found' not in p]
        else: log.warning('event %d held: numbers not supported by the sources %s', eid, fl.get('ungrounded_by_field'))
    # --- hard gates
    for g in ('ru', 'ua', 'ind'):
        s = card['sides'][g]
        if s['summary'] and not s['quotes']: reasons.append('quote_check')
    if any(p.startswith(('bad title', 'diverge missing', 'no side summaries')) or 'summary missing' in p or 'group without articles' in p or 'English words' in p or 'not Russian' in p or 'summary too long' in p for p in probs): reasons.append('llm_output_invalid')
    if fl['ungrounded'] >= CFG.get('MAX_UNGROUNDED', 1): reasons.append('numbers_ungrounded')   # same rule as tools/editor_import.py
    if fl['uncertainty'] == 'high': reasons.append('model_uncertain')
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        _store(db, eid, h, 'withheld', reasons, None, dict(flags=fl, problems=probs), '+'.join(res['path']), e, card=card); return 'withheld'
    # --- translations
    ru = dict(t=card['title'], p=card['place'] or '—', d=card['diverge'], **{g: card['sides'][g]['summary'] for g in ('ru', 'ua', 'ind')})
    upd = card.pop('update_new', None)
    if upd: ru['u'] = upd['note']                       # translated in the same call as the rest of the card
    tr = {}
    for lang in ('uk', 'en'):
        t, tpath = translate.translate(llm, ru, lang, bridge=tr.get('uk') if lang == 'en' else None)
        if t is None: reasons.append('translation'); break
        tr[lang] = t
    if reasons:
        _store(db, eid, h, 'withheld', reasons, None, dict(flags=fl, problems=probs), '+'.join(res['path']), e, card=card); return 'withheld'
    # --- confidence
    qc = 'pass' if fl['dropped_quotes'] == 0 else 'partial'
    conf = confidence.compute(rel, rel_agreement=agree, model_uncertainty=fl['uncertainty'], model_contradiction=fl['contradiction'],
                              numeric_conflict=fl['numeric_conflict'], numeric_cross=fl['numeric_cross'], quote_check=qc, numbers_ungrounded=fl['ungrounded'])
    conf['conflicts'] = fl.get('conflicts', []); conf['uncertainty_reason'] = fl.get('uncertainty_reason', '')
    dates = sorted(r['date_local'][:10] for r in rel)
    updates = list((old_card or {}).get('updates') or [])
    if upd:
        updates.append(dict(date=dt.datetime.now(BERLIN).date().isoformat(), verdict=upd['verdict'], side=upd['side'], id=upd['id'],
                            text=dict(ru=upd['note'], uk=tr['uk'].pop('u', None) or upd['note'], en=tr['en'].pop('u', None) or upd['note'])))
    else:
        for lang in ('uk', 'en'): tr[lang].pop('u', None)
    ru.pop('u', None)
    card.update(start=dates[0], end=dates[-1], article_ids=[r['id'] for r in rel], tr=tr, tr_ru=ru, updates=updates[-5:])
    if card.get('divergence') is None and (old_card or {}).get('divergence') and sum(1 for g in ('ru', 'ua', 'ind') if card['sides'][g]['summary']) >= 2:
        card['divergence'] = old_card['divergence']
    _store(db, eid, h, 'published', [], conf, dict(flags=fl, problems=probs), '+'.join(res['path']), e, card=card)
    return 'published'

def _strip_ungrounded(eid, card, fl, sel, rel):
    """Remove the sentences that carry unsupported numbers; on success set fl['ungrounded'] = 0 and fl['numbers_stripped']."""
    new = {}
    for fld, bad in fl['ungrounded_by_field'].items():
        txt = card['diverge'] if fld == 'diverge' else card['sides'][fld]['summary']
        out = checks.strip_sentences_with(txt, bad, min_left=60 if fld != 'diverge' else 40)
        if not out or out == txt: return
        new[fld] = out
    alltx = [r['text'] + ' ' + r['title'] for r in rel]
    for fld, out in new.items():
        tx = alltx if fld == 'diverge' else [a['text'] + ' ' + a['title'] for a in sel.get(fld, [])] or alltx
        if checks.ungrounded_numbers(out, tx) or checks.casualty_mismatch(out, tx): return
        if fld != 'diverge' and (summarize.not_russian(out) or not out.strip()): return
    for fld, out in new.items():
        if fld == 'diverge': card['diverge'] = out
        else: card['sides'][fld]['summary'] = out
    n = sum(len(b) for b in fl['ungrounded_by_field'].values())
    log.info('event %d: %d unsupported number(s) removed with their sentences (%s)', eid, n, fl['ungrounded_by_field'])
    fl['numbers_stripped'] = n; fl['ungrounded'] = 0

def _keeps_detail(prev, new):
    """An automatic rewrite of an editor card must not lose detail: total text >= 85% and quotes >= the old count (per side, capped at 2)."""
    L = lambda c: len(c['diverge']) + sum(len(c['sides'][g]['summary'] or '') for g in ('ru', 'ua', 'ind'))
    if L(new) < 0.85 * L(prev): return False
    for g in ('ru', 'ua', 'ind'):
        if len(new['sides'][g]['quotes']) < min(2, len(prev['sides'][g]['quotes'])): return False
    return True

def _editor_link_only(db, eid, h, rel, why):
    """Keep the editor text; add the new relevant articles as extra source links ('also') and widen the date range."""
    with DBLOCK:
        old = db.execute('select * from cards where event_id=?', (eid,)).fetchone()
        pl = json.loads(old['card']); c = pl['card']
        known = set(c.get('article_ids') or [])
        new = [r for r in rel if r['id'] not in known]
        for r in new:
            s = c['sides'][r['grp']]
            if s.get('summary') and r['id'] not in s['also'] and r['id'] not in {a for a, _ in s['quotes']}: s['also'] = (s['also'] + [r['id']])[-8:]
        c['article_ids'] = sorted(known | {r['id'] for r in rel})
        dates = sorted(r['date_local'][:10] for r in rel)
        if dates: c['end'] = max(c.get('end') or dates[-1], dates[-1])
        c['n_rel'] = max(c.get('n_rel') or 0, len(rel))
        db.execute('update cards set card=?, input_hash=?, built=? where event_id=?', (json.dumps(pl, ensure_ascii=False), h + '-edkeep', now_iso(), eid)); db.commit()
        log.info('event %d: editor card kept, %d new source links (%s)', eid, len(new), why)

def _store(*a, **k):
    with DBLOCK:
        db, eid, h, status = a[0], a[1], a[2], a[3]
        old = db.execute('select model_path,status from cards where event_id=?', (eid,)).fetchone()
        if status != 'published' and old and old['model_path'] == 'editor' and old['status'] == 'published':
            # an editor-written card got new articles but the automatic rewrite failed a gate: keep the published editor card as is
            rel = [r for r in event_state(db, eid) if r['rel'] == 'yes']
            _editor_link_only(db, eid, h, rel, 'auto rewrite withheld: %s' % (a[4],)); return
        return _store_locked(*a, **k)

def _store_locked(db, eid, h, status, reasons, conf, extra, path, e, card=None):
    slug = None
    old = db.execute('select * from cards where event_id=?', (eid,)).fetchone()
    if card is not None:
        slug = old['slug'] if old and old['slug'] else None
        if not slug:
            base = '%s-%s' % (card['start'] if card.get('start') else (e['first_art'] or '')[:10], slugify(card['title']))
            slug = base
            taken = {r[0] for r in db.execute('select slug from cards where slug is not null and event_id!=?', (eid,))}
            if slug in taken: slug = '%s-%d' % (base, eid)
    payload = dict(card=card, extra=extra, title=e['title'])
    first_pub = old['first_published'] if old else None
    if status == 'published' and not first_pub: first_pub = now_iso()
    was_pub = old and old['status'] == 'published'
    db.execute('insert or replace into cards(event_id,input_hash,status,reasons,card,confidence,built,first_published,model_path,slug) values(?,?,?,?,?,?,?,?,?,?)',
               (eid, h, status, json.dumps(reasons), json.dumps(payload, ensure_ascii=False), json.dumps(conf, ensure_ascii=False) if conf else None, now_iso(), first_pub, path, slug))
    db.commit()
    if was_pub and status != 'published':
        log_correction(eid, slug or old['slug'], e['title'], reasons, auto=True)

def log_correction(eid, slug, title, reasons, auto=True):
    p = os.path.join(DATA, 'corrections.json')
    data = load_json(p, {'entries': []})
    data['entries'].append(dict(date=now_iso()[:10], kind='withdrawn', auto=auto, event_id='a%d' % eid, slug=slug, title_ru=title, reasons=reasons))
    save_json(p, data)

def run_cards(db, llm, run_id, dry=False, only=None):
    """Write/extend cards, high-importance first. When daily/run budget is tight, defer low-importance clusters (they stay in backlog)."""
    stats = dict(published=0, withheld=0, unchanged=0, budget=0, sol_cap=0, deferred=0, held=0)
    if not dry and not only:
        try:
            rs = roundup.assign(db); log.info('roundups: %s', rs)
            stats['roundup_members'] = rs.get('members', 0)
        except Exception as ex:
            log.exception('roundup assignment failed (ignored): %s', ex)
    else: roundup.ensure(db)
    members = roundup.member_ids(db)
    with DBLOCK: rups = {r[0] for r in db.execute('select id from roundups')}
    ids = [r[0] for r in db.execute("""select e.id from events e where e.merged_into is null order by e.last_art""")]
    ids = [i for i in ids if i not in members]         # members are written only as part of their roundup card
    if only: ids = [i for i in ids if i in only]
    elif CFG.get('NEW_ONLY_SINCE'):        # new-news-only: events without articles after the cutoff are never (re)written (explicit --only-events still works)
        recent = {r[0] for r in db.execute('select id from events where last_art>=?', (CFG['NEW_ONLY_SINCE'],))}
        ids = [i for i in ids if i in recent]
    todo = []; rtodo = []
    for eid in ids:
        rows = event_state(db, eid)
        if any(r['rel'] is None for r in rows):        # relevance not judged yet (e.g. budget stop) -> skip
            continue
        if not only and _surely_unchanged(db, eid, rows):   # nothing to do (no LLM call would happen): keeps 'deferred' counts honest
            stats['unchanged'] += 1; continue
        (rtodo if eid in rups else todo).append(eid)
    scored = importance.sort_event_ids(db, todo) if todo else []
    if scored:
        top = ', '.join('e%d=%.0f' % (eid, sc) for sc, eid, _ in scored[:8])
        log.info('cards: %d events to evaluate (importance-sorted; top: %s)', len(scored), top)
    else:
        log.info('cards: 0 events to evaluate')
    if dry: return stats
    def one(eid):
        try: return eid, process_event(db, llm, eid, None)
        except BudgetExceeded as ex:
            log.error('budget stop at event %d: %s', eid, ex); return eid, 'budget'
        except Exception as ex:
            log.exception('event %d failed: %s', eid, ex); return eid, 'error'
    # Process in importance waves so parallel workers do not burn remaining budget on low-value clusters.
    high_cut = CFG.get('IMPORTANCE_HIGH', 80)
    med_cut = CFG.get('IMPORTANCE_MED', 40)
    waves = [
        ('high', [eid for sc, eid, _ in scored if sc >= high_cut]),
        ('roundup', rtodo),                                   # daily roundups of minor events: cheap bulk model, right after the important ones
        ('med',  [eid for sc, eid, _ in scored if med_cut <= sc < high_cut]),
        ('low',  [eid for sc, eid, _ in scored if sc < med_cut]),
    ]
    stop = False
    for name, batch in waves:
        if not batch or stop: 
            if batch and stop: stats['deferred'] += len(batch)
            continue
        rem = llm.remaining()
        # For med/low waves, apply the tight-budget deferral using the wave's minimum score.
        min_sc = high_cut if name == 'high' else (med_cut if name == 'med' else 0)
        if name == 'roundup':
            if rem < CFG.get('ROUNDUP_MIN_USD', 0.02):
                log.info('cards: deferring %d roundups (remaining $%.3f)', len(batch), rem); stats['deferred'] += len(batch); continue
        elif name != 'high' and importance.should_defer(min_sc, rem):
            log.info('cards: deferring %d %s-importance events (remaining $%.3f)', len(batch), name, rem)
            stats['deferred'] += len(batch)
            continue
        log.info('cards: processing %d %s-importance events (remaining $%.3f)', len(batch), name, rem)
        with ThreadPoolExecutor(CFG['WORKERS']) as ex:
            for eid, st in ex.map(one, batch):
                stats[st] = stats.get(st, 0) + 1
                if st == 'budget': stop = True
    return stats

def _surely_unchanged(db, eid, rows):
    """Cheap mirror of the 'unchanged' exits at the top of process_event (no LLM call would be made)."""
    with DBLOCK: old = db.execute('select input_hash,status,reasons,built,model_path from cards where event_id=?', (eid,)).fetchone()
    if not old: return False
    h = input_hash([r['id'] for r in rows if r['rel'] == 'yes'])
    if old['model_path'] == 'editor' and old['input_hash'] in (h + '-ed', h + '-edkeep'): return True
    if old['input_hash'] == h and old['status'] in ('published', 'withheld') and not any(x in (old['reasons'] or '') for x in ('budget', 'sol_cap', 'llm_output_invalid')): return True
    if old['input_hash'] == h and old['built'] and old['built'] > (dt.datetime.now(BERLIN) - dt.timedelta(hours=6)).isoformat(timespec='seconds'): return True
    return False

def merged_cleanup(db):
    for r in db.execute("select c.event_id, c.slug, c.status, e.title from cards c join events e on e.id=c.event_id where e.merged_into is not null and c.status!='merged'").fetchall():
        if r['status'] == 'published': log_correction(r['event_id'], r['slug'], r['title'], ['merged'])
        db.execute("update cards set status='merged' where event_id=?", (r['event_id'],))
    db.commit()

def export(db):
    out = []; wh = {}
    for r in db.execute("select c.*, e.title as etitle from cards c join events e on e.id=c.event_id where c.status in ('published','withheld') order by c.event_id"):
        reasons = json.loads(r['reasons'] or '[]')
        pl = json.loads(r['card'])
        rec = dict(event_id=r['event_id'], status=r['status'], reasons=reasons, built=r['built'], first_published=r['first_published'], slug=r['slug'], model_path=r['model_path'], working_title=r['etitle'])
        if r['status'] == 'published':
            cf = json.loads(r['confidence'])
            lab, why = confidence.label(cf['features'])        # re-derive from stored features so rule changes apply retroactively
            cf.update(label=lab, why=why, score=confidence.score(cf['features'], lab))
            rec.update(card=pl['card'], confidence=cf)
        else:
            for x in reasons: wh[x] = wh.get(x, 0) + 1
        out.append(rec)
    pub = [x for x in out if x['status'] == 'published']
    meta = dict(generated=now_iso(), published=len(pub), withheld=len(out) - len(pub), withheld_by_reason=wh)
    save_json(os.path.join(DATA, 'cards_auto.json'), dict(meta=meta, cards=out, redirects=redirects(db, {x['event_id']: x['slug'] for x in pub})))
    return meta

def redirects(db, pub_slugs):
    """[from_slug, to_slug] for every once-published card that was merged into another event: the merged_into chain is
    followed to the event whose card is published now (point 5: merged duplicates answer with a 301 instead of a 404)."""
    out = []
    parent = {r[0]: r[1] for r in db.execute('select id, merged_into from events where merged_into is not null')}
    for r in db.execute("select event_id, slug from cards where status='merged' and slug is not null and first_published is not null"):
        x, seen = r['event_id'], set()
        while x in parent and x not in seen: seen.add(x); x = parent[x]
        to = pub_slugs.get(x)
        if to and to != r['slug']: out.append([r['slug'], to])
    return sorted(out)
