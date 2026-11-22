#!/usr/bin/env python3
"""Telegram auto-posting for twoversions.com -> channel @twoversions_war (no LLM calls).

Called by pipeline/auto_run.py after a successful deploy + live 200 check; also standalone:
    python3 pipeline/telegram_post.py --dry-run            # show what would be posted, touch nothing
    python3 pipeline/telegram_post.py --init               # mark every currently published card as already posted (no backfill)
    python3 pipeline/telegram_post.py --one                # post exactly one card (the most important new one) -- setup test
    python3 pipeline/telegram_post.py [--fast]             # normal run (as auto_run does); --fast = hot_check fast path (max 1 post)

Input: data/tg_feed.json (written by build.py: Russian texts, importance, divergence, OG image). State: data/telegram_posted.json
({"cards": {event_id: {...}}, "digests": {date: {...}}}). Posting is OFF unless data/telegram_enabled exists (kill switch: delete it).
Token: env TELEGRAM_BOT_TOKEN only -- never printed or logged (error texts are scrubbed). Never raises into the caller."""
import os, sys, json, time, html, re, datetime as dt, urllib.request, urllib.parse, urllib.error, uuid
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data'); LOGS = os.path.join(ROOT, 'logs')
FEED = os.path.join(DATA, 'tg_feed.json'); STATE = os.path.join(DATA, 'telegram_posted.json'); ENABLED = os.path.join(DATA, 'telegram_enabled')
CHAT = os.environ.get('TELEGRAM_CHAT', '@twoversions_war')
BERLIN = ZoneInfo('Europe/Berlin')
MIN_SCORE = float(os.environ.get('TG_MIN_SCORE', '150'))          # build.ev_importance; both sides + a topic ~ 150+
ONESIDE_MIN = float(os.environ.get('TG_ONESIDE_MIN', '190'))      # one-sided cards only when very important
# roundup cards: same thresholds, and only once the day is mostly complete (after 18:00 Berlin or a past day)
MAX_RUN, MAX_FAST, MAX_DAY = int(os.environ.get('TG_MAX_RUN', '3')), 1, int(os.environ.get('TG_MAX_DAY', '12'))
FRESH_H = 24            # never post a card first published more than this ago (no backfill even if state is lost)
DIGEST_HOUR = 21
DIV_RU = {'agree': 'версии совпадают', 'details': 'версии расходятся в деталях', 'contradict': 'версии противоречат друг другу'}

def say(*a):
    line = '%s telegram: %s' % (dt.datetime.now(BERLIN).strftime('%Y-%m-%d %H:%M:%S'), ' '.join(str(x) for x in a))
    tok = os.environ.get('TELEGRAM_BOT_TOKEN')
    if tok: line = line.replace(tok, '***')
    line = re.sub(r'bot\d+:[A-Za-z0-9_-]{20,}', 'bot***', line)
    print(line, flush=True)
    try:
        with open(os.path.join(LOGS, 'telegram.log'), 'a', encoding='utf-8') as f: f.write(line + '\n')
    except OSError: pass

def load_state():
    try: s = json.load(open(STATE, encoding='utf-8'))
    except (OSError, ValueError): s = None
    return s
def save_state(s):
    tmp = STATE + '.tmp'; json.dump(s, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1); os.replace(tmp, STATE)

# ------------------------------------------------------------------ Telegram API
def api(method, params=None, files=None, tries=4):
    tok = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not tok: raise RuntimeError('TELEGRAM_BOT_TOKEN not set')
    url = 'https://api.telegram.org/bot%s/%s' % (tok, method)
    for attempt in range(tries):
        if files:
            b = uuid.uuid4().hex; body = b''
            for k, v in (params or {}).items():
                body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode()
            for k, (fn, data, ct) in files.items():
                body += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n' % (b, k, fn, ct)).encode() + data + b'\r\n'
            body += ('--%s--\r\n' % b).encode(); hdr = {'Content-Type': 'multipart/form-data; boundary=' + b}
        else:
            body = json.dumps(params or {}).encode(); hdr = {'Content-Type': 'application/json'}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdr), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try: j = json.load(e)
            except Exception: j = {'ok': False, 'error_code': e.code, 'description': 'HTTP %d' % e.code}
            ra = (j.get('parameters') or {}).get('retry_after')
            if e.code == 429 and ra and attempt + 1 < tries:
                say('429 rate limit on %s, retry after %ss' % (method, ra)); time.sleep(min(int(ra) + 1, 120)); continue
            if e.code >= 500 and attempt + 1 < tries: time.sleep(5 * (attempt + 1)); continue
            return j
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt + 1 < tries: time.sleep(5 * (attempt + 1)); continue
            return {'ok': False, 'description': 'network: %s' % type(e).__name__}
    return {'ok': False, 'description': 'retries exhausted'}

# ------------------------------------------------------------------ formatting
def esc(s): return html.escape(s or '', quote=False)
def trim(s, n):
    s = re.sub(r'\s+', ' ', s or '').strip()
    if len(s) <= n: return s
    cut = s[:n - 1].rsplit(' ', 1)[0].rstrip(' ,;:–-—')
    return cut + '…'
def first_sent(s, n):
    s = re.sub(r'\s+', ' ', s or '').strip()
    m = re.search(r'(?<=[.!?])\s+(?=[«“"A-ZА-ЯЁІЇЄҐ])', s)
    if m and m.start() >= 40: s = s[:m.start()]
    return trim(s, n)
def card_text(c, n_side):
    link = c['url'] + '?utm_source=telegram'
    lines = ['<b>%s</b>' % esc(c['title'])]
    if c.get('one_sided'): lines.append('<i>Событие описывает только одна сторона — другой версии пока нет.</i>')
    if c.get('roundup'): lines.append('<i>Сводка за день: несколько однотипных событий.</i>')
    lines.append('')
    for k, lab in (('ru', '🇷🇺 Российские СМИ:'), ('ua', '🇺🇦 Украинские СМИ:'), ('ind', '🌐 Независимые СМИ:')):
        if c.get(k): lines.append('<b>%s</b> %s' % (lab, esc(first_sent(c[k], n_side))))
    if c.get('div') in DIV_RU: lines += ['', '⚖️ %s' % DIV_RU[c['div']].capitalize()]
    lines += ['', '<a href="%s">Сравнение полностью →</a>' % esc(link)]
    return '\n'.join(lines)
def fit_caption(c):
    """(text, is_caption): caption <= 1024 visible chars if possible by shortening the side lines; else a longer message text."""
    for n in (300, 240, 190, 150, 120):
        t = card_text(c, n)
        if len(re.sub(r'<[^>]+>', '', html.unescape(t))) <= 1000: return t, True
    return card_text(c, 400), False

def quiet(now): return now.hour >= 23 or now.hour < 7

# ------------------------------------------------------------------ selection
def candidates(feed, state, now):
    out = []
    for c in feed['cards']:
        if c['event_id'] in state['cards']: continue
        try: fp = dt.datetime.fromisoformat(c['first_published'])
        except ValueError: continue
        if now - fp > dt.timedelta(hours=FRESH_H): continue
        sc = c['importance']
        if c.get('roundup') and not (c['start'] < now.date().isoformat() or now.hour >= 18): continue
        if c.get('one_sided'):
            if sc < ONESIDE_MIN: continue
        elif sc < MIN_SCORE: continue
        out.append(c)
    out.sort(key=lambda c: (-c['importance'], c['first_published']))
    return out

def posted_today(state, now):
    d = now.date().isoformat()
    return sum(1 for v in state['cards'].values() if v.get('msg_id') and (v.get('at') or '')[:10] == d)

def post_card(c, now, dry):
    text, is_cap = fit_caption(c)
    silent = quiet(now)
    if dry:
        say('DRY would post %s (score %.0f, %s, silent=%s): %s' % (c['event_id'], c['importance'], 'photo' if is_cap else 'message', silent, c['title'][:80]))
        print(text); return None
    if is_cap and c.get('og'):
        r = api('sendPhoto', dict(chat_id=CHAT, photo=c['og'], caption=text, parse_mode='HTML', disable_notification=silent))
        if r.get('ok'): return r['result']['message_id']
        say('sendPhoto failed (%s) -- falling back to sendMessage' % r.get('description'))
    r = api('sendMessage', dict(chat_id=CHAT, text=text, parse_mode='HTML', disable_notification=silent,
                                link_preview_options=dict(url=c['url'], prefer_large_media=True)))
    if r.get('ok'): return r['result']['message_id']
    say('sendMessage failed for %s: %s' % (c['event_id'], r.get('description'))); return None

def digest_text(day, d):
    dd = dt.date.fromisoformat(day)
    mon = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'][dd.month - 1]
    lines = ['<b>Итоги дня, %d %s: главное в двух версиях</b>' % (dd.day, mon), '']
    lines += ['• %s' % esc(t) for t in d['top'][:5]]
    lines += ['', '<a href="%s">Все события дня (%d) →</a>' % (esc(d['url'] + '?utm_source=telegram'), d['n'])]
    return '\n'.join(lines)

def run(fast=False, dry=False, one=False, init=False):
    now = dt.datetime.now(BERLIN)
    try: feed = json.load(open(FEED, encoding='utf-8'))
    except (OSError, ValueError) as e: say('no tg_feed.json (%s) -- nothing to do' % type(e).__name__); return 0
    state = load_state()
    if state is None or init:
        state = state or {'cards': {}, 'digests': {}}
        n = 0
        for c in feed['cards']:
            if c['event_id'] not in state['cards']: state['cards'][c['event_id']] = {'init': True, 'slug': c['slug']}; n += 1
        state['digests'].setdefault(now.date().isoformat(), {'init': True}) if now.hour >= DIGEST_HOUR else None
        if not dry: save_state(state)
        say('state initialised: %d published cards marked as already posted (no backfill)%s' % (n, ' [dry]' if dry else ''))
        if init: return 0
    enabled = os.path.exists(ENABLED)
    if not enabled and not dry and not one:
        say('posting disabled (no data/telegram_enabled) -- would consider %d card(s)' % len(candidates(feed, state, now))); return 0
    cap = 1 if (fast or one) else MAX_RUN
    left_day = MAX_DAY - posted_today(state, now)
    todo = candidates(feed, state, now)[:max(0, min(cap, left_day))]
    if one and not todo:                       # setup test: the most important recent card even if no new one passed the filter
        fresh = [c for c in feed['cards'] if not c.get('one_sided') and not c.get('roundup') and c['importance'] >= MIN_SCORE]
        fresh.sort(key=lambda c: c['first_published'], reverse=True)
        recent = [c for c in fresh if now - dt.datetime.fromisoformat(c['first_published']) < dt.timedelta(hours=12)]
        todo = sorted(recent, key=lambda c: -c['importance'])[:1] or fresh[:1]
    for c in todo:
        mid = post_card(c, now, dry)
        if mid:
            state['cards'][c['event_id']] = {'msg_id': mid, 'at': now.isoformat(timespec='seconds'), 'slug': c['slug'], 'score': c['importance']}
            save_state(state)
            say('posted %s -> https://t.me/%s/%s (score %.0f): %s' % (c['event_id'], CHAT.lstrip('@'), mid, c['importance'], c['title'][:80]))
            time.sleep(3)
    if not todo: say('no new card above threshold (%d posted today)' % posted_today(state, now))
    # daily digest: first run after 21:00 Berlin, once per day, only if the day page exists
    day = now.date().isoformat(); d = (feed.get('digests') or {}).get(day)
    if not one and now.hour >= DIGEST_HOUR and day not in state['digests'] and d and d.get('top'):
        text = digest_text(day, d)
        if dry: say('DRY would post digest for %s' % day); print(text)
        else:
            r = api('sendMessage', dict(chat_id=CHAT, text=text, parse_mode='HTML', disable_notification=quiet(now), link_preview_options=dict(is_disabled=True)))
            if r.get('ok'):
                state['digests'][day] = {'msg_id': r['result']['message_id'], 'at': now.isoformat(timespec='seconds')}; save_state(state)
                say('digest %s -> https://t.me/%s/%s' % (day, CHAT.lstrip('@'), r['result']['message_id']))
            else: say('digest failed: %s' % r.get('description'))
    return 0

def safe_run(**kw):
    """Entry point for auto_run: never raises."""
    try: return run(**kw)
    except Exception as e:
        say('ERROR (ignored): %s' % re.sub(r'bot\d+:[A-Za-z0-9_-]+', 'bot***', repr(e))[:300]); return 0

if __name__ == '__main__':
    a = sys.argv[1:]
    sys.exit(safe_run(fast='--fast' in a, dry='--dry-run' in a, one='--one' in a, init='--init' in a))
