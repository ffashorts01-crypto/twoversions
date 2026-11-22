# -*- coding: utf-8 -*-
"""Cheap heuristic importance scoring for event clusters.
Used to process high-importance cards first and to defer low-importance work when the daily/run budget is tight (~$30/month target).
No LLM calls — scoring is free."""
import re
from .common import CFG, log, DBLOCK

# Cross-side presence is the strongest signal for this site (comparison needs opposing versions).
SIDE_BOTH = 100          # Russian-side AND Ukrainian-side sources
SIDE_PLUS_IND = 25       # bonus when an independent outlet is also present
SIDE_ONE_PLUS_IND = 45   # one war side + independent (still useful contrast)
SIDE_IND_ONLY = 15
SIDE_SINGLE = 5

# Topic boosts (title + article titles; RU/UK/EN).
TOPIC = [
    (re.compile(r'удар|обстрел|обстріл|атак[аиу]|ракет|дрон|БПЛА|strike|missile|shelling|bombard', re.I), 25, 'strike'),
    (re.compile(r'погиб|загин|убит|ранен|поранен|потер|втрат|жертв|casualt|killed|wounded|deaths?', re.I), 30, 'casualties'),
    (re.compile(r'занял|занятт|захват|взял[иа]? |освобод|звільн|продвиж|відступ|отступ|территори|captured|seized|liberat|advance|withdraw|frontline|фронт', re.I), 28, 'territory'),
    (re.compile(r'перемир|ceasefire|переговор|diplom|мирн\w* план| negotiat|саммит|summit|мирной инициатив', re.I), 30, 'diplomacy'),
    (re.compile(r'поставк\w* оруж|arms deliver|танк|F-16|ATACMS|HIMARS|Patriot|ПВО|ППО|ракетн\w* систем|военн\w* помощ', re.I), 25, 'arms'),
    (re.compile(r'\bNATO\b|НАТО|ЕС\b|ЄС\b|\bEU\b|European Union|Евросоюз|санкац|sanction', re.I), 22, 'nato_eu'),
]

# Noise / low-value (subtract).
NOISE = [
    (re.compile(r'мнение|колонк|opinion|editorial|блог|интервью знаменит|celebrity|спорт|football|футбол|теннис|Eurovision|кинопремьер', re.I), -40, 'noise'),
    (re.compile(r'повседневн|routine|сводка дня без|дайджест без конкретик', re.I), -15, 'routine'),
]

def _text_blob(title, art_titles):
    return ' | '.join([title or ''] + list(art_titles or []))

def score_rows(title, rows):
    """Score one cluster from its working title and article rows (need keys: grp, source, title).
    Returns (score: float, meta: dict)."""
    grps = {r.get('grp') for r in rows if r.get('grp')}
    sources = {r.get('source') for r in rows if r.get('source')}
    art_titles = [r.get('title') or '' for r in rows]
    blob = _text_blob(title, art_titles)
    score = 0.0
    tags = []

    has_ru = 'ru' in grps
    has_ua = 'ua' in grps
    has_ind = 'ind' in grps
    if has_ru and has_ua:
        score += SIDE_BOTH; tags.append('both_sides')
        if has_ind:
            score += SIDE_PLUS_IND; tags.append('plus_ind')
    elif (has_ru or has_ua) and has_ind:
        score += SIDE_ONE_PLUS_IND; tags.append('one_side_plus_ind')
    elif has_ind and not (has_ru or has_ua):
        score += SIDE_IND_ONLY; tags.append('ind_only')
    else:
        score += SIDE_SINGLE; tags.append('single_side')

    n_src = len(sources)
    if n_src >= 4:
        score += 15; tags.append('many_sources')
    elif n_src >= 3:
        score += 8
    elif n_src <= 1:
        score -= 25; tags.append('single_outlet')

    n_arts = len(rows)
    if n_arts >= 5:
        score += 10
    elif n_arts <= 1:
        score -= 20; tags.append('single_article')

    for rx, pts, name in TOPIC:
        if rx.search(blob):
            score += pts; tags.append(name)
    for rx, pts, name in NOISE:
        if rx.search(blob):
            score += pts; tags.append(name)

    return score, dict(grps=sorted(grps), n_sources=n_src, n_articles=n_arts, tags=tags, score=score)

def score_event(db, eid):
    """Load articles for event_id and score. Prefer rel='yes' rows when any exist; else all clustered."""
    with DBLOCK:
        e = db.execute('select title from events where id=?', (eid,)).fetchone()
        rows = [dict(r) for r in db.execute(
            'select a.grp,a.source,a.title,ea.rel from event_articles ea join articles a on a.id=ea.article_id where ea.event_id=?',
            (eid,))]
    title = e['title'] if e else ''
    yes = [r for r in rows if r.get('rel') == 'yes']
    use = yes if yes else rows
    sc, meta = score_rows(title, use)
    meta['event_id'] = eid
    meta['title'] = (title or '')[:80]
    return sc, meta

def sort_event_ids(db, ids):
    """Return event ids sorted by importance descending (stable on id)."""
    scored = []
    for eid in ids:
        sc, meta = score_event(db, eid)
        scored.append((sc, eid, meta))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored

def should_defer(score, remaining_usd):
    """When budget is low, defer low-importance clusters (leave for a later run / backlog)."""
    high = CFG.get('IMPORTANCE_HIGH', 80)
    med = CFG.get('IMPORTANCE_MED', 40)
    tight = CFG.get('IMPORTANCE_TIGHT_USD', 0.20)
    very = CFG.get('IMPORTANCE_VERY_TIGHT_USD', 0.10)
    if remaining_usd < very:
        return score < high
    if remaining_usd < tight:
        return score < med
    return False  # plenty of budget: process everything, still high-first
