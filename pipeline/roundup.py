# -*- coding: utf-8 -*-
"""Daily roundup cards for minor events (point 3 of the Nov 21 pipeline improvements).

Minor clusters (importance below IMPORTANCE_MED, not reported by both war sides) of a groupable kind -- drone attacks on
Russian regions, strikes/shelling in one Ukrainian oblast -- are no longer written (or deferred) one by one. Every run they
are assigned, deterministically and without LLM calls, to ONE roundup event per (Berlin day, kind), e.g.
«Атаки дронов на регионы России, 20 ноября». The roundup card is written with the cheap bulk model (qwen3.7-flash) from
the union of its members' relevant articles and refreshed at most every ROUNDUP_REFRESH_HOURS while the day is open.

Members stay normal events (nothing is moved): if a member grows (other side reports it, casualties -> importance >= MED)
it is detached and gets its own card. Events that already have a published card never become members.
Tables: roundups(id = events.id of the roundup event, day, kind), roundup_members(event_id, roundup_id)."""
import re, datetime as dt
from .common import CFG, log, now_iso, BERLIN, DBLOCK
from . import importance

SCHEMA = """CREATE TABLE IF NOT EXISTS roundups(id INTEGER PRIMARY KEY, day TEXT, kind TEXT, created TEXT, UNIQUE(day, kind));
CREATE TABLE IF NOT EXISTS roundup_members(event_id INTEGER PRIMARY KEY, roundup_id INTEGER, added TEXT);"""

MON_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']

DRONE = re.compile(r'дрон|БПЛА|БпЛА|беспилот|безпілот|drone|UAV', re.I)
ATTACK = re.compile(r'удар|обстрел|обстріл|атак|дрон|БПЛА|БпЛА|беспилот|безпілот|ракет|взрыв|вибух|прилет|приліт|сбит|збит|пораж|ураж|shelling|strike|attack|drone|missile', re.I)
# not a "minor strike": diplomacy, front-line claims, arrests, exchanges etc. stay individual cards
OTHER = re.compile(r'переговор|санкц|обмен|обмін|пленн|полон|задерж|затрим|арест|суд\b|приговор|вирок|заяв|встреч|зустр|визит|візит|продвиж|просув|занят|захват|освобо|звільн|контрол|наступ|бои за|бої за|штурм|NATO|НАТО|ЕС\b|Евросоюз|мобилизац|ТЦК|АЭС|ЗАЭС|DDoS|кибер|хакер|теракт|диверс|поджог|підпал', re.I)

RU_REG = re.compile(r'(?<![А-Яа-яЁёІіЇїЄєҐґ])(?:Белгород(?!-Днестр)|Бєлгород|Курск|Курськ|Брянськ|Ростовськ|Краснодарськ|Москов|Татарстан|Самарськ|Пензенськ|Хабаров|Ленинград|Ленінград|Брянск|Воронеж|Ростов|Краснодар|Кубан|Новороссийск|Туапсе|Сочи|Анап|Москв|Подмосков|Тульск|Туле\b|Тула\b|Калуж|Калуг|Орлов|Орле\b|Орёл|Липецк|Тамбов|Рязан|Смоленск|Твер|Ярослав|Саратов|Волгоград|Самар|Татарстан|Башкир|Чуваш|Ленинградск|Новгород|Псков|Пенз|Нижегород|Астрахан|Ставропол|Адыге|Мордови|Калмык|Дагестан|Чечн|Ингушет|Оренбург|Ульяновск|Удмурт|Марий|Костром|Иванов|Владимир|Перм|Челябинск|Свердлов|Курган|Тюмен|регион\w* России|регион\w* РФ|над Росси|российск\w+ регион)|регион\w* России|регион\w* РФ|над Росси|российск\w+ регион')
UA_OBL = [  # (kind suffix, regex on the event title (Russian or Ukrainian working title), place phrase, region key)
    ('kharkiv', r'Харьков|Харків|Харківщин|Купянск|Куп.янськ|Изюм|Ізюм|Чугуев|Чугуїв|Балакле|Балаклі|Лозов|Лозів|Волчанск|Вовчанськ|Богодухів|Богодухов|Золочев|Золочів', 'Харьковская область', 'kharkiv'),
    ('sumy', r'Сум(?:ы|ах|ам|ами|ской|ская|щин)|Сум(?:и|ській|ська|щин)|Шостк|Конотоп|Ахтырк|Охтирк|Ромн|Глухов|Глухів|Краснопіл|Краснопол|Белопол|Білопіл|Путивл', 'Сумская область', 'sumy'),
    ('zaporizhzhia', r'Запорож|Запоріж|Гуляйпол|Гуляйпіл|Орехов|Оріхів|Степногорськ|Степногорск|Пологи|Васильевк|Василівк|Энергодар|Енергодар', 'Запорожская область', 'zaporizhzhia'),
    ('dnipro', r'Днепр(?!о-Бугск)|Дніпр|Днепропетров|Дніпропетров|Никопол|Нікопол|Марганц|Павлоград|Кривой Рог|Кривом Роге|Кривого Рога|Кривий Ріг|Кривого Рогу|Кривому Розі|Каменское|Кам.янськ|Новомосковск|Синельников|Синельников|Желт\w* Вод|Жовт\w* Вод', 'Днепропетровская область', 'dnipro'),
    ('kherson', r'Херсон|Берислав|Антоновк|Антонівк|Белозерк|Білозерк|Каховк|Голая Пристань|Голой Пристани|Гола Пристань', 'Херсонская область', 'kherson'),
    ('odesa', r'Одесс|Одесск|Одес[аиіу]|Одещин|Измаил|Ізмаїл|Черноморск|Чорноморськ|Белгород-Днестров|Білгород-Дністров|Рени\b|Рені\b', 'Одесская область', 'odesa'),
    ('donetsk', r'Донецк|Донецьк|Донеччин|Донбас|Краматорск|Краматорськ|Славянск|Слов.янськ|Константиновк|Костянтинівк|Покровск|Покровськ|Дружковк|Дружківк|Доброполь|Добропіль|Мирноград|Северск(?!\w* район)|Сіверськ|Лиман|Часов Яр|Часів Яр|Бахмут|Горловк|Мариупол|Маріупол', 'Донецкая область', 'donetsk'),
    ('mykolaiv', r'Николаев|Миколаїв|Миколаївщин|Очаков|Очаків', 'Николаевская область', 'ukraine-wide'),
    ('chernihiv', r'Чернигов|Чернігів|Чернігівщин|Нежин|Ніжин|Новгород-Сіверськ|Новгород-Северск|Прилук', 'Черниговская область', 'ukraine-wide'),
    ('kyiv', r'Киев|Київ|Києв|Київщин|Бровар|Бориспол|Бориспіл|Белая Церковь|Белой Церкви|Біла Церква|Білій Церкві|Вышгород|Вишгород|Обухов|Обухів|Фастов|Фастів|Ирпен|Ірпін|Буч[аеіи]', 'Киев и Киевская область', 'kyiv'),
    ('poltava', r'Полтав|Кременчуг|Кременчук|Миргород|Лубн', 'Полтавская область', 'ukraine-wide'),
    ('vinnytsia', r'Винниц|Вінниц|Вінничин|Крыжопол|Крижопіл|Калиновк|Калинівк|Ладыжин|Ладижин', 'Винницкая область', 'ukraine-wide'),
    ('kirovohrad', r'Кировоград|Кіровоград|Кропивницк|Кропивницьк|Знаменк|Олександрі|Александрия', 'Кировоградская область', 'ukraine-wide'),
    ('west', r'Львов|Льві?в|Ивано-Франков|Івано-Франків|Ровно|Ровен|Рівн|Луцк|Луцьк|Волын|Волин|Тернопол|Тернопіл|Хмельницк|Хмельницьк|Ужгород|Закарпат|Черновц|Чернівц|Буковин', 'западные области Украины', 'ukraine-wide'),
    ('center', r'Черкас|Житомир|Житомирщин|Бердичев|Бердичів|Коростен|Умань', 'Черкасская и Житомирская области', 'ukraine-wide'),
]
UA_OBL = [(k, re.compile(r'(?<![А-Яа-яЁёІіЇїЄєҐґ])(?:%s)' % rx), ph, rg) for k, rx, ph, rg in UA_OBL]
UA_ANY = re.compile('|'.join(x[1].pattern for x in UA_OBL) + r'|Украин|Україн')

def kind_info(kind):
    """kind -> (title prefix, place, region, prompt description)."""
    if kind == 'ru_attacks':
        return 'Атаки дронов и удары по регионам России', 'регионы России', 'russia-other', 'drone attacks and strikes on Russian regions reported that day'
    for k, _, ph, rg in UA_OBL:
        if kind == 'ua_strikes:' + k:
            return 'Удары и обстрелы: %s' % ph, ph, rg, 'strikes / shelling / drone attacks in %s that day' % ph
    raise KeyError(kind)

def roundup_title(kind, day):
    d = dt.date.fromisoformat(day)
    return '%s, %d %s' % (kind_info(kind)[0], d.day, MON_GEN[d.month - 1])

def classify(title, art_titles=()):
    """Deterministic kind of a (minor) event from its Russian working title (+ article titles for the drone keyword), or None."""
    t = title or ''
    blob = t + ' | ' + ' | '.join(art_titles or [])
    if not ATTACK.search(t) or OTHER.search(t): return None
    ru, ua = bool(RU_REG.search(t)), bool(UA_ANY.search(t))
    if ru and not ua: return 'ru_attacks'
    if ua and not ru:
        hits = [k for k, rx, _, _ in UA_OBL if rx.search(t)]
        if len(hits) == 1: return 'ua_strikes:' + hits[0]
    return None

def ensure(db):
    db.executescript(SCHEMA)

def is_roundup(db, eid):
    with DBLOCK:
        return db.execute('select day, kind from roundups where id=?', (eid,)).fetchone()

def member_rows(db, rid):
    with DBLOCK:
        return [dict(r) for r in db.execute('''select a.*, ea.rel from event_articles ea join articles a on a.id=ea.article_id
            where ea.event_id in (select event_id from roundup_members where roundup_id=?) order by a.date_utc''', (rid,))]

def member_ids(db):
    with DBLOCK:
        return {r[0] for r in db.execute('select event_id from roundup_members')}

def assign(db, dry=False):
    """Recompute roundup membership for today and yesterday (Europe/Berlin). Returns stats. No LLM calls."""
    ensure(db)
    if CFG.get('ROUNDUPS', True) is False: return dict(disabled=1)
    today = dt.datetime.now(BERLIN).date()
    days = {(today - dt.timedelta(days=i)).isoformat() for i in range(2)}
    floor = (dt.datetime.combine(today - dt.timedelta(days=1), dt.time(0), BERLIN)).astimezone(dt.timezone.utc).isoformat()
    med = CFG.get('IMPORTANCE_MED', 40)
    stats = dict(candidates=0, members=0, added=0, detached=0, roundups=0, new_roundups=0)
    with DBLOCK:
        rows = db.execute("""select e.id, e.title, e.first_art, c.status cst from events e left join cards c on c.event_id=e.id
                             where e.merged_into is null and e.status not in ('merged','roundup') and e.first_art>=?""", (floor,)).fetchall()
        current = {r[0]: r[1] for r in db.execute('select event_id, roundup_id from roundup_members')}
    want = {}
    for r in rows:
        eid = r['id']
        if r['cst'] == 'published' and eid not in current: continue          # already has its own card: never absorbed
        with DBLOCK:
            arts = db.execute('select a.title, a.grp, a.source, a.date_local, ea.rel from event_articles ea join articles a on a.id=ea.article_id where ea.event_id=?', (eid,)).fetchall()
        if not arts or any(a['rel'] is None for a in arts): continue          # relevance not judged yet
        yes = [a for a in arts if a['rel'] == 'yes']
        if not yes: continue
        grps = {a['grp'] for a in yes}
        if 'ru' in grps and 'ua' in grps: continue                          # both sides: a real comparison -> own card
        sc, _ = importance.score_event(db, eid)
        if sc >= med: continue
        kind = classify(r['title'], [a['title'] for a in yes])
        if not kind: continue
        day = min(a['date_local'][:10] for a in yes)
        if day not in days: continue
        stats['candidates'] += 1
        want.setdefault((day, kind), []).append(eid)
    # existing roundups for the window
    with DBLOCK:
        existing = {(r['day'], r['kind']): r['id'] for r in db.execute('select id, day, kind from roundups where day in (%s)' % ','.join('?' * len(days)), tuple(days))}
    newmem = {}
    for key, eids in want.items():
        if len(eids) < 2 and key not in existing: continue                   # a roundup needs >= 2 minor events
        newmem[key] = eids
    if dry:
        stats['roundups'] = len(newmem); stats['members'] = sum(len(v) for v in newmem.values()); stats['groups'] = {'%s %s' % k: v for k, v in newmem.items()}
        return stats
    with DBLOCK:
        for key, eids in newmem.items():
            rid = existing.get(key)
            if rid is None:
                title = roundup_title(key[1], key[0])
                cur = db.execute("insert into events(title,created,updated,status,dirty) values(?,?,?,'roundup',1)", (title, now_iso(), now_iso()))
                rid = cur.lastrowid
                db.execute('insert into roundups(id,day,kind,created) values(?,?,?,?)', (rid, key[0], key[1], now_iso())); stats['new_roundups'] += 1
                existing[key] = rid
                log.info('roundup: new %d %r', rid, title)
            for x in eids:
                if current.get(x) != rid:
                    db.execute('insert or replace into roundup_members(event_id, roundup_id, added) values(?,?,?)', (x, rid, now_iso())); stats['added'] += 1
        keep = {x for v in newmem.values() for x in v}
        for x, rid in current.items():
            rk = db.execute('select day from roundups where id=?', (rid,)).fetchone()
            if rk and rk[0] not in days: continue                            # closed days are frozen
            if x not in keep:
                db.execute('delete from roundup_members where event_id=?', (x,)); stats['detached'] += 1
                log.info('roundup: event %d detached from roundup %d (grew / merged / no longer minor)', x, rid)
        # refresh first/last article dates of the roundup events
        for key, rid in existing.items():
            r = db.execute('''select min(a.date_utc), max(a.date_utc) from event_articles ea join articles a on a.id=ea.article_id
                              where ea.event_id in (select event_id from roundup_members where roundup_id=?)''', (rid,)).fetchone()
            if r[0]: db.execute('update events set first_art=?, last_art=? where id=?', (r[0], r[1], rid))
        db.commit()
        stats['roundups'] = len(existing); stats['members'] = db.execute('select count(*) from roundup_members where roundup_id in (%s)' % ','.join(str(v) for v in existing.values())).fetchone()[0] if existing else 0
    return stats
