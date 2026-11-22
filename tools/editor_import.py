#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import editor-written cards (no LLM) into the pipeline store, applying the SAME code checks and quality gates as the automatic path:
verbatim quotes (pipeline.summarize.verify_card), number grounding, translation checks (pipeline.translate.check), confidence scale in code.
Input: data/editor/*.json -- each a list of card objects (schema = the summarizer's JSON + 'key', 'ids', 'uk', 'en').
    python3 tools/editor_import.py            # import / update all, then export data/cards_auto.json
    python3 tools/editor_import.py --check    # validate only, print problems, write nothing
"""
import os, sys, json, glob, argparse
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, 'src'))
from pipeline import common, summarize, translate, confidence, cards as cardsmod, checks
from pipeline.common import now_iso, CFG

KEYS = os.path.join(HERE, 'data', 'editor', '_keys.json')

def load_all():
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, 'data', 'editor', '2*.json'))):
        for c in json.load(open(p, encoding='utf-8')): c['_file'] = os.path.basename(p); out.append(c)
    return out

def validate(db, c, owner):
    """Returns (card, conf, reasons, problems, rel_rows)."""
    ids = [int(x) for x in c['ids']]
    rows = [dict(r) for r in db.execute('select * from articles where id in (%s) order by date_utc' % ','.join('?' * len(ids)), ids)]
    problems = []
    if len(rows) != len(set(ids)): problems.append('unknown article ids: %s' % (set(ids) - {r['id'] for r in rows}))
    for r in rows:
        o = owner.get(r['id'])
        if o and o != c['key']: problems.append('article %d already belongs to %s' % (r['id'], o))
    reasons = list(c.get('withhold') or [])
    srcs = {r['source'] for r in rows}
    if len(rows) < CFG['MIN_REL_ARTICLES']: reasons.append('few_articles')
    elif len(srcs) < CFG['MIN_REL_SOURCES']: reasons.append('few_sources')
    if reasons: return None, None, list(dict.fromkeys(reasons)), problems, rows
    sel = {g: [r for r in rows if r['grp'] == g] for g in ('ru', 'ua', 'ind')}
    all_texts = [r['text'] + ' ' + r['title'] for r in rows]
    card, probs, fl = summarize.verify_card(c, sel, all_texts)
    problems += probs
    if card is None: return None, None, ['llm_output_invalid'], problems, rows
    for g in ('ru', 'ua', 'ind'):
        s = card['sides'][g]
        if s['summary'] and not s['quotes']: reasons.append('quote_check')
        quoted = {a for a, _ in s['quotes']}
        s['also'] = [r['id'] for r in sel[g] if r['id'] not in quoted][:6]
    if fl['dropped_quotes']: reasons.append('quote_check')
    if any(p.startswith(('bad title', 'diverge missing', 'no side summaries')) or 'summary missing' in p or 'group without articles' in p for p in probs): reasons.append('llm_output_invalid')
    if fl['ungrounded'] >= 1: reasons.append('numbers_ungrounded')      # stricter than the auto path (>=2): editor text must be fully grounded
    if fl['uncertainty'] == 'high': reasons.append('model_uncertain')
    ru = dict(t=card['title'], p=card['place'] or '—', d=card['diverge'], **{g: card['sides'][g]['summary'] for g in ('ru', 'ua', 'ind')})
    tr = {}
    for lang in ('uk', 'en'):
        t = c.get(lang)
        tp = translate.check(ru, t, lang)
        if not tp and lang == 'en':
            import re
            for k, v in t.items():
                if v and re.search(r'[А-Яа-яЁёІіЇїЄєҐґ]', v): tp.append('%s: Cyrillic left in English' % k)
        if tp: problems += ['%s: %s' % (lang, p) for p in tp]; reasons.append('translation')
        else: tr[lang] = {k: (t[k].strip() if t[k] is not None else None) for k in ru}
    reasons = list(dict.fromkeys(reasons))
    if reasons: return card, None, reasons, problems, rows
    conf = confidence.compute(rows, rel_agreement=1.0, model_uncertainty=fl['uncertainty'], model_contradiction=fl['contradiction'],
                              numeric_conflict=fl['numeric_conflict'], numeric_cross=fl['numeric_cross'], quote_check='pass', numbers_ungrounded=fl['ungrounded'])
    conf['conflicts'] = fl.get('conflicts', []); conf['uncertainty_reason'] = fl.get('uncertainty_reason', '')
    dates = sorted(r['date_local'][:10] for r in rows)
    card.update(start=dates[0], end=dates[-1], article_ids=[r['id'] for r in rows], tr=tr, tr_ru=ru, n_rel=len(rows))
    return card, conf, [], problems, rows

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--check', action='store_true'); ap.add_argument('--quiet', action='store_true'); a = ap.parse_args()
    db = common.connect()
    keys = common.load_json(KEYS, {})
    allc = load_all()
    seen = {}
    for c in allc:
        if c['key'] in seen: print('DUPLICATE KEY', c['key']); return 1
        seen[c['key']] = 1
    owner = {}
    for c in allc:
        for x in c['ids']:
            if int(x) in owner: print('ARTICLE %s in two cards: %s / %s' % (x, owner[int(x)], c['key']))
            owner[int(x)] = c['key']
    # articles already owned by pipeline events / hand cards (not editor ones)
    ed_events = set(keys.values())
    for r in db.execute('select article_id, event_id, hand_id from event_articles'):
        if r['hand_id'] or (r['event_id'] not in ed_events):
            if r['article_id'] in owner: print('ARTICLE %d (%s) already in pipeline event %s/H%s' % (r['article_id'], owner[r['article_id']], r['event_id'], r['hand_id']))
    stat = dict(published=0, withheld=0); bad = 0
    for c in allc:
        card, conf, reasons, problems, rows = validate(db, c, owner)
        hard = [p for p in problems if 'already belongs' in p or 'unknown article' in p]
        if problems and not a.quiet or reasons and not c.get('withhold'):
            print('%-34s %s %s %s' % (c['key'], 'PUBLISH' if not reasons else 'WITHHELD ' + ','.join(reasons), c['_file'], ('\n    - ' + '\n    - '.join(problems)) if problems else ''))
        if hard: bad += 1; continue
        stat['published' if not reasons else 'withheld'] += 1
        if a.check: continue
        # event row
        eid = keys.get(c['key'])
        title = c.get('title') or c.get('working_title') or c['key']
        if eid is None:
            cur = db.execute("insert into events(title,created,updated,status,dirty) values(?,?,?,'closed',0)", (title[:160], now_iso(), now_iso()))
            eid = cur.lastrowid; keys[c['key']] = eid
        else:
            db.execute('update events set title=?, updated=? where id=?', (title[:160], now_iso(), eid))
        db.execute('delete from event_articles where event_id=?', (eid,))
        for r in rows:
            db.execute('insert or replace into event_articles(article_id,event_id,hand_id,rel,rel_note) values(?,?,NULL,?,?)', (r['id'], eid, 'yes', 'editor'))
            db.execute("insert into art_state(article_id,state,attempts,updated) values(?,'clustered',0,?) on conflict(article_id) do update set state='clustered', updated=excluded.updated", (r['id'], now_iso()))
        if rows:
            db.execute('update events set first_art=?, last_art=? where id=?', (rows[0]['date_utc'], rows[-1]['date_utc'], eid))
        db.commit()
        e = db.execute('select * from events where id=?', (eid,)).fetchone()
        h = cardsmod.input_hash([r['id'] for r in rows]) + '-ed'
        cardsmod._store(db, eid, h, 'published' if not reasons else 'withheld', reasons, conf, dict(editor=True, problems=problems), 'editor', e, card=card if not reasons else card)
    if not a.check:
        common.save_json(KEYS, keys)
        meta = cardsmod.export(db); print('export:', meta)
    print('editor cards:', stat, 'hard errors:', bad)
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
