# -*- coding: utf-8 -*-
"""Clustering of new military-related articles into events with Luna (batches <= 100), attaching to recent open events,
id validation/repair, orphan pass, and a merge pass for split events."""
import re, json, sys, os, datetime as dt
from .common import CFG, now_iso, log, ROOT, BERLIN
from .llm import BudgetExceeded, ContentFiltered

SYSTEM = """You group news articles about the war in Ukraine into EVENTS. An event is ONE concrete WAR-RELATED incident or development: a specific strike/attack on a place, a battle or claimed capture of a named settlement, a named military operation, casualties of one incident, a specific military/security incident (sabotage, arrest of alleged agents, drone incursion), prisoner exchange, a specific ceasefire/arms-delivery decision directly tied to the war. Purely political, economic, sports or lifestyle news is NOT an event for this task (put it in "unassigned"). Articles in Russian, Ukrainian and English about the same incident belong together, whoever reports it (Russian MoD, Ukrainian outlets, international media).
Rules:
- Group only articles that clearly describe the same incident. When in doubt, DO NOT group.
- Generic daily digests (losses of the day, daily MoD summary, 'night attack overview' listing many places), opinion pieces, background analysis, and articles that are not about a concrete incident are NOT events: put them in "unassigned".
- Prefer attaching an article to an EXISTING open event (listed with refs like E12 or H3) when it is the same incident; use its ref. Otherwise create a new event with ref N1, N2, ... and give it a short neutral title in RUSSIAN (<= 100 characters: place + what happened; no loaded words, no attribution of blame, no 'liberated'/'terrorist').
- A new event needs at least 2 articles from this batch. Single articles that match nothing go to "unassigned".
- EVERY article id of the batch must appear exactly once: either in one event's "ids" or in "unassigned". Do not invent ids.
Return ONLY JSON: {"events":[{"ref":"E12","ids":[1,2]},{"ref":"N1","title":"...","ids":[3,4,5]}],"unassigned":[6,7]}"""

def _short_src(s):
    return re.sub(r'\s*\(.*?\)', '', s).replace('Минобороны РФ', 'MoD-RU')[:22]

def _line(a):
    lead = re.sub(r'\s+', ' ', a['text'])[:260]
    return '%d | %s | %s | %s | %s' % (a['id'], _short_src(a['source']), a['date_local'][:16].replace('T', ' '), re.sub(r'\s+', ' ', a['title'])[:160], lead)

def hand_events():
    sys.path.insert(0, os.path.join(ROOT, 'src'))
    from cards import CARDS
    import events as ev_meta
    out = {}
    for c in CARDS:
        ids = set()
        for k in ('ru', 'ua', 'ind'):
            ids |= {a for a, _ in c[k]['quotes']} | set(c[k]['also'])
        out[c['id']] = dict(title=c['title'], date=c['date'], ids=ids)
    return out

def editor_event_ids():
    p = os.path.join(ROOT, 'data', 'editor', '_keys.json')
    try: return list(json.load(open(p, encoding='utf-8')).values())
    except (OSError, ValueError): return []

def reopen_editor_events(db):
    """Editor-written events whose newest article is recent become attach targets, so new reports extend them instead of duplicating them."""
    cutoff = (dt.datetime.now(BERLIN) - dt.timedelta(days=CFG['OPEN_DAYS'])).astimezone(dt.timezone.utc).isoformat()
    ids = editor_event_ids()
    n = 0
    for eid in ids:
        n += db.execute("update events set status='open' where id=? and status='closed' and merged_into is null and last_art>=?", (eid, cutoff)).rowcount
    db.commit(); return n

def init_hand(db):
    """Attach the key articles of the 24 hand-written cards to hand_id (one-off, idempotent)."""
    for hid, h in hand_events().items():
        for aid in h['ids']:
            db.execute('insert or ignore into event_articles(article_id,event_id,hand_id) values(?,NULL,?)', (aid, hid))
            db.execute("insert or replace into art_state(article_id,state,attempts,updated) values(?,?,?,?)", (aid, 'hand', 0, now_iso()))
    db.commit()

def candidates(db, include_pending=True):
    """Unassigned military articles that still deserve a clustering attempt."""
    rows = db.execute("""select a.id,a.source,a.grp,a.lang,a.title,a.date_utc,a.date_local,a.text,coalesce(s.attempts,0) att,s.state st
        from articles a left join art_state s on s.article_id=a.id
        where a.military=1 and length(a.text)>=100 and a.id not in (select article_id from event_articles)
        order by a.date_utc""").fetchall()
    out = []
    newest = rows[-1]['date_utc'] if rows else ''
    floor = max(CFG.get('CLUSTER_SINCE') or '', CFG.get('NEW_ONLY_SINCE') or '')   # NEW_ONLY_SINCE: backlog before the cutoff is never clustered
    for r in rows:
        if floor and r['date_utc'] < floor: continue
        if CFG.get('CLUSTER_UNTIL') and r['date_utc'] >= CFG['CLUSTER_UNTIL']: continue
        if r['st'] in ('skipped', 'hand', 'filtered'): continue
        if r['st'] == 'pending':
            if r['att'] >= 4: continue
        out.append(dict(r))
    mx = CFG.get('CLUSTER_MAX') or 0
    if mx and len(out) > mx:          # big backlog: newest articles first (fresh news), plus the oldest ones so the backlog drains
        k = int(mx * 0.75); out = out[:mx - k] + out[-k:]
        log.info('cluster: backlog capped to %d of the candidates this run', mx)
    return out

def open_events(db, since_iso, hand, limit=150):
    evs = []
    for r in db.execute("""select e.id,e.title,e.last_art,e.first_art,(select count(*) from event_articles where event_id=e.id) n
                           from events e where e.status='open' and e.merged_into is null and e.last_art>=? order by e.last_art desc limit ?""", (since_iso, limit)):
        evs.append(('E%d' % r['id'], r['title'], r['first_art'][:10], r['last_art'][:10], r['n']))
    for hid, h in hand.items():
        evs.append(('H%d' % hid, h['title'], h['date'], '', len(h['ids'])))
    return evs

def run_cluster(db, llm, run_id, dry=False, passes=2):
    stats = dict(batches=0, articles=0, dup=0, missing=0, unknown=0, new_events=0, attached=0, hand_attached=0, orphan_calls=0, filtered_batches=0, filtered_articles=0, filtered_events=0)
    hand = hand_events()
    init_hand(db)
    for p in range(passes):
        cands = candidates(db)
        if p == 1:   # second pass: only articles still unassigned after pass 1 (now with all events of this run available)
            cands = [a for a in cands if a['st'] == 'pending' and a['id'] in stats.get('_pending', set())]
        if not cands: break
        log.info('cluster pass %d: %d candidate articles', p + 1, len(cands))
        B = CFG['CLUSTER_BATCH']
        stats['_pending'] = set()
        for i in range(0, len(cands), B):
            batch = cands[i:i + B]; stats['batches'] += 1; stats['articles'] += len(batch)
            if dry:
                continue
            try:
                _cluster_batch(db, llm, batch, hand, stats, run_id)
            except BudgetExceeded as e:
                log.error('cluster stopped: %s', e); raise
        if dry: break
    stats.pop('_pending', None)
    return stats

def _ev_line(ev):
    r, t, d1, d2, n = ev
    return '%s | %s | %s%s | %d articles' % (r, t, d1, ('..' + d2) if d2 else '', n)

def _cluster_batch(db, llm, batch, hand, stats, run_id, drop_refs=frozenset(), depth=0):
    """One clustering call. If the provider's content filter rejects the input (data_inspection_failed), the offending
    article(s) are isolated with cheap probes and set aside (art_state 'filtered'), and the rest of the batch is clustered;
    if no single article is to blame, offending event titles are dropped from the context; as a last resort the batch is halved."""
    if not batch: return
    ids = {a['id'] for a in batch}
    since = (dt.datetime.fromisoformat(batch[0]['date_utc']) - dt.timedelta(days=CFG['OPEN_DAYS'])).isoformat()
    evs = [x for x in open_events(db, since, hand) if x[0] not in drop_refs]
    ev_txt = '\n'.join(_ev_line(x) for x in evs) or '(none)'
    user = 'EXISTING OPEN EVENTS (ref | title | dates | size):\n%s\n\nBATCH ARTICLES (id | source | date | title | lead):\n%s' % (ev_txt, '\n'.join(_line(a) for a in batch))
    try:
        obj, raw = llm.call('cluster', CFG['MODEL_CLUSTER'], SYSTEM, user, max_out=14000, filter_raise=True)
    except ContentFiltered:
        stats['filtered_batches'] += 1
        bad = llm.isolate(batch, _line)
        if bad:
            for a in bad:
                db.execute("insert into art_state(article_id,state,attempts,updated) values(?, 'filtered', 1, ?) on conflict(article_id) do update set state='filtered', updated=excluded.updated", (a['id'], now_iso()))
            db.commit(); stats['filtered_articles'] += len(bad)
            log.warning('cluster: content filter -- %d article(s) set aside (%s), re-clustering the other %d', len(bad), ','.join(str(a['id']) for a in bad), len(batch) - len(bad))
            badids = {a['id'] for a in bad}
            return _cluster_batch(db, llm, [a for a in batch if a['id'] not in badids], hand, stats, run_id, drop_refs, depth + 1)
        if not drop_refs:
            bev = llm.isolate(evs, _ev_line)
            if bev:
                stats['filtered_events'] += len(bev)
                log.warning('cluster: content filter -- %d event title(s) dropped from the context (%s)', len(bev), ','.join(x[0] for x in bev))
                return _cluster_batch(db, llm, batch, hand, stats, run_id, frozenset(x[0] for x in bev), depth + 1)
        if len(batch) > 1 and depth < 6:
            mid = len(batch) // 2
            log.warning('cluster: content filter, no single trigger found -- splitting the batch %d -> %d + %d', len(batch), mid, len(batch) - mid)
            _cluster_batch(db, llm, batch[:mid], hand, stats, run_id, drop_refs, depth + 1)
            return _cluster_batch(db, llm, batch[mid:], hand, stats, run_id, drop_refs, depth + 1)
        log.warning('cluster: content filter on %d article(s) -- left pending', len(batch)); _mark_pending(db, ids); stats['_pending'] |= ids; db.commit(); return
    if not isinstance(obj, dict) or not isinstance(obj.get('events'), list):
        log.warning('cluster batch: bad JSON, articles stay pending'); _mark_pending(db, ids); stats['_pending'] |= ids; return
    assigned = {}; dup = 0; unknown = 0; groups = []
    for e in obj['events']:
        if not isinstance(e, dict): continue
        ref = str(e.get('ref', '')).strip(); good = []
        for x in e.get('ids') or []:
            try: x = int(x)
            except (TypeError, ValueError): continue
            if x not in ids: unknown += 1; continue
            if x in assigned: dup += 1; continue
            assigned[x] = ref; good.append(x)
        if good: groups.append((ref, str(e.get('title', '')).strip(), good))
    un = set()
    for x in obj.get('unassigned') or []:
        try: x = int(x)
        except (TypeError, ValueError): continue
        if x in ids and x not in assigned: un.add(x)
    missing = ids - set(assigned) - un
    stats['dup'] += dup; stats['unknown'] += unknown; stats['missing'] += len(missing)
    if missing:                                    # orphan repair: ask once more for just the missing ones, including the events created above
        stats['orphan_calls'] += 1
        orph = [a for a in batch if a['id'] in missing]
        newgroups = [(r, t, g) for r, t, g in groups if r.startswith('N')]
        ev2 = ev_txt + ''.join('\n%s | %s | new in this batch | %d articles' % (r, t, len(g)) for r, t, g in newgroups)
        user2 = 'EXISTING OPEN EVENTS (ref | title | dates | size):\n%s\n\nBATCH ARTICLES (id | source | date | title | lead):\n%s' % (ev2, '\n'.join(_line(a) for a in orph))
        try: obj2, _ = llm.call('cluster-orphans', CFG['MODEL_CLUSTER'], SYSTEM, user2, max_out=8000, filter_raise=True)
        except ContentFiltered: obj2 = None; stats['filtered_batches'] += 1     # orphans simply stay unassigned (retried next run)
        if isinstance(obj2, dict):
            for e in obj2.get('events') or []:
                if not isinstance(e, dict): continue
                ref = str(e.get('ref', '')).strip(); good = []
                for x in e.get('ids') or []:
                    try: x = int(x)
                    except (TypeError, ValueError): continue
                    if x in missing and x not in assigned: assigned[x] = ref; good.append(x)
                if good: groups.append((ref, str(e.get('title', '')).strip(), good))
        missing = ids - set(assigned) - un
        un |= missing                              # still missing -> unassigned
    # apply
    newmap = {}
    for ref, title, g in groups:
        m = re.fullmatch(r'([EH])(\d+)', ref)
        if m and m.group(1) == 'H' and int(m.group(2)) in hand:
            for x in g: db.execute('insert or ignore into event_articles(article_id,event_id,hand_id) values(?,NULL,?)', (x, int(m.group(2)))); _state(db, x, 'hand')
            stats['hand_attached'] += len(g); continue
        if m and m.group(1) == 'E' and db.execute("select 1 from events where id=? and merged_into is null and status!='roundup'", (int(m.group(2)),)).fetchone():
            eid = int(m.group(2))
            for x in g: db.execute('insert or ignore into event_articles(article_id,event_id) values(?,?)', (x, eid)); _state(db, x, 'clustered')
            _touch(db, eid); stats['attached'] += len(g); continue
        # new event (ref N*, or a bad/unknown ref) -- same N ref twice in one batch means the same event
        if len(g) < 2 and ref not in newmap: un |= set(g); continue
        if ref in newmap:
            eid = newmap[ref]
        else:
            if not title: un |= set(g); continue
            cur = db.execute('insert into events(title,created,updated,status,dirty) values(?,?,?,?,1)', (title[:160], now_iso(), now_iso(), 'open'))
            eid = cur.lastrowid; newmap[ref] = eid; stats['new_events'] += 1
        for x in g: db.execute('insert or ignore into event_articles(article_id,event_id) values(?,?)', (x, eid)); _state(db, x, 'clustered')
        _touch(db, eid)
    for x in un:
        if not db.execute('select 1 from event_articles where article_id=?', (x,)).fetchone(): _mark_pending(db, {x}); stats['_pending'].add(x)
    db.commit()
    log.info('cluster batch of %d: events %d (new %d), dup %d, unknown %d, orphans %d -> unassigned %d', len(batch), len(groups), len(newmap), dup, unknown, len(missing), len(un))

def _state(db, aid, st):
    db.execute('insert into art_state(article_id,state,attempts,updated) values(?,?,0,?) on conflict(article_id) do update set state=excluded.state, updated=excluded.updated', (aid, st, now_iso()))
def _mark_pending(db, ids):
    for x in ids:
        db.execute("insert into art_state(article_id,state,attempts,updated) values(?, 'pending', 1, ?) on conflict(article_id) do update set state='pending', attempts=attempts+1, updated=excluded.updated", (x, now_iso()))
def _touch(db, eid):
    r = db.execute('select min(a.date_utc), max(a.date_utc) from event_articles ea join articles a on a.id=ea.article_id where ea.event_id=?', (eid,)).fetchone()
    db.execute('update events set first_art=?, last_art=?, updated=?, dirty=1 where id=?', (r[0], r[1], now_iso(), eid))

# ------------------------------------------------------------------------------------------------ merge pass
MERGE_SYSTEM = """You review a list of news EVENTS (each with a title, dates, size and sample article titles) and find events that are the SAME incident split into several entries (e.g. the same strike on the same city described twice, or the same battle for one settlement). Merge only when it is clearly the same concrete incident at the same place and time window. Different strikes on the same city on different days, a general overview vs a specific incident, or related-but-separate developments must NOT be merged. When in doubt, do not merge.
Return ONLY JSON: {"merge":[{"ids":[12,15],"reason":"..."}]} using the numeric event ids given; every id at most once in total; an empty list if nothing to merge."""

def run_merge(db, llm, run_id, dry=False):
    recent = (dt.datetime.now(BERLIN) - dt.timedelta(days=2 * CFG['OPEN_DAYS'])).astimezone(dt.timezone.utc).isoformat()
    evs = db.execute("select id,title,first_art,last_art from events where merged_into is null and status not in ('merged','roundup') and last_art>=? order by first_art", (recent,)).fetchall()
    editor = set(editor_event_ids())
    stats = dict(calls=0, merged=0)
    if len(evs) < 2 or dry: return stats
    done = set(); STEP, W = 80, 120
    for i in range(0, max(1, len(evs) - 30), STEP):
        chunk = [e for e in evs[i:i + W] if e['id'] not in done]
        if len(chunk) < 2: continue
        lines = []
        for e in chunk:
            arts = db.execute('select a.title,a.source from event_articles ea join articles a on a.id=ea.article_id where ea.event_id=? limit 3', (e['id'],)).fetchall()
            n = db.execute('select count(*) from event_articles where event_id=?', (e['id'],)).fetchone()[0]
            lines.append('%d | %s | %s..%s | %d art. | e.g. %s' % (e['id'], e['title'], e['first_art'][5:16], e['last_art'][5:16], n, ' // '.join(re.sub(r'\s+', ' ', a['title'])[:70] for a in arts)))
        stats['calls'] += 1
        try: obj, _ = llm.call('merge', CFG['MODEL_CLUSTER'], MERGE_SYSTEM, '\n'.join(lines), max_out=6000, filter_raise=True)
        except ContentFiltered:
            bad = {l for l in llm.isolate(lines, lambda x: x)}
            if not bad: continue
            log.warning('merge: content filter -- %d event line(s) left out of this merge pass', len(bad))
            try: obj, _ = llm.call('merge', CFG['MODEL_CLUSTER'], MERGE_SYSTEM, '\n'.join(l for l in lines if l not in bad), max_out=6000, filter_raise=True)
            except ContentFiltered: continue
        if not isinstance(obj, dict): continue
        valid = {e['id'] for e in chunk}
        for g in obj.get('merge') or []:
            try: gid = [int(x) for x in (g.get('ids') or [])]
            except (TypeError, ValueError, AttributeError): continue
            gid = [x for x in dict.fromkeys(gid) if x in valid and x not in done]
            if len(gid) < 2: continue
            if sum(x in editor for x in gid) >= 2: continue      # never merge two editor-written cards automatically
            # safety: refuse to merge events whose dates are > 3 days apart
            rows = [db.execute('select first_art,last_art from events where id=?', (x,)).fetchone() for x in gid]
            if (max(dt.datetime.fromisoformat(r[0]) for r in rows) - min(dt.datetime.fromisoformat(r[1]) for r in rows)).days > 3: continue
            win = max(gid, key=lambda x: (x in editor, db.execute('select count(*) from event_articles where event_id=?', (x,)).fetchone()[0]))
            for x in gid:
                if x == win: continue
                db.execute('update event_articles set event_id=?, rel=NULL where event_id=?', (win, x))
                db.execute("update events set merged_into=?, status='merged' where id=?", (win, x))
                db.execute("update cards set status='merged' where event_id=?", (x,)); done.add(x); stats['merged'] += 1
            _touch(db, win); done.add(win)
            log.info('merged events %s into %d (%s)', gid, win, str(g.get('reason', ''))[:80])
        db.commit()
    return stats
