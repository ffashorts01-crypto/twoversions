# -*- coding: utf-8 -*-
"""Draft per-side summaries, 'where versions diverge' and verbatim quotes (ru originals) with Luna; verify in code; retry once with Sol;
route events with numeric conflicts or many sources straight to Sol."""
import re, json, hashlib
from .common import CFG, log, now_iso
from .llm import BudgetExceeded, ContentFiltered
from . import checks

REGIONS = ['kyiv', 'kharkiv', 'dnipro', 'zaporizhzhia', 'kherson', 'odesa', 'sumy', 'donetsk', 'moldova-romania', 'krasnodar', 'russia-other', 'ukraine-wide']
PROMPT_VERSION = 'v1'

SYSTEM = """You write a neutral comparative card about ONE event of the war in Ukraine, from news articles supplied in three groups: "ru" (Russian state-affiliated/Russian outlets), "ua" (Ukrainian outlets), "ind" (independent/international outlets). Write in RUSSIAN. Use ONLY facts stated in the supplied articles.
LANGUAGE: title, place, diverge and every summary MUST be written in Russian (Russian spelling: и/ы/э, not і/ї/є), even when the articles are in Ukrainian or English. Only the quotes keep the original language of the article. Keep personal names and surnames exactly as in the articles (transliterate to Russian Cyrillic without changing them).
Rules:
- Attribute every claim to its source or party ("Минобороны РФ заявляет…", "по данным ОВА…", "BBC пишет…"). Never state a contested claim as fact. No evaluative adjectives, no loaded words (no 'освобождение', 'террористы', 'героический'), no opinion of your own.
- Per group: if the group has no supplied articles, "summary" must be null and "quotes" empty. Otherwise a 2-4 sentence summary of what THAT group's articles say about this event (concrete: who, what, where, figures with attribution).
- "quotes": 1-2 per non-empty group; each an EXACT, character-for-character copy of one self-contained sentence or clause (40-250 characters) from the text of the article with that id (keep the article's own language; do not translate, correct or shorten inside it; no ellipsis). "id" must be an id of an article of that same group. Prefer informative sentences that carry a claim or a figure.
- "diverge": 2-5 sentences stating concretely WHERE the groups differ (figures with who says what, nature of the target, who controls a place, who is to blame, what is omitted) or, if they agree, say that the accounts agree and name any detail given by only one group. If a group has no articles, say so ("материалов российской группы в подборке нет"). Do not say who is right.
- "figures": list the key numbers that appear (casualties, drones/missiles, areas, etc.): [{"metric":"killed|injured|drones|missiles|area|other","claims":[{"id":<article id>,"value":<number>}]}]. Only numbers that literally occur in that article.
- "contradiction": true only if groups give INCOMPATIBLE accounts of facts (different figures for the same thing, opposite statements). "uncertainty": "low" | "medium" | "high" = how reliable THIS CARD is given the supplied texts (very short/truncated texts, unclear which incident is meant, internally inconsistent articles). It is NOT about whether other groups are missing - a one-sided event can still have low uncertainty. "uncertainty_reason": short.
- "title": neutral Russian headline, ideally 45-65 characters (never more than 80), worded the way people search for news: the event type in plain words (удар, ракетный удар, атака дронов, обстрел, удар по НПЗ, удар по энергетике, взрыв, бои за…, обмен пленными, переговоры, санкции…) + the concrete place (city / oblast / object: e.g. «Атака дронов на НПЗ в Туапсе», «Удар по Харькову: пострадавшие и разрушения», «Обмен пленными между Россией и Украиной»). Use exactly the event type and place the articles state (never turn a drone attack into a missile strike, never add casualties not reported). Put the place and event type at the start; prefer the city name over vague wording ("на юге", "в регионе"). No date, no clickbait, no questions, no evaluative words, no source names unless the event is a statement by that party; "place": short place name; "region": one of %s; "tags": 0-3 from %s.
- "divergence": how far the groups' versions of THIS event differ, judged only from the supplied articles; null when fewer than two groups have articles. "agree" = the groups report the same core facts (they may stress different things); "details" = same core event, but they differ in figures, details, wording of the target, attribution or omissions; "contradict" = incompatible accounts of a core fact (who struck whom/what, opposite statements, clearly different figures for the same thing, one side denies what the other reports).
Return ONLY JSON: {"title":..,"place":..,"region":..,"tags":[..],"divergence":"agree|details|contradict"|null,"diverge":..,"ru":{"summary":..|null,"quotes":[{"id":..,"text":..}]},"ua":{..},"ind":{..},"figures":[..],"contradiction":bool,"uncertainty":..,"uncertainty_reason":..}"""

def latin_words(t):
    """Lower-case Latin words inside Russian text (e.g. 'supplied', 'injury') -- outlet names (BBC, Kyiv Independent, Mash) are capitalised and allowed."""
    return [w for w in re.findall(r'(?<![A-Za-z@/.])[a-z]{3,}(?![A-Za-z])', t or '') if w not in ('www', 'com', 'net', 'org', 'ru', 'ua')]

UK_ONLY = re.compile(r'[іїєґІЇЄҐ]')
CYR_ALL = re.compile(r'[А-Яа-яЁёІіЇїЄєҐґ]')
def _uk_words(t):
    """Lower-case words with Ukrainian-only letters (i/ї/є/ґ) outside quotes; capitalised words (Ukrainian place/outlet names) are tolerated."""
    t = re.sub(r'«[^»]*»|"[^"]*"|“[^”]*”', '', t or '')
    out = []
    for w in re.findall(r"[А-Яа-яЁёІіЇїЄєҐґ'’]+", t):
        lw = w.lower()
        if (UK_ONLY.search(w) and not w[0].isupper()) or lw in UK_WORDS or (len(lw) > 5 and lw.endswith(('ння', 'нню', 'нням', 'ннями')) and not lw.startswith('ранн')):
            out.append(w)
    return out
UK_WORDS = {'що', 'щодо', 'з', 'який', 'яка', 'яке', 'які', 'зі', 'внаслідок', 'загибель', 'уражено', 'повідомляє', 'заявив'}
def not_russian(t):
    """True if a text that must be Russian is (partly) Ukrainian."""
    return len(_uk_words(t)) >= 2
def title_not_russian(t):
    return len(_uk_words(t)) >= 1

def select_articles(arts, per_side=5, per_source=2):
    """Pick the most informative relevant articles per group (longest texts first, at most `per_source` per outlet)."""
    out = {}
    for g in ('ru', 'ua', 'ind'):
        pool = sorted([a for a in arts if a['grp'] == g], key=lambda a: -min(len(a['text']), 3500))
        cnt = {}; sel = []
        for a in pool:
            if cnt.get(a['source'], 0) >= per_source: continue
            cnt[a['source']] = cnt.get(a['source'], 0) + 1; sel.append(a)
            if len(sel) >= per_side: break
        out[g] = sel
    return out

def build_prompt(title, sel, tags):
    parts = ['EVENT (working title): %s\n' % title]
    for g in ('ru', 'ua', 'ind'):
        parts.append('=== GROUP %s (%d articles) ===' % (g, len(sel[g])))
        if not sel[g]: parts.append('(no articles)')
        for a in sel[g]:
            parts.append('--- id=%d | %s | %s | lang=%s\nTITLE: %s\nTEXT: %s' % (a['id'], a['source'], a['date_local'][:16].replace('T', ' '), a['lang'], re.sub(r'\s+', ' ', a['title']), a['text'][:3200]))
    return '\n'.join(parts)

def verify_card(obj, sel, all_texts, new_ids=None):
    """Returns (card_dict_or_None, problems[list], flags). Quotes are repaired to exact substrings; bad quotes are dropped and reported."""
    problems = []; flags = dict(dropped_quotes=0, ungrounded=0)
    if not isinstance(obj, dict): return None, ['not a JSON object'], flags
    byid = {a['id']: a for g in sel.values() for a in g}
    card = dict(title=str(obj.get('title') or '').strip(), place=str(obj.get('place') or '').strip(), diverge=str(obj.get('diverge') or '').strip(),
                region=obj.get('region') if obj.get('region') in REGIONS else 'ukraine-wide', tags=[], sides={})
    import sys, os
    from .common import ROOT
    sys.path.insert(0, os.path.join(ROOT, 'src')); import ui
    card['tags'] = [t for t in (obj.get('tags') or []) if t in ui.TAGS][:3]
    if not card['title'] or len(card['title']) > 160: problems.append('bad title')
    if not card['diverge'] or len(card['diverge']) < 30: problems.append('diverge missing/too short')
    for g in ('ru', 'ua', 'ind'):
        s = obj.get(g) if isinstance(obj.get(g), dict) else {}
        summ = s.get('summary'); summ = summ.strip() if isinstance(summ, str) and summ.strip() else None
        have = bool(sel[g])
        if have and not summ: problems.append('%s: summary missing although articles supplied' % g); continue
        if not have:
            if summ: problems.append('%s: summary given for a group without articles' % g)
            card['sides'][g] = dict(summary=None, quotes=[], also=[]); continue
        if len(summ) > 1400: problems.append('%s: summary too long' % g)
        lat = latin_words(summ)
        if lat: problems.append('%s: English words in Russian text: %s' % (g, ', '.join(lat[:5])))
        if not_russian(summ): problems.append('%s: summary not Russian (Ukrainian letters i/ї/є); write it in Russian' % g)
        quotes = []; seen = set()
        for q in s.get('quotes') or []:
            try: aid = int(q.get('id')); txt = q.get('text')
            except (TypeError, ValueError, AttributeError): flags['dropped_quotes'] += 1; problems.append('%s: malformed quote' % g); continue
            a = byid.get(aid)
            if not a or a['grp'] != g: flags['dropped_quotes'] += 1; problems.append('%s: quote from article %s not in this group' % (g, aid)); continue
            ex = checks.verify_quote(txt, a['text'])
            if not ex: flags['dropped_quotes'] += 1; problems.append('%s: quote not verbatim in article %d: %s' % (g, aid, (txt or '')[:70])); continue
            if (aid, ex) in seen: continue
            seen.add((aid, ex)); quotes.append([aid, ex])
        if not quotes: problems.append('%s: no verified quote' % g)
        stexts = [a['text'] + ' ' + a['title'] for a in sel[g]]
        bad = checks.ungrounded_numbers(summ, stexts)
        if bad: flags['ungrounded'] += len(bad); problems.append('%s: numbers not found in sources: %s' % (g, ', '.join(bad)))
        cas = [n for n in checks.casualty_mismatch(summ, stexts) if n not in {re.sub(r'\D', '', b) for b in bad}]
        if cas: flags['ungrounded'] += len(cas); problems.append('%s: casualty figures not given by any source of this group next to a casualty word (numbers not found as casualties): %s' % (g, ', '.join(cas)))
        if bad or cas: flags.setdefault('ungrounded_by_field', {})[g] = list(bad) + cas
        quoted = {a for a, _ in quotes}
        card['sides'][g] = dict(summary=summ, quotes=quotes, also=[])
    lat = latin_words(card['diverge']) + latin_words(card['title'])
    if lat: problems.append('diverge/title: English words in Russian text: %s' % ', '.join(lat[:5]))
    if not_russian(card['diverge']) or title_not_russian(card['title']): problems.append('diverge/title: not Russian (Ukrainian letters i/ї/є); write it in Russian')
    bad = checks.ungrounded_numbers(card['diverge'], all_texts)
    if bad: flags['ungrounded'] += len(bad); problems.append('diverge: numbers not found in sources: %s' % ', '.join(bad))
    cas = [n for n in checks.casualty_mismatch(card['diverge'], all_texts) if n not in {re.sub(r'\D', '', b) for b in bad}]
    if cas: flags['ungrounded'] += len(cas); problems.append('diverge: casualty figures not given by any source next to a casualty word (numbers not found as casualties): %s' % ', '.join(cas))
    if bad or cas: flags.setdefault('ungrounded_by_field', {})['diverge'] = list(bad) + cas
    # figures: ground every claim in its cited article, then detect conflicts in code
    metrics = {}
    for f in obj.get('figures') or []:
        if not isinstance(f, dict): continue
        m = str(f.get('metric') or 'other')
        for cl in f.get('claims') or []:
            try: aid = int(cl.get('id')); val = cl.get('value')
            except (TypeError, ValueError, AttributeError): continue
            a = byid.get(aid)
            if not a or not isinstance(val, (int, float)) or isinstance(val, bool): continue
            if str(int(val)) not in checks.numbers(a['text'] + ' ' + a['title']) and not (float(val) != int(val) and str(val) in a['text']): continue   # ungrounded claim: ignored
            metrics.setdefault(m, []).append((a['grp'], float(val), aid))
    conflict = cross = False; confl = []
    for m, cl in metrics.items():
        if m == 'other': continue
        vals = {v for _, v, _ in cl}
        if len(vals) >= 2:
            conflict = True
            by_g = {}
            for g, v, _ in cl: by_g.setdefault(g, set()).add(v)
            if len(by_g) >= 2 and len(set.union(*by_g.values())) > len(set.intersection(*by_g.values())) and not any(s <= set.intersection(*by_g.values()) for s in by_g.values()):
                cross = True
            confl.append(dict(metric=m, values=sorted(vals)))
    flags.update(numeric_conflict=conflict, numeric_cross=cross, conflicts=confl)
    flags['uncertainty'] = obj.get('uncertainty') if obj.get('uncertainty') in ('low', 'medium', 'high') else 'medium'
    flags['uncertainty_reason'] = str(obj.get('uncertainty_reason') or '')[:200]
    flags['contradiction'] = bool(obj.get('contradiction'))
    if not any(card['sides'][g]['summary'] for g in card['sides']): problems.append('no side summaries')
    nsides = sum(1 for g in ('ru', 'ua', 'ind') if card['sides'].get(g, {}).get('summary'))
    dv = obj.get('divergence')
    card['divergence'] = dv if dv in DIVERGENCE and nsides >= 2 else None      # soft field: never a reason to reject a card
    card['update_new'] = _verify_update(obj.get('update'), card, byid, new_ids or set())
    return card, problems, flags

DIVERGENCE = ('agree', 'details', 'contradict')
def _verify_update(u, card, byid, new_ids):
    """'Confirmed / refuted later' note (only on regenerated cards with NEW articles). Kept only when it cites a NEW article and the
    claimed side has a version on the card; otherwise dropped silently (never a reason to reject the card)."""
    if not isinstance(u, dict) or not new_ids: return None
    verdict, side, note = u.get('verdict'), u.get('side'), str(u.get('note') or '').strip()
    try: aid = int(u.get('id'))
    except (TypeError, ValueError): return None
    if verdict not in ('confirmed', 'refuted') or side not in ('ru', 'ua', 'ind') or aid not in new_ids or aid not in byid: return None
    if not card['sides'].get(side, {}).get('summary') or not (15 <= len(note) <= 260) or latin_words(note) or not_russian(note): return None
    return dict(verdict=verdict, side=side, id=aid, note=note)

ROUNDUP_NOTE = """

THIS IS A DAILY ROUNDUP card, not a single incident: the articles describe several minor incidents of the same kind (%s). Per group, list the incidents that group reports (place + what happened + figures, with attribution) in 2-5 sentences. "diverge": where the groups' accounts of these incidents differ, or which incidents only one group reports. "title" will be replaced automatically (any short Russian title is fine). Do not merge separate incidents into one and do not invent a total that no article gives."""

PREV_NOTE = """

PREVIOUS PUBLISHED CARD for this event (written earlier from the older articles). New articles have been added since. EXTEND it: keep all of its facts, attributions and (where the article is supplied above) its quotes unless a newer article contradicts or updates them (then say who reports what); add the new facts from the newer articles; keep the same style and roughly the same or larger level of detail. Articles marked NEW appeared after the previous card.
%s"""

UPDATE_NOTE = """

Articles marked NEW appeared after this event's card was first published. "update": if — and only if — a NEW article EXPLICITLY reports that a specific claim made earlier by one group was later CONFIRMED (e.g. the other side acknowledged it, satellite imagery, independent verification, official investigation results) or REFUTED, return {"verdict":"confirmed"|"refuted","side":"ru"|"ua"|"ind" (whose earlier claim),"id":<id of that NEW article>,"note":"one short Russian sentence (max 200 chars) saying what was confirmed/refuted and by whom, with attribution"}. Otherwise "update": null. Do not infer: a new article simply repeating or adding figures is NOT a confirmation. Add "update" to the JSON."""

def summarize_event(llm, title, arts, prev=None, new_ids=None, roundup=None, force_bulk=False):
    """arts: relevant articles (dicts). prev: previously published card (dict) to extend. Returns dict(card, flags, problems, path)."""
    from .common import ROOT
    import sys, os
    sys.path.insert(0, os.path.join(ROOT, 'src')); import ui
    if prev:
        sel = select_articles(arts, per_side=8, per_source=3)
        old_ids = set(prev.get('article_ids') or [])
        must = {a for g in ('ru', 'ua', 'ind') for a, _ in prev['sides'][g]['quotes']} | {a['id'] for a in arts if a['id'] not in old_ids}
        for a in arts:
            if a['id'] in must and a not in sel[a['grp']]: sel[a['grp']].append(a)
    elif roundup:
        sel = select_articles(arts, per_side=8, per_source=3)
    else:
        sel = select_articles(arts)
    if prev:
        new_ids = {a['id'] for a in arts if a['id'] not in old_ids}
    new_ids0 = set(new_ids or ())
    def make_prompt(sel):
        prompt_in = build_prompt(title, sel, ui.TAGS)
        nid = set(new_ids0)
        for i in sorted(nid): prompt_in = prompt_in.replace('--- id=%d |' % i, '--- NEW id=%d |' % i)
        if prev:
            pv = dict(title=prev['title'], place=prev.get('place'), diverge=prev['diverge'], **{g: dict(summary=prev['sides'][g]['summary'], quotes=[dict(id=a, text=t) for a, t in prev['sides'][g]['quotes']]) for g in ('ru', 'ua', 'ind')})
            prompt_in += PREV_NOTE % json.dumps(pv, ensure_ascii=False)
        if roundup: prompt_in += ROUNDUP_NOTE % roundup
        if nid and any(('--- NEW id=%d |' % i) in prompt_in for i in nid): prompt_in += UPDATE_NOTE
        else: nid = set()
        return prompt_in, nid
    prompt_in, new_ids = make_prompt(sel)
    system = SYSTEM % (', '.join(REGIONS), '; '.join('"%s"' % t for t in ui.TAGS))
    all_texts = [a['text'] + ' ' + a['title'] for a in arts]
    n_rel = len(arts)
    direct_sol = n_rel >= CFG['SOL_MANY_SOURCES'] or checks.prelim_numeric_conflict(arts)
    if prev and CFG.get('EXTEND_LUNA_ONLY', True): direct_sol = False   # extensions of editor cards: Luna only (cheap); on failure the editor text is kept
    if force_bulk: direct_sol = False                                     # roundup cards: cheap bulk model only
    path = []
    model = CFG['MODEL_HARD'] if direct_sol else CFG['MODEL_BULK']
    result = None
    for attempt in range(2):
        path.append('sol' if model == CFG['MODEL_HARD'] else 'luna')
        user = prompt_in
        if result and result['problems']:
            user += '\n\nA previous draft was REJECTED by automatic checks for these reasons; fix them (quotes must be exact substrings of the article text; every number must literally occur in the articles):\n- ' + '\n- '.join(result['problems'][:12])
        try:
            obj, raw = llm.call('summary', model, system, user, max_out=9000, effort='low' if model == CFG['MODEL_HARD'] else None, filter_raise=True)
        except ContentFiltered:
            # provider content filter: find the article(s) that trip it (cheap probes), drop them and retry once without them
            pool = [a for g in ('ru', 'ua', 'ind') for a in sel[g]]
            bad = llm.isolate(pool, lambda a: build_prompt('', dict(ru=[a] if a['grp'] == 'ru' else [], ua=[a] if a['grp'] == 'ua' else [], ind=[a] if a['grp'] == 'ind' else []), ui.TAGS))
            obj = None
            if bad:
                badids = {a['id'] for a in bad}
                log.warning('summary: content filter -- dropping article(s) %s from the prompt', ','.join(str(x) for x in sorted(badids)))
                sel = {g: [a for a in sel[g] if a['id'] not in badids] for g in sel}
                prompt_in, new_ids = make_prompt(sel)
                user = prompt_in
                try: obj, raw = llm.call('summary', model, system, user, max_out=9000, effort='low' if model == CFG['MODEL_HARD'] else None, filter_raise=True)
                except ContentFiltered: obj = None
            if obj is None:
                result = dict(card=None, problems=['content filter (data_inspection_failed)'], flags=dict(dropped_quotes=0, ungrounded=0), path=list(path) + ['filtered'], sel=sel)
                return result
        card, problems, flags = verify_card(obj, sel, all_texts, new_ids)
        hard = [p for p in problems if not p.startswith('diverge: numbers') and 'numbers not found' not in p] if card else problems or ['no output']
        result = dict(card=card, problems=problems, flags=flags, path=list(path), sel=sel)
        if not problems: break
        if model == CFG['MODEL_HARD']: break          # Sol already tried (directly or as the retry)
        if (prev and CFG.get('EXTEND_LUNA_ONLY', True)) or force_bulk:
            if attempt == 0: continue                  # one Luna retry with feedback, no Sol
            break
        model = CFG['MODEL_HARD']                      # retry once with Sol, with feedback
    if result['card'] is None: return result
    # fill 'also' links: other relevant articles of the side that were selected but not quoted
    for g in ('ru', 'ua', 'ind'):
        if g not in result['card']['sides']: continue        # side missing (already reported as a problem -> the card is withheld)
        quoted = {a for a, _ in result['card']['sides'][g]['quotes']}
        result['card']['sides'][g]['also'] = [a['id'] for a in sel[g] if a['id'] not in quoted][:4]
    result['card']['n_rel'] = n_rel
    return result
