# -*- coding: utf-8 -*-
"""Per-article relevance filter: 'does this article actually describe this event?'  Only articles judged 'yes' may become part of a side."""
import re, json
from concurrent.futures import ThreadPoolExecutor
from .common import CFG, log, now_iso, DBLOCK
from . import importance
from .llm import BudgetExceeded, ContentFiltered

SYSTEM = """You verify the grouping of news articles into one EVENT. For each article decide whether it actually describes THE EVENT given (the same concrete incident, place and time).
- "yes": the article is mainly about this very event (it may also give other details).
- "partial": the article mentions the event only in passing, inside a digest/overview of many incidents, or is about a related but different incident, or only gives background.
- "no": the article is about a different event or a different topic.
Be strict: a generic daily summary, a different settlement/city, a different day's strike, or a different person's story is NOT "yes". Judge only from the title and text excerpt.
Return ONLY JSON: {"verdicts":[{"id":123,"v":"yes|partial|no","why":"<=12 words"}]} with exactly one verdict per given id."""

HAND_SQL = "create table if not exists hand_rel(hand_id integer, article_id integer, rel text, primary key(hand_id, article_id))"

def _lines(arts):
    return '\n'.join('%d | %s | %s | %s | %s' % (a['id'], re.sub(r'\s*\(.*?\)', '', a['source'])[:24], a['date_local'][:10], re.sub(r'\s+', ' ', a['title'])[:150], re.sub(r'\s+', ' ', a['text'])[:550]) for a in arts)

def judge(llm, title, dates, arts, stage='relevance'):
    out = {}
    for i in range(0, len(arts), 25):
        chunk = arts[i:i + 25]
        ask = lambda ch: llm.call(stage, CFG['MODEL_BULK'], SYSTEM, 'EVENT: %s\nDATES: %s\n\nARTICLES (id | source | date | title | excerpt):\n%s' % (title, dates, _lines(ch)), max_out=4000, filter_raise=True)
        try: obj, _ = ask(chunk)
        except ContentFiltered:
            bad = llm.isolate(chunk, lambda a: _lines([a]))
            if not bad:
                log.warning('relevance: content filter on event %r, no single article to blame -- left unjudged', title[:60]); continue
            for a in bad: out[a['id']] = ('no', 'content filter (data_inspection_failed)')
            log.warning('relevance: content filter -- %d article(s) excluded from event %r', len(bad), title[:60])
            chunk = [a for a in chunk if a not in bad]
            if not chunk: continue
            try: obj, _ = ask(chunk)
            except ContentFiltered: continue
        if not isinstance(obj, dict): continue
        for v in obj.get('verdicts') or []:
            try: aid = int(v.get('id'))
            except (TypeError, ValueError, AttributeError): continue
            val = str(v.get('v', '')).strip().lower()
            if aid in {a['id'] for a in chunk} and val in ('yes', 'partial', 'no'): out[aid] = (val, str(v.get('why', ''))[:120])
    return out

def run_relevance(db, llm, run_id, dry=False):
    """Judge relevance high-importance first; defer low-importance when budget is tight (rel stays null → backlog)."""
    stats = dict(events=0, judged=0, yes=0, partial=0, no=0, deferred=0)
    todo_ids = [r[0] for r in db.execute("""select distinct e.id from events e join event_articles ea on ea.event_id=e.id where e.merged_into is null and ea.rel is null""")]
    scored = importance.sort_event_ids(db, todo_ids) if todo_ids else []
    log.info('relevance: %d events with unjudged articles (importance-sorted)', len(scored))
    if dry: return stats
    def one(eid):
        with DBLOCK:
            e = db.execute('select * from events where id=?', (eid,)).fetchone()
            arts = [dict(r) for r in db.execute('select a.* from event_articles ea join articles a on a.id=ea.article_id where ea.event_id=? and ea.rel is null order by a.date_utc', (eid,))]
            allr = db.execute('select min(a.date_local),max(a.date_local) from event_articles ea join articles a on a.id=ea.article_id where ea.event_id=?', (eid,)).fetchone()
        return eid, judge(llm, e['title'], '%s .. %s' % (allr[0][:10], allr[1][:10]), arts)
    high_cut = CFG.get('IMPORTANCE_HIGH', 80)
    med_cut = CFG.get('IMPORTANCE_MED', 40)
    waves = [
        ('high', [(sc, eid) for sc, eid, _ in scored if sc >= high_cut]),
        ('med',  [(sc, eid) for sc, eid, _ in scored if med_cut <= sc < high_cut]),
        ('low',  [(sc, eid) for sc, eid, _ in scored if sc < med_cut]),
    ]
    for name, batch in waves:
        if not batch: continue
        rem = llm.remaining()
        min_sc = high_cut if name == 'high' else (med_cut if name == 'med' else 0)
        # relevance runs on the cheap bulk model (~$0.0001 per event): only defer when the budget is nearly exhausted,
        # so minor events still get judged and can go into the daily roundup cards (pipeline/roundup.py)
        if name != 'high' and rem < CFG.get('RELEVANCE_MIN_USD', 0.03):
            log.info('relevance: deferring %d %s-importance events (remaining $%.3f)', len(batch), name, rem)
            stats['deferred'] += len(batch)
            continue
        log.info('relevance: judging %d %s-importance events (remaining $%.3f)', len(batch), name, rem)
        with ThreadPoolExecutor(CFG['WORKERS']) as ex:
            futs = [ex.submit(one, eid) for sc, eid in batch]
            for f in futs:
                try: eid, res = f.result()
                except BudgetExceeded: raise
                for aid, (v, why) in res.items():
                    db.execute('update event_articles set rel=?, rel_note=? where article_id=?', (v, why, aid)); stats['judged'] += 1; stats[v] += 1
                stats['events'] += 1
                db.execute('update events set dirty=1 where id=?', (eid,))
        db.commit()
    db.commit()
    return stats

def run_hand_relevance(db, llm, hand):
    """Judge the key articles of the 24 hand-written cards once (for the confidence scale feature 'relevance-filter agreement')."""
    db.execute(HAND_SQL)
    done = {r[0] for r in db.execute('select distinct hand_id from hand_rel')}
    n = 0
    for hid, h in hand.items():
        if hid in done: continue
        arts = [dict(r) for r in db.execute('select * from articles where id in (%s) order by date_utc' % ','.join(str(x) for x in h['ids']))]
        res = judge(llm, h['title'], h['date'], arts, stage='relevance-hand')
        for aid, (v, why) in res.items(): db.execute('insert or replace into hand_rel values(?,?,?)', (hid, aid, v)); n += 1
        db.commit()
    return n
