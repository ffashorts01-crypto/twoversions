# -*- coding: utf-8 -*-
"""Incremental, rate-limited article collection. Reuses the v1 logic (compare-site/scripts/collect.py) and adds RSS-based sources.
Only articles that are not yet in the DB (by URL) are fetched. Every host is throttled and robots.txt is honoured for article/listing pages."""
import warnings, re, time, html, json, threading, datetime as dt, urllib.robotparser, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import requests
from bs4 import BeautifulSoup
from .common import CFG, BERLIN, UTC, now_iso, log

from bs4 import XMLParsedAsHTMLWarning
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36 (compare-site research bot; non-commercial)'
MSK = __import__('zoneinfo').ZoneInfo('Europe/Moscow')

# military filter used since v1 (title + first 500 chars) -- deliberately broad; the LLM clustering leaves irrelevant items unassigned
KW = re.compile(r"""удар|обстрел|обстріл|атак|дрон|БПЛА|БпЛА|безпілот|беспилот|ракет|ПВО|ППО|сбит|збит|фронт|наступ|наступл|освобожд|звільн|погиб|загин|убит|ранен|поранен|потер|втрат|ВСУ|ЗСУ|Сил оборони|Генштаб|Минобороны|Міноборони|армия|армі|військ|военн|воен|бой|бої|бойов|штурм|окруж|оборон|взрыв|вибух|Харьк|Харк|Купянск|Куп'ян|Покровск|Покровськ|Запорож|Запоріж|Сумы|Суми|Херсон|Донецк|Донеччин|Крым|Крим|Одесс|Одес|Киев|Київ|Києв|Днепр|Дніпр|Николаев|Миколаїв|НПЗ|энергетик|енергетик|Pokrovsk|Kupiansk|Kharkiv|Zaporizh|Kherson|Sumy|Donetsk|Odesa|Dnipro|Kyiv|Crimea|drone|missile|strike|attack|shell|frontline|front line|offensive|troops|killed|wounded|casualt|air defen|Ukrainian forces|Russian forces|Ministry of Defense|war|Lyman|Huliaipole|Kostiantynivka|Chasiv|Siversk|Vovchansk|Bakhmut|Kursk|Belgorod|Bryansk|refinery|Moldova|Romania""", re.I)
# generic (non-Ukraine-specific) feeds are pre-filtered with this to avoid fetching unrelated pages
UA_REL = re.compile(r"Украин|Україн|Ukrain|Росси|Russia|Росі|Киев|Київ|Києв|Kyiv|Kiev|ВСУ|ЗСУ|Москв|Moscow|Кремл|Kremlin|Зеленск|Zelensk|Путин|Putin|СВО\b|спецоперац|Донбас|Donbas|Крым|Крим|Crimea|Харьков|Харків|Харков|Kharkiv|Одесс|Одес|Odesa|Запорож|Запоріж|Zaporizh|Херсон|Kherson|Белгород|Belgorod|Курск|Kursk|Брянск|Bryansk|НАТО|NATO|дрон|drone|БПЛА|беспилот|Sumy|Сумы|Суми|Донецк|Donetsk|Днепр|Dnipro|Moldova|Молдов", re.I)

# keyword filter for URL slugs (sitemaps without titles): English + Latin transliteration of ru/uk
SLUG_REL = re.compile(r'ukrain|russi|rossi|rosi|kyiv|kiev|kyiev|kiiv|kyyiv|kharkiv|harkiv|harkov|kharkov|zelensk|zelensk|putin|kremlin|drone|dron|bpla|raket|missile|udar|ataka|atak|obstril|obstrel|vibuh|vzryv|front|zsu|vsu|odes|zaporiz|zaporoz|dnipr|dnepr|sumy|sumi|kherson|herson|mykola|mikola|nikola|donetsk|donbas|pokrovsk|kupyan|kupiansk|crimea|krim|krym|belgorod|bilgorod|kursk|bryansk|briansk|moscow|moskv|nato|ppo|pvo|energ|nafta|npz|refiner|war|vijn|voin|vojn|ceasefire|peremir|sanction|sankc|prisoner|obmin|obmen|polon|plen', re.I)

# --------------------------------------------------------------------------------------------------- HTTP
class Http:
    def __init__(self, delay):
        self.s = requests.Session(); self.s.headers['User-Agent'] = UA
        self.delay = delay; self.last = {}; self.lock = threading.Lock(); self.robots = {}
        self.errors = []; self.blocked_robots = set(); self.stat = {}
    def _wait(self, host):
        with self.lock:
            t = time.time(); nxt = max(t, self.last.get(host, 0) + self.delay); self.last[host] = nxt
        if nxt > t: time.sleep(nxt - t)
    def robots_ok(self, url):
        u = urllib.parse.urlsplit(url); host = u.scheme + '://' + u.netloc
        with self.lock:
            rp = self.robots.get(host)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._wait(u.netloc)
                r = self.s.get(host + '/robots.txt', timeout=20)
                if r.status_code == 200 and 'html' not in r.headers.get('content-type', '').lower(): rp.parse(r.text.splitlines())
                else: rp.parse([])            # no robots.txt (or an HTML error page) -> everything allowed
            except requests.RequestException: rp.parse([])
            with self.lock: self.robots[host] = rp
        ok = rp.can_fetch('*', url)
        if not ok: self.blocked_robots.add(u.netloc)
        return ok
    def get(self, url, check_robots=True, **kw):
        host = urllib.parse.urlsplit(url).netloc
        if check_robots and not self.robots_ok(url):
            self.errors.append((url, 'robots')); return None
        for i in range(4):
            self._wait(host)
            try:
                r = self.s.get(url, timeout=30, **kw)
            except requests.RequestException:
                time.sleep(1.5 * (i + 1)); continue
            if r.status_code == 200:
                enc = r.encoding if r.encoding and 'utf' in r.encoding.lower() else 'utf-8'
                r.encoding = enc
                return r.text
            if r.status_code == 429 and i < 3:
                time.sleep(min(60, int(r.headers.get('Retry-After', 0) or 0) or 8 * (i + 1))); continue
            self.errors.append((url, r.status_code)); return None
        self.errors.append((url, 'ERR')); return None

def soup(t): return BeautifulSoup(t, 'lxml')
def clean(s): return re.sub(r'\s+', ' ', html.unescape(s or '')).strip()
def ptext(ps): return '\n'.join(x for x in (clean(p.get_text(' ', strip=True)) for p in ps) if x)
def parse_iso(s):
    d = dt.datetime.fromisoformat(s.strip().replace('Z', '+00:00'))
    return d if d.tzinfo else d.replace(tzinfo=BERLIN)
def parse_rfc(s):
    from email.utils import parsedate_to_datetime
    d = parsedate_to_datetime(s.strip()); return d if d.tzinfo else d.replace(tzinfo=UTC)
def ld_date(h):
    m = re.search(r'"datePublished":\s*"([^"]+)"', h)
    try: return parse_iso(m.group(1)) if m else None
    except ValueError: return None
def meta(s, prop):
    m = s.find('meta', attrs={'property': prop}) or s.find('meta', attrs={'name': prop})
    return clean(m.get('content')) if m and m.get('content') else None
def rss_items(xml):
    out = []
    for it in re.findall(r'<item[ >].*?</item>', xml, re.S):
        def g(tag):
            m = re.search(r'<%s(?: [^>]*)?>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>' % (tag, tag), it, re.S)
            return m.group(1).strip() if m else ''
        out.append(dict(title=clean(g('title')), link=html.unescape(g('link')).strip(), date=g('pubDate') or g('dc:date'), desc=g('description'),
                        content=g('content:encoded'), fulltext=g('fulltext')))
    return out

# --------------------------------------------------------------------------------------------------- source registry
# group: ru / ua / ind (labels shown on the site).  origin: where the outlet comes from (used by the confidence scale).  Notes are honest descriptions.
SOURCES = {}
def source(key, name, group, lang, origin, note, access):
    def deco(f):
        SOURCES[key] = dict(key=key, name=name, group=group, lang=lang, origin=origin, note=note, access=access, fn=f); return f
    return deco

class Ctx:
    def __init__(self, http, db, since, until, stats):
        self.http, self.db, self.since, self.until, self.stats = http, db, since, until, stats
        self.known = {r[0] for r in db.execute('select url from articles')}
        self.out = []; self.lock = threading.Lock(); self.urls_seen = 0
    def new(self, urls):
        urls = [u for u in dict.fromkeys(urls) if u not in self.known]
        self.urls_seen += len(urls); return urls
    def add(self, source_name, group, lang, title, url, date, text, note=''):
        if not (title and url and date and text): return
        if date < self.since: return
        with self.lock:
            self.out.append(dict(source=source_name, group=group, lang=lang, title=title, url=url, date_utc=date.astimezone(UTC).isoformat(timespec='seconds'),
                                 date_local=date.astimezone(BERLIN).isoformat(timespec='minutes'), text=text, note=note))
    def pmap(self, f, items, n=4):
        with ThreadPoolExecutor(n) as ex: list(ex.map(f, items))
    def days(self):
        d = self.since.astimezone(BERLIN).date(); end = dt.datetime.now(BERLIN).date()
        while d <= end: yield d; d += dt.timedelta(days=1)

# ---------------- RIA Novosti
@source('ria', 'РИА Новости', 'ru', 'ru', 'ru', 'ria.ru: RSS + тег «Украина» + дневные архивы разделов + страницы статей (подгрузка /services/ запрещена robots.txt)', 'ria.ru RSS + daily archives')
def ria(c):
    urls = set(); since8 = c.since.astimezone(MSK).strftime('%Y%m%d')
    rss = c.http.get('https://ria.ru/export/rss2/archive/index.xml')
    if rss: urls |= set(re.findall(r'<link>(https://ria.ru/\d{8}/[^<]+)</link>', rss))
    # NOTE: ria.ru/robots.txt disallows /services/ (the infinite-scroll loader used in v1), so only the tag page, RSS and the plain daily
    # section archives (ria.ru/<section>/YYYYMMDD/, ~20 items each) are read. Coverage of RIA is therefore thinner than in v1.
    pages = ['https://ria.ru/location_Ukraine/']
    for d in c.days():
        for sec in ('location_Ukraine', 'world', 'defense_safety', 'politics'):
            pages.append('https://ria.ru/%s/%s/' % (sec, d.strftime('%Y%m%d')))
    for pg in pages:
        page = c.http.get(pg)
        if page: urls |= {a['href'] for a in soup(page).select('a.list-item__title')}
    urls = c.new(sorted(u for u in urls if re.search(r'/(\d{8})/', u) and re.search(r'/(\d{8})/', u).group(1) >= since8))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); pt = meta(s, 'article:published_time')
        if not pt: return
        d = dt.datetime.strptime(pt, '%Y%m%dT%H%M').replace(tzinfo=MSK)
        c.add('РИА Новости', 'ru', 'ru', meta(s, 'og:title'), u, d, ptext(s.select('div.article__text')))
    c.pmap(one, urls, 3)

# ---------------- TASS
@source('tass', 'ТАСС', 'ru', 'en', 'ru', 'tass.com: sitemap + страницы (EN); tass.ru: RSS (только заголовок и лид)', 'tass.com sitemaps + tass.ru RSS')
def tass(c):
    urls = {}
    # sitemap_news0 holds only the newest items; older ones are in news1..news9 (read until a file has nothing newer than since)
    for sm in ('hot',) + tuple('news%d' % i for i in range(10)):
        x = c.http.get('https://tass.com/sitemap/sitemap_%s.xml' % sm) or ''
        fresh = 0
        for u, l in re.findall(r'<loc>([^<]*)</loc>\s*<lastmod>([^<]*)', x):
            try:
                if parse_iso(l) >= c.since:
                    fresh += 1
                    if u.split('/')[3] in ('defense', 'politics', 'world', 'emergencies'): urls[u] = l
            except ValueError: pass
        if sm.startswith('news') and not fresh: break
    urls = c.new(list(urls))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); d = ld_date(h)
        t = s.select_one('h1.news-header__title'); title = clean(t.get_text()) if t else meta(s, 'og:title')
        if d: c.add('ТАСС (tass.com, EN)', 'ru', 'en', title, u, d, ptext(s.select('.text-block p')))
    c.pmap(one, urls, 3)
    x = c.http.get('https://tass.ru/rss/v2.xml') or ''
    for it in rss_items(x):
        if it['link'] in c.known: continue
        try: d = parse_rfc(it['date'])
        except Exception: continue
        lead = clean(it['desc'])
        c.add('ТАСС (tass.ru, RU, RSS)', 'ru', 'ru', it['title'], it['link'], d, (it['title'] + '. ' + lead) if lead else it['title'], note='только заголовок и лид из RSS')

# ---------------- Russian MoD via public Telegram preview
@source('mod', 'Минобороны РФ (Telegram)', 'ru', 'ru', 'ru', 'официальный канал @mod_russia через публичное веб-превью t.me/s/', 't.me/s/mod_russia')
def mod(c):
    before = None; stop = False
    for _ in range(60):
        h = c.http.get('https://t.me/s/mod_russia' + ('?before=%s' % before if before else ''), check_robots=False)
        if not h: break
        msgs = soup(h).select('.tgme_widget_message')
        if not msgs: break
        ids = []
        for m in msgs:
            post = m.get('data-post'); ids.append(int(post.split('/')[1]))
            te = m.select_one('.tgme_widget_message_text'); tm = m.select_one('time[datetime]')
            if not te or not tm: continue
            for br in te.find_all('br'): br.replace_with('\n')
            text = clean(te.get_text('\n').replace('\n', ' ')); d = parse_iso(tm['datetime'])
            if d < c.since: stop = True; continue
            url = 'https://t.me/' + post
            if url in c.known: continue
            c.urls_seen += 1
            c.add('Минобороны РФ (Telegram @mod_russia)', 'ru', 'ru', re.split(r'(?<=[.!?])\s', text)[0][:140], url, d, text)
        if stop or not ids: break
        before = min(ids)

# ---------------- Suspilne
@source('suspilne', 'Суспільне', 'ua', 'uk', 'ua', 'публичный медиа-вещатель Украины; sitemaps + RSS', 'suspilne.media sitemaps + RSS')
def suspilne(c):
    urls = set()
    for sm in ('post-sitemap0', 'post-sitemap1', 'post-sitemap3'):
        x = c.http.get('https://suspilne.media/suspilne/sitemap/%s.xml' % sm) or ''
        for u, l in re.findall(r'<loc>([^<]*)</loc>.*?<lastmod>([^<]*)</lastmod>', x, re.S):
            try:
                if parse_iso(l) >= c.since: urls.add(u)
            except ValueError: pass
    x = c.http.get('https://suspilne.media/suspilne/sitemap/news.xml') or ''
    urls |= set(re.findall(r'<loc>([^<]*)</loc>', x))
    x = c.http.get('https://suspilne.media/rss/all.rss') or ''
    urls |= set(re.findall(r'<link>(https://suspilne.media/[^<]+)</link>', x))
    urls = c.new(sorted(u for u in urls if re.search(r'/\d{6,}-', u)))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); pt = [m.get('content') for m in s.find_all('meta') if m.get('property') == 'article:published_time']
        d = parse_iso(pt[0]) if pt else ld_date(h); a = s.select_one('.post-body')
        if d: c.add('Суспільне', 'ua', 'uk', meta(s, 'og:title'), u, d, ptext(a.find_all('p')) if a else '')
    c.pmap(one, urls, 2)

# ---------------- Ukrainska Pravda
@source('upravda', 'Українська правда', 'ua', 'uk', 'ua', 'частное украинское издание; архив новостей по дням', 'pravda.com.ua/archives/date_DDMMYYYY/ + /news/')
def upravda(c):
    urls = set()
    for d in c.days():
        h = c.http.get('https://www.pravda.com.ua/archives/date_%s/' % d.strftime('%d%m%Y'))
        if h: urls |= set(re.findall(r'(?:href="|"url":\s*")((?:https://www\.pravda\.com\.ua)?/news/\d{4}/\d\d/\d\d/\d+/)"', h))
    # 2026-10-19: the daily archive page can lag (Oct 19 page had no links for ~10 h) -> also read the /news/ feed page;
    # only links dated on/after the collection start (the page also carries old "related" links)
    h = c.http.get('https://www.pravda.com.ua/news/') or ''
    d0 = c.since.astimezone(BERLIN).date() - dt.timedelta(days=1)
    for u, y, m, dd in re.findall(r'(?:href="|"url":\s*")((?:https://www\.pravda\.com\.ua)?/news/(\d{4})/(\d\d)/(\d\d)/\d+/)"', h):
        if dt.date(int(y), int(m), int(dd)) >= d0: urls.add(u)
    urls = c.new(sorted({u if u.startswith('http') else 'https://www.pravda.com.ua' + u for u in urls}))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); d = ld_date(h); t = s.find('h1'); b = s.select_one('div.post_news_body')
        if d and b: c.add('Українська правда', 'ua', 'uk', clean(t.get_text()) if t else None, u, d, ptext(b.find_all('p')))
    c.pmap(one, urls, 2)

# ---------------- BBC
@source('bbc', 'BBC News', 'ind', 'en', 'intl', 'общественный вещатель Великобритании (финансируется лицензионным сбором); RSS (~100 свежих статей)', 'feeds.bbci.co.uk RSS')
def bbc(c):
    urls = {}
    for f in ('news/world/europe', 'news/world', 'news', 'russian'):
        x = c.http.get('https://feeds.bbci.co.uk/%s/rss.xml' % f) or ''
        for l in re.findall(r'<link>([^<]+)</link>', x):
            if '/articles/' in l: urls[l.split('?')[0]] = 1
    h = c.http.get('https://www.bbc.co.uk/news/war-in-ukraine') or ''
    for l in set(re.findall(r'href="(/news/articles/[a-z0-9]+)"', h)): urls['https://www.bbc.co.uk' + l] = 1
    urls = c.new(list(urls))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); d = ld_date(h); a = s.find('article'); t = s.find('h1')
        if not (a and d): return
        ru = '/russian/' in u
        ps = [p for p in a.find_all('p') if not p.find_parent(attrs={'data-component': re.compile('links|related|byline|topic')})]
        c.add('BBC News' + (' Русская служба' if ru else ''), 'ind', 'ru' if ru else 'en', clean(t.get_text()) if t else meta(s, 'og:title'), u, d, ptext(ps))
    c.pmap(one, urls, 3)

# ---------------- AP
KW_AP = re.compile(r'ukrain|russia|kyiv|kharkiv|zelensk|putin|drone|moscow|kremlin|donetsk|odesa|black sea|nato', re.I)
# DISABLED 2026-10-18: apnews.com returns HTTP 403 (Cloudflare bot challenge) to sitemap/hub/article pages since 2026-10-04; replaced by Moscow Times + France 24
# @source('ap', 'Associated Press', 'ind', 'en', 'intl', 'международное агентство (кооператив американских СМИ); sitemap + хаб', 'apnews.com news-sitemap + hub')
def ap(c):
    urls = set()
    x = c.http.get('https://apnews.com/news-sitemap-content.xml') or ''
    for blk in re.findall(r'<url>(.*?)</url>', x, re.S):
        u = re.search(r'<loc>([^<]+)', blk); t = re.search(r'<news:title>([^<]*)', blk)
        if u and '/article/' in u.group(1) and t and KW_AP.search(html.unescape(t.group(1))): urls.add(u.group(1))
    h = c.http.get('https://apnews.com/hub/russia-ukraine') or ''
    urls |= set(re.findall(r'href="(https://apnews.com/article/[^"]+)"', h))
    urls = c.new(sorted(urls))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); d = ld_date(h) or (parse_iso(meta(s, 'article:published_time')) if meta(s, 'article:published_time') else None)
        b = s.select_one('div.RichTextStoryBody')
        if d and b: c.add('Associated Press', 'ind', 'en', meta(s, 'og:title'), u, d, ptext(b.find_all('p')))
    c.pmap(one, urls, 3)

# ---------------- Kyiv Independent
@source('kyivind', 'Kyiv Independent', 'ind', 'en', 'ua', 'украинское частное англоязычное издание (позиция может быть проукраинской); sitemap + RSS', 'kyivindependent.com sitemap-posts.xml')
def kyivind(c):
    urls = set()
    x = c.http.get('https://kyivindependent.com/sitemap-posts.xml') or ''
    for u, l in re.findall(r'<loc>([^<]*)</loc><lastmod>([^<]*)', x):
        try:
            if parse_iso(l) >= c.since: urls.add(u)
        except ValueError: pass
    x = c.http.get('https://kyivindependent.com/news-archive/rss/') or ''
    urls |= set(re.findall(r'<link>(https://kyivindependent.com/[^<]+)</link>', x))
    urls = c.new(sorted(u for u in urls if u.count('/') == 4))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); d = ld_date(h); a = s.find('article')
        if not (a and d): return
        t = s.find('h1'); c.add('Kyiv Independent', 'ind', 'en', clean(t.get_text()) if t else meta(s, 'og:title'), u, d, ptext(a.find_all('p')))
    c.pmap(one, urls, 3)

# ================================================================== NEW SOURCES (v2 auto-update)
def feed_pages(c, feeds, select, source_name, group, lang, filt=None, link_ok=None, title_sel='h1', date_fn=None, n=2, extra_urls=()):
    """Generic: read RSS feed(s), keep new links matching the filter, fetch article pages and extract <select> paragraphs."""
    items = {}
    for f in feeds:
        x = c.http.get(f)
        for it in rss_items(x or ''):
            lk = it['link'].split('?')[0]
            if not lk.startswith('http') or (link_ok and not link_ok(lk)): continue
            if filt and not filt.search(it['title'] + ' ' + clean(re.sub('<[^>]+>', ' ', it['desc']))): continue
            items[lk] = it
    for u in extra_urls:          # archive/sitemap URLs (already filtered by the caller); date comes from the page itself
        u = u.split('?')[0]
        if u not in items and (not link_ok or link_ok(u)): items[u] = dict(title='', link=u, date='', desc='')
    urls = c.new(list(items))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); d = (date_fn(h, s) if date_fn else None) or ld_date(h)
        if not d and items[u]['date']:
            try: d = parse_rfc(items[u]['date'])
            except Exception: d = None
        t = s.find(title_sel); ps = s.select(select)
        text = ptext(ps)
        if d and text: c.add(source_name, group, lang, clean(t.get_text()) if t else items[u]['title'], u, d, text)
    c.pmap(one, urls, n)

@source('meduza', 'Meduza', 'ind', 'ru', 'exile', 'независимое издание в изгнании (юрисдикция Латвии), русская и английская версии; RSS с полным текстом; в РФ признано «нежелательной организацией»', 'meduza.io/rss/all, /rss/en/all')
def meduza(c):
    for feed, lang in (('https://meduza.io/rss/all', 'ru'), ('https://meduza.io/rss/en/all', 'en')):
        x = c.http.get(feed) or ''
        for it in rss_items(x):
            if it['link'] in c.known or '/news/' not in it['link'] and '/feature/' not in it['link']: continue
            if not UA_REL.search(it['title'] + ' ' + clean(re.sub('<[^>]+>', ' ', it['desc']))): continue
            c.urls_seen += 1
            ps = soup(it['content'] or it['desc']).find_all('p')
            try: d = parse_rfc(it['date'])
            except Exception: continue
            c.add('Meduza' + (' (EN)' if lang == 'en' else ''), 'ind', lang, it['title'], it['link'], d, ptext(ps))

@source('guardian', 'The Guardian', 'ind', 'en', 'intl', 'британская газета (принадлежит Scott Trust); RSS-лента «Украина» + страницы', 'theguardian.com/world/ukraine/rss')
def guardian(c):
    extra = []
    for d in c.days():     # daily tag archive (the RSS keeps only ~20 items): /world/ukraine/YYYY/mon/DD/all
        h = c.http.get('https://www.theguardian.com/world/ukraine/%s/all' % d.strftime('%Y/%b/%d').lower()) or ''
        extra += re.findall(r'href="(https://www\.theguardian\.com/[a-z-]+(?:/[a-z-]+)?/\d{4}/[a-z]{3}/\d\d/[^"#?]+)"', h)
    feed_pages(c, ['https://www.theguardian.com/world/ukraine/rss'], 'div[data-gu-name=body] p', 'The Guardian', 'ind', 'en', link_ok=lambda u: '/video/' not in u and '/gallery/' not in u and '/audio/' not in u and '/picture/' not in u and not re.search(r'/(all|altdate)$', u) and '/live/' not in u,
               extra_urls=extra)

@source('dw', 'DW', 'ind', 'en', 'intl', 'международный вещатель Германии (финансируется из федерального бюджета, редакционная независимость закреплена законом); RSS EN и RU + страницы', 'rss.dw.com/xml/rss-en-all, rss-ru-all')
def dw(c):
    for feed, lang, pat in (('https://rss.dw.com/xml/rss-en-all', 'en', '/en/'), ('https://rss.dw.com/xml/rss-ru-all', 'ru', '/ru/')):
        extra = []
        x = c.http.get('https://www.dw.com/%s/article-sitemap.xml' % lang) or ''     # article sitemap (lastmod, no titles) -> URL slug keyword filter
        for u, l in re.findall(r'<loc>([^<]+)</loc>\s*<lastmod>([^<]+)', x):
            try:
                if parse_iso(l) >= c.since and SLUG_REL.search(u): extra.append(u)
            except ValueError: pass
        feed_pages(c, [feed], 'div.rich-text p', 'DW' + (' (RU)' if lang == 'ru' else ''), 'ind', lang, filt=UA_REL, link_ok=lambda u, p=pat: p in u and '/video' not in u and '/a-' in u, extra_urls=extra)

@source('aljazeera', 'Al Jazeera', 'ind', 'en', 'intl', 'катарский вещатель (государственное финансирование Катара); RSS + страницы', 'aljazeera.com/xml/rss/all.xml')
def aljazeera(c):
    extra = []
    months = sorted({(d.year, d.month) for d in c.days()})
    for y, m in months:   # monthly article archive sitemap; URL slugs are filtered by keyword because the sitemap has no titles
        x = c.http.get('https://www.aljazeera.com/sitemaps/article-archive/%d/%02d.xml' % (y, m)) or ''
        for u in re.findall(r'<loc>([^<]+)</loc>', x):
            mm = re.search(r'/news/(\d{4})/(\d{1,2})/(\d{1,2})/', u)
            if mm and dt.date(int(mm.group(1)), int(mm.group(2)), int(mm.group(3))) >= c.since.astimezone(BERLIN).date() - dt.timedelta(days=1) and SLUG_REL.search(u): extra.append(u)
    feed_pages(c, ['https://www.aljazeera.com/xml/rss/all.xml'], '.wysiwyg p', 'Al Jazeera', 'ind', 'en', filt=UA_REL, link_ok=lambda u: '/news/' in u, extra_urls=extra)

@source('rbcua', 'РБК-Україна', 'ua', 'uk', 'ua', 'частное украинское издание (RBC-Ukraine); RSS с полным текстом', 'rbc.ua/static/rss/newsline.ukr.rss.xml')
def rbcua(c):
    x = c.http.get('https://www.rbc.ua/static/rss/newsline.ukr.rss.xml') or ''
    for it in rss_items(x):
        if it['link'] in c.known or 'wave.rbc.ua' in it['link'] or not UA_REL.search(it['title'] + ' ' + clean(it['desc'])): continue
        c.urls_seen += 1
        body = soup(html.unescape(it['fulltext'])).find_all(['p']) if it['fulltext'] else []
        text = ptext(body) or clean(it['desc'])
        try: d = parse_rfc(it['date'])
        except Exception: continue
        c.add('РБК-Україна', 'ua', 'uk', it['title'], it['link'], d, text)
    # older items: weekly sitemaps (the RSS keeps only ~1-2 days). The page language follows the site's own setting (often ru), stored as such.
    idx = c.http.get('https://www.rbc.ua/static/sitemap/sitemap.index.xml') or ''
    maps = ['https://www.rbc.ua/static/sitemap/lastweekukr.xml']
    for sm, a, b in re.findall(r'<loc>(https://www\.rbc\.ua/static/sitemap/week/week(\d{8})-(\d{8})ukr\.xml)</loc>', idx):
        if dt.datetime.strptime(b, '%Y%m%d').date() >= c.since.astimezone(BERLIN).date(): maps.append(sm)
    urls = set()
    for sm in maps:
        x = c.http.get(sm) or ''
        urls |= {u for u in re.findall(r'<loc>([^<]+)</loc>', x) if u.startswith('https://www.rbc.ua/ukr/news/') and SLUG_REL.search(u)}
    urls = c.new(sorted(urls))
    def one(u):
        h = c.http.get(u)
        if not h: return
        s = soup(h); d = ld_date(h); t = s.find('h1'); ps = s.select('div.txt p')
        lang = (s.find('html').get('lang') or 'uk')[:2] if s.find('html') else 'uk'
        if d and ps: c.add('РБК-Україна', 'ua', 'ru' if lang == 'ru' else 'uk', clean(t.get_text()) if t else meta(s, 'og:title'), u, d, ptext(ps))
    c.pmap(one, urls, 2)

@source('interfax', 'Интерфакс', 'ru', 'ru', 'ru', 'частное российское информагентство (лояльно к власти; в РФ работает легально); RSS + страницы', 'interfax.ru/rss.asp')
def interfax(c):
    extra = []
    for d in c.days():      # daily news archive /news/YYYY/MM/DD/page_N (links only; the military keyword flag filters later)
        for pg in range(1, 9):
            h = c.http.get('https://www.interfax.ru/news/%s/%s' % (d.strftime('%Y/%m/%d'), '' if pg == 1 else 'page_%d' % pg))
            if not h: break
            for path, title in re.findall(r'<a href="(/(?:russia|world|politics|moscow)/\d+)"[^>]*>\s*(?:<h3>)?\s*([^<]{10,300})', h):
                if UA_REL.search(title) or KW.search(title): extra.append('https://www.interfax.ru' + path)
            if 'page_%d' % (pg + 1) not in h: break
    feed_pages(c, ['https://www.interfax.ru/rss.asp'], 'article p', 'Интерфакс', 'ru', 'ru', filt=UA_REL,
               link_ok=lambda u: re.search(r'interfax\.ru/(russia|world|politics|moscow)/\d+', u) is not None, extra_urls=extra)

@source('moscowtimes', 'The Moscow Times', 'ind', 'en', 'exile', 'независимое англоязычное издание в изгнании (Амстердам); в РФ признано «нежелательной организацией»; RSS + месячная карта сайта', 'themoscowtimes.com RSS + static sitemap')
def moscowtimes(c):
    extra = []
    months = sorted({(d.year, d.month) for d in c.days()})
    for y, m in months:
        x = c.http.get('https://static.themoscowtimes.com/sitemap/%d-%d.xml' % (y, m)) or ''
        for u in re.findall(r'<loc>([^<]+)</loc>', x):
            mm = re.search(r'/(\d{4})/(\d\d)/(\d\d)/([^/]+)$', u)
            if not mm or not SLUG_REL.search(mm.group(4)): continue
            if dt.date(int(mm.group(1)), int(mm.group(2)), int(mm.group(3))) >= c.since.date() - dt.timedelta(days=1): extra.append(u)
    feed_pages(c, ['https://www.themoscowtimes.com/rss/news'], 'div.article__content p', 'The Moscow Times', 'ind', 'en', filt=UA_REL,
               link_ok=lambda u: re.search(r'themoscowtimes\.com/\d{4}/\d\d/\d\d/', u) is not None, extra_urls=extra)

@source('france24', 'France 24', 'ind', 'en', 'intl', 'международный вещатель Франции (France Médias Monde, финансируется государством); RSS разделов', 'france24.com RSS')
def france24(c):
    feed_pages(c, ['https://www.france24.com/en/europe/rss', 'https://www.france24.com/en/rss'], 'div.t-content__body p', 'France 24', 'ind', 'en', filt=UA_REL,
               link_ok=lambda u: re.search(r'france24\.com/en/[a-z-]+/\d{8}-', u) is not None and '-live-' not in u)

@source('mediazona', 'Медиазона', 'ind', 'ru', 'exile', 'независимое издание (основано в 2014 г., работает из-за рубежа); в РФ признано «иностранным агентом»; RSS + страницы', 'zona.media/rss')
def mediazona(c):
    feed_pages(c, ['https://zona.media/rss'], '.mz-publish__text p', 'Медиазона', 'ind', 'ru', filt=UA_REL,
               link_ok=lambda u: re.search(r'zona\.media/(news|article)/\d{4}/\d\d/\d\d/', u) is not None)

@source('euronews', 'Euronews', 'ind', 'en', 'intl', 'европейский телеканал (основной владелец — Alpac Capital, Португалия; софинансирование ЕС); RSS EN + страницы (русская версия ru.euronews.com отвечает 406)', 'euronews.com/rss')
def euronews(c):
    feed_pages(c, ['https://www.euronews.com/rss'], '.c-article-content p', 'Euronews', 'ind', 'en', filt=UA_REL,
               link_ok=lambda u: re.search(r'euronews\.com/(?:[a-z-]+/)?\d{4}/\d\d/\d\d/', u) is not None and '/video/' not in u)

PREFIX = dict(ria='РИА', tass='ТАСС', mod='Минобороны', suspilne='Суспільне', upravda='Українська', bbc='BBC', ap='Associated', kyivind='Kyiv Independent',
              meduza='Meduza', guardian='The Guardian', dw='DW', aljazeera='Al Jazeera', rbcua='РБК', interfax='Интерфакс', moscowtimes='The Moscow Times', france24='France 24',
              mediazona='Медиазона', euronews='Euronews')

# sources that were tested and are NOT used (kept here so the About page and README can report them)
NOT_USED = [
    dict(name='Reuters', group='ind', status='bad', why='HTTP 401 (требуется JS/cookies)'),
    dict(name='Ukrinform', group='ua', status='bad', why='ukrinform.ua/.net возвращают 403 на любые запросы'),
    dict(name='Коммерсантъ', group='ru', status='bad', why='RSS отдаётся, но robots.txt запрещает автоматическое чтение страниц статей — не используется'),
    dict(name='Lenta.ru', group='ru', status='bad', why='robots.txt запрещает автоматическое чтение страниц статей — не используется'),
    dict(name='РБК (rbc.ru)', group='ru', status='bad', why='HTTP 401'),
    dict(name='ISW', group='ind', status='mid', why='страницы отчётов отдаются, но это аналитические дайджесты, а не репортажи об отдельных событиях — не используется'),
    dict(name='Associated Press', group='ind', status='bad', why='с 4.10.2026 apnews.com отвечает HTTP 403 (защита Cloudflare от ботов) на карту сайта, хаб и статьи; обходить защиту не стали — заменён на The Moscow Times и France 24'),
    dict(name='UNIAN', group='ind', status='na', why='RSS отдаётся, но не включены (достаточно других источников в группе; можно добавить позже)'),
]

def run_collect(db, run_id, since_default, only=None, dry_run=False, until=None, force_since=None):
    """Collect new articles. Returns dict(per_source stats). since per source = latest stored article of that source minus 3 h (or since_default)."""
    http = Http(CFG['HTTP_DELAY']); res = {}
    until = until or dt.datetime.now(BERLIN)
    for key, S in SOURCES.items():
        if only and key not in only: continue
        last = db.execute('select max(date_utc) from articles where source like ?', (PREFIX[key] + '%',)).fetchone()[0]
        since = (parse_iso(last) - dt.timedelta(hours=3)) if last else since_default
        if force_since: since = force_since
        if CFG.get('NEW_ONLY_SINCE'): since = max(since, parse_iso(CFG['NEW_ONLY_SINCE']))   # new-news-only: never fetch the backlog before the cutoff
        c = Ctx(http, db, since, until, res); t0 = time.time(); e0 = len(http.errors)
        try:
            S['fn'](c)
            err = None
        except Exception as e:
            err = '%s: %s' % (type(e).__name__, e); log.error('collect %s failed: %s', key, err)
        rows = []
        seen = set()
        for a in sorted(c.out, key=lambda a: a['date_utc']):
            if a['url'] in seen: continue
            if CFG.get('NEW_ONLY_SINCE') and a['date_utc'] < CFG['NEW_ONLY_SINCE']: continue   # backlog before the new-news-only cutoff
            seen.add(a['url'])
            rows.append((a['source'], a['group'], a['lang'], a['title'], a['url'], a['date_utc'], a['date_local'], a['text'][:400], a['text'][:6000], a['note'],
                         1 if KW.search(a['title'] + ' ' + a['text'][:500]) else 0))
        n = 0
        if not dry_run and rows:
            cur = db.executemany('INSERT OR IGNORE INTO articles(source,grp,lang,title,url,date_utc,date_local,excerpt,text,note,military) VALUES(?,?,?,?,?,?,?,?,?,?,?)', rows)
            db.commit(); n = len(rows)
        nerr = len(http.errors) - e0
        res[key] = dict(new=n if not dry_run else 0, would_add=len(rows), urls_new=c.urls_seen, errors=nerr, secs=round(time.time() - t0), error=err, since=since.astimezone(BERLIN).isoformat(timespec='minutes'))
        log.info('collect %-9s since %s: new urls %4d, stored %4d, http errors %3d, %ds%s', key, res[key]['since'], c.urls_seen, n if not dry_run else len(rows), nerr, res[key]['secs'], ' [DRY]' if dry_run else '')
        if not dry_run:
            db.execute('insert into source_log(ts,run_id,source,new_articles,urls_seen,errors,note) values(?,?,?,?,?,?,?)', (now_iso(), run_id, key, n, c.urls_seen, nerr, err))
            db.commit()
    res['_robots_blocked'] = sorted(http.blocked_robots)
    if http.blocked_robots: log.warning('robots.txt blocked automated access to: %s', ', '.join(sorted(http.blocked_robots)))
    res['_http_errors'] = len(http.errors)
    return res
