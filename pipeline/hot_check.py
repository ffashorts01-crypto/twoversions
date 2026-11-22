#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast path for big events (point 6 of the Nov 21 pipeline improvements). Meant to run every 30 minutes.

    python3 pipeline/hot_check.py              # collect feeds (no LLM) -> detect -> if big: run pipeline/auto_run.py now
    python3 pipeline/hot_check.py --detect-only  # no collect, no trigger: just print what the detector sees
    python3 pipeline/hot_check.py --backtest 7   # replay the detector on the last 7 days (every 30 min), no writes

Cost: zero LLM calls unless a big event is detected (then exactly one normal auto_run, with the usual $0.45/run and $1/day caps).
A "big event" = within the last HOT_WINDOW_MIN minutes (default 90), at least HOT_MIN_ARTICLES (8) new, not yet clustered
war-related articles from at least HOT_MIN_SOURCES (5) outlets, including BOTH a Russian and a Ukrainian outlet, that share
one story key in the HEADLINE (a city/region named in ru/uk/en, or 'massive attack' / 'energy' / 'prisoner exchange' wording)
and whose headline names an incident (strike, attack, explosion, casualties ...).
Skips (exit 0, one log line): editor RUNNING marker, update.lock held, a regular even-hour run is due within 25 minutes or
started less than 20 minutes ago, the last auto_run finished less than HOT_MIN_GAP_MIN (40) minutes ago, the same story key
already triggered within HOT_KEY_COOLDOWN_H (3) hours, or less than HOT_MIN_BUDGET_USD ($0.15) of today's $1 is left.
Log: logs/hot_check.log; state: data/hot_state.json."""
import os, sys, re, json, time, fcntl, argparse, subprocess, sqlite3, datetime as dt
from zoneinfo import ZoneInfo
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data'); LOGS = os.path.join(ROOT, 'logs')
BERLIN = ZoneInfo('Europe/Berlin'); UTC = dt.timezone.utc
STATE = os.path.join(DATA, 'hot_state.json')
E = lambda k, d: float(os.environ.get(k, d))
WINDOW = E('HOT_WINDOW_MIN', 90); MIN_ART = E('HOT_MIN_ARTICLES', 8); MIN_SRC = E('HOT_MIN_SOURCES', 5)
MIN_GAP = E('HOT_MIN_GAP_MIN', 40); KEY_CD = E('HOT_KEY_COOLDOWN_H', 3); MIN_BUDGET = E('HOT_MIN_BUDGET_USD', 0.15)
DAILY = E('LLM_DAILY_BUDGET_USD', 1.0)

KEYS = [
    ('kyiv', r'Киев|Київ|Києв|Kyiv|Kiev'), ('kharkiv', r'Харьков|Харків|Kharkiv'), ('odesa', r'Одесс|Одес[аиіу]|Одещин|Odesa|Odessa'),
    ('dnipro', r'Днепр|Дніпр|Dnipro'), ('zaporizhzhia', r'Запорож|Запоріж|Zaporizh'), ('lviv', r'Львов|Льві?в|Lviv'),
    ('sumy', r'Сумы|Сумах|Суми|Сумщин|Сумск|Сумськ|Sumy'), ('kherson', r'Херсон|Kherson'), ('mykolaiv', r'Николаев|Миколаїв|Mykolaiv'),
    ('chernihiv', r'Чернигов|Чернігів|Chernihiv'), ('poltava', r'Полтав|Кременчуг|Кременчук|Poltava|Kremenchuk'),
    ('kryvyi_rih', r'Кривой Рог|Кривом Роге|Кривого Рога|Кривий Ріг|Кривому Розі|Кривого Рогу|Kryvyi Rih'),
    ('ternopil', r'Тернопол|Тернопіл|Ternopil'), ('khmelnytskyi', r'Хмельницк|Хмельницьк|Khmelnytsk'), ('vinnytsia', r'Винниц|Вінниц|Vinnyts'),
    ('ivano_frankivsk', r'Ивано-Франков|Івано-Франків|Ivano-Frankivsk'), ('rivne', r'Ровно|Рівн|Rivne'), ('lutsk', r'Луцк|Луцьк|Lutsk'),
    ('zhytomyr', r'Житомир|Zhytomyr'), ('cherkasy', r'Черкас|Cherkasy'), ('kropyvnytskyi', r'Кропивницк|Кропивницьк|Kropyvnytsk'),
    ('donetsk', r'Донецк|Донецьк|Donetsk'), ('pokrovsk', r'Покровск|Покровськ|Pokrovsk'), ('kupiansk', r'Купянск|Куп.янськ|Kupiansk'),
    ('kostiantynivka', r'Константиновк|Костянтинівк|Kostiantynivka'), ('huliaipole', r'Гуляйпол|Гуляйпіл|Huliaipole'),
    ('moscow', r'Москв|Moscow'), ('belgorod', r'Белгород(?!-Дн)|Бєлгород|Belgorod'), ('kursk', r'Курск|Курськ|Kursk'),
    ('bryansk', r'Брянск|Брянськ|Bryansk'), ('krasnodar', r'Краснодар|Новороссийск|Новоросійськ|Туапсе|Krasnodar|Novorossiysk|Tuapse'),
    ('rostov', r'Ростов|Таганрог|Rostov|Taganrog'), ('voronezh', r'Воронеж|Voronezh'), ('crimea', r'Крым|Крим|Севастопол|Crimea|Sevastopol'),
    ('petersburg', r'Петербург|Ленинградск|Ленінградськ|Petersburg|Leningrad'),
    ('massive', r'масштабн|массирован|масован|комбинирован|комбінован|massive|large-scale|combined attack|record number'),
    ('energy', r'энергетик|енергетик|энергосистем|енергосистем|power grid|energy infrastructure|blackout|отключени\w* света|відключен\w* світла'),
    ('exchange', r'обмен\w* пленн|обмін\w* полонен|prisoner exchange|prisoner swap'),
]
INCIDENT = re.compile(r'удар|атак|взрыв|вибух|обстрел|обстріл|ракет|дрон|БПЛА|БпЛА|беспилот|безпілот|балісти|баллисти|погиб|загин|убит|вбил|ранен|поранен|постраждал|пострадав|пожар|пожеж|обмен|обмін|attack|strike|explosion|blast|missile|drone|killed|injured|wounded|ballistic|prisoner', re.I)
KEYS = [(k, re.compile(rx, re.I)) for k, rx in KEYS]

def say(*a):
    line = '%s hot_check: %s' % (dt.datetime.now(BERLIN).strftime('%Y-%m-%d %H:%M:%S'), ' '.join(str(x) for x in a))
    print(line, flush=True)
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, 'hot_check.log'), 'a', encoding='utf-8') as f: f.write(line + '\n')

def load_state():
    try: return json.load(open(STATE, encoding='utf-8'))
    except (OSError, ValueError): return {}
def save_state(st):
    tmp = STATE + '.tmp'; json.dump(st, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1); os.replace(tmp, STATE)

def detect(db, now_utc, unclustered_only=True, window=None):
    """Return [(key, n_articles, n_sources, groups, sample titles)] sorted by size, for keys that pass the thresholds."""
    since = (now_utc - dt.timedelta(minutes=window or WINDOW)).isoformat(timespec='seconds')
    upto = now_utc.isoformat(timespec='seconds')
    q = '''select a.id, a.source, a.grp, a.title, substr(a.text,1,600) t from articles a
           where a.military=1 and a.date_utc>=? and a.date_utc<=?''' + (' and a.id not in (select article_id from event_articles)' if unclustered_only else '')
    rows = db.execute(q, (since, upto)).fetchall()
    by = {}
    for r in rows:
        title = r[3] or ''
        if not INCIDENT.search(title): continue                 # only incident headlines count (not politics that names a city)
        for k, rx in KEYS:
            if rx.search(title): by.setdefault(k, []).append(r)
    out = []
    for k, rs in by.items():
        srcs = {r[1] for r in rs}; grps = {r[2] for r in rs}
        if len(rs) >= MIN_ART and len(srcs) >= MIN_SRC and 'ru' in grps and 'ua' in grps:
            out.append((k, len(rs), len(srcs), ''.join(sorted(g[0] for g in grps)), [r[3][:70] for r in rs[:3]]))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out, len(rows)

def last_auto_run():
    """Finish time (Berlin, naive ISO) of the last auto_run from logs/cost.csv."""
    try:
        with open(os.path.join(LOGS, 'cost.csv'), encoding='utf-8') as f: lines = f.read().strip().splitlines()
        return dt.datetime.fromisoformat(lines[-1].split(',')[0]).replace(tzinfo=BERLIN) if lines else None
    except (OSError, ValueError, IndexError): return None

def spent_today(db):
    return db.execute('select coalesce(sum(cost),0) from llm_usage where ts>=?', (dt.datetime.now(BERLIN).strftime('%Y-%m-%d'),)).fetchone()[0]

def regular_run_near(now):
    """The regular routine fires at even hours (Berlin): skip from 25 min before to 20 min after."""
    m = now.hour * 60 + now.minute
    nxt = ((m // 120) + 1) * 120; prev = (m // 120) * 120
    return nxt - m <= 25 or m - prev < 20

def backtest(db, days):
    now = dt.datetime.now(UTC).replace(second=0, microsecond=0)
    t = now - dt.timedelta(days=days); hits = []; last_key = {}
    while t <= now:
        found, _ = detect(db, t, unclustered_only=False)
        tb = t.astimezone(BERLIN)
        for k, n, ns, g, sample in found[:1]:
            if k in last_key and t - last_key[k] < dt.timedelta(hours=KEY_CD): break
            if hits and t - hits[-1][0] < dt.timedelta(minutes=MIN_GAP): break
            if regular_run_near(tb): break
            last_key[k] = t; hits.append((t, k, n, ns, g, sample))
        t += dt.timedelta(minutes=30)
    for t, k, n, ns, g, sample in hits:
        print('%s  %-12s %2d art %2d src %s | %s' % (t.astimezone(BERLIN).strftime('%m-%d %H:%M'), k, n, ns, g, ' // '.join(sample)))
    print('triggers: %d in %d days (%.1f/day)' % (len(hits), days, len(hits) / max(1, days)))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--detect-only', action='store_true'); ap.add_argument('--backtest', type=int, default=0)
    ap.add_argument('--no-collect', action='store_true'); ap.add_argument('--no-trigger', action='store_true')
    a = ap.parse_args()
    db = sqlite3.connect(os.path.join(DATA, 'articles.db'), timeout=60)
    if a.backtest: backtest(db, a.backtest); return 0
    now = dt.datetime.now(BERLIN)
    if a.detect_only:
        found, n = detect(db, now.astimezone(UTC)); print('window %d min: %d new unclustered articles; big: %s' % (WINDOW, n, found or 'none')); return 0
    marker = os.path.join(DATA, 'editor', 'RUNNING')
    if os.path.exists(marker) and time.time() - os.path.getmtime(marker) < 3 * 3600: say('skip: editor RUNNING marker'); return 0
    if regular_run_near(now) and not a.no_trigger: say('skip: regular even-hour run is due / running'); return 0
    lr = last_auto_run()
    if lr and now - lr < dt.timedelta(minutes=MIN_GAP) and not a.no_trigger: say('skip: last auto_run finished %s' % lr.strftime('%H:%M')); return 0
    lock = open(os.path.join(DATA, 'update.lock'), 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: say('skip: update.lock is held (a run is in progress)'); return 0
    t0 = time.time()
    if not a.no_collect:
        env = dict(os.environ, UPDATE_LOCK_HELD='1'); env.pop('OPENAI_API_KEY', None)
        r = subprocess.run([sys.executable, 'update.py', '--skip-llm', '--no-build'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=25 * 60)
        new = sum(int(m) for m in re.findall(r'stored\s+(\d+)', r.stdout + r.stderr))
        if r.returncode != 0: say('collect rc=%d (continuing with what is stored)' % r.returncode)
    else: new = 0
    found, n = detect(db, dt.datetime.now(UTC))
    st = load_state(); st['last_check'] = now.isoformat(timespec='seconds')
    cool = st.setdefault('key_triggered', {})
    fresh = [f for f in found if not (cool.get(f[0]) and now - dt.datetime.fromisoformat(cool[f[0]]) < dt.timedelta(hours=KEY_CD))]
    if not fresh:
        save_state(st)
        say('no big event (collected %d new; %d unclustered war articles in %d min%s) %.0fs' % (new, n, WINDOW, '; on cooldown: ' + ','.join(f[0] for f in found) if found else '', time.time() - t0))
        return 0
    k, na, ns, g, sample = fresh[0]
    left = DAILY - spent_today(db)
    if left < MIN_BUDGET:
        save_state(st); say('BIG event %s (%d art, %d src) but only $%.3f of today\'s budget left -- not triggering' % (k, na, ns, left)); return 0
    if a.no_trigger:
        save_state(st); say('BIG event %s (%d art, %d src, %s) -- --no-trigger set' % (k, na, ns, g)); return 0
    for f in fresh: cool[f[0]] = now.isoformat(timespec='seconds')
    st.setdefault('history', []).append(dict(ts=now.isoformat(timespec='seconds'), key=k, articles=na, sources=ns, sample=sample)); st['history'] = st['history'][-100:]
    save_state(st)
    say('BIG event: %s -- %d articles from %d outlets (%s) in %d min: %s -> running auto_run.py now' % (k, na, ns, g, WINDOW, ' // '.join(sample)))
    fcntl.flock(lock, fcntl.LOCK_UN); lock.close()                       # auto_run takes the lock itself
    r = subprocess.run([sys.executable, 'pipeline/auto_run.py'], cwd=ROOT, env=dict(os.environ, TG_FAST='1'), timeout=95 * 60)
    say('auto_run finished rc=%d (%.0fs)' % (r.returncode, time.time() - t0))
    return r.returncode

if __name__ == '__main__':
    sys.exit(main())
