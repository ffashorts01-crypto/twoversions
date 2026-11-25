#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command static site generator.   Usage:  python3 build.py [--base-url URL] [--no-check]

Reads  data/articles.db (collected articles) + src/cards.py (24 event cards, Russian originals)
       + src/tr_uk.py / src/tr_en.py (translations) + src/ui.py (UI strings).
Verifies that every quote is a verbatim substring of the stored article text (build fails otherwise).
Writes the site into  public/  (/ru/, /uk/, /en/, sitemap.xml, robots.txt, assets/).

Config (environment variables or flags):
  BASE_URL        public origin used in canonicals / hreflang / sitemap / OG tags.
                  Default: newest *.trycloudflare.com URL found in tunnel.log, else http://localhost:8089
                  >>> CHANGE TO THE REAL DOMAIN BEFORE GOING LIVE <<<
  CONTACT_EMAIL   address for the feedback mailto link (default twoversions@proton.me)
  ANALYTICS       '' (default, tracking OFF) | plausible | umami      (+ ANALYTICS_DOMAIN / ANALYTICS_SRC / ANALYTICS_ID)
  PUBLISHED       datePublished for JSON-LD (default 2026-09-30, the data collection date)
"""
import os, sys, re, json, html, shutil, sqlite3, hashlib, datetime as dt, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'src'))
from cards import CARDS
import ui, events, about, topics, geo
import tr_uk, tr_en
sys.path.insert(0, HERE)
from pipeline import confidence as conf_mod

OUT = os.environ.get('BUILD_OUT') or os.path.join(HERE, 'public')   # BUILD_OUT: test builds into another directory
DEFAULT_BASE = 'http://localhost:8089'

def detect_base():
    if '--base-url' in sys.argv:
        return sys.argv[sys.argv.index('--base-url') + 1].rstrip('/')
    if os.environ.get('BASE_URL'):
        return os.environ['BASE_URL'].rstrip('/')
    try:
        found = re.findall(r'https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com', open(os.path.join(HERE, 'tunnel.log'), encoding='utf-8', errors='ignore').read())
        if found:
            return found[-1]
    except OSError:
        pass
    return DEFAULT_BASE

BASE = detect_base()
CONTACT = os.environ.get('CONTACT_EMAIL', 'twoversions@proton.me')
ANALYTICS = os.environ.get('ANALYTICS', '').strip().lower()
PUBLISHED = os.environ.get('PUBLISHED', '2026-09-30')   # datePublished of the 24 hand-written cards; auto cards use their own first-publication date
MODIFIED = dt.date.today().isoformat()
LANGS = ui.LANGS
e = html.escape

# ------------------------------------------------------------------ data + quote verification
db = sqlite3.connect(os.path.join(HERE, 'data', 'articles.db'))
A = {r[0]: dict(id=r[0], source=r[1], grp=r[2], lang=r[3], title=r[4], url=r[5], date=r[6], text=r[7]) for r in
     db.execute('select id,source,grp,lang,title,url,date_local,text from articles')}
SIDES = (('ru', 'ru'), ('ua', 'ua'), ('ind', 'ind'))

def load_auto():
    """Published auto cards from data/cards_auto.json (written by update.py). Each is re-verified here; a card that fails is quarantined, not fatal."""
    try: data = json.load(open(os.path.join(HERE, 'data', 'cards_auto.json'), encoding='utf-8'))
    except (OSError, ValueError): return [], dict(published=0, withheld=0, withheld_by_reason={}), []
    out, rejected = [], []
    for rec in data.get('cards', []):
        if rec.get('status') != 'published': continue
        c = rec['card']; errs = []
        card = dict(id='a%d' % rec['event_id'], title=c['title'], place=c.get('place') or '—', tags=c.get('tags', []), diverge=c['diverge'], auto=True, region=c.get('region', 'ukraine-wide'),
                    slug=rec['slug'], start=c['start'], end=c['end'], tr=c.get('tr', {}), conf=rec['confidence'], first_published=(rec.get('first_published') or '')[:10], first_published_ts=rec.get('first_published') or '', built=rec.get('built') or '', event_id=rec['event_id'], divergence=c.get('divergence'), updates=c.get('updates') or [], roundup=c.get('roundup'))
        for key, g in (('ru', 'ru'), ('ua', 'ua'), ('ind', 'ind')):
            sd = c['sides'][key]
            card[key] = dict(summary=sd['summary'], quotes=[tuple(q) for q in sd['quotes']], also=list(sd['also']))
            if (sd['summary'] is None) != (card['tr'].get('uk', {}).get(key) is None) or (sd['summary'] is None) != (card['tr'].get('en', {}).get(key) is None): errs.append('translation presence mismatch in ' + key)
            if sd['summary'] and not sd['quotes']: errs.append('side %s has a summary without verified quotes' % key)
        for lang in ('uk', 'en'):
            t = card['tr'].get(lang) or {}
            if not all(isinstance(t.get(k), str) and t.get(k) for k in ('t', 'p', 'd')): errs.append('missing %s translation' % lang)
        for tg in card['tags']:
            if tg not in ui.TAGS: errs.append('untranslated tag ' + tg)
        errs += check_quotes(card)
        if errs: rejected.append((card['id'], errs[:3]))
        else: out.append(card)
    return out, data.get('meta', {}), rejected

def check_quotes(c):
    errors = []
    for key, g in SIDES:
        side = c[key]
        for aid, q in side['quotes']:
            a = A.get(aid)
            if not a: errors.append('no such article %s' % aid); continue
            if a['grp'] != g: errors.append('group mismatch %s vs %s' % (a['grp'], g))
            if q not in a['text']: errors.append('QUOTE NOT VERBATIM: ' + q[:80])
        for aid in side['also']:
            a = A.get(aid)
            if not a: errors.append('no such article (also) %s' % aid)
            elif a['grp'] != g: errors.append('group mismatch (also) ' + a['grp'])
    return errors

AUTO_CARDS, AUTO_META, AUTO_REJECTED = [], {}, []
def verify():
    global AUTO_CARDS, AUTO_META, AUTO_REJECTED
    errors = []
    nq = 0
    for c in CARDS:
        nq += sum(len(c[k]['quotes']) for k, _ in SIDES)
        errors += [(c['id'], x) for x in check_quotes(c)]
    # translations complete + consistent with the Russian originals (hand cards)
    for lang, T in (('uk', tr_uk.TR), ('en', tr_en.TR)):
        for c in CARDS:
            t = T.get(c['id'])
            if not t: errors.append((c['id'], lang, 'missing translation')); continue
            for key, _ in SIDES:
                if (c[key]['summary'] is None) != (t[key] is None):
                    errors.append((c['id'], lang, 'summary presence mismatch in ' + key))
            for tg in c['tags']:
                if tg not in ui.TAGS: errors.append((c['id'], lang, 'untranslated tag ' + tg))
            if c['id'] not in events.SLUG or c['id'] not in events.REGION: errors.append((c['id'], 'slug/region missing'))
    if errors:
        for x in errors: print('ERROR', x)
        sys.exit(1)
    AUTO_CARDS, AUTO_META, AUTO_REJECTED = load_auto()
    for cid, errs in AUTO_REJECTED: print('WARNING: auto card %s quarantined at build: %s' % (cid, errs))
    nq += sum(len(c[k]['quotes']) for c in AUTO_CARDS for k, _ in SIDES)
    return nq

# ------------------------------------------------------------------ event model
MON_RU = ui.MONTHS['ru']
def parse_dates(s):
    m = re.match(r'(\d+)(?:–(\d+))? (\w+) (\d{4})$', s)
    d1, d2, mon, y = int(m.group(1)), int(m.group(2) or m.group(1)), MON_RU.index(m.group(3)) + 1, int(m.group(4))
    return dt.date(y, mon, d1), dt.date(y, mon, d2)

def fmt_day(lang, d):
    return '%d %s %d' % (d.day, ui.MONTHS[lang][d.month - 1], d.year)
def fmt_range(lang, a, b):
    if a == b: return fmt_day(lang, a)
    if a.month == b.month and a.year == b.year: return '%d–%d %s %d' % (a.day, b.day, ui.MONTHS[lang][a.month - 1], a.year)
    return fmt_day(lang, a) + ' – ' + fmt_day(lang, b)
def fmt_ts(lang, iso):
    d = dt.datetime.fromisoformat(iso)
    return '%d %s, %02d:%02d' % (d.day, ui.MONTHS[lang][d.month - 1], d.hour, d.minute)

NQ = verify()

def hand_conf(c):
    """Confidence for a hand-written card, from the same features (articles = quoted + linked ones)."""
    arts = []
    for k, _ in SIDES:
        for aid in [x for x, _ in c[k]['quotes']] + c[k]['also']:
            if aid in A and A[aid] not in arts: arts.append(A[aid])
    agree = None
    try:
        rows = db.execute('select rel from hand_rel where hand_id=?', (c['id'],)).fetchall()
        if rows: agree = sum(1 for r in rows if r[0] != 'no') / len(rows)   # same definition as for auto cards: share kept by the filter (yes+partial)
    except sqlite3.Error: pass
    tags = c['tags']
    contra = any(t.startswith('расхождение') and t not in ('расхождение: формулировки', 'расхождение: подтверждение') for t in tags)
    return conf_mod.compute(arts, rel_agreement=agree, model_uncertainty=None, model_contradiction=contra, numeric_conflict=any('числ' in t for t in tags), numeric_cross=False, quote_check='pass')

def build_events():
    ev = []
    for c in CARDS:
        s_, f_ = parse_dates(c['date'])
        ev.append(dict(id=c['id'], card=c, start=s_, end=f_, region=events.REGION[c['id']], slug='%s-%s' % (s_.isoformat(), events.SLUG[c['id']]), conf=hand_conf(c), auto=False, published=PUBLISHED))
    for c in AUTO_CARDS:
        ev.append(dict(id=c['id'], card=c, start=dt.date.fromisoformat(c['start']), end=dt.date.fromisoformat(c['end']), region=c['region'], slug=c['slug'], conf=c['conf'], auto=True,
                       published=c['first_published'] or MODIFIED))
    return ev
def uniquify_slugs(ev):
    """Two events with the same slug would overwrite each other's page and fail the
    build assert. Keep the oldest event's slug (stable URL) and append the event id
    to the others."""
    from collections import defaultdict
    buckets = defaultdict(list)
    for x in ev:
        buckets[x['slug']].append(x)
    n, taken = 0, {x['slug'] for x in ev}
    for slug, xs in buckets.items():
        if len(xs) < 2:
            continue
        keep, *rest = sorted(xs, key=lambda x: (x['start'], str(x['id'])))
        for x in rest:
            new = '%s-%s' % (x['slug'], x['id'])
            i = 2
            while new in taken:
                new = '%s-%s-%d' % (x['slug'], x['id'], i)
                i += 1
            x['slug'] = new
            taken.add(new)
            n += 1
    if n:
        print('WARNING: uniquified %d duplicate event slugs (appended event id; oldest URL kept)' % n)
    return ev

EV = uniquify_slugs(build_events())
EV_BY_SLUG = {x['slug']: x for x in EV}
CHRONO = sorted(EV, key=lambda x: (x['start'], str(x['id'])))                  # oldest first (prev/next)
LISTING = sorted(EV, key=lambda x: (-x['start'].toordinal(), str(x['id'])))   # newest first (indexes)
DAYS = sorted({x['start'] for x in EV})
REGION_KEYS = [k for k in ui.REGIONS if any(x['region'] == k for x in EV)]
assert len({x['slug'] for x in EV}) == len(EV), 'duplicate slugs'
PERIOD = (min(x['start'] for x in EV), max(x['end'] for x in EV)) if EV else (dt.date.today(), dt.date.today())
# ------------------------------------------------------------------ topic hubs (src/topics.py; deterministic, no LLM)
def _topic_text(ev):
    c = ev['card']; return c['title'], c.get('place') or ''
TOPIC_EVS = {t['slug']: [x for x in LISTING if topics.matches(t, *_topic_text(x))] for t in topics.TOPICS}
HUBS = [t for t in topics.TOPICS if len(TOPIC_EVS[t['slug']]) >= topics.MIN_CARDS]
HUB_BY_SLUG = {t['slug']: t for t in HUBS}
EV_HUBS = {}
for _t in HUBS:
    for _x in TOPIC_EVS[_t['slug']]: EV_HUBS.setdefault(_x['slug'], []).append(_t)
def topic_name(lang, t): return t['name'][LANGS.index(lang)]

# ------------------------------------------------------------------ divergence meter (card field from the writing call; backfill cache for older cards)
try: DIV_CACHE = json.load(open(os.path.join(HERE, 'data', 'divergence_cache.json'), encoding='utf-8'))
except (OSError, ValueError): DIV_CACHE = {}
DIV_LEVELS = ('agree', 'details', 'contradict')
def div_level(ev):
    if sum(1 for k, _ in SIDES if ev['card'][k]['summary'] is not None) < 2: return None
    lab = ev['card'].get('divergence') if ev['auto'] else None
    if lab not in DIV_LEVELS: lab = (DIV_CACHE.get(str(ev['id'])) or {}).get('label')
    return lab if lab in DIV_LEVELS else None
COLLECTED = (db.execute('select max(date_local) from articles').fetchone()[0] or MODIFIED)[:10]
def period_text(lang): return fmt_range(lang, *PERIOD)
def collected_text(lang): return fmt_day(lang, dt.date.fromisoformat(COLLECTED))

def content(lang, ev):
    c = ev['card']
    if lang == 'ru':
        return dict(t=c['title'], p=c['place'], d=c['diverge'], ru=c['ru']['summary'], ua=c['ua']['summary'], ind=c['ind']['summary'])
    if c.get('auto'):
        t = c['tr'][lang]
        return dict(t=t['t'], p=t['p'], d=t['d'], ru=t['ru'], ua=t['ua'], ind=t['ind'])
    return (tr_uk if lang == 'uk' else tr_en).TR[c['id']]

DESC_LIMIT = 158
def event_desc_base(lang, ev):
    """Default meta description: date range, place, opening of the divergence text."""
    T = content(lang, ev)
    return trunc('%s, %s. %s %s' % (fmt_range(lang, ev['start'], ev['end']), T['p'], versions_phrase(lang, ev), T['d']), DESC_LIMIT)

VERSIONS = {  # meta-description lead: which versions the card compares (searchable wording, true for the card)
    'ru': {3: 'Что пишут Россия, Украина и независимые СМИ.', 'ru+ua': 'Что пишут Россия и Украина.', 'ru+ind': 'Версии российских и независимых СМИ.', 'ua+ind': 'Версии украинских и независимых СМИ.', 'ru': 'Версия российских СМИ.', 'ua': 'Версия украинских СМИ.', 'ind': 'Версия независимых СМИ.', 1: 'Как событие освещают СМИ.'},
    'uk': {3: 'Що пишуть Росія, Україна та незалежні медіа.', 'ru+ua': 'Що пишуть Росія та Україна.', 'ru+ind': 'Версії російських і незалежних медіа.', 'ua+ind': 'Версії українських і незалежних медіа.', 'ru': 'Версія російських медіа.', 'ua': 'Версія українських медіа.', 'ind': 'Версія незалежних медіа.', 1: 'Як подію висвітлюють медіа.'},
    'en': {3: 'What Russia, Ukraine and independent media say.', 'ru+ua': 'What Russia and Ukraine say.', 'ru+ind': 'Russian and independent media versions.', 'ua+ind': 'Ukrainian and independent media versions.', 'ru': 'The Russian media version.', 'ua': 'The Ukrainian media version.', 'ind': 'The independent media version.', 1: 'How the media cover it.'},
}
def versions_phrase(lang, ev):
    have = [k for k, _ in SIDES if ev['card'][k]['summary'] is not None]
    V = VERSIONS[lang]
    return V[3] if len(have) == 3 else V.get('+'.join(have), V[1])

def unique_event_descs():
    """If two event pages would share a meta description (same date+place+truncated
    opening), append the card title (and the event id if the title also collides)
    so the build is never blocked by a duplicate-description check."""
    from collections import defaultdict
    out, n_fixed = {}, 0
    for lang in LANGS:
        buckets = defaultdict(list)
        for ev in EV:
            buckets[event_desc_base(lang, ev)].append(ev)
        for ev in EV:
            d0 = event_desc_base(lang, ev)
            if len(buckets[d0]) == 1:
                out[(lang, ev['slug'])] = d0
                continue
            n_fixed += 1
            T = content(lang, ev)
            out[(lang, ev['slug'])] = trunc('%s, %s. %s' % (fmt_range(lang, ev['start'], ev['end']), T['p'], T['t']), DESC_LIMIT)
        again = defaultdict(list)
        for ev in EV:
            again[out[(lang, ev['slug'])]].append(ev)
        for evs in again.values():
            if len(evs) < 2:
                continue
            for ev in evs:
                T = content(lang, ev)
                out[(lang, ev['slug'])] = trunc('%s, %s. %s [%s]' % (fmt_range(lang, ev['start'], ev['end']), T['p'], T['t'], ev['id']), DESC_LIMIT)
    if n_fixed:
        print('WARNING: uniquified %d event meta descriptions that collided (appended title/id)' % n_fixed)
    return out

TITLE_HARD_MAX = 90   # longer <title>s are cut by every search engine; drop the site name, then trim at a word
TITLE_MAX = 88   # the site-name suffix already gives ~110-char titles; the phrase is used for short (new-style) headlines
TITLE_SEO = {'ru': ' — что пишут Россия и Украина', 'uk': ' — що пишуть Росія та Україна', 'en': ' — what Russia and Ukraine say'}
def event_title_base(lang, ev):
    """<title> of an event page: headline + searchable 'what Russia and Ukraine say' when both sides are covered and it fits
    in TITLE_MAX characters; otherwise headline + site name (as before)."""
    t = content(lang, ev)['t']
    c = ev['card']
    if c['ru']['summary'] is not None and c['ua']['summary'] is not None and len(t) + len(TITLE_SEO[lang]) <= TITLE_MAX:
        return t + TITLE_SEO[lang]
    if len(t) + 3 + len(ui.UI[lang]['site_name']) <= TITLE_HARD_MAX:
        return '%s — %s' % (t, ui.UI[lang]['site_name'])
    return trunc(t, TITLE_HARD_MAX)          # very long (old-style) headline: the <h1> keeps it whole; the <title> drops the site name

def _title_with_extra(lang, headline_or_title, extra, site=True):
    """Insert a distinguishing extra before the site name: 'Headline — 16 ноября 2026 — Site'."""
    site_name = ui.UI[lang]['site_name']
    suf = ' — ' + site_name
    if headline_or_title.endswith(suf):
        return '%s — %s%s' % (headline_or_title[:-len(suf)], extra, suf)
    if site:
        return '%s — %s — %s' % (headline_or_title, extra, site_name)
    return '%s — %s' % (headline_or_title, extra)

def unique_event_titles():
    """If two event pages would share a <title> (same headline + site name),
    append the localized date (and the event id if the date also collides)
    so the build is never blocked by a duplicate-title check."""
    from collections import defaultdict
    out, n_fixed = {}, 0
    for lang in LANGS:
        buckets = defaultdict(list)
        for ev in EV:
            buckets[event_title_base(lang, ev)].append(ev)
        for ev in EV:
            t0 = event_title_base(lang, ev)
            if len(buckets[t0]) == 1:
                out[(lang, ev['slug'])] = t0
                continue
            n_fixed += 1
            out[(lang, ev['slug'])] = _title_with_extra(lang, t0, fmt_range(lang, ev['start'], ev['end']))
        again = defaultdict(list)
        for ev in EV:
            again[out[(lang, ev['slug'])]].append(ev)
        for evs in again.values():
            if len(evs) < 2:
                continue
            for ev in evs:
                t1 = event_title_base(lang, ev)
                dated = _title_with_extra(lang, t1, fmt_range(lang, ev['start'], ev['end']))
                out[(lang, ev['slug'])] = _title_with_extra(lang, dated, ev['id'])
    if n_fixed:
        print('WARNING: uniquified %d event <title>s that collided (appended date/id)' % n_fixed)
    return out

def tag_text(lang, tg):
    return tg if lang == 'ru' else ui.TAGS[tg][0 if lang == 'uk' else 1]
def region_name(lang, k): return ui.REGIONS[k][LANGS.index(lang)]

# ------------------------------------------------------------------ URLs
def path(lang, key):
    k = key[0]
    if k == 'home': return '/%s/' % lang
    if k == 'about': return '/%s/about/' % lang
    if k == 'corrections': return '/%s/corrections/' % lang
    if k == 'days': return '/%s/%s/' % (lang, DAY_SLUG[lang])
    if k == 'day': return '/%s/%s/%s/' % (lang, DAY_SLUG[lang], key[1])
    if k == 'weeks': return '/%s/%s/' % (lang, WEEK_SLUG[lang])
    if k == 'week': return '/%s/%s/%s/' % (lang, WEEK_SLUG[lang], key[1])
    if k == 'glossary': return '/%s/%s/' % (lang, GLOSS_SLUG[lang])
    if k == 'search': return '/%s/%s/' % (lang, SEARCH_SLUG[lang])
    if k == 'support': return '/%s/%s/' % (lang, SUPPORT_SLUG[lang])
    if k == 'regions': return '/%s/region/' % lang
    if k == 'region': return ('/%s/region/%s/page/%d/' % (lang, key[1], key[2])) if len(key) > 2 and key[2] > 1 else '/%s/region/%s/' % (lang, key[1])
    if k == 'dates': return '/%s/date/' % lang
    if k == 'date': return '/%s/date/%s/' % (lang, key[1])
    if k == 'event': return '/%s/%s/' % (lang, key[1])
    if k == 'divergence': return '/%s/%s/' % (lang, DIV_SLUG[lang])
    if k == 'map': return '/%s/%s/' % (lang, MAP_SLUG[lang])
    if k == 'topics': return '/%s/%s/' % (lang, topics.PREFIX[lang])
    if k == 'topic': return ('/%s/%s/%s/%d/' % (lang, topics.PREFIX[lang], key[1], key[2])) if len(key) > 2 and key[2] > 1 else '/%s/%s/%s/' % (lang, topics.PREFIX[lang], key[1])
    raise KeyError(key)
def absu(p): return BASE + p
def trunc(s, n):
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) <= n: return s
    cut = s[:n - 1].rsplit(' ', 1)[0].rstrip(' ,;:–-—')
    return cut + '…'
def first_sentence(s, n=220):
    m = re.search(r'(?<=[.!?])\s+(?=[«“"A-ZА-ЯІЇЄҐ])', s)
    return trunc(s[:m.start()] if m else s, n)

EVENT_DESCS = unique_event_descs()
EVENT_TITLES = unique_event_titles()

# ------------------------------------------------------------------ rendering helpers
def jsonld(obj):
    return '<script type="application/ld+json">%s</script>' % json.dumps(obj, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')

def analytics_block():
    comment = ('<!-- Analytics: DISABLED by default (no tracking). To enable, rebuild with ANALYTICS=plausible ANALYTICS_DOMAIN=your-domain.tld '
               'or ANALYTICS=umami ANALYTICS_SRC=https://your-umami/script.js ANALYTICS_ID=website-uuid. Equivalent markup:\n'
               '<script defer data-domain="YOUR-DOMAIN" src="https://plausible.io/js/script.js"></script>\n'
               '<script defer src="https://YOUR-UMAMI-HOST/script.js" data-website-id="YOUR-WEBSITE-ID"></script> -->')
    if ANALYTICS == 'plausible':
        return '<script defer data-domain="%s" src="%s"></script>' % (e(os.environ.get('ANALYTICS_DOMAIN', 'example.com')), e(os.environ.get('ANALYTICS_SRC', 'https://plausible.io/js/script.js')))
    if ANALYTICS == 'umami':
        return '<script defer src="%s" data-website-id="%s"></script>' % (e(os.environ.get('ANALYTICS_SRC', 'https://cloud.umami.is/script.js')), e(os.environ.get('ANALYTICS_ID', 'YOUR-WEBSITE-ID')))
    return ''

CSS_V = hashlib.md5(open(os.path.join(HERE, 'src/assets/style.css'), 'rb').read()).hexdigest()[:8]
JS_V = hashlib.md5(open(os.path.join(HERE, 'src/assets/filter.js'), 'rb').read()).hexdigest()[:8]
MAP_V = hashlib.md5(open(os.path.join(HERE, 'src/assets/map.js'), 'rb').read()).hexdigest()[:8]
TH_V = hashlib.md5(open(os.path.join(HERE, 'src/assets/theme.js'), 'rb').read()).hexdigest()[:8]
SRCH_V = hashlib.md5(open(os.path.join(HERE, 'src/assets/search.js'), 'rb').read()).hexdigest()[:8]
SUP_V = hashlib.md5(open(os.path.join(HERE, 'src/assets/support.js'), 'rb').read()).hexdigest()[:8]
# Performance: the stylesheet is small, so it is minified and inlined into every page (no render-blocking request);
# scripts get content-hashed file names under /v/ which _headers serves as immutable (1 year).
def minify_css(css):
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'\s+', ' ', css)
    css = re.sub(r'\s*([{};,>])\s*', r'\1', css)
    return css.replace(';}', '}').strip()
_FAV = open(os.path.join(HERE, 'src/assets/favicon.svg'), encoding='utf-8').read().strip()
CSS_MIN = minify_css(open(os.path.join(HERE, 'src/assets/style.css'), encoding='utf-8').read()).replace(
    'url(favicon.svg)', 'url(data:image/svg+xml;base64,%s)' % __import__('base64').b64encode(_FAV.encode()).decode())
STYLE_TAG = '<style>%s</style>' % CSS_MIN
JS_FILTER = '/v/filter.%s.js' % JS_V
JS_THEME = '/v/theme.%s.js' % TH_V
JS_MAP = '/v/map.%s.js' % MAP_V
JS_SUPPORT = '/v/support.%s.js' % SUP_V
JS_SEARCH = '/v/search.%s.js' % SRCH_V
HOME_MIN = int(os.environ.get('HOME_MIN', '36'))   # home lists the newest whole days until at least this many cards; the rest load on demand
OG_IMG = absu('/assets/og.png')
THEME_LABEL = {'ru': ('Тема оформления', 'Светлая / тёмная тема'), 'uk': ('Тема оформлення', 'Світла / темна тема'), 'en': ('Colour theme', 'Switch light / dark theme')}
FILTER_RESET = {'ru': 'Сбросить', 'uk': 'Скинути', 'en': 'Reset'}
# pre-paint theme (avoids a flash); the manual choice lives in localStorage['theme'] = light | dark
THEME_INIT = '<script>try{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}</script>'
GLYPH = {'ru': '■', 'ua': '●', 'ind': '◆'}      # distinct shapes: colour is never the only cue

def head(lang, key, title, desc, og_type='website', ld=(), robots='index,follow,max-image-preview:large', alt_keys=True):
    U = ui.UI[lang]
    url = absu(path(lang, key))
    h = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>%s</title>' % e(title), '<meta name="description" content="%s">' % e(desc),
         '<meta name="robots" content="%s">' % robots, '<link rel="canonical" href="%s">' % e(url)]
    for l in LANGS:
        h.append('<link rel="alternate" hreflang="%s" href="%s">' % (l, e(absu(path(l, key)))))
    xd = absu('/') if key[0] == 'home' else absu(path('en', key))
    h.append('<link rel="alternate" hreflang="x-default" href="%s">' % e(xd))
    h.append('<link rel="alternate" type="application/rss+xml" title="%s" href="%s">' % (e(U['site_name'] + ' — RSS'), e(absu(rss_path(lang)))))
    if key[0] == 'topic' and key[1] in HUB_BY_SLUG:
        h.append('<link rel="alternate" type="application/rss+xml" title="%s" href="%s">' % (e(topic_name(lang, HUB_BY_SLUG[key[1]]) + ' — RSS'), e(absu(rss_path(lang, key[1])))))
    h += ['<meta property="og:site_name" content="%s">' % e(U['site_name']), '<meta property="og:type" content="%s">' % og_type,
          '<meta property="og:title" content="%s">' % e(title), '<meta property="og:description" content="%s">' % e(desc),
          '<meta property="og:url" content="%s">' % e(url), '<meta property="og:locale" content="%s">' % ui.OG_LOCALE[lang]]
    for l in LANGS:
        if l != lang: h.append('<meta property="og:locale:alternate" content="%s">' % ui.OG_LOCALE[l])
    og_url, og_alt = CUR_OG[0] or (OG_IMG, None)
    h += ['<meta property="og:image" content="%s">' % e(og_url), '<meta property="og:image:type" content="image/png">', '<meta property="og:image:width" content="1200">', '<meta property="og:image:height" content="630">',
          '<meta property="og:image:alt" content="%s">' % e(og_alt or (U['site_name'] + ' — ' + U['site_tagline'])),
          '<meta name="twitter:card" content="summary_large_image">', '<meta name="twitter:title" content="%s">' % e(title),
          '<meta name="twitter:description" content="%s">' % e(desc), '<meta name="twitter:image" content="%s">' % e(og_url)]
    if og_type == 'article':
        h += ['<meta property="article:published_time" content="%s">' % CUR_PUB[0], '<meta property="article:modified_time" content="%s">' % CUR_MOD[0]]
    h += ['<meta name="theme-color" content="#f5f5f2" media="(prefers-color-scheme: light)">', '<meta name="theme-color" content="#111317" media="(prefers-color-scheme: dark)">',
          '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">', THEME_INIT,
          STYLE_TAG]
    h += [jsonld(x) for x in ld]
    h.append(analytics_block())
    return '\n'.join(h)

def mailto(lang, key, ev=None):
    U = ui.UI[lang]
    subj = '%s: %s' % (U['site_name'], U['err_subject'] if ev else U['feedback_subject'])
    body = '%s: %s\n' % (U['err_body'] if ev else U['feedback_body'], absu(path(lang, key)))
    if ev: body += 'ID: %s\n' % ev['id']
    return 'mailto:%s?subject=%s&body=%s' % (CONTACT, urllib.parse.quote(subj), urllib.parse.quote(body + '\n'))

def header(lang, key):
    U = ui.UI[lang]
    nav = ''.join('<a href="%s"%s>%s</a>' % (path(lang, k), ' aria-current="page"' if key[0] == k[0] or (k[0] == 'home' and key[0] == 'event') or (k[0] == 'topics' and key[0] == 'topic') or (k[0] == 'days' and key[0] == 'day') else '', U.get(lab) or TOPIC_UI[lang].get(lab) or (DAY_UI[lang]['nav'] if lab == 'nav_day' else MAP_UI[lang][lab]))
                  for k, lab in ((('home',), 'nav_events'), (('days',), 'nav_day'), (('topics',), 'nav_topics'), (('map',), 'nav_map'), (('regions',), 'nav_regions'), (('dates',), 'nav_dates'), (('about',), 'nav_about')))
    langs = ''.join('<a href="%s" hreflang="%s" lang="%s" title="%s"%s>%s</a>' % (path(l, key), l, l, e(ui.LANG_NAME[l]), ' aria-current="true"' if l == lang else '', ui.LANG_SHORT[l]) for l in LANGS)
    tl = THEME_LABEL[lang]
    theme = ('<button type="button" class="theme" id="theme-toggle" hidden aria-pressed="false" aria-label="%s" title="%s">'
             '<svg class="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
             '<svg class="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg></button>') % (e(tl[0]), e(tl[1]))
    return ('<a class="skip" href="#main">%s</a>\n<header class="top"><div class="in"><a class="brand" href="%s">%s</a>'
            '<nav aria-label="%s">%s</nav><a class="srch" href="%s" title="%s" aria-label="%s"%s><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg></a><nav class="langs" aria-label="%s">%s</nav>%s</div></header>') % (
        e(U['skip']), path(lang, ('home',)), e(U['site_name']), e(U['nav_label']), nav, path(lang, ('search',)), e(SEARCH_UI[lang]['nav']), e(SEARCH_UI[lang]['nav']), ' aria-current="page"' if key[0] == 'search' else '', e(U['lang_label']), langs, theme)

def footer(lang, key):
    U = ui.UI[lang]
    return ('<footer class="f"><div class="in"><p>%s</p><p>%s</p><p class="fl"><a href="%s">%s</a> · <a href="%s">%s</a> · <a href="%s">%s</a> · <a href="%s">%s</a> · <a href="%s">%s</a> · <a href="%s">%s</a> · <a href="%s" rel="nofollow">%s</a> · <a href="/sitemap.xml">sitemap.xml</a> · <a href="%s">RSS</a> · <a href="https://t.me/twoversions_war" rel="noopener">Telegram</a></p><p class="fl">%s<a href="%s">%s</a></p><p class="sup-f"><a class="sup-btn" href="%s?from=footer">%s</a></p></div></footer>') % (
        e(U['footer_disclaimer']), e(U['footer_base'].format(collected=collected_text(lang))), path(lang, ('about',)), e(U['nav_about']), path(lang, ('corrections',)), e(U['footer_corr']),
        path(lang, ('regions',)), e(U['nav_regions']), path(lang, ('topics',)), e(TOPIC_UI[lang]['nav_topics']), path(lang, ('map',)), e(MAP_UI[lang]['nav_map']), path(lang, ('divergence',)), e(DIV_UI[lang]['nav']), e(mailto(lang, key)), e(U['corr_report']), rss_path(lang), (('<a href="%s">%s</a> · ' % (path(lang, ('weeks',)), e(WEEK_UI[lang]['h1']))) if WEEKS else ''), path(lang, ('glossary',)), e(GLOSS_UI[lang]['h1']), path(lang, ('support',)), e(SUPPORT_UI[lang]['btn']))

def crumbs(lang, items):
    """items: [(name, key)] — last one is the current page. Returns (html, jsonld)."""
    U = ui.UI[lang]
    parts = [(U['crumb_home'], ('home',))] + items
    li = []
    for i, (name, k) in enumerate(parts):
        if i == len(parts) - 1: li.append('<li aria-current="page">%s</li>' % e(name))
        else: li.append('<li><a href="%s">%s</a></li>' % (path(lang, k), e(name)))
    ld = {'@context': 'https://schema.org', '@type': 'BreadcrumbList',
          'itemListElement': [{'@type': 'ListItem', 'position': i + 1, 'name': n, 'item': absu(path(lang, k))} for i, (n, k) in enumerate(parts)]}
    return '<nav class="crumbs" aria-label="%s"><ol>%s</ol></nav>' % (e(U['breadcrumbs']), ''.join(li)), ld

CUR_OG = [None]         # (url, alt) share image of the page being rendered (cards); None = site default
CUR_PUB = [PUBLISHED]   # datePublished of the page being rendered (set per event)
CUR_MOD = [MODIFIED]
OG_RU = {}   # slug -> ru share-image URL (for data/tg_feed.json)
LOGO_IMG = absu('/assets/logo.png')
def event_times(ev):
    """(datePublished, dateModified) as full ISO-8601 with offset for auto cards (first publication / last rebuild), plain dates for hand cards."""
    c = ev['card']
    if not ev['auto']: return PUBLISHED, MODIFIED
    pub = c.get('first_published_ts') or ev['published']
    mod = pub
    try:
        if c.get('built') and dt.datetime.fromisoformat(c['built']) > dt.datetime.fromisoformat(pub): mod = c['built']
    except (ValueError, TypeError): pass
    return pub, mod
PAGES = []      # registry of generated pages (for the sitemap): key tuples
def write(rel, text):
    fp = os.path.join(OUT, rel.lstrip('/'))
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    open(fp, 'w', encoding='utf-8').write(text)

_USED_TITLES, _USED_DESCS = {}, {}

def _seo_extra(lang, key):
    if key and key[0] == 'event':
        ev = EV_BY_SLUG.get(key[1])
        if ev:
            return [fmt_range(lang, ev['start'], ev['end']), str(ev['id'])]
    extras = [key[0]] if key else ['page']
    if key and len(key) > 1:
        extras.append(str(key[1]))
    return extras

def _uniquify_seo_text(lang, key, text, used, kind):
    """Last-resort uniqueness for <title> / meta description across all page types."""
    if not text or text not in used:
        if text:
            used[text] = (lang, key)
        return text
    candidate = text
    for extra in _seo_extra(lang, key):
        candidate = _title_with_extra(lang, text, extra, site=False) if kind == 'title' else (text.rstrip('…') + ' — ' + extra)
        if kind == 'desc':
            candidate = trunc(candidate, DESC_LIMIT)
        if candidate not in used:
            break
    else:
        n = 2
        base = candidate
        while candidate in used:
            candidate = ('%s [%d]' % (base, n)) if kind == 'title' else trunc(base + ' [%d]' % n, DESC_LIMIT)
            n += 1
    used[candidate] = (lang, key)
    print('WARNING: uniquified duplicate %s for %s' % (kind, path(lang, key)))
    return candidate

NOINDEX_KEYS = {'search'}   # thin utility pages: noindex,follow and not in the sitemap (canonical stays self)
def page(lang, key, title, desc, main_html, og_type='website', ld=(), extra_head='', scripts=''):
    U = ui.UI[lang]
    title = _uniquify_seo_text(lang, key, title, _USED_TITLES, 'title')
    desc = _uniquify_seo_text(lang, key, desc, _USED_DESCS, 'desc')
    doc = ('<!doctype html>\n<html lang="%s">\n<head>\n%s\n%s\n</head>\n<body><!--email_off-->\n%s\n<main id="main">\n%s\n</main>\n%s<!--/email_off-->\n'
           '<script>try{localStorage.setItem("lang","%s")}catch(e){}</script>\n<script src="%s" defer></script>\n%s</body>\n</html>\n') % (
        lang, head(lang, key, title, desc, og_type, ld, **({'robots': 'noindex,follow'} if key[0] in NOINDEX_KEYS else {})), extra_head, header(lang, key), main_html, footer(lang, key), lang, JS_THEME, scripts)
    write(path(lang, key) + 'index.html', doc)
    PAGES.append((lang, key))

def conf_badge(lang, ev, link=False):
    U = ui.UI[lang]; cf = ev['conf']; L = conf_mod.LABELS[cf['label']]
    txt = L[lang]
    inner = '<span class="ic" aria-hidden="true">%s</span><span class="tx"><span class="vh">%s: </span>%s</span>' % (e(L['icon']), e(U['conf_h']), e(txt))
    return '<span class="conf c-%s" title="%s">%s</span>' % (cf['label'], e(conf_mod.EXPLAIN[cf['label']][lang]), inner)

def cov_badges(lang, ev):
    U = ui.UI[lang]; c = ev['card']; out = []
    for key, g in SIDES:
        has = c[key]['summary'] is not None
        out.append('<span class="cv %s %s" title="%s: %s"><span class="g" aria-hidden="true">%s</span>%s <span class="s" aria-hidden="true">%s</span><span class="vh">: %s</span></span>' % (
            g, 'on' if has else 'off', e(U['cov_' + g]), e(U['cov_yes'] if has else U['cov_no']), GLYPH[g], e(U['cov_' + g]), '✓' if has else '–', e(U['cov_yes'] if has else U['cov_no'])))
    return '<div class="cov" role="group" aria-label="%s">%s</div>' % (e(U['f_cov']), ''.join(out))
def ncov(ev): return sum(1 for k, _ in SIDES if ev['card'][k]['summary'] is not None)

# one-sided cards (exactly one of the two war sides reported; independent outlets do not count as a side) get a visible label
ONESIDE = {'ru': 'Другая сторона не комментирует', 'uk': 'Інша сторона не коментує', 'en': 'The other side has not commented'}
ONESIDE_TIP = {'ru': 'В отслеживаемых источниках этой стороны сообщений о событии не найдено.',
               'uk': 'У відстежуваних джерелах цієї сторони повідомлень про подію не знайдено.',
               'en': 'No reports of this event were found in the sources we track for this side.'}
ROUNDUP_UI = {'ru': 'Сводка дня', 'uk': 'Зведення дня', 'en': 'Daily roundup'}
ROUNDUP_NOTE = {'ru': 'Сводка нескольких небольших однотипных событий за день, собранная автоматически.',
                'uk': 'Зведення кількох невеликих однотипних подій за день, зібране автоматично.',
                'en': 'An automatic roundup of several minor events of the same kind on one day.'}
def one_sided(ev):
    c = ev['card']; return (c['ru']['summary'] is not None) != (c['ua']['summary'] is not None)
def oneside_badge(lang, ev):
    if not one_sided(ev): return ''
    return '<span class="oneside" title="%s">%s</span>' % (e(ONESIDE_TIP[lang]), e(ONESIDE[lang]))
def roundup_badge(lang, ev):
    if not ev.get('roundup'): return ''
    return '<span class="rndup" title="%s">%s</span>' % (e(ROUNDUP_NOTE[lang]), e(ROUNDUP_UI[lang]))

def event_li(lang, ev, with_filter_data=False, h='h3'):
    U = ui.UI[lang]; T = content(lang, ev)
    attrs = ''
    if with_filter_data:
        attrs = ' data-n="%d" data-region="%s" data-date="%s" data-conf="%s" data-text="%s"' % (ncov(ev), ev['region'], ev['start'].isoformat(), ev['conf']['label'], e((T['t'] + ' ' + T['p'] + ' ' + region_name(lang, ev['region'])).lower()))
    return ('<li class="ev"%s><article><%s><a href="%s">%s</a></%s>'
            '<p class="chips"><time class="chip d" datetime="%s">%s</time><span class="chip p">%s</span><a class="chip" href="%s">%s</a></p><p class="lead">%s</p>%s</article></li>') % (
        attrs, h, path(lang, ('event', ev['slug'])), e(T['t']), h, ev['start'].isoformat(), e(fmt_range(lang, ev['start'], ev['end'])), e(T['p']),
        path(lang, ('region', ev['region'])), e(region_name(lang, ev['region'])), e(first_sentence(T['d'])), cov_badges(lang, ev) + conf_badge(lang, ev) + div_badge(lang, ev) + oneside_badge(lang, ev) + roundup_badge(lang, ev) + upd_marker(lang, ev))

def itemlist_ld(lang, evs, name, key):
    return {'@context': 'https://schema.org', '@type': 'CollectionPage', 'name': name, 'url': absu(path(lang, key)), 'inLanguage': lang,
            'mainEntity': {'@type': 'ItemList', 'numberOfItems': len(evs), 'itemListElement': [
                {'@type': 'ListItem', 'position': i + 1, 'url': absu(path(lang, ('event', x['slug']))), 'name': content(lang, x)['t']} for i, x in enumerate(evs)]}}

# ------------------------------------------------------------------ pages
def counts():
    return {g: db.execute('select count(*) from articles where grp=?', (g,)).fetchone()[0] for g in ('ru', 'ua', 'ind')}
CNT = counts()

HOME_MORE = {'ru': ('Показать ещё', 'Все события по датам'), 'uk': ('Показати ще', 'Усі події за датами'), 'en': ('Show more', 'All events by date')}
def home_events():
    out = []
    for d in reversed(DAYS):
        out += [x for x in LISTING if x['start'] == d]
        if len(out) >= HOME_MIN: break
    return out

def build_events_json(lang):
    """Compact index of every event for the home filters (fetched only when the visitor filters or asks for the full list).
    Content-hashed name under /v/, so it is cached as immutable and a new build never serves a stale list."""
    rows = []
    for ev in LISTING:
        T = content(lang, ev)
        rows.append([ncov(ev), ev['region'], ev['start'].isoformat(), ev['conf']['label'], (T['t'] + ' ' + T['p'] + ' ' + region_name(lang, ev['region'])).lower(), event_li(lang, ev, False)])
    data = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    rel = '/v/events-%s.%s.json' % (lang, hashlib.md5(data.encode('utf-8')).hexdigest()[:10])
    write(rel, data)
    return rel

def build_home(lang):
    U = ui.UI[lang]; key = ('home',)
    HOME = home_events()
    evj = build_events_json(lang)
    ui_json = dict(cov=U['f_cov'], all=U['f_all'], three=U['f_three'], two=U['f_two'], one=U['f_one'], regionL=U['f_region_l'], allRegions=U['f_region'],
                   dateL=U['f_date_l'], allDates=U['f_date'], confL=U['f_conf'], allConf=U['f_conf_all'], confs=[[k, v[lang]] for k, v in conf_mod.LABELS.items()], reset=FILTER_RESET[lang], search=U['f_search'], none=U['f_none'], shown=U['f_shown'],
                   regions=[[k, region_name(lang, k)] for k in REGION_KEYS], dates=[[d.isoformat(), fmt_day(lang, d)] for d in DAYS],
                   total=len(EV), src=evj, more=HOME_MORE[lang][0])
    body = ('<section class="hero" aria-labelledby="top-h"><div><h1 id="top-h">%s</h1>\n<p class="lede">%s</p>\n<p class="meta stats">%s</p></div>\n'
            '<section class="box" aria-labelledby="howto"><h2 id="howto">%s</h2><p>%s</p></section></section>\n'
            '<section aria-labelledby="evs"><div class="sect-h"><h2 id="evs">%s</h2></div>\n<div class="filters" id="filters" hidden data-ui="%s"></div>\n<ul class="evlist" id="events">\n%s\n</ul>\n<p class="more" id="more"><a href="%s">%s →</a></p></section>\n%s') % (
        e(U['home_h1']), e(U['site_tagline']), e(U['stats'].format(n=len(EV), period=period_text(lang), **CNT)), e(U['howto_h']), e(U['howto']), e(U['list_h']),
        e(json.dumps(ui_json, ensure_ascii=False)), '\n'.join(event_li(lang, x, True) for x in HOME), path(lang, ('dates',)), e('%s (%d)' % (HOME_MORE[lang][1], len(EV))),
        ('<section aria-labelledby="hub-h"><h2 id="hub-h"><a href="%s">%s</a></h2>%s</section>' % (path(lang, ('topics',)), e(TOPIC_UI[lang]['home_h']), topic_pills(lang))) if HUBS else '')
    site = {'@context': 'https://schema.org', '@type': 'WebSite', '@id': absu('/') + '#website', 'url': absu(path(lang, key)), 'name': U['site_name'],
            'description': U['site_tagline'], 'inLanguage': lang,
            'potentialAction': {'@type': 'SearchAction', 'target': {'@type': 'EntryPoint', 'urlTemplate': absu(path(lang, ('search',))) + '?q={search_term_string}'}, 'query-input': 'required name=search_term_string'}}
    page(lang, key, U['home_title'] + ' — ' + U['site_name'] if False else U['home_title'], U['home_desc'].format(n=len(EV), period=period_text(lang)), body,
         ld=[site, itemlist_ld(lang, HOME, U['home_title'], key)], scripts='<script src="%s" defer></script>\n' % JS_FILTER)

def side_html(lang, ev, key, g):
    U = ui.UI[lang]; c = ev['card']; side = c[key]; T = content(lang, ev)
    out = ['<section class="side side-%s" aria-labelledby="h-%s"><h2 id="h-%s"><span class="g" aria-hidden="true">%s</span>%s</h2>' % (g, g, g, GLYPH[g], e(U['h_' + g]))]
    if side['summary'] is None:
        if key in ('ru', 'ua') and one_sided(ev):
            out.append('<p class="none oneside-note"><strong>%s</strong> — %s</p></section>' % (e(ONESIDE[lang]), e(U['none_found']))); return '\n'.join(out)
        out.append('<p class="none">%s</p></section>' % e(U['none_found'])); return '\n'.join(out)
    out.append('<p class="sum"><span class="auto">%s</span> %s</p>' % (e(U['auto']), e(T[key])))
    for aid, q in side['quotes']:
        a = A[aid]
        out.append('<blockquote lang="%s"><p>%s%s%s</p><footer><a href="%s" target="_blank" rel="noopener">%s</a>, <time datetime="%s">%s</time> <span class="langtag">%s: %s</span></footer></blockquote>' % (
            a['lang'], U['q_open'], e(q), U['q_close'], e(a['url']), e(ui.SRC_NAME[lang].get(a['source'], a['source'])), e(a['date']), e(fmt_ts(lang, a['date'])),
            e(U['orig']), e(U['orig_lang'][a['lang']])))
    seen = []
    for aid in [x for x, _ in side['quotes']] + side['also']:
        if aid not in seen: seen.append(aid)
    out.append('<ul class="links" aria-label="%s">' % e(U['sources_links']))
    for aid in seen:
        a = A[aid]; t = trunc(a['title'].replace('\n', ' '), 110)
        out.append('<li><a href="%s" target="_blank" rel="noopener" lang="%s">%s</a> <span class="src">— %s, %s</span></li>' % (
            e(a['url']), a['lang'], e(t), e(ui.SRC_NAME[lang].get(a['source'], a['source'])), e(fmt_ts(lang, a['date']))))
    out.append('</ul></section>')
    return '\n'.join(out)

def conf_box(lang, ev):
    U = ui.UI[lang]; cf = ev['conf']; f = cf['features']
    facts = {'ru': 'Материалов: %d, источников: %d, групп: %d.', 'uk': 'Матеріалів: %d, джерел: %d, груп: %d.', 'en': 'Articles: %d, sources: %d, groups: %d.'}[lang] % (f['n_articles'], f['n_sources'], f['n_groups'])
    return ('<section class="confbox c-%s" aria-labelledby="h-conf"><h2 id="h-conf">%s</h2><p class="cb">%s</p><p>%s</p><p class="meta">%s %s <a href="%s#conf">%s</a></p></section>') % (
        cf['label'], e(U['conf_h']), conf_badge(lang, ev), e(conf_mod.EXPLAIN[cf['label']][lang]), e(facts), e(U['conf_note']), path(lang, ('about',)), e(U['conf_more']))

def build_event(lang, ev):
    U = ui.UI[lang]; c = ev['card']; T = content(lang, ev); key = ('event', ev['slug'])
    rng = fmt_range(lang, ev['start'], ev['end']); rname = region_name(lang, ev['region'])
    title = EVENT_TITLES[(lang, ev['slug'])]
    desc = EVENT_DESCS[(lang, ev['slug'])]
    cr_html, cr_ld = crumbs(lang, [(rname, ('region', ev['region'])), (T['t'], key)])
    i = CHRONO.index(ev)
    def pn(x, cls, lab):
        if x is None: return ''
        return '<a rel="%s" href="%s"><small>%s</small>%s</a>' % (cls, path(lang, ('event', x['slug'])), e(U[lab]), e(content(lang, x)['t']))
    prev_, next_ = (CHRONO[i - 1] if i > 0 else None), (CHRONO[i + 1] if i + 1 < len(CHRONO) else None)
    def li(x):
        return '<li class="ev"><h3><a href="%s">%s</a></h3><p class="meta"><time datetime="%s">%s</time> · %s</p></li>' % (
            path(lang, ('event', x['slug'])), e(content(lang, x)['t']), x['start'].isoformat(), e(fmt_range(lang, x['start'], x['end'])), e(content(lang, x)['p']))
    similar = similar_events(ev)
    sim_ids = set(id(x) for x in similar)
    sim_html = ('<section class="similar" aria-labelledby="sim"><h2 id="sim">%s</h2><ul class="evlist">%s</ul></section>' % (e(SIM_H[lang]), ''.join(li(x) for x in similar))) if similar else ''
    related_all = [x for x in LISTING if x['region'] == ev['region'] and x is not ev]
    near = set(id(x) for x in sorted([x for x in related_all if id(x) not in sim_ids], key=lambda x: (abs((x['start'] - ev['start']).days), str(x['id'])))[:RELATED_N])
    related = [x for x in related_all if id(x) in near]          # newest first, like the full list
    rel_html = ''
    if related:
        more_rel = ('<p class="more"><a href="%s">%s →</a></p>' % (path(lang, ('region', ev['region'])), e(REL_ALL[lang].format(r=rname, n=len(related_all) + 1)))) if len(related_all) > len(related) + len(sim_ids) else ''
        rel_html = '<section aria-labelledby="rel"><h2 id="rel">%s</h2><ul class="evlist">%s</ul>%s</section>' % (e(U['related']), ''.join(li(x) for x in related), more_rel)
    rel_html = sim_html + rel_html
    tags = ''.join('<span>%s</span>' % e(tag_text(lang, t)) for t in c['tags'])
    body = ('%s\n<article lang="%s">\n<header class="ehead"><h1>%s</h1>\n<p class="chips"><time class="chip d" datetime="%s">%s</time><span class="chip p"><span class="vh">%s: </span>%s</span>'
            '<a class="chip" href="%s"><span class="vh">%s: </span>%s</a><span class="auto">%s</span></p>\n%s\n'
            '<div class="tags" aria-label="%s">%s</div>%s</header>\n'
            '<section class="diverge" aria-labelledby="h-div"><h2 id="h-div">%s</h2><p>%s</p></section>\n%s<div class="cols">\n%s\n%s\n%s\n</div>\n'
            '%s\n<p class="note">%s %s <a href="%s" rel="nofollow">%s</a></p>\n%s</article>\n<nav class="pn" aria-label="%s">%s%s</nav>\n%s') % (
        cr_html, lang, e(T['t']), ev['start'].isoformat(), e(rng), e(U['meta_place']), e(T['p']), path(lang, ('region', ev['region'])), e(U['meta_region']), e(rname), e(U['auto']),
        cov_badges(lang, ev) + div_badge(lang, ev, link=True) + oneside_badge(lang, ev) + roundup_badge(lang, ev), e(U['tags_label']), tags, hub_links(lang, ev),
        e(U['h_diverge']), e(T['d']), upd_block(lang, ev),
        side_html(lang, ev, 'ru', 'ru'), side_html(lang, ev, 'ua', 'ua'), side_html(lang, ev, 'ind', 'ind'),
        conf_box(lang, ev),
        e(U['event_note']), e(U['time_note']), e(mailto(lang, key, ev)), e(U['err_report']), support_card_block(lang, ev, T['t']),
        e(U['pn_nav']), pn(prev_, 'prev', 'prev'), pn(next_, 'next', 'next'), rel_html)
    cites = []
    for k, _ in SIDES:
        for aid in [x for x, _ in c[k]['quotes']] + c[k]['also']:
            a = A[aid]
            if a['url'] not in [z['url'] for z in cites]:
                cites.append({'@type': 'NewsArticle', 'url': a['url'], 'headline': trunc(a['title'].replace('\n', ' '), 110), 'inLanguage': a['lang'],
                              'publisher': {'@type': 'Organization', 'name': a['source']}})
    org = {'@type': 'Organization', '@id': absu('/') + '#org-' + lang, 'name': U['site_name'], 'url': absu(path(lang, ('home',))),
           'logo': {'@type': 'ImageObject', 'url': LOGO_IMG, 'width': 512, 'height': 512}}
    pub_ts, mod_ts = event_times(ev)
    art = {'@context': 'https://schema.org', '@type': 'NewsArticle', '@id': absu(path(lang, key)) + '#article', 'headline': trunc(T['t'], 110), 'description': desc,
           'inLanguage': lang, 'url': absu(path(lang, key)), 'mainEntityOfPage': {'@type': 'WebPage', '@id': absu(path(lang, key))},
           'datePublished': pub_ts, 'dateModified': mod_ts, 'image': [og_image(lang, ev)],
           'additionalProperty': [{'@type': 'PropertyValue', 'name': U['conf_h'], 'propertyID': 'confirmationLevel', 'value': conf_mod.LABELS[ev['conf']['label']][lang],
                                   'description': conf_mod.EXPLAIN[ev['conf']['label']][lang]},
                                  {'@type': 'PropertyValue', 'name': 'sourcesCount', 'value': ev['conf']['features']['n_sources']}],
           'creditText': U['auto_pub'], 'author': org, 'publisher': org, 'isAccessibleForFree': True,
           'articleSection': rname, 'keywords': ', '.join(tag_text(lang, t) for t in c['tags']), 'contentLocation': {'@type': 'Place', 'name': T['p']},
           'temporalCoverage': '%s/%s' % (ev['start'].isoformat(), ev['end'].isoformat()), 'citation': cites}
    CUR_PUB[0] = pub_ts; CUR_MOD[0] = mod_ts; CUR_OG[0] = (art['image'][0], T['t'])
    if lang == 'ru': OG_RU[ev['slug']] = art['image'][0]
    page(lang, key, title, desc, body, og_type='article', ld=[art, cr_ld], scripts='<script src="%s" defer></script>\n' % JS_SUPPORT)
    CUR_PUB[0] = PUBLISHED; CUR_MOD[0] = MODIFIED; CUR_OG[0] = None

# ------------------------------------------------------------------ topic hub pages
TOPIC_UI = {
    'ru': dict(nav_topics='Темы', topics_h1='Темы: подборки сравнений', topics_title='Темы: Киев, Харьков, удары по НПЗ, переговоры — что пишут Россия и Украина',
               topics_desc='Подборки по городам и темам ({n}): удары по НПЗ и энергетике, переговоры, обмены пленными. Как одно событие описывают СМИ России, Украины и независимые.',
               topics_lede='Каждая тема собирает все карточки о городе, регионе или явлении — от новых к старым. В каждой карточке рядом стоят версии российских, украинских и независимых СМИ.',
               places='Города и регионы', themes='Темы', topic_h1='{t}: что пишут Россия и Украина', topic_title='{t}: что пишут Россия и Украина — все события',
               topic_desc='{t}: {n} сравнений, {period}. Версии российских, украинских и независимых СМИ. Последнее: {latest}',
               stats='Карточек: {n} · период: {period} · все три группы источников: {n3}', hubs_label='Темы', other='Другие темы', all_topics='Все темы',
               home_h='Темы'),
    'uk': dict(nav_topics='Теми', topics_h1='Теми: добірки порівнянь', topics_title='Теми: Київ, Харків, удари по НПЗ, переговори — що пишуть Росія та Україна',
               topics_desc='Добірки за містами й темами ({n}): удари по НПЗ та енергетиці, переговори, обміни полоненими. Як одну подію описують медіа Росії, України та незалежні.',
               topics_lede='Кожна тема збирає всі картки про місто, регіон чи явище — від нових до старих. У кожній картці поруч стоять версії російських, українських і незалежних медіа.',
               places='Міста й регіони', themes='Теми', topic_h1='{t}: що пишуть Росія та Україна', topic_title='{t}: що пишуть Росія та Україна — усі події',
               topic_desc='{t}: {n} порівнянь, {period}. Версії російських, українських і незалежних медіа. Останнє: {latest}',
               stats='Карток: {n} · період: {period} · усі три групи джерел: {n3}', hubs_label='Теми', other='Інші теми', all_topics='Усі теми',
               home_h='Теми'),
    'en': dict(nav_topics='Topics', topics_h1='Topics: collections of comparisons', topics_title='Topics: Kyiv, Kharkiv, refinery strikes, peace talks — what Russia and Ukraine say',
               topics_desc='Collections by city and theme ({n}): refinery and energy strikes, peace talks, prisoner exchanges. How Russian, Ukrainian and independent media describe each event.',
               topics_lede='Each topic collects every card about a city, region or theme, newest first. Every card puts the Russian, Ukrainian and independent versions side by side.',
               places='Cities and regions', themes='Themes', topic_h1='{t}: what Russia and Ukraine say', topic_title='{t}: what Russia and Ukraine say — all events',
               topic_desc='{t}: {n} comparisons, {period}. Russian, Ukrainian and independent media versions. Latest: {latest}',
               stats='Cards: {n} · period: {period} · all three source groups: {n3}', hubs_label='Topics', other='Other topics', all_topics='All topics',
               home_h='Topics'),
}

def hub_links(lang, ev):
    hs = EV_HUBS.get(ev['slug']) or []
    if not hs: return ''
    TU = TOPIC_UI[lang]
    return '<p class="hubs"><span class="meta">%s:</span> %s</p>' % (e(TU['hubs_label']), ' · '.join(
        '<a href="%s">%s</a>' % (path(lang, ('topic', t['slug'])), e(topic_name(lang, t))) for t in hs))

def topic_pills(lang, cur=None):
    return '<ul class="pills">%s</ul>' % ''.join('<li><a href="%s">%s <small class="meta">%d</small></a></li>' % (
        path(lang, ('topic', t['slug'])), e(topic_name(lang, t)), len(TOPIC_EVS[t['slug']])) for t in HUBS if t is not cur)

def build_topics(lang):
    U = ui.UI[lang]; TU = TOPIC_UI[lang]; key = ('topics',)
    cr_html, cr_ld = crumbs(lang, [(TU['topics_h1'], key)])
    secs = []
    for kind, lab in (('place', TU['places']), ('theme', TU['themes'])):
        hs = [t for t in HUBS if t['kind'] == kind]
        if not hs: continue
        secs.append('<section aria-labelledby="tk-%s"><h2 id="tk-%s">%s</h2><ul class="evlist">%s</ul></section>' % (kind, kind, e(lab), ''.join(
            '<li class="ev"><h3><a href="%s">%s</a> <small class="meta">(%s)</small></h3><p class="meta">%s</p></li>' % (
                path(lang, ('topic', t['slug'])), e(topic_name(lang, t)), e(U['events_count'].format(n=len(TOPIC_EVS[t['slug']]))),
                e(t['intro'][LANGS.index(lang)])) for t in hs)))
    body = '%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<p class="more"><a href="%s">%s →</a> · <a href="%s">%s →</a></p>\n%s' % (cr_html, e(TU['topics_h1']), e(TU['topics_lede']),
        path(lang, ('divergence',)), e(DIV_UI[lang]['topics_link']), path(lang, ('map',)), e(MAP_UI[lang]['h1']), '\n'.join(secs))
    ld = {'@context': 'https://schema.org', '@type': 'CollectionPage', 'name': TU['topics_h1'], 'url': absu(path(lang, key)), 'inLanguage': lang,
          'mainEntity': {'@type': 'ItemList', 'numberOfItems': len(HUBS), 'itemListElement': [
              {'@type': 'ListItem', 'position': i + 1, 'url': absu(path(lang, ('topic', t['slug']))), 'name': topic_name(lang, t)} for i, t in enumerate(HUBS)]}}
    page(lang, key, TU['topics_title'], trunc(TU['topics_desc'].format(n=len(HUBS)), DESC_LIMIT), body, ld=[cr_ld, ld])

TOPIC_PAGE = int(os.environ.get('TOPIC_PAGE', '24'))   # hub pages: /lang/tema/<slug>/, /lang/tema/<slug>/2/, ...
def build_topic(lang, t):
    U = ui.UI[lang]; TU = TOPIC_UI[lang]; name = topic_name(lang, t)
    all_evs = TOPIC_EVS[t['slug']]
    per = fmt_range(lang, min(x['start'] for x in all_evs), max(x['end'] for x in all_evs))
    n3 = sum(1 for x in all_evs if ncov(x) == 3)
    npg = max(1, -(-len(all_evs) // TOPIC_PAGE))
    for pg in range(1, npg + 1):
        key = ('topic', t['slug']) if pg == 1 else ('topic', t['slug'], pg)
        evs = all_evs[(pg - 1) * TOPIC_PAGE: pg * TOPIC_PAGE]
        pw = (' — ' + PAGE_WORD[lang][0] % pg) if pg > 1 else ''
        items = [(name, ('topic', t['slug']))] + ([(PAGE_WORD[lang][0] % pg, key)] if pg > 1 else [])
        cr_html, cr_ld = crumbs(lang, [(TU['topics_h1'], ('topics',))] + items)
        pager, links = '', []
        if npg > 1:
            pager = '<nav class="pager" aria-label="%s"><ul class="pills">%s</ul></nav>' % (e(PAGE_WORD[lang][1]), ''.join(
                ('<li><a aria-current="page">%d</a></li>' % i) if i == pg else '<li><a href="%s">%d</a></li>' % (path(lang, ('topic', t['slug'], i)), i) for i in range(1, npg + 1)))
            if pg > 1: links.append('<link rel="prev" href="%s">' % e(absu(path(lang, ('topic', t['slug'], pg - 1)))))
            if pg < npg: links.append('<link rel="next" href="%s">' % e(absu(path(lang, ('topic', t['slug'], pg + 1)))))
        intro = ('<p class="lede">%s</p>\n<p class="meta stats">%s · <a href="%s">RSS</a></p>\n' % (e(t['intro'][LANGS.index(lang)]), e(TU['stats'].format(n=len(all_evs), period=per, n3=n3)), rss_path(lang, t['slug']))) if pg == 1 else ''
        body = ('%s\n<h1>%s</h1>\n%s<ul class="evlist">\n%s\n</ul>\n%s\n<h2>%s</h2>%s\n<p class="more"><a href="%s">%s →</a></p>') % (
            cr_html, e(TU['topic_h1'].format(t=name) + pw), intro,
            '\n'.join(event_li(lang, x, False, 'h2') for x in evs), pager, e(TU['other']), topic_pills(lang, t), path(lang, ('topics',)), e(TU['all_topics']))
        desc = TU['topic_desc'].format(t=name, n=len(all_evs), period=per, latest=content(lang, evs[0])['t'])
        if pg > 1: desc = PAGE_WORD[lang][0].capitalize() % pg + '. ' + desc
        title = TU['topic_title'].format(t=name)
        if len(title) + len(pw) > 65: title = TU['topic_h1'].format(t=name)          # keep <title> short (~65 chars)
        page(lang, key, title + pw, trunc(desc, DESC_LIMIT), body, ld=[cr_ld, itemlist_ld(lang, evs, name + pw, key)], extra_head='\n'.join(links))

# ------------------------------------------------------------------ RSS 2.0 feeds (/lang/rss.xml, /lang/<tema|topic>/<slug>/rss.xml)
RSS_N = 50
RSS_UI = {'ru': ('Последние сравнения: что пишут российские, украинские и независимые СМИ', 'ru-RU'),
          'uk': ('Останні порівняння: що пишуть російські, українські та незалежні медіа', 'uk-UA'),
          'en': ('Latest comparisons: what Russian, Ukrainian and independent media say', 'en-US')}
def rss_path(lang, slug=None):
    return ('/%s/%s/%s/rss.xml' % (lang, topics.PREFIX[lang], slug)) if slug else '/%s/rss.xml' % lang
def _rfc822(ev):
    ts = ev['card'].get('first_published_ts') if ev['auto'] else ''
    try:
        d = dt.datetime.fromisoformat(ts) if ts else None
    except ValueError:
        d = None
    if d is None: d = dt.datetime.combine(dt.date.fromisoformat(ev['published']), dt.time(12, 0))
    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    import email.utils
    return email.utils.format_datetime(d), d
def build_rss(lang, slug=None):
    U = ui.UI[lang]
    evs = TOPIC_EVS[slug] if slug else EV
    evs = sorted(evs, key=lambda x: (_rfc822(x)[1], x['start'], str(x['id'])), reverse=True)[:RSS_N]
    if slug:
        t = HUB_BY_SLUG[slug]; title = '%s — %s' % (topic_name(lang, t), U['site_name']); link = absu(path(lang, ('topic', slug))); desc = t['intro'][LANGS.index(lang)]
    else:
        title = U['site_name']; link = absu(path(lang, ('home',))); desc = RSS_UI[lang][0]
    x = lambda v: html.escape(v, quote=False)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', '<channel>',
           '<title>%s</title>' % x(title), '<link>%s</link>' % x(link), '<description>%s</description>' % x(desc), '<language>%s</language>' % RSS_UI[lang][1],
           '<atom:link href="%s" rel="self" type="application/rss+xml"/>' % e(absu(rss_path(lang, slug))), '<ttl>120</ttl>']
    if evs: out.append('<lastBuildDate>%s</lastBuildDate>' % _rfc822(evs[0])[0])
    for ev in evs:
        T = content(lang, ev); u = absu(path(lang, ('event', ev['slug'])))
        cats = [region_name(lang, ev['region'])] + [topic_name(lang, h) for h in EV_HUBS.get(ev['slug'], [])]
        out += ['<item>', '<title>%s</title>' % x(T['t']), '<link>%s</link>' % x(u), '<guid isPermaLink="true">%s</guid>' % x(u),
                '<pubDate>%s</pubDate>' % _rfc822(ev)[0], '<description>%s</description>' % x(EVENT_DESCS[(lang, ev['slug'])])]
        out += ['<category>%s</category>' % x(c) for c in cats]
        out.append('</item>')
    out += ['</channel>', '</rss>']
    write(rss_path(lang, slug), '\n'.join(out) + '\n')

# ------------------------------------------------------------------ events map (src/geo.py curated coordinates; Leaflet loaded only here)
MAP_SLUG = {'ru': 'karta', 'uk': 'karta', 'en': 'map'}
MAP_UI = {
    'ru': dict(nav_map='Карта', h1='Карта событий', title='Карта событий войны: что пишут Россия и Украина о каждом месте',
               desc='Карта событий в Украине, России и соседних странах: {n} карточек в {p} местах. Нажмите на точку — откроется список сравнений версий российских, украинских и независимых СМИ.',
               lede='Каждая точка — место, о котором есть карточки; размер точки зависит от их числа. Координаты приблизительные (центр города или области). Карта не показывает линию фронта и не делает утверждений о контроле над территориями.',
               loading='Карта загружается…', periods=(('7', '7 дней'), ('30', '30 дней'), ('0', 'Всё время')), count='{n} карт.', more='и ещё {n} — см. список ниже', shown='На карте: {n} карточек',
               list_h='Места и карточки', nomap='Не на карте ({n}): события без конкретного места (например, «Украина») или вне карты.', noscript='Для интерактивной карты нужен JavaScript; список мест — ниже.'),
    'uk': dict(nav_map='Мапа', h1='Мапа подій', title='Мапа подій війни: що пишуть Росія та Україна про кожне місце',
               desc='Мапа подій в Україні, Росії та сусідніх країнах: {n} карток у {p} місцях. Натисніть на точку — відкриється список порівнянь версій російських, українських і незалежних медіа.',
               lede='Кожна точка — місце, про яке є картки; розмір точки залежить від їх кількості. Координати приблизні (центр міста чи області). Мапа не показує лінію фронту й не робить тверджень про контроль над територіями.',
               loading='Мапа завантажується…', periods=(('7', '7 днів'), ('30', '30 днів'), ('0', 'Увесь час')), count='{n} карт.', more='і ще {n} — див. список нижче', shown='На мапі: {n} карток',
               list_h='Місця й картки', nomap='Не на мапі ({n}): події без конкретного місця (наприклад, «Україна») або поза мапою.', noscript='Для інтерактивної мапи потрібен JavaScript; список місць — нижче.'),
    'en': dict(nav_map='Map', h1='Map of events', title='Map of war events: what Russia and Ukraine say about each place',
               desc='Map of events in Ukraine, Russia and neighbouring countries: {n} cards in {p} places. Click a point to see comparisons of Russian, Ukrainian and independent media versions.',
               lede='Each point is a place with cards; its size depends on how many. Coordinates are approximate (city or region centre). The map does not show the front line and makes no claims about control of territory.',
               loading='Loading the map…', periods=(('7', '7 days'), ('30', '30 days'), ('0', 'All time')), count='{n} cards', more='and {n} more — see the list below', shown='On the map: {n} cards',
               list_h='Places and cards', nomap='Not on the map ({n}): events without a specific place (e.g. “Ukraine”) or outside the map.', noscript='The interactive map needs JavaScript; the list of places is below.'),
}
def map_points():
    pts = {}
    for ev in LISTING:
        c = ev['card']; loc = geo.locate(c.get('place') or '', c['title'])
        if not loc: continue
        pts.setdefault(loc[2], dict(lat=loc[0], lon=loc[1], evs=[]))['evs'].append(ev)
    return sorted(pts.values(), key=lambda p: -len(p['evs']))
MAP_POINTS = map_points()
def _pt_name(lang, p):
    from collections import Counter
    cnt = Counter(content(lang, x)['p'] for x in p['evs'])
    return sorted(cnt.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]

def build_map(lang):
    U = ui.UI[lang]; M = MAP_UI[lang]; key = ('map',)
    idx, rows = {}, []
    for ev in LISTING:
        idx[ev['slug']] = len(rows); T = content(lang, ev)
        rows.append([ev['start'].isoformat(), T['t'], path(lang, ('event', ev['slug'])), fmt_range(lang, ev['start'], ev['end'])])
    pts = [[round(p['lat'], 3), round(p['lon'], 3), _pt_name(lang, p), [idx[x['slug']] for x in p['evs']]] for p in MAP_POINTS]
    used = sorted({i for p in pts for i in p[3]}); remap = {o: n for n, o in enumerate(used)}
    data = json.dumps(dict(last=max(r[0] for r in rows) if rows else MODIFIED, c=[rows[i] for i in used], p=[[a, b, n, [remap[i] for i in l]] for a, b, n, l in pts]), ensure_ascii=False, separators=(',', ':'))
    rel = '/v/map-%s.%s.json' % (lang, hashlib.md5(data.encode('utf-8')).hexdigest()[:10])
    write(rel, data)
    n_on = len(used)
    cfg = dict(leaflet='/assets/leaflet/leaflet.js', css='/assets/leaflet/leaflet.css', data=rel, count=M['count'], more=M['more'], shown=M['shown'])
    cr_html, cr_ld = crumbs(lang, [(M['h1'], key)])
    btns = ''.join('<button type="button" data-days="%s" aria-pressed="%s" hidden>%s</button>' % (d, 'true' if d == '0' else 'false', e(lab)) for d, lab in M['periods'])
    lst = ''.join('<li><strong>%s</strong> <small class="meta">(%d)</small>: %s</li>' % (e(_pt_name(lang, p)), len(p['evs']), ' · '.join(
        '<a href="%s">%s</a>' % (path(lang, ('event', x['slug'])), e(content(lang, x)['t'])) for x in p['evs'][:3])) for p in MAP_POINTS)
    body = ('%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<div class="mapf">%s <span class="meta" id="map-n">%s</span></div>\n'
            '<div id="map" data-cfg="%s" role="region" aria-label="%s">%s</div>\n<noscript><p class="meta">%s</p></noscript>\n'
            '<section aria-labelledby="ml"><h2 id="ml">%s</h2><ul class="maplist">%s</ul><p class="meta">%s</p></section>') % (
        cr_html, e(M['h1']), e(M['lede']), btns, e(M['shown'].format(n=n_on)), e(json.dumps(cfg, ensure_ascii=False)), e(M['h1']), e(M['loading']), e(M['noscript']),
        e(M['list_h']), lst, e(M['nomap'].format(n=len(EV) - n_on)))
    page(lang, key, M['title'], trunc(M['desc'].format(n=n_on, p=len(MAP_POINTS)), DESC_LIMIT), body, ld=[cr_ld],
         extra_head='<link rel="preconnect" href="https://server.arcgisonline.com" crossorigin>', scripts='<script src="%s" defer></script>\n' % JS_MAP)

DIV_SLUG = {'ru': 'rashozhdeniya', 'uk': 'rozbizhnosti', 'en': 'divergence'}
DIV_UI = {
    'ru': dict(nav='Расхождения', label='Версии', agree='совпадают', details='расходятся в деталях', contradict='противоречат друг другу',
               title='Где версии России и Украины расходятся: статистика по темам', h1='Насколько расходятся версии',
               desc='Статистика расхождений: в {n} карточках версии совпадают, расходятся в деталях или противоречат друг другу. Темы, где противоречий больше всего.',
               lede='Для каждой карточки, где есть версии хотя бы двух групп источников, отмечено, насколько они расходятся.',
               method='Как считается: метку ставит языковая модель вместе с текстом карточки (для старых карточек — отдельным проходом по готовому тексту), только по тем материалам, что собраны в карточке. «Совпадают» — группы сообщают одни и те же основные факты; «расходятся в деталях» — событие одно, но различаются цифры, детали, описание цели, атрибуция или умолчания; «противоречат друг другу» — несовместимые версии основного факта (кто, по кому, что произошло, противоположные заявления, явно разные цифры одного и того же). Метка не говорит, кто прав. Карточки с версией только одной группы не размечаются.',
               levels_h='По уровню', topics_h='По темам: где больше всего противоречий', th=('Тема', 'Размечено', 'Совпадают', 'В деталях', 'Противоречат', 'Доля противоречий'),
               latest_h='Последние карточки, где версии противоречат', unl='Без метки: {n} (версия только одной группы или метка не поставлена).', topics_link='Где версии расходятся сильнее всего',
               upd_h='Обновления', upd_confirmed='подтвердилась версия {s}', upd_refuted='опровергнута версия {s}', upd_mark='обновлено',
               side=dict(ru='российской стороны', ua='украинской стороны', ind='независимых СМИ'), upd_src='источник'),
    'uk': dict(nav='Розбіжності', label='Версії', agree='збігаються', details='розходяться в деталях', contradict='суперечать одна одній',
               title='Де версії Росії та України розходяться: статистика за темами', h1='Наскільки розходяться версії',
               desc='Статистика розбіжностей: у {n} картках версії збігаються, розходяться в деталях або суперечать одна одній. Теми, де суперечностей найбільше.',
               lede='Для кожної картки, де є версії щонайменше двох груп джерел, позначено, наскільки вони розходяться.',
               method='Як рахується: позначку ставить мовна модель разом із текстом картки (для старих карток — окремим проходом за готовим текстом), лише за матеріалами, зібраними в картці. «Збігаються» — групи повідомляють ті самі основні факти; «розходяться в деталях» — подія одна, але різняться цифри, деталі, опис цілі, атрибуція чи замовчування; «суперечать одна одній» — несумісні версії основного факту (хто, по кому, що сталося, протилежні заяви, явно різні цифри одного й того самого). Позначка не каже, хто має рацію. Картки з версією лише однієї групи не позначаються.',
               levels_h='За рівнем', topics_h='За темами: де найбільше суперечностей', th=('Тема', 'Позначено', 'Збігаються', 'У деталях', 'Суперечать', 'Частка суперечностей'),
               latest_h='Останні картки, де версії суперечать', unl='Без позначки: {n} (версія лише однієї групи або позначку не поставлено).', topics_link='Де версії розходяться найбільше',
               upd_h='Оновлення', upd_confirmed='підтвердилася версія {s}', upd_refuted='спростовано версію {s}', upd_mark='оновлено',
               side=dict(ru='російської сторони', ua='української сторони', ind='незалежних медіа'), upd_src='джерело'),
    'en': dict(nav='Divergence', label='Versions', agree='agree', details='differ in details', contradict='contradict each other',
               title='Where Russian and Ukrainian versions diverge: statistics by topic', h1='How far the versions diverge',
               desc='Divergence statistics: in {n} cards the versions agree, differ in details or contradict each other. The topics with the most contradictions.',
               lede='Every card with versions from at least two source groups is labelled with how far they diverge.',
               method='How it works: a language model sets the label together with the card text (for older cards, in a separate pass over the finished text), using only the reports collected in the card. “Agree” — the groups report the same core facts; “differ in details” — the same event, but figures, details, the description of the target, attribution or omissions differ; “contradict each other” — incompatible accounts of a core fact (who struck whom or what, opposite statements, clearly different figures for the same thing). The label does not say who is right. Cards with only one group’s version are not labelled.',
               levels_h='By level', topics_h='By topic: where contradictions are most frequent', th=('Topic', 'Labelled', 'Agree', 'Details', 'Contradict', 'Share contradicting'),
               latest_h='Latest cards where the versions contradict', unl='Not labelled: {n} (only one group’s version, or no label set).', topics_link='Where the versions diverge most',
               upd_h='Updates', upd_confirmed='the version of {s} was confirmed', upd_refuted='the version of {s} was refuted', upd_mark='updated',
               side=dict(ru='the Russian side', ua='the Ukrainian side', ind='independent media'), upd_src='source'),
}
DIV_DOTS = {'agree': 1, 'details': 2, 'contradict': 3}
def div_badge(lang, ev, link=False):
    lv = div_level(ev)
    if not lv: return ''
    D = DIV_UI[lang]; n = DIV_DOTS[lv]
    dots = '<span class="dm" aria-hidden="true">%s</span>' % ''.join('<i class="%s"></i>' % ('on' if i < n else '') for i in range(3))
    inner = '%s<span>%s: %s</span>' % (dots, e(D['label']), e(D[lv]))
    if link: return '<a class="div d-%s" href="%s">%s</a>' % (lv, path(lang, ('divergence',)), inner)
    return '<span class="div d-%s">%s</span>' % (lv, inner)

def _upd_text(lang, u):
    D = DIV_UI[lang]
    return (D['upd_confirmed'] if u['verdict'] == 'confirmed' else D['upd_refuted']).format(s=D['side'][u['side']])
def upd_marker(lang, ev):
    ups = ev['card'].get('updates') if ev['auto'] else None
    if not ups: return ''
    u = ups[-1]; d = dt.date.fromisoformat(u['date'])
    return '<span class="updm" title="%s">↻ %s %02d.%02d</span>' % (e(_upd_text(lang, u)), e(DIV_UI[lang]['upd_mark']), d.day, d.month)
def upd_block(lang, ev):
    ups = ev['card'].get('updates') if ev['auto'] else None
    if not ups: return ''
    D = DIV_UI[lang]; li = []
    for u in reversed(ups):
        d = dt.date.fromisoformat(u['date']); a = A.get(u.get('id'))
        src = (' <a href="%s" target="_blank" rel="noopener">%s: %s</a>' % (e(a['url']), e(D['upd_src']), e(ui.SRC_NAME[lang].get(a['source'], a['source'])))) if a else ''
        li.append('<li><strong>%s %02d.%02d:</strong> %s — %s%s</li>' % (e(D['upd_mark'].capitalize()), d.day, d.month, e(_upd_text(lang, u)), e(u['text'].get(lang) or u['text']['ru']), src))
    return '<section class="upd" aria-labelledby="h-upd"><h2 id="h-upd">%s</h2><ul>%s</ul></section>\n' % (e(D['upd_h']), ''.join(li))

def build_divergence(lang):
    U = ui.UI[lang]; D = DIV_UI[lang]; key = ('divergence',)
    lab = [(x, div_level(x)) for x in LISTING]
    done = [(x, l) for x, l in lab if l]
    tot = len(done) or 1
    cnt = {l: sum(1 for _, v in done if v == l) for l in DIV_LEVELS}
    lv = ''.join('<li><span class="div d-%s"><span class="dm" aria-hidden="true">%s</span><span>%s</span></span> <strong>%d</strong> <span class="meta">(%d%%)</span><span class="bar"><i style="width:%d%%"></i></span></li>' % (
        l, ''.join('<i class="%s"></i>' % ('on' if i < DIV_DOTS[l] else '') for i in range(3)), e(D[l]), cnt[l], round(100 * cnt[l] / tot), round(100 * cnt[l] / tot)) for l in DIV_LEVELS)
    rows = []
    for t in HUBS:
        ls = [div_level(x) for x in TOPIC_EVS[t['slug']]]; ls = [l for l in ls if l]
        if len(ls) < 3: continue
        c = {l: ls.count(l) for l in DIV_LEVELS}
        rows.append((c['contradict'] / len(ls), len(ls), t, c))
    rows.sort(key=lambda r: (-r[0], -r[1], r[2]['slug']))
    th = ''.join('<th scope="col">%s</th>' % e(h) for h in D['th'])
    trs = ''.join('<tr><th scope="row"><a href="%s">%s</a></th><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d%%</td></tr>' % (
        path(lang, ('topic', t['slug'])), e(topic_name(lang, t)), n, c['agree'], c['details'], c['contradict'], round(100 * sh)) for sh, n, t, c in rows)
    latest = [x for x, l in done if l == 'contradict'][:12]
    cr_html, cr_ld = crumbs(lang, [(D['h1'], key)])
    body = ('%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<section aria-labelledby="dl"><h2 id="dl">%s</h2><ul class="divlv">%s</ul><p class="meta">%s</p></section>\n'
            '<section aria-labelledby="dt"><h2 id="dt">%s</h2><div class="tw"><table class="dtab"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div></section>\n'
            '<section aria-labelledby="dx"><h2 id="dx">%s</h2><ul class="evlist">%s</ul></section>\n<section class="box" aria-labelledby="dm"><h2 id="dm">%s</h2><p>%s</p></section>') % (
        cr_html, e(D['h1']), e(D['lede']), e(D['levels_h']), lv, e(D['unl'].format(n=len(LISTING) - len(done))), e(D['topics_h']), th, trs,
        e(D['latest_h']), '\n'.join(event_li(lang, x, False, 'h3') for x in latest), e(U['about_h1'] if False else {'ru': 'Методика', 'uk': 'Методика', 'en': 'Method'}[lang]), e(D['method']))
    page(lang, key, D['title'], trunc(D['desc'].format(n=len(done)), DESC_LIMIT), body, ld=[cr_ld])

def build_regions(lang):
    U = ui.UI[lang]; key = ('regions',)
    cr_html, cr_ld = crumbs(lang, [(U['regions_h1'], key)])
    secs = []
    for k in REGION_KEYS:
        evs = [x for x in LISTING if x['region'] == k]
        secs.append('<section aria-labelledby="r-%s"><h2 id="r-%s"><a href="%s">%s</a> <small class="meta">(%s)</small></h2><ul class="evlist">%s</ul></section>' % (
            k, k, path(lang, ('region', k)), e(region_name(lang, k)), e(U['events_count'].format(n=len(evs))),
            ''.join('<li class="ev"><h3><a href="%s">%s</a></h3><p class="meta"><time datetime="%s">%s</time> · %s</p></li>' % (
                path(lang, ('event', x['slug'])), e(content(lang, x)['t']), x['start'].isoformat(), e(fmt_range(lang, x['start'], x['end'])), e(content(lang, x)['p'])) for x in evs)))
    body = '%s\n<h1>%s</h1>\n%s' % (cr_html, e(U['regions_h1']), '\n'.join(secs))
    page(lang, key, U['regions_title'] + ' — ' + U['site_name'], U['regions_desc'].format(n=len(EV)), body, ld=[cr_ld])

RELATED_N = 6             # event pages also link the 6 other region events nearest in time (+ a link to the region page), not the whole region
SIM_N, SIM_MIN = 6, 3
SIM_H = {'ru': 'Похожие события', 'uk': 'Схожі події', 'en': 'Related events'}
_STOP = {w[:5] for w in 'и в во на по о об от до из за для при с со к у не что как это его её их так также после перед через под над между обстрел обстрелы удар удары ударов атака атаки область области района район город россии украины украина россия заявил заявили сообщил сообщили данные против более около новые новый'.split()}
def _words(s): return {w[:5] for w in re.findall(r'[a-zа-яёіїєґ0-9]{4,}', (s or '').lower()) if w[:5] not in _STOP and not w.isdigit()}
def similar_events(ev, n=SIM_N):
    """'Related events' on a card: same topic hub / place / headline words / region, near in time. Only live (published, not merged)
    cards are in LISTING, so merged/redirected cards never appear. Deterministic: score = topical overlap + time proximity."""
    hubs = {t['slug'] for t in EV_HUBS.get(ev['slug']) or []}
    place = (ev['card'].get('place') or '').lower(); pw = _words(place); tw = _words(ev['card']['title'])
    scored = []
    for x in LISTING:
        if x is ev: continue
        d = abs((x['start'] - ev['start']).days)
        if d > 45: continue
        xc = x['card']; xp = (xc.get('place') or '').lower()
        top = 3 * len(hubs & {t['slug'] for t in EV_HUBS.get(x['slug']) or []})
        if place and place != '—' and xp == place: top += 3
        elif pw & _words(xp): top += 1.5
        top += 0.75 * min(3, len(tw & _words(xc['title'])))
        if x['region'] == ev['region'] and ev['region'] != 'ukraine-wide': top += 1
        if top < 1.5: continue
        scored.append((top + 2.0 / (1 + d / 2), top, x['start'], str(x['id']), x))
    scored.sort(key=lambda r: (-r[0], -r[2].toordinal(), r[3]))
    out = [r[4] for r in scored if r[1] >= 2][:n]
    if len(out) < SIM_MIN:                       # weaker matches (one shared headline word + same place word) only to reach the minimum
        out += [r[4] for r in scored if r[1] < 2][:SIM_MIN - len(out)]
    return out if len(out) >= SIM_MIN else []
REGION_PAGE = 30          # region pages are paginated: /lang/region/k/, /lang/region/k/page/2/, ...
REL_ALL = {'ru': 'Все события региона «{r}» ({n})', 'uk': 'Усі події регіону «{r}» ({n})', 'en': 'All events in “{r}” ({n})'}
PAGE_WORD = {'ru': ('страница %d', 'Страницы'), 'uk': ('сторінка %d', 'Сторінки'), 'en': ('page %d', 'Pages')}
def build_region(lang, k):
    U = ui.UI[lang]; rname = region_name(lang, k)
    all_evs = [x for x in LISTING if x['region'] == k]
    npg = max(1, -(-len(all_evs) // REGION_PAGE))
    others = ''.join('<li><a href="%s">%s</a></li>' % (path(lang, ('region', o)), e(region_name(lang, o))) for o in REGION_KEYS if o != k)
    for pg in range(1, npg + 1):
        key = ('region', k) if pg == 1 else ('region', k, pg)
        evs = all_evs[(pg - 1) * REGION_PAGE: pg * REGION_PAGE]
        pw = (' — ' + PAGE_WORD[lang][0] % pg) if pg > 1 else ''
        items = [(rname, ('region', k))] + ([(PAGE_WORD[lang][0] % pg, key)] if pg > 1 else [])
        cr_html, cr_ld = crumbs(lang, [(U['regions_h1'], ('regions',))] + items)
        pager = ''
        if npg > 1:
            pager = '<nav class="pager" aria-label="%s"><ul class="pills">%s</ul></nav>' % (e(PAGE_WORD[lang][1]), ''.join(
                ('<li><a aria-current="page">%d</a></li>' % i) if i == pg else '<li><a href="%s">%d</a></li>' % (path(lang, ('region', k, i)), i) for i in range(1, npg + 1)))
        body = '%s\n<h1>%s</h1>\n<ul class="evlist">\n%s\n</ul>\n%s\n<h2>%s</h2><ul class="pills">%s</ul>' % (
            cr_html, e(U['region_h1'].format(r=rname) + pw), '\n'.join(event_li(lang, x, False, 'h2') for x in evs), pager, e(U['other_regions']), others)
        titles = '; '.join(content(lang, x)['t'] for x in evs)
        desc = U['region_desc'].format(r=rname, n=len(all_evs), titles=titles)
        if pg > 1: desc = PAGE_WORD[lang][0].capitalize() % pg + '. ' + desc
        page(lang, key, U['region_title'].format(r=rname) + pw + ' — ' + U['site_name'], trunc(desc, 158), body,
             ld=[cr_ld, itemlist_ld(lang, evs, rname + pw, key)])

def build_dates(lang):
    U = ui.UI[lang]; key = ('dates',)
    cr_html, cr_ld = crumbs(lang, [(U['dates_h1'], key)])
    secs = []
    for d in reversed(DAYS):
        evs = [x for x in LISTING if x['start'] == d]
        secs.append('<section aria-labelledby="d-%s"><h2 id="d-%s"><a href="%s"><time datetime="%s">%s</time></a> <small class="meta">(%s)</small></h2><ul class="evlist">%s</ul></section>' % (
            d.isoformat(), d.isoformat(), path(lang, ('date', d.isoformat())), d.isoformat(), e(fmt_day(lang, d)), e(U['events_count'].format(n=len(evs))),
            ''.join('<li class="ev"><h3><a href="%s">%s</a></h3><p class="meta">%s · %s</p></li>' % (
                path(lang, ('event', x['slug'])), e(content(lang, x)['t']), e(fmt_range(lang, x['start'], x['end'])), e(content(lang, x)['p'])) for x in evs)))
    body = '%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n%s' % (cr_html, e(U['dates_h1']), e(U['start_note']), '\n'.join(secs))
    page(lang, key, U['dates_title'] + ' — ' + U['site_name'], U['dates_desc'].format(period=period_text(lang)), body, ld=[cr_ld])

def build_date(lang, d):
    U = ui.UI[lang]; key = ('date', d.isoformat()); dn = fmt_day(lang, d)
    evs = [x for x in LISTING if x['start'] == d]
    cr_html, cr_ld = crumbs(lang, [(U['dates_h1'], ('dates',)), (dn, key)])
    others = ''.join('<li><a href="%s">%s</a></li>' % (path(lang, ('date', o.isoformat())), e(fmt_day(lang, o))) for o in DAYS if o != d)
    body = '%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<ul class="evlist">\n%s\n</ul>\n<h2>%s</h2><ul class="pills">%s</ul>' % (
        cr_html, e(U['date_h1'].format(d=dn)), e(U['start_note']), '\n'.join(event_li(lang, x, False, 'h2') for x in evs), e(U['other_dates']), others)
    titles = '; '.join(content(lang, x)['t'] for x in evs)
    page(lang, key, U['date_title'].format(d=dn) + ' — ' + U['site_name'], trunc(U['date_desc'].format(d=dn, n=len(evs), titles=titles), 158), body, ld=[cr_ld, itemlist_ld(lang, evs, dn, key)])

def build_about(lang):
    U = ui.UI[lang]; Ab = about.ABOUT[lang]; key = ('about',); li = LANGS.index(lang)
    cr_html, cr_ld = crumbs(lang, [(U['about_h1'], key)])
    cnt = {}
    for src, n in db.execute('select source,count(*) from articles group by source'): cnt[src] = n
    def count_for(prefixes): return sum(n for src, n in cnt.items() if any(src.startswith(p) for p in prefixes))
    def labels_html():
        rows = []
        rule = {'confirmed': {'ru': 'Нет проверочных замечаний и противоречий; событие описывают ≥2 международных/эмигрантских независимых источника, либо ≥1 такой источник и ещё одна сторона (Россия или Украина).',
                              'uk': 'Немає зауважень перевірок і суперечностей; подію описують ≥2 міжнародні/емігрантські незалежні джерела, або ≥1 таке джерело і ще одна сторона (Росія чи Україна).',
                              'en': 'No check warnings or contradictions; the event is described by ≥2 international/exile independent sources, or ≥1 such source plus another side (Russia or Ukraine).'},
                'contradict': {'ru': 'Модель по тексту отметила несовместимые утверждения, а событие описывают минимум две «стороны». Различие цифр само по себе метки не даёт.',
                               'uk': 'Модель за текстом позначила несумісні твердження, а подію описують щонайменше дві «сторони». Відмінність цифр сама по собі позначки не дає.',
                               'en': 'The model, reading the texts, flagged incompatible statements, and at least two “sides” describe the event. A difference in figures alone does not set the label.'},
                'both_sides': {'ru': 'Есть и российские, и украинские материалы, нет международных независимых, нет выявленных противоречий.',
                               'uk': 'Є і російські, і українські матеріали, немає міжнародних незалежних, немає виявлених суперечностей.',
                               'en': 'Both Russian and Ukrainian items exist, no international independent ones, no detected contradiction.'},
                'one_side': {'ru': 'Материалы только одной «стороны» (российской или украинской) без международного независимого источника.',
                             'uk': 'Матеріали лише однієї «сторони» (російської або української) без міжнародного незалежного джерела.',
                             'en': 'Items from one “side” only (Russian or Ukrainian), with no international independent source.'},
                'insufficient': {'ru': 'Любое из: менее двух источников, фильтр релевантности подтвердил менее 60% материалов, модель сообщила о высокой неуверенности, часть цитат не прошла проверку, есть не найденное в источниках число, материалы состоят только из коротких заголовков. Проверяется первым.',
                                 'uk': 'Будь-що з: менше двох джерел, фільтр релевантності підтвердив менше 60% матеріалів, модель повідомила про високу невпевненість, частина цитат не пройшла перевірку, є число, не знайдене в джерелах, матеріали складаються лише з коротких заголовків. Перевіряється першим.',
                                 'en': 'Any of: fewer than two sources, the relevance filter confirmed under 60% of the articles, the model reported high uncertainty, some quotes failed the check, a number not found in the sources, or only short headlines available. Checked first.'}}
        hd = {'ru': ('Метка', 'Что означает', 'Когда выставляется'), 'uk': ('Мітка', 'Що означає', 'Коли виставляється'), 'en': ('Label', 'Meaning', 'When assigned')}[lang]
        for k in ('confirmed', 'contradict', 'both_sides', 'one_side', 'insufficient'):
            rows.append('<tr><td><span class="conf c-%s"><span class="ic" aria-hidden="true">%s</span><span class="tx">%s</span></span></td><td>%s</td><td>%s</td></tr>' % (
                k, e(conf_mod.LABELS[k]['icon']), e(conf_mod.LABELS[k][lang]), e(conf_mod.EXPLAIN[k][lang]), e(rule[k][lang])))
        return '<div class="tw"><table><tr>%s</tr>%s</table></div>' % (''.join('<th>%s</th>' % e(x) for x in hd), ''.join(rows))
    def sources_html():
        rows = []
        for g, pref, names, notes, access, st in about.SOURCES_TABLE:
            rows.append('<tr><td>%s</td><td>%s</td><td>%s</td><td class="%s">%s</td><td>%d</td></tr>' % (e(U['grp'][g]), e(names[li]), e(notes[li]), st, e(U['status'][st]), count_for(pref)))
        th = {'ru': ('Группа', 'Источник', 'Принадлежность и способ доступа', 'Статус', 'Материалов'), 'uk': ('Група', 'Джерело', 'Належність і спосіб доступу', 'Статус', 'Матеріалів'),
              'en': ('Group', 'Source', 'Affiliation and access', 'Status', 'Articles')}[lang]
        return '<div class="tw"><table><tr>%s</tr>%s</table></div>' % (''.join('<th>%s</th>' % e(x) for x in th), ''.join(rows))
    def blocked_html():
        return '<ul>%s</ul>' % ''.join('<li><b>%s</b> — %s</li>' % (e(n[li]), e(w[li])) for n, w in about.BLOCKED)
    def contact_html():
        txt = {'ru': 'Сообщить об ошибке или предложить источник:', 'uk': 'Повідомити про помилку або запропонувати джерело:',
               'en': 'To report an error or suggest a source:'}[lang]
        lk = {'ru': 'написать по электронной почте', 'uk': 'написати електронною поштою', 'en': 'write by e-mail'}[lang]
        return '<p>%s <a href="%s" rel="nofollow">%s</a> (<code>%s</code>). <a href="%s">%s</a>.</p><p>%s %s</p>' % (e(txt), e(mailto(lang, key)), e(lk), e(CONTACT), path(lang, ('corrections',)), e(U['corr_title']), e(TG_UI[lang]['about']), tg_link())
    toc, body_secs = [], []
    for sid, h, blocks in Ab['sections']:
        toc.append('<li><a href="#%s">%s</a></li>' % (sid, e(h)))
        out = ['<section id="%s" aria-labelledby="h-%s"><h2 id="h-%s">%s</h2>' % (sid, sid, sid, e(h))]
        for b in blocks:
            if b[0] == 'p': out.append('<p>%s</p>' % e(b[1]))
            elif b[0] == 'ul': out.append('<ul>%s</ul>' % ''.join('<li>%s</li>' % e(x) for x in b[1]))
            elif b[0] == 'labels': out.append(labels_html())
            elif b[0] == 'sources': out.append(sources_html())
            elif b[0] == 'blocked': out.append(blocked_html())
            elif b[0] == 'contact': out.append(contact_html())
        out.append('</section>'); body_secs.append('\n'.join(out))
    body = '%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<nav class="toc" aria-label="%s"><ol>%s</ol></nav>\n%s' % (cr_html, e(U['about_h1']), e(Ab['intro']), e(Ab['toc']), ''.join(toc), '\n'.join(body_secs))
    page(lang, key, U['about_title'] + ' — ' + U['site_name'], U['about_desc'], body, ld=[cr_ld, {'@context': 'https://schema.org', '@type': 'AboutPage', 'name': U['about_h1'], 'url': absu(path(lang, key)), 'inLanguage': lang}])

def build_corrections(lang):
    U = ui.UI[lang]; key = ('corrections',); li = LANGS.index(lang)
    cr_html, cr_ld = crumbs(lang, [(U['corr_h1'], key)])
    try: data = json.load(open(os.path.join(HERE, 'data', 'corrections.json'), encoding='utf-8')).get('entries', [])
    except (OSError, ValueError): data = []
    slug_by_id = {str(x['id']): x for x in EV}
    rows = []
    for en in sorted(data, key=lambda x: x.get('date', ''), reverse=True):
        ev = slug_by_id.get(str(en.get('event_id')))
        title = (content(lang, ev)['t'] if ev else (en.get('title_ru') or '—'))
        card = ('<a href="%s">%s</a>' % (path(lang, ('event', ev['slug'])), e(title))) if ev else e(title)
        reasons = '; '.join(ui.REASON_TEXT.get(r, (r, r, r))[li] for r in en.get('reasons', [])) or e(en.get('note_' + lang, '') or '—')
        what = ui.KIND_TEXT.get(en.get('kind'), (en.get('kind', ''),) * 3)[li]
        rows.append('<tr><td><time datetime="%s">%s</time></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            e(en.get('date', '')), e(fmt_day(lang, dt.date.fromisoformat(en['date']))), card, e(what), reasons if en.get('reasons') else e(en.get('note_' + lang, '—')), e(U['corr_auto'] if en.get('auto') else U['corr_manual'])))
    table = ('<div class="tw"><table><tr>%s</tr>%s</table></div>' % (''.join('<th>%s</th>' % e(x) for x in U['corr_th']), ''.join(rows))) if rows else '<p class="none">%s</p>' % e(U['corr_empty'])
    meta_ = AUTO_META or {}
    wh = meta_.get('withheld_by_reason', {})
    pub, nwh = meta_.get('published', 0), meta_.get('withheld', 0)
    stats = ''
    if meta_:
        stats = '<h2>%s</h2><p>%s</p><p>%s</p><ul>%s</ul>' % (e(U['corr_stats_h']), e(U['corr_stats_p']), e(U['corr_stats_line'].format(n=pub + nwh, pub=pub, wh=nwh)),
            ''.join('<li>%s — %d</li>' % (e(ui.REASON_TEXT.get(r, (r, r, r))[li]), n) for r, n in sorted(wh.items(), key=lambda kv: -kv[1])))
    body = '%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n%s\n%s\n<p><a href="%s" rel="nofollow">%s</a></p>' % (cr_html, e(U['corr_h1']), e(U['corr_intro']), table, stats, e(mailto(lang, key)), e(U['corr_report']))
    page(lang, key, U['corr_title'] + ' — ' + U['site_name'], U['corr_desc'], body, ld=[cr_ld])

# ------------------------------------------------------------------ "Help the site" (non-monetary): share, RSS, corrections
# Counting: Cloudflare Web Analytics (auto-injected beacon) counts page views of /ru/pomoch/, /uk/dopomohty/, /en/support/;
# links carry ?from=footer|card (no tracking script of our own, no cookies).
SUPPORT_SLUG = {'ru': 'pomoch', 'uk': 'dopomohty', 'en': 'support'}
TG_URL = 'https://t.me/twoversions_war'
TG_UI = {'ru': dict(h='Telegram-канал', p='Важные новые сравнения и вечерние итоги дня — в Telegram-канале «Две версии»:', about='Новые сравнения публикуются также в Telegram-канале:'),
         'uk': dict(h='Telegram-канал', p='Важливі нові порівняння та вечірні підсумки дня — у Telegram-каналі «Дві версії» (російською):', about='Нові порівняння публікуються також у Telegram-каналі (російською):'),
         'en': dict(h='Telegram channel', p='Important new comparisons and an evening round-up of the day are posted to the Telegram channel (in Russian):', about='New comparisons are also posted to the Telegram channel (in Russian):')}
def tg_link(): return '<a href="%s" rel="noopener">t.me/twoversions_war</a>' % TG_URL

SUPPORT_UI = {
    'ru': dict(btn='Помочь сайту', h1='Помочь сайту', title='Как помочь сайту — без денег', desc='Как помочь проекту: поделиться ссылкой на сайт или карточку, подписаться через RSS, прислать исправление или источник. Пожертвования не принимаются.',
               lede='Проект не принимает пожертвований и не показывает рекламу. Помочь можно иначе — несколькими простыми способами.',
               share_h='Поделиться', share_p='Отправьте ссылку на сайт или на конкретную карточку тем, кому это может быть полезно.', copy='Скопировать ссылку', copied='Скопировано',
               rss_h='Подписаться через RSS', rss_p='Новые карточки появляются в RSS-лентах — их можно читать в любом RSS-ридере.', rss_all='Все карточки', rss_topics='Ленты по темам — на страницах тем',
               corr_h='Прислать исправление или источник', corr_p='Заметили ошибку, неточную цитату или знаете источник, которого нет на сайте? Напишите — исправления публикуются в журнале.', corr_log='Журнал исправлений',
               tell_h='Рассказать другим', tell_p='Расскажите о сайте в чатах, соцсетях или лично. Чем больше людей сравнивают версии, тем сложнее подменить факты.',
               card_h='Помочь сайту', card_p='Поделитесь этой карточкой:', card_more='Другие способы помочь', share_text='Как это событие описывают СМИ России, Украины и независимые'),
    'uk': dict(btn='Допомогти сайту', h1='Допомогти сайту', title='Як допомогти сайту — без грошей', desc='Як допомогти проєкту: поділитися посиланням на сайт або картку, підписатися через RSS, надіслати виправлення чи джерело. Пожертви не приймаються.',
               lede='Проєкт не приймає пожертв і не показує реклами. Допомогти можна інакше — кількома простими способами.',
               share_h='Поділитися', share_p='Надішліть посилання на сайт або на конкретну картку тим, кому це може бути корисно.', copy='Скопіювати посилання', copied='Скопійовано',
               rss_h='Підписатися через RSS', rss_p='Нові картки з’являються в RSS-стрічках — їх можна читати в будь-якому RSS-рідері.', rss_all='Усі картки', rss_topics='Стрічки за темами — на сторінках тем',
               corr_h='Надіслати виправлення або джерело', corr_p='Помітили помилку, неточну цитату чи знаєте джерело, якого немає на сайті? Напишіть — виправлення публікуються в журналі.', corr_log='Журнал виправлень',
               tell_h='Розповісти іншим', tell_p='Розкажіть про сайт у чатах, соцмережах або особисто. Що більше людей порівнюють версії, то складніше підмінити факти.',
               card_h='Допомогти сайту', card_p='Поділіться цією карткою:', card_more='Інші способи допомогти', share_text='Як цю подію описують медіа Росії, України та незалежні'),
    'en': dict(btn='Support the site', h1='Support the site', title='How to support the site — no money involved', desc='How to help the project: share a link to the site or a card, subscribe via RSS, send a correction or a source. No donations are accepted.',
               lede='The project accepts no donations and shows no ads. You can help in a few simple ways instead.',
               share_h='Share', share_p='Send a link to the site or to a specific card to people who may find it useful.', copy='Copy link', copied='Copied',
               rss_h='Subscribe via RSS', rss_p='New cards appear in the RSS feeds, which work in any RSS reader.', rss_all='All cards', rss_topics='Per-topic feeds are on the topic pages',
               corr_h='Send a correction or a source', corr_p='Spotted a mistake, an inaccurate quote, or know a source the site is missing? Write to us — corrections are published in the log.', corr_log='Corrections log',
               tell_h='Tell others', tell_p='Mention the site in chats, on social media or in person. The more people compare the versions, the harder it is to swap the facts.',
               card_h='Support the site', card_p='Share this card:', card_more='Other ways to help', share_text='How Russian, Ukrainian and independent media describe this event'),
}
def share_links(lang, url, text):
    q = urllib.parse.quote
    items = (('Telegram', 'https://t.me/share/url?url=%s&text=%s' % (q(url, safe=''), q(text, safe=''))),
             ('X', 'https://x.com/intent/post?url=%s&text=%s' % (q(url, safe=''), q(text, safe=''))),
             ('Facebook', 'https://www.facebook.com/sharer/sharer.php?u=%s' % q(url, safe='')),
             ('WhatsApp', 'https://wa.me/?text=%s' % q(text + ' ' + url, safe='')))
    S = SUPPORT_UI[lang]
    return ('<p class="share">%s<button type="button" class="sh" data-copy="%s" data-done="%s" hidden>%s</button></p>' % (
        ''.join('<a class="sh" href="%s" target="_blank" rel="noopener nofollow">%s</a>' % (e(u), n) for n, u in items), e(url), e(S['copied']), e(S['copy'])))
def support_card_block(lang, ev, title):
    S = SUPPORT_UI[lang]
    return ('<aside class="sup" aria-labelledby="h-sup"><h2 id="h-sup">%s</h2><p>%s</p>%s<p><a class="sup-btn" href="%s?from=card">%s</a></p></aside>\n') % (
        e(S['card_h']), e(S['card_p']), share_links(lang, absu(path(lang, ('event', ev['slug']))), title), path(lang, ('support',)), e(S['card_more']))
def build_support(lang):
    U = ui.UI[lang]; S = SUPPORT_UI[lang]; key = ('support',)
    cr_html, cr_ld = crumbs(lang, [(S['h1'], key)])
    home = absu(path(lang, ('home',)))
    body = ('%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n'
            '<section><h2>%s</h2><p>%s</p>%s</section>\n'
            '<section><h2>%s</h2><p>%s %s</p></section>\n'
            '<section><h2>%s</h2><p>%s</p><ul><li><a href="%s">%s</a> — <code>%s</code></li><li><a href="%s">%s</a></li></ul></section>\n'
            '<section><h2>%s</h2><p>%s</p><p><a href="%s" rel="nofollow">%s</a> (<code>%s</code>) · <a href="%s">%s</a></p></section>\n'
            '<section><h2>%s</h2><p>%s</p></section>') % (
        cr_html, e(S['h1']), e(S['lede']),
        e(S['share_h']), e(S['share_p']), share_links(lang, home, U['site_name']),
        e(TG_UI[lang]['h']), e(TG_UI[lang]['p']), tg_link(),
        e(S['rss_h']), e(S['rss_p']), rss_path(lang), e(S['rss_all']), e(absu(rss_path(lang))), path(lang, ('topics',)), e(S['rss_topics']),
        e(S['corr_h']), e(S['corr_p']), e(mailto(lang, key)), e(U['corr_report']), e(CONTACT), path(lang, ('corrections',)), e(S['corr_log']),
        e(S['tell_h']), e(S['tell_p']))
    page(lang, key, S['title'] + ' — ' + U['site_name'], S['desc'], body, ld=[cr_ld], scripts='<script src="%s" defer></script>\n' % JS_SUPPORT)

# ------------------------------------------------------------------ daily digest "День в двух версиях" (deterministic, no LLM)
DAY_SLUG = {'ru': 'den', 'uk': 'den', 'en': 'day'}
DAY_UI = {
    'ru': dict(nav='День', h1='День в двух версиях', idx_title='День в двух версиях: что произошло за день — версии России и Украины',
               idx_desc='Ежедневная сводка: 5 главных событий каждого дня войны и как их описывают российские, украинские и независимые СМИ. Архив по дням.',
               idx_lede='Каждый день — пять главных событий: рядом версии российских и украинских СМИ. Важность считается автоматически (сколько сторон и источников, о чём событие) с учётом того, насколько расходятся версии.',
               title='Что произошло {d}: главное за день в двух версиях — Россия и Украина', h='Что произошло {d}: день в двух версиях',
               desc='Что произошло {d}: {n} главных событий дня и как их описывают СМИ России и Украины. {titles}',
               intro='{d}: на сайте {n} {cards}. {div}Ниже — главные события дня по автоматической оценке важности; у каждого — коротко версии сторон.',
               div_c='версии сторон противоречат друг другу', div_d='версии расходятся в деталях', div_tpl='В {parts}. ', div_in='{n} {cards}: {what}', cards=('карточка', 'карточки', 'карточек'), cards_in=('карточке', 'карточках', 'карточках'),
               ru='Россия', ua='Украина', ind='Независимые', none='нет материалов', all_day='Все события {d}', open='Карточка →', ongoing='День ещё не закончился — сводка обновляется.',
               prev='← Предыдущий день', next='Следующий день →', n_ev='{n} соб.'),
    'uk': dict(nav='День', h1='День у двох версіях', idx_title='День у двох версіях: що сталося за день — версії Росії та України',
               idx_desc='Щоденне зведення: 5 головних подій кожного дня війни і як їх описують російські, українські та незалежні медіа. Архів за днями.',
               idx_lede='Щодня — п’ять головних подій: поруч версії російських і українських медіа. Важливість рахується автоматично (скільки сторін і джерел, про що подія) з урахуванням того, наскільки розходяться версії.',
               title='Що сталося {d}: головне за день у двох версіях — Росія та Україна', h='Що сталося {d}: день у двох версіях',
               desc='Що сталося {d}: {n} головних подій дня і як їх описують медіа Росії та України. {titles}',
               intro='{d}: на сайті {n} {cards}. {div}Нижче — головні події дня за автоматичною оцінкою важливості; до кожної — коротко версії сторін.',
               div_c='версії сторін суперечать одна одній', div_d='версії розходяться в деталях', div_tpl='У {parts}. ', div_in='{n} {cards}: {what}', cards=('картка', 'картки', 'карток'), cards_in=('картці', 'картках', 'картках'),
               ru='Росія', ua='Україна', ind='Незалежні', none='немає матеріалів', all_day='Усі події {d}', open='Картка →', ongoing='День ще не закінчився — зведення оновлюється.',
               prev='← Попередній день', next='Наступний день →', n_ev='{n} под.'),
    'en': dict(nav='Day', h1='The day in two versions', idx_title='The day in two versions: what happened each day — Russian and Ukrainian accounts',
               idx_desc='Daily digest: the 5 main events of each day of the war and how Russian, Ukrainian and independent media describe them. Archive by day.',
               idx_lede='Every day, the five main events with the Russian and Ukrainian media versions side by side. Importance is scored automatically (how many sides and sources, what the event is about), plus how far the versions diverge.',
               title='What happened on {d}: the day in two versions — Russia and Ukraine', h='What happened on {d}: the day in two versions',
               desc='What happened on {d}: the {n} main events of the day and how Russian and Ukrainian media describe them. {titles}',
               intro='{d}: {n} {cards} on the site. {div}Below are the main events of the day by automatic importance score, each with the sides’ versions in brief.',
               div_c='the sides’ versions contradict each other', div_d='the versions differ in details', div_tpl='In {parts}. ', div_in='{n} {cards} {what}', cards=('card', 'cards', 'cards'), cards_in=('card', 'cards', 'cards'),
               ru='Russia', ua='Ukraine', ind='Independent', none='no coverage', all_day='All events of {d}', open='Card →', ongoing='The day is not over yet — the digest is updating.',
               prev='← Previous day', next='Next day →', n_ev='{n} ev.'),
}
DIGEST_N = 5
DIGEST_MIN = 3          # days with fewer cards get no digest page
def plural(lang, n, forms):
    if lang == 'en': return forms[0] if n == 1 else forms[1]
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11: return forms[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14: return forms[1]
    return forms[2]
def ev_article_ids(ev):
    c = ev['card']; out = []
    for k, _ in SIDES:
        for aid in [x for x, _ in c[k]['quotes']] + list(c[k]['also']):
            if aid in A and aid not in out: out.append(aid)
    return out
from pipeline import importance as _imp
_IMP = {}
def ev_importance(ev):
    if ev['id'] in _IMP: return _IMP[ev['id']]
    rows = [A[a] for a in ev_article_ids(ev)]
    sc, _ = _imp.score_rows(ev['card']['title'], rows)
    sc += {'contradict': 35, 'details': 15, 'agree': 0}.get(div_level(ev), 0) + 8 * ncov(ev)
    if ev['conf']['label'] == 'disputed': sc += 10
    _IMP[ev['id']] = sc
    return sc
DIGEST_DAYS = [d for d in DAYS if sum(1 for x in EV if x['start'] == d) >= DIGEST_MIN]
def digest_events(d):
    evs = [x for x in EV if x['start'] == d]
    return sorted(evs, key=lambda x: (-ev_importance(x), str(x['id'])))[:DIGEST_N], len(evs)
def build_digest(lang, d):
    U = ui.UI[lang]; Dg = DAY_UI[lang]; key = ('day', d.isoformat()); dn = fmt_day(lang, d)
    top, n_all = digest_events(d)
    evs_all = [x for x in EV if x['start'] == d]
    nc = sum(1 for x in evs_all if div_level(x) == 'contradict'); nd = sum(1 for x in evs_all if div_level(x) == 'details')
    parts = [Dg['div_in'].format(n=n_, cards=plural(lang, n_, Dg['cards_in']), what=Dg[w]) for n_, w in ((nc, 'div_c'), (nd, 'div_d')) if n_]
    divp = Dg['div_tpl'].format(parts='; '.join(parts)) if parts else ''
    intro = Dg['intro'].format(d=dn, n=n_all, cards=plural(lang, n_all, Dg['cards']), div=divp)
    cr_html, cr_ld = crumbs(lang, [(Dg['h1'], ('days',)), (dn, key)])
    items = []
    for x in top:
        T = content(lang, x)
        sides = ''.join('<p class="dv"><b>%s:</b> %s</p>' % (e(Dg[k]), e(trunc(first_sentence(T[k], 260), 230)) if T[k] else '<i>%s</i>' % e(Dg['none']))
                        for k in ('ru', 'ua') ) + (('<p class="dv"><b>%s:</b> %s</p>' % (e(Dg['ind']), e(trunc(first_sentence(T['ind'], 200), 180)))) if T['ind'] and not (T['ru'] and T['ua']) else '')
        items.append('<li class="ev dg"><article><h2><a href="%s">%s</a></h2><p class="chips"><span class="chip p">%s</span>%s%s</p>%s<p><a href="%s">%s</a></p></article></li>' % (
            path(lang, ('event', x['slug'])), e(T['t']), e(T['p']), div_badge(lang, x), conf_badge(lang, x), sides, path(lang, ('event', x['slug'])), e(Dg['open'])))
    i = DIGEST_DAYS.index(d)
    pn = ''.join(('<a class="%s" rel="%s" href="%s">%s</a>' % (c, c, path(lang, ('day', o.isoformat())), e(lab))) for c, o, lab in
                 (('prev', DIGEST_DAYS[i - 1] if i > 0 else None, Dg['prev']), ('next', DIGEST_DAYS[i + 1] if i + 1 < len(DIGEST_DAYS) else None, Dg['next'])) if o)
    ongoing = ('<p class="note">%s</p>' % e(Dg['ongoing'])) if d.isoformat() >= COLLECTED else ''
    body = ('%s\n<h1>%s</h1>\n<p class="lede">%s</p>%s\n<ol class="evlist digest">%s</ol>\n<p><a href="%s">%s</a></p>\n<nav class="pn" aria-label="%s">%s</nav>') % (
        cr_html, e(Dg['h'].format(d=dn)), e(intro), ongoing, '\n'.join(items), path(lang, ('date', d.isoformat())), e(Dg['all_day'].format(d=dn)), e(U['pn_nav']), pn)
    titles = '; '.join(content(lang, x)['t'] for x in top)
    page(lang, key, Dg['title'].format(d=dn), trunc(Dg['desc'].format(d=dn, n=len(top), titles=titles), 158), body,
         ld=[cr_ld, itemlist_ld(lang, top, Dg['h'].format(d=dn), key)])
def build_days(lang):
    Dg = DAY_UI[lang]; key = ('days',)
    cr_html, cr_ld = crumbs(lang, [(Dg['h1'], key)])
    li = []
    for d in reversed(DIGEST_DAYS):
        top, n = digest_events(d)
        li.append('<li class="ev"><h2><a href="%s">%s</a> <small class="meta">(%s)</small></h2><p class="lead">%s</p></li>' % (
            path(lang, ('day', d.isoformat())), e(Dg['h'].format(d=fmt_day(lang, d))), e(Dg['n_ev'].format(n=n)), e(' · '.join(content(lang, x)['t'] for x in top[:3]))))
    body = '%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<ul class="evlist">%s</ul>' % (cr_html, e(Dg['h1']), e(Dg['idx_lede']), '\n'.join(li))
    page(lang, key, Dg['idx_title'], Dg['idx_desc'], body, ld=[cr_ld])

# ------------------------------------------------------------------ static site search (/ru/poisk/, /uk/poshuk/, /en/search/; JSON index lazy-loaded on that page only)
SEARCH_SLUG = {'ru': 'poisk', 'uk': 'poshuk', 'en': 'search'}
SEARCH_UI = {
    'ru': dict(nav='Поиск', h1='Поиск по сайту', title='Поиск по событиям: город, тема, дата', desc='Поиск по всем карточкам сайта: события войны, места, даты и версии российских, украинских и независимых СМИ.',
               lede='Ищет по заголовкам, местам, датам и кратким версиям сторон во всех карточках. Можно вводить начало слова.', ph='Например: Харьков, НПЗ, обмен пленными', btn='Найти',
               loading='Загрузка индекса…', found='Найдено: {n}', none='Ничего не найдено', noscript='Поиск работает в браузере и требует JavaScript. Без него воспользуйтесь архивом по датам или темами.'),
    'uk': dict(nav='Пошук', h1='Пошук по сайту', title='Пошук подій: місто, тема, дата', desc='Пошук по всіх картках сайту: події війни, місця, дати й версії російських, українських і незалежних медіа.',
               lede='Шукає в заголовках, місцях, датах і коротких версіях сторін в усіх картках. Можна вводити початок слова.', ph='Наприклад: Харків, НПЗ, обмін полоненими', btn='Знайти',
               loading='Завантаження індексу…', found='Знайдено: {n}', none='Нічого не знайдено', noscript='Пошук працює в браузері й потребує JavaScript. Без нього скористайтеся архівом за датами або темами.'),
    'en': dict(nav='Search', h1='Search the site', title='Search events: city, topic, date', desc='Search all cards on the site: war events, places, dates and the Russian, Ukrainian and independent media versions.',
               lede='Searches titles, places, dates and the short versions of each side in every card. Word beginnings work too.', ph='E.g. Kharkiv, refinery, prisoner exchange', btn='Search',
               loading='Loading the index…', found='Found: {n}', none='Nothing found', noscript='Search runs in your browser and needs JavaScript. Without it, use the date archive or the topics.'),
}
SEARCH_V = {}
def build_search_index(lang):
    rows = []
    for x in LISTING:
        T = content(lang, x)
        side = ' '.join(trunc(first_sentence(T[k], 160), 110) for k in ('ru', 'ua', 'ind') if T[k])
        rows.append([path(lang, ('event', x['slug'])), T['t'], T['p'], x['start'].isoformat(), region_name(lang, x['region']) + ' ' + ' '.join(topic_name(lang, t) for t in EV_HUBS.get(x['slug'], [])),
                     trunc(first_sentence(T['d'], 180), 150) + ' ' + side, fmt_range(lang, x['start'], x['end'])])
    data = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    h = hashlib.md5(data.encode()).hexdigest()[:8]
    rel = '/v/search-%s.%s.json' % (lang, h)
    write(rel, data)
    SEARCH_V[lang] = rel
    return rel
def build_search(lang):
    S = SEARCH_UI[lang]; key = ('search',)
    idx = build_search_index(lang)
    cr_html, cr_ld = crumbs(lang, [(S['h1'], key)])
    uij = dict(loading=S['loading'], found=S['found'], none=S['none'])
    body = ('%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<div id="search" data-index="%s" data-ui="%s"><form id="sform" role="search" action="%s" method="get" class="sform">'
            '<label class="vh" for="q">%s</label><input id="q" name="q" type="search" autocomplete="off" placeholder="%s"><button type="submit">%s</button></form>'
            '<p id="sstatus" class="meta" role="status" aria-live="polite"></p><ul class="evlist" id="results"></ul></div><noscript><p>%s <a href="%s">%s</a> · <a href="%s">%s</a></p></noscript>') % (
        cr_html, e(S['h1']), e(S['lede']), e(idx), e(json.dumps(uij, ensure_ascii=False)), path(lang, key), e(S['h1']), e(S['ph']), e(S['btn']),
        e(S['noscript']), path(lang, ('dates',)), e(ui.UI[lang]['dates_h1']), path(lang, ('topics',)), e(TOPIC_UI[lang]['nav_topics']))
    page(lang, key, S['title'] + ' — ' + ui.UI[lang]['site_name'], S['desc'], body, ld=[cr_ld], scripts='<script src="%s" defer></script>\n' % JS_SEARCH)

# ------------------------------------------------------------------ glossary of wording (src/glossary.py; examples found in the source articles of published cards, no LLM)
GLOSS_SLUG = {'ru': 'slovar', 'uk': 'slovnyk', 'en': 'glossary'}
GLOSS_UI = {
    'ru': dict(nav='Словарь', h1='Словарь формулировок', title='Словарь формулировок: как российские и украинские СМИ называют одно и то же',
               desc='СВО или вторжение, ВСУ или ЗСУ, «освобождение» или «оккупация», Kiev или Kyiv: {n} пар формулировок российских и украинских СМИ с реальными примерами из источников.',
               lede='Как стороны называют одни и те же вещи. У каждой пары — формулировка российских СМИ, украинских СМИ, нейтральный вариант независимых изданий, короткое пояснение и реальные цитаты из материалов, на которых основаны карточки сайта. Словарь описывает словоупотребление и не оценивает, кто прав.',
               ru='Российские СМИ', ua='Украинские СМИ', ind='Независимые СМИ', neutral='Нейтрально', ex='Примеры из источников', in_cards='встречается в материалах {n} {cards}', in_forms=('карточки', 'карточек', 'карточек'),
               no_ex='в материалах карточек примеров пока нет', toc='Содержание', method='Как собраны примеры: поиск по исходным текстам статей, на которых основаны опубликованные карточки (российская сторона — РИА Новости, ТАСС, Интерфакс, Минобороны РФ; украинская — Суспильне, «Украинская правда», РБК‑Украина; независимые — BBC, DW, Meduza и др.). Пары без единого примера на сайт не попадают. Цитаты приводятся дословно, на языке оригинала.', card='карточка'),
    'uk': dict(nav='Словник', h1='Словник формулювань', title='Словник формулювань: як російські й українські медіа називають те саме',
               desc='СВО чи вторгнення, ВСУ чи ЗСУ, «звільнення» чи «окупація», Kiev чи Kyiv: {n} пар формулювань російських і українських медіа з реальними прикладами з джерел.',
               lede='Як сторони називають ті самі речі. До кожної пари — формулювання російських медіа, українських медіа, нейтральний варіант незалежних видань, коротке пояснення й реальні цитати з матеріалів, на яких засновані картки сайту. Словник описує слововживання і не оцінює, хто правий.',
               ru='Російські медіа', ua='Українські медіа', ind='Незалежні медіа', neutral='Нейтрально', ex='Приклади з джерел', in_cards='трапляється в матеріалах {n} {cards}', in_forms=('картки', 'карток', 'карток'),
               no_ex='у матеріалах карток прикладів поки немає', toc='Зміст', method='Як зібрано приклади: пошук у вихідних текстах статей, на яких засновані опубліковані картки (російська сторона — РИА Новости, ТАСС, Інтерфакс, Міноборони РФ; українська — Суспільне, «Українська правда», РБК-Україна; незалежні — BBC, DW, Meduza тощо). Пари без жодного прикладу на сайт не потрапляють. Цитати наведено дослівно, мовою оригіналу.', card='картка'),
    'en': dict(nav='Glossary', h1='Glossary of wording', title='Glossary of wording: how Russian and Ukrainian media name the same things',
               desc='“Special military operation” or invasion, VSU or ZSU, “liberation” or “occupation”, Kiev or Kyiv: {n} pairs of Russian and Ukrainian media wording with real source examples.',
               lede='How the sides name the same things. Each pair gives the Russian media wording, the Ukrainian media wording, the neutral wording of independent outlets, a short note, and real quotes from the articles behind the site’s cards. The glossary describes usage; it does not judge who is right.',
               ru='Russian media', ua='Ukrainian media', ind='Independent media', neutral='Neutral', ex='Examples from sources', in_cards='appears in the sources of {n} {cards}', in_forms=('card', 'cards', 'cards'),
               no_ex='no examples in card sources yet', toc='Contents', method='How the examples were found: a search of the original texts of the articles behind published cards (Russian side: RIA Novosti, TASS, Interfax, Russian MoD; Ukrainian side: Suspilne, Ukrainska Pravda, RBC-Ukraine; independent: BBC, DW, Meduza and others). Pairs without a single example are not shown. Quotes are verbatim, in the original language.', card='card'),
}
import glossary as gloss_mod
_GLOSS = None
def _snippet(text, m, n=190):
    a, b = m.start(), m.end()
    s = max(text.rfind('. ', 0, a) + 2, a - n // 2, 0)
    if s > 0 and text[s - 1] not in ' \n':
        sp = text.find(' ', s, a); s = sp + 1 if sp >= 0 else s
    f = text.find('.', b)
    if f < 0 or f > b + n // 2:
        sp = text.rfind(' ', b, b + n // 2); f = sp if sp > b else min(len(text), b + n // 2)
    else: f += 1
    pre, mid, post = text[s:a], text[a:b], text[b:f]
    pre = re.sub(r'\s+', ' ', pre); post = re.sub(r'\s+', ' ', post)
    return ('…' if s > 0 and text[s - 2:s] != '. ' else '') + pre, mid, post + ('…' if f < len(text) and text[f - 1] != '.' else '')
def gloss_examples():
    global _GLOSS
    if _GLOSS is not None: return _GLOSS
    _GLOSS = {}
    for en in gloss_mod.ENTRIES:
        res = {}
        for g, rx in en['rx'].items():
            R = re.compile(rx); ex, cards_n = [], 0
            for x in LISTING:
                c = x['card']; hit = None
                for aid, q in c[g]['quotes']:
                    m = R.search(q)
                    if m: hit = (aid, q, m); break
                if not hit:
                    for aid in [a for a, _ in c[g]['quotes']] + list(c[g]['also']):
                        a = A.get(aid)
                        if not a: continue
                        for fld in (a['title'] or '', a['text'] or ''):
                            m = R.search(fld)
                            if m: hit = (aid, fld, m); break
                        if hit: break
                if hit:
                    cards_n += 1
                    sn_ = _snippet(hit[1], hit[2])
                    if len(ex) < 2 and all(hit[0] != z[1]['id'] and sn_ != z[2] for z in ex): ex.append((x, A[hit[0]], sn_))
            res[g] = (ex, cards_n)
        _GLOSS[en['key']] = res
    return _GLOSS
def gloss_entries():
    G = gloss_examples()
    return [en for en in gloss_mod.ENTRIES if any(G[en['key']].get(g, ([], 0))[1] for g in en.get('require', ('ru', 'ua')))]
def build_glossary(lang):
    Gu = GLOSS_UI[lang]; key = ('glossary',); G = gloss_examples(); ents = gloss_entries()
    cr_html, cr_ld = crumbs(lang, [(Gu['h1'], key)])
    secs, toc = [], []
    for en in ents:
        exs = []
        for g in ('ru', 'ua', 'ind'):
            if g not in en['rx']: continue
            ex, n = G[en['key']][g]
            if not ex: continue
            li = ''.join('<li><q lang="%s">%s<mark>%s</mark>%s</q> — <a href="%s" target="_blank" rel="noopener">%s</a>, %s · <a href="%s">%s</a></li>' % (
                a['lang'], e(sn[0]), e(sn[1]), e(sn[2]), e(a['url']), e(ui.SRC_NAME[lang].get(a['source'], a['source'])), e(fmt_day(lang, dt.date.fromisoformat(a['date'][:10])) if a.get('date') else ''),
                path(lang, ('event', x['slug'])), e(Gu['card'])) for x, a, sn in ex)
            exs.append('<p class="gx-h"><b>%s</b> <small class="meta">(%s)</small></p><ul class="gx">%s</ul>' % (e(Gu[g]), e(Gu['in_cards'].format(n=n, cards=plural(lang, n, Gu['in_forms']))), li))
        head = '%s / %s' % (en['ru'][lang], en['ua'][lang])
        toc.append('<li><a href="#%s">%s</a></li>' % (en['key'], e(head)))
        secs.append(('<section class="gl" id="%s" aria-labelledby="g-%s"><h2 id="g-%s">%s</h2><dl class="gd"><dt>%s</dt><dd>%s</dd><dt>%s</dt><dd>%s</dd><dt>%s</dt><dd>%s</dd></dl><p>%s</p>'
                     '<details%s><summary>%s</summary>%s</details></section>') % (
            en['key'], en['key'], en['key'], e(head), e(Gu['ru']), e(en['ru'][lang]), e(Gu['ua']), e(en['ua'][lang]), e(Gu['neutral']), e(en['neutral'][lang]), e(en['note'][lang]),
            ' open' if len(secs) < 3 else '', e(Gu['ex']), ''.join(exs) or '<p class="meta">%s</p>' % e(Gu['no_ex'])))
    body = '%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<nav class="toc" aria-label="%s"><ol>%s</ol></nav>\n%s\n<p class="note">%s</p>' % (
        cr_html, e(Gu['h1']), e(Gu['lede']), e(Gu['toc']), ''.join(toc), '\n'.join(secs), e(Gu['method']))
    dterms = {'@context': 'https://schema.org', '@type': 'DefinedTermSet', 'name': Gu['h1'], 'url': absu(path(lang, key)), 'inLanguage': lang,
              'hasDefinedTerm': [{'@type': 'DefinedTerm', 'name': '%s / %s' % (x['ru'][lang], x['ua'][lang]), 'description': x['note'][lang]} for x in ents]}
    page(lang, key, Gu['title'], trunc(Gu['desc'].format(n=len(ents)), 158), body, ld=[cr_ld, dterms])

# ------------------------------------------------------------------ social share images per card (tools/ogimg.py; cached by content hash in data/og_cache/)
sys.path.insert(0, os.path.join(HERE, 'tools'))
import ogimg
OG_CACHE = os.path.join(HERE, 'data', 'og_cache')
OG_SIDE = {'ru': ('Россия', 'Украина', 'Независимые'), 'uk': ('Росія', 'Україна', 'Незалежні'), 'en': ('Russia', 'Ukraine', 'Independent')}
OG_STATS = dict(rendered=0, cached=0)
try: OG_MAN = json.load(open(os.path.join(OG_CACHE, 'manifest.json')))
except (OSError, ValueError): OG_MAN = {}
def og_image(lang, ev):
    """Returns the absolute URL of the card's share image (rendered only when its content changed)."""
    T = content(lang, ev); lv = div_level(ev)
    sides = [(k, OG_SIDE[lang][i], first_sentence(T[k], 320) if T[k] else '— %s —' % DAY_UI[lang]['none']) for i, k in enumerate(('ru', 'ua', 'ind')) if T[k] or k != 'ind']
    spec = dict(site=ui.UI[lang]['site_name'], domain='twoversions.com', date=fmt_range(lang, ev['start'], ev['end']), title=T['t'],
                div=('%s: %s' % (DIV_UI[lang]['label'], DIV_UI[lang][lv])) if lv else '', div_level=lv or '', sides=sides)
    h = ogimg.spec_hash(spec); k = '%s/%s' % (lang, ev['slug'])
    src = os.path.join(OG_CACHE, lang, ev['slug'] + '.png')
    if OG_MAN.get(k) != h or not os.path.exists(src):
        os.makedirs(os.path.dirname(src), exist_ok=True)
        open(src, 'wb').write(ogimg.render(spec)); OG_MAN[k] = h; OG_STATS['rendered'] += 1
    else: OG_STATS['cached'] += 1
    rel = '/og/%s/%s.png' % (lang, ev['slug'])
    dst = os.path.join(OUT, rel.lstrip('/'))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try: os.link(src, dst)
    except OSError: shutil.copy2(src, dst)
    return absu(rel) + '?v=' + h[:8]
def og_save():
    os.makedirs(OG_CACHE, exist_ok=True)
    live = {'%s/%s' % (l, x['slug']) for l in LANGS for x in EV}
    for k in [k for k in OG_MAN if k not in live]: OG_MAN.pop(k, None)
    json.dump(OG_MAN, open(os.path.join(OG_CACHE, 'manifest.json'), 'w'), separators=(',', ':'))

# ------------------------------------------------------------------ weekly review "Разбор недели" (data/weekly.json, written once per closed week by tools/weekly.py)
WEEK_SLUG = {'ru': 'nedelya', 'uk': 'tyzhden', 'en': 'week'}
WEEK_UI = {
    'ru': dict(nav='Разбор недели', h1='Разбор недели', idx_title='Разбор недели: где версии России и Украины расходились сильнее всего',
               idx_desc='Еженедельные обзоры: события, в которых российские и украинские СМИ противоречили друг другу или расходились в деталях, со ссылками на карточки.',
               idx_lede='Раз в неделю — короткий обзор событий, в которых версии сторон расходились сильнее всего (метки «противоречат» и «расходятся в деталях»). Без оценок, кто прав: только то, что пишут источники.',
               title='Разбор недели {r}: {t}', week='Неделя {r}', cards_h='Карточки недели', about='Обзор составлен автоматически (языковая модель Qwen) только по карточкам этой недели; числа и ссылки на карточки проверены кодом. Сайт не делает собственных выводов о том, чья версия верна.',
               all='Все разборы'),
    'uk': dict(nav='Розбір тижня', h1='Розбір тижня', idx_title='Розбір тижня: де версії Росії та України розходилися найбільше',
               idx_desc='Щотижневі огляди: події, у яких російські й українські медіа суперечили одні одним або розходилися в деталях, з посиланнями на картки.',
               idx_lede='Раз на тиждень — короткий огляд подій, у яких версії сторін розходилися найбільше (мітки «суперечать» і «розходяться в деталях»). Без оцінок, хто правий: лише те, що пишуть джерела.',
               title='Розбір тижня {r}: {t}', week='Тиждень {r}', cards_h='Картки тижня', about='Огляд складено автоматично (мовна модель Qwen) лише за картками цього тижня; числа й посилання на картки перевірено кодом. Сайт не робить власних висновків про те, чия версія правильна.',
               all='Усі розбори'),
    'en': dict(nav='Week in review', h1='Week in review', idx_title='Week in review: where Russian and Ukrainian versions diverged most',
               idx_desc='Weekly reviews: events where Russian and Ukrainian media contradicted each other or differed in details, with links to the cards.',
               idx_lede='Once a week, a short review of the events where the sides’ versions diverged most (labels “contradict” and “differ in details”). No verdict on who is right: only what the sources say.',
               title='Week in review, {r}: {t}', week='Week of {r}', cards_h='This week’s cards', about='Written automatically (Qwen language model) from this week’s cards only; numbers and card links are checked in code. The site draws no conclusions of its own about whose version is correct.',
               all='All reviews'),
}
try: WEEKLY = json.load(open(os.path.join(HERE, 'data', 'weekly.json'), encoding='utf-8'))
except (OSError, ValueError): WEEKLY = {}
EV_BY_EID = {x['card'].get('event_id'): x for x in EV if x['auto']}
WEEKS = sorted(WEEKLY)
_WK_PREFIX = re.compile(r'^(?:(?:Обзор|Разбор|Итоги) недели|(?:Огляд|Розбір|Підсумки) тижня|Week(?:ly)?(?: in)? [Rr]eview|Weekly [Oo]verview)\s*[:.—–-]\s*')
def wk_title(t):
    t = _WK_PREFIX.sub('', t or '').strip(); return t[:1].upper() + t[1:]
def week_dates(wk):
    a = dt.date.fromisocalendar(int(wk[:4]), int(wk[6:]), 1); return a, a + dt.timedelta(days=6)
def build_week(lang, wk):
    W_ = WEEK_UI[lang]; r = WEEKLY[wk]; T = r[lang]; key = ('week', wk.lower()); a, b = week_dates(wk); rng = fmt_range(lang, a, b)
    cr_html, cr_ld = crumbs(lang, [(W_['h1'], ('weeks',)), (W_['week'].format(r=rng), key)])
    secs = []
    for i, ids in enumerate(r['sections_cards']):
        if ('h%d' % i) not in T: continue
        evs = [EV_BY_EID[x] for x in ids if x in EV_BY_EID]
        li = ''.join('<li><a href="%s">%s</a> %s</li>' % (path(lang, ('event', x['slug'])), e(content(lang, x)['t']), div_badge(lang, x)) for x in evs)
        secs.append('<section><h2>%s</h2><p>%s</p>%s</section>' % (e(T['h%d' % i]), e(T['x%d' % i]), ('<ul class="wk">%s</ul>' % li) if li else ''))
    allev = [EV_BY_EID[x] for x in r['cards'] if x in EV_BY_EID]
    i = WEEKS.index(wk)
    pn = ''.join(('<a class="%s" rel="%s" href="%s">%s</a>' % (c, c, path(lang, ('week', o.lower())), e(W_['week'].format(r=fmt_range(lang, *week_dates(o)))))) for c, o in
                 (('prev', WEEKS[i - 1] if i > 0 else None), ('next', WEEKS[i + 1] if i + 1 < len(WEEKS) else None)) if o)
    body = ('%s\n<p class="meta">%s</p>\n<h1>%s</h1>\n<p class="lede">%s</p>\n%s\n%s<h2>%s</h2>\n<ul class="evlist">%s</ul>\n<p class="note">%s</p>\n<nav class="pn" aria-label="%s">%s</nav>') % (
        cr_html, e(W_['week'].format(r=rng)), e(wk_title(T['t'])), e(T['lede']), '\n'.join(secs), ('<p>%s</p>\n' % e(T['outro'])) if T.get('outro') else '', e(W_['cards_h']),
        '\n'.join(event_li(lang, x, False, 'h3') for x in allev), e(W_['about']), e(ui.UI[lang]['pn_nav']), pn)
    art = {'@context': 'https://schema.org', '@type': 'Article', 'headline': trunc(wk_title(T['t']), 110), 'inLanguage': lang, 'url': absu(path(lang, key)),
           'datePublished': r['generated'], 'dateModified': r['generated'], 'image': [OG_IMG], 'author': {'@type': 'Organization', 'name': ui.UI[lang]['site_name']},
           'temporalCoverage': '%s/%s' % (a.isoformat(), b.isoformat())}
    page(lang, key, trunc(W_['title'].format(r=rng, t=wk_title(T['t'])), 110), trunc(T['lede'], 158), body, og_type='article', ld=[cr_ld, art])
def build_weeks(lang):
    W_ = WEEK_UI[lang]; key = ('weeks',)
    cr_html, cr_ld = crumbs(lang, [(W_['h1'], key)])
    li = ''.join('<li class="ev"><h2><a href="%s">%s</a></h2><p class="chips"><span class="chip d">%s</span></p><p class="lead">%s</p></li>' % (
        path(lang, ('week', wk.lower())), e(wk_title(WEEKLY[wk][lang]['t'])), e(W_['week'].format(r=fmt_range(lang, *week_dates(wk)))), e(WEEKLY[wk][lang]['lede'])) for wk in reversed(WEEKS))
    body = '%s\n<h1>%s</h1>\n<p class="lede">%s</p>\n<ul class="evlist">%s</ul>' % (cr_html, e(W_['h1']), e(W_['idx_lede']), li)
    page(lang, key, W_['idx_title'], W_['idx_desc'], body, ld=[cr_ld])

# ------------------------------------------------------------------ root, 404, sitemap, robots
def build_root():
    U = ui.UI['ru']; url = absu('/')
    alts = ''.join('<link rel="alternate" hreflang="%s" href="%s">\n' % (l, e(absu('/%s/' % l))) for l in LANGS)
    links = ''.join('<a href="/%s/" hreflang="%s" lang="%s">%s</a>' % (l, l, l, ui.LANG_NAME[l]) for l in LANGS)
    desc = U['root_desc']
    doc = ('<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>%s</title>\n<meta name="description" content="%s">\n'
           '<meta name="robots" content="index,follow">\n<link rel="canonical" href="%s">\n%s<link rel="alternate" hreflang="x-default" href="%s">\n'
           '<meta property="og:type" content="website">\n<meta property="og:site_name" content="%s">\n<meta property="og:title" content="%s">\n<meta property="og:description" content="%s">\n<meta property="og:url" content="%s">\n'
           '<meta property="og:image" content="%s">\n<meta name="twitter:card" content="summary_large_image">\n<meta name="twitter:title" content="%s">\n<meta name="twitter:description" content="%s">\n<meta name="twitter:image" content="%s">\n'
           '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">\n%s\n%s\n%s\n%s\n</head>\n<body>\n'
           '<main id="main" style="max-width:760px;margin:8vh auto;padding:0 16px"><h1>%s</h1>\n<p class="lede">%s</p>\n<nav class="pick" aria-label="Language">%s</nav>\n</main>\n'
           '<script>(function(){try{var p=localStorage.getItem("lang");if(!/^(ru|uk|en)$/.test(p||"")){var n=(navigator.language||"").slice(0,2).toLowerCase();p=/^(ru|uk|en)$/.test(n)?n:""}'
           'if(p)location.replace("/"+p+"/")}catch(e){}})()</script>\n</body>\n</html>\n') % (
        e(U['root_title']), e(desc), e(url), alts, e(url), e(U['site_name']), e(U['root_title']), e(desc), e(url), e(OG_IMG), e(U['root_title']), e(desc), e(OG_IMG), THEME_INIT, STYLE_TAG,
        jsonld({'@context': 'https://schema.org', '@type': 'WebSite', 'url': url, 'name': U['site_name'], 'inLanguage': LANGS}), analytics_block(), e(U['root_h1']), e(U['site_tagline']), links)
    write('/index.html', doc)

def build_404():
    U = ui.UI['ru']
    doc = ('<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>%s</title>\n<meta name="robots" content="noindex">\n'
           '%s\n%s\n</head>\n<body>\n<main id="main" style="max-width:760px;margin:8vh auto;padding:0 16px"><h1>%s / Сторінку не знайдено / Page not found</h1>\n'
           '<p><a href="/ru/">Русский</a> · <a href="/uk/">Українська</a> · <a href="/en/">English</a></p></main>\n</body>\n</html>\n') % (e(U['notfound_title']), THEME_INIT, STYLE_TAG, e(U['notfound_h1']))
    write('/404.html', doc)

def build_sitemap():
    allp = [('root', None)] + [(l, k) for l, k in PAGES]
    seen, keys = set(), []
    for l, k in PAGES:
        if k not in seen and k[0] not in NOINDEX_KEYS: seen.add(k); keys.append(k)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    def entry(loc, alts, xd):
        s = ['<url>', '<loc>%s</loc>' % e(loc), '<lastmod>%s</lastmod>' % MODIFIED]
        s += ['<xhtml:link rel="alternate" hreflang="%s" href="%s"/>' % (l, e(u)) for l, u in alts]
        s.append('<xhtml:link rel="alternate" hreflang="x-default" href="%s"/>' % e(xd)); s.append('</url>')
        return s
    out += entry(absu('/'), [(l, absu('/%s/' % l)) for l in LANGS], absu('/'))
    n = 1
    for k in keys:
        alts = [(l, absu(path(l, k))) for l in LANGS]
        xd = absu('/') if k[0] == 'home' else absu(path('en', k))
        for l in LANGS:
            out += entry(absu(path(l, k)), alts, xd); n += 1
    out.append('</urlset>')
    write('/sitemap.xml', '\n'.join(out) + '\n')
    return n

NEWS_HOURS = int(os.environ.get('NEWS_SITEMAP_HOURS', '48'))
def build_news_sitemap():
    """/news-sitemap.xml (Google News sitemap): auto cards first published in the last NEWS_HOURS hours, all three languages.
    Rebuilt on every build; an empty <urlset> is valid when nothing is that fresh."""
    now = dt.datetime.now(dt.timezone.utc); out = []
    for ev in sorted(EV, key=lambda x: x['card'].get('first_published_ts') or '', reverse=True):
        if not ev['auto']: continue
        pub, _ = event_times(ev)
        try: t = dt.datetime.fromisoformat(pub)
        except ValueError: continue
        if t.tzinfo is None: t = t.replace(tzinfo=dt.timezone.utc)
        if now - t > dt.timedelta(hours=NEWS_HOURS): continue
        for l in LANGS:
            out.append('<url><loc>%s</loc><news:news><news:publication><news:name>%s</news:name><news:language>%s</news:language></news:publication>'
                       '<news:publication_date>%s</news:publication_date><news:title>%s</news:title></news:news></url>' % (
                           e(absu(path(l, ('event', ev['slug'])))), e(ui.UI[l]['site_name']), l, t.isoformat(timespec='seconds'), e(content(l, ev)['t'])))
    out = out[:1000]
    write('/news-sitemap.xml', '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">\n'
          + '\n'.join(out) + ('\n' if out else '') + '</urlset>\n')
    write('/sitemap-index.xml', '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + ''.join('<sitemap><loc>%s</loc><lastmod>%s</lastmod></sitemap>\n' % (absu(f), now.isoformat(timespec='seconds')) for f in ('/sitemap.xml', '/news-sitemap.xml'))
          + '</sitemapindex>\n')
    return len(out)

def build_tg_feed():
    """data/tg_feed.json: what pipeline/telegram_post.py needs (Russian texts, importance, divergence level, share image), computed with the
    same helpers as the site so the post matches the card page. Not deployed (lives in data/)."""
    cards = []
    for ev in EV:
        if not ev['auto']: continue
        T = content('ru', ev); c = ev['card']
        cards.append(dict(event_id=str(c['event_id']), slug=ev['slug'], title=T['t'], place=T['p'], diverge=T['d'], ru=T['ru'], ua=T['ua'], ind=T['ind'],
                          div=div_level(ev), importance=ev_importance(ev), one_sided=one_sided(ev), roundup=bool(c.get('roundup')),
                          first_published=c.get('first_published_ts') or '', start=ev['start'].isoformat(),
                          url=absu(path('ru', ('event', ev['slug']))), og=OG_RU.get(ev['slug'])))
    days = {}
    for d in DIGEST_DAYS[-3:]:
        top, n = digest_events(d)
        days[d.isoformat()] = dict(url=absu(path('ru', ('day', d))), n=n, top=[content('ru', x)['t'] for x in top[:5]])
    tmp = os.path.join(HERE, 'data', 'tg_feed.json.tmp')
    json.dump(dict(generated=dt.datetime.now().astimezone().isoformat(timespec='seconds'), base=BASE, cards=cards, digests=days), open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, os.path.join(HERE, 'data', 'tg_feed.json'))

def build_redirects():
    """/assets/redirects.json: old URL -> new URL (301) for cards merged into another card (duplicates; pipeline/dedup.py and the
    clustering merge pass). Applied by functions/_middleware.js only when the static file is missing (404), so live pages always win."""
    try: data = json.load(open(os.path.join(HERE, 'data', 'cards_auto.json'), encoding='utf-8'))
    except (OSError, ValueError): data = {}
    live = {ev['slug'] for ev in EV}
    m = {}
    for frm, to in data.get('redirects') or []:
        if frm in live or to not in live: continue
        for lang in LANGS: m[path(lang, ('event', frm))] = path(lang, ('event', to))
    write('/assets/redirects.json', json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    print('redirects: %d merged-card URLs -> main card' % len(m))

def main():
    nq = NQ
    _USED_TITLES.clear(); _USED_DESCS.clear(); PAGES.clear()
    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):                      # clean contents, keep the directory (a server may be running in it)
        p = os.path.join(OUT, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    shutil.copytree(os.path.join(HERE, 'src', 'assets'), os.path.join(OUT, 'assets'))
    os.makedirs(os.path.join(OUT, 'v'), exist_ok=True)
    shutil.copy2(os.path.join(HERE, 'src', 'assets', 'filter.js'), os.path.join(OUT, JS_FILTER.lstrip('/')))
    shutil.copy2(os.path.join(HERE, 'src', 'assets', 'theme.js'), os.path.join(OUT, JS_THEME.lstrip('/')))
    shutil.copy2(os.path.join(HERE, 'src', 'assets', 'map.js'), os.path.join(OUT, JS_MAP.lstrip('/')))
    shutil.copy2(os.path.join(HERE, 'src', 'assets', 'support.js'), os.path.join(OUT, JS_SUPPORT.lstrip('/')))
    shutil.copy2(os.path.join(HERE, 'src', 'assets', 'search.js'), os.path.join(OUT, JS_SEARCH.lstrip('/')))
    # Cloudflare Pages response headers: hashed files are immutable; plain assets a day; HTML/JSON revalidate (default)
    write('/_headers', '/v/*\n  Cache-Control: public, max-age=31536000, immutable\n'
                       '/assets/*\n  Cache-Control: public, max-age=86400, stale-while-revalidate=604800\n'
                       '/*/rss.xml\n  Content-Type: application/rss+xml; charset=utf-8\n')
    sd = os.path.join(HERE, 'src', 'static')                 # root-level static files (e.g. IndexNow key)
    if os.path.isdir(sd):
        for f in os.listdir(sd): shutil.copy2(os.path.join(sd, f), os.path.join(OUT, f))
    for lang in LANGS:
        build_home(lang); build_about(lang); build_corrections(lang); build_support(lang); build_search(lang); build_glossary(lang);
        if WEEKS:
            build_weeks(lang)
            for wk in WEEKS: build_week(lang, wk)
        build_regions(lang); build_dates(lang)
        for k in REGION_KEYS: build_region(lang, k)
        build_topics(lang)
        for t in HUBS: build_topic(lang, t); build_rss(lang, t['slug'])
        build_rss(lang)
        build_map(lang)
        build_divergence(lang)
        for d in DAYS: build_date(lang, d)
        build_days(lang)
        for d in DIGEST_DAYS: build_digest(lang, d)
        for ev in EV: build_event(lang, ev)
    build_root(); build_404(); og_save()
    print('OG images: %d rendered, %d cached' % (OG_STATS['rendered'], OG_STATS['cached']))
    nsm = build_sitemap()
    build_redirects()
    try: build_tg_feed()
    except Exception as ex: print('WARNING: tg_feed.json not written: %r' % (ex,))
    nnews = build_news_sitemap()
    print('news sitemap: %d URLs (cards first published in the last %dh x %d languages)' % (nnews, NEWS_HOURS, len(LANGS)))
    write('/robots.txt', 'User-agent: *\nAllow: /\n\nSitemap: {b}/sitemap-index.xml\nSitemap: {b}/sitemap.xml\nSitemap: {b}/news-sitemap.xml\n'.format(b=BASE))
    total = len(PAGES) + 1
    print('BASE_URL = %s' % BASE)
    print('topic hubs: %d per language (%s)' % (len(HUBS), ', '.join('%s=%d' % (t['slug'], len(TOPIC_EVS[t['slug']])) for t in HUBS)))
    print('OK: %d quotes verified verbatim; %d events x %d languages; %d HTML pages (+ root + 404); %d sitemap URLs; output: %s' % (nq, len(EV), len(LANGS), len(PAGES), nsm, OUT))
    if '--no-check' not in sys.argv:
        sys.path.insert(0, os.path.join(HERE, 'tools'))
        import check, check_rss
        rc = check.run(OUT, BASE)
        nr = bad = 0
        for d_, _, fs in os.walk(OUT):
            for f in fs:
                if f == 'rss.xml':
                    nr += 1; errs = check_rss.check(os.path.join(d_, f))
                    if errs: bad += 1; print('WARNING: RSS %s: %s' % (os.path.relpath(os.path.join(d_, f), OUT), errs[:3]))
        print('RSS: %d feeds, %d with problems' % (nr, bad))
        sys.exit(rc)

if __name__ == '__main__':
    main()
