# -*- coding: utf-8 -*-
"""Cheap deterministic place-name / surname consistency check for translations (no LLM).

Candidates are proper names in the Russian original: capitalised Cyrillic words, not sentence-initial, not inside quotes, not frequent
lowercase words of the corpus (so «Северный мост», «Оперативное командование» are skipped).
  * English: only known toponyms are checked (GeoNames gazetteer of Ukraine + Russia, data/gazetteer.json.gz built by tools/build_gazetteer.py);
    the English text must contain a word whose consonant skeleton matches the Russian transliteration, the Ukrainian form of the name
    (from the Ukrainian translation) or a GeoNames Latin spelling (Volnogorsk / Vilnohirsk / Vil'nohirs'k). Kiev ~ Kyiv, Kharkov ~ Kharkiv.
  * Ukrainian: every candidate (places and people: Самуэленко -> Самуеленко) must have a Cyrillic counterpart with a matching skeleton.
Skeleton = transliteration without vowels/glides/soft signs/doubled letters, with g~h~kh, so case endings and ru/uk vowel shifts are tolerated;
a small edit distance is allowed. Typical catch: Вольногорск -> "Vovchansk" (another town). Known limitation: vowel-only changes
(Самуэленко -> Самойленко) are not detected."""
import re, os, gzip, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TR = {'а':'a','б':'b','в':'v','г':'g','ґ':'g','д':'d','е':'e','ё':'e','є':'e','ж':'zh','з':'z','и':'i','і':'i','ї':'i','й':'i','к':'k','л':'l','м':'m',
       'н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e',
       'ю':'iu','я':'ia',"'":'','’':'','ʼ':''}
def translit(w):
    w = re.sub(r'^у(?=[иэоаеі])', 'в', w.lower())      # Уиткофф / Уотсон = Witkoff / Watson (UK: Віткофф, Вотсон)
    return ''.join(_TR.get(ch, ch) for ch in w)

import unicodedata
def skeleton_lat(w):
    s = w.lower().replace('ł', 'l')
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c)) if re.search(r'[^\x00-\x7f]', s) and not re.search(r'[а-яёіїєґ]', s) else s
    s = re.sub(r"['’ʼ`\-]", '', s)
    s = s.replace('ł', 'l').replace('ó', 'o').replace('ś', 's').replace('ż', 'zh').replace('ź', 'z').replace('ć', 'ch').replace('ń', 'n').replace('ę', 'e').replace('ą', 'a')
    s = s.replace('x', 'ks')
    for a, b in (('shch', '5'), ('tsch', '4'), ('sch', '5'), ('dzh', '2'), ('zh', '2'), ('rz', '2'), ('sz', '5'), ('cz', '4'), ('kh', 'h'), ('ts', '3'), ('tz', '3'),
                 ('ch', '4'), ('sh', '5'), ('th', 't'), ('wh', 'w'), ('ck', 'k'), ('ph', 'f'), ('c', 'k'), ('w', 'v'), ('g', 'h'), ('q', 'k')):
        s = s.replace(a, b)
    s = re.sub(r'^j', '2', s)               # English J = Дж (Jared, Jinping)
    s = re.sub(r'[aeiouyj]', '', s)         # vowels and glides (Kyiv ~ Kiev, -ii/-yi endings)
    s = re.sub(r'(.)\1+', r'\1', s)         # doubled letters (Odessa ~ Odesa, Zaporizhzhia)
    return s

def skeleton_cyr(w):
    return skeleton_lat(translit(w).replace('shch', 'shch'))

END_NOUN = re.compile(r'(ами|ями|ого|его|ему|ому|ыми|ими|ой|ей|ом|ем|ах|ях|ам|ям|ую|юю|ая|яя|ое|ее|ые|ие|ых|их|ый|ий|ій|ої|ою|ым|им|ов|ев|а|я|у|ю|е|и|ы|і|ї|ь|о)$')
END_ADJ = re.compile(r'(ск|цк|зск|ськ|цьк|зьк)(ая|ой|ий|ое|ом|ую|их|ими|ого|ому|ій|ої|ою|им|е|у|а|і|ї|ый|ые|ие)$')
END_SHCH = re.compile(r'щин(а|ы|е|у|ой|и|і|ою)?$')
END_CHCH = re.compile(r'ччин(а|и|і|у|ою)?$')     # Вінниччина = Винницкая (область), Донеччина
def keys(w):
    """Normalised stems of a (possibly inflected) Cyrillic word: Вольногорске -> вольногорск, Харьковской -> харьков, Харківщині -> харків."""
    w = w.lower().replace('ё', 'е').replace('’', "'").replace('ʼ', "'")
    out = {w}
    for a, b in (('ськ', 'ск'), ('зьк', 'зк'), ('цьк', 'цк'), ('сск', 'ск')): w = w.replace(a, b)
    out.add(w)
    n = w
    for a, b in (('жск', 'зк'), ('зск', 'зк'), ('гск', 'зк')): n = n.replace(a, b)   # Калужская = Калузька, Бугский = Бузький
    out.add(n)
    m = re.search(r'(з|ц|с)і$', w)                        # Ukrainian locative: Гамбурзі / Петербурзі / Розі = -г, -ці = -к, -сі = -х
    if m and len(w) > 4: out.add(w[:-2] + {'з': 'г', 'ц': 'к', 'с': 'х'}[m.group(1)])
    m = END_CHCH.search(w)
    if m: out.add(w[:-len(m.group(0))] + 'ц')
    for x in list(out):
        for rx in (END_NOUN, END_ADJ, END_SHCH):
            m = rx.search(x)
            if m and len(x) - len(m.group(0)) >= 3:
                st = x[:-len(m.group(0))]
                if rx is END_ADJ and m.group(1)[0] in 'цз': st += m.group(1)[0]     # Винницкая -> винниц, Калузька -> калуз
                out.add(st)
                if rx is END_ADJ and re.search(r'[аяи]н$', st) and len(st) >= 5: out.add(st[:-2])   # Бучанский -> буча, Ялтинская -> ялта, Туапсинский -> туапсе
    return out
def stem_key(w): return min(keys(w), key=len)

def cyr_forms(w): return {skeleton_cyr(x) for x in keys(w)} - {''}
def lat_forms(w):
    w = w.lower().replace('’', "'")
    f = {w, re.sub(r"'s$", '', w)}
    f |= {x.replace('ch', 'kh') for x in f if 'ch' in x}          # Polish/German ch = х (Michał, Kirchner)
    for suf in ('ian', 'an', 'ians', 'ans', 'ese', 'ish', 'skyi', 'sky', 'ska', 'ske', 's'):
        if w.endswith(suf) and len(w) - len(suf) >= 4: f.add(w[:-len(suf)])
    return {skeleton_lat(x) for x in f} - {''}

# Known renamings / exonyms / ru-uk given names: every name in a group is equivalent.
ALIASES = [
    'Москва Moscow', 'Россия Росія Russia Russian', 'Украина Україна Ukraine Ukrainian', 'Беларусь Белоруссия Білорусь Belarus Belarusian',
    'Николаев Миколаїв Mykolaiv Nikolaev', 'Артемовск Бахмут Bakhmut Artemovsk', 'Красноармейск Покровск Покровськ Pokrovsk',
    'Димитров Мирноград Myrnohrad', 'Дзержинск Торецк Торецьк Toretsk', 'Лиман Lyman', 'Кировоград Кропивницкий Кропивницький Kropyvnytskyi',
    'Днепр Дніпро Dnipro Dnieper Днепропетровск Дніпропетровськ Dnipropetrovsk', 'Городской Міський Miskyi', 'Би-би-си Бі-бі-сі ВВС BBC', 'Запорожье Запоріжжя Запорізька Zaporizhzhia Zaporozhye', 'Харьков Харків Kharkiv',
    'Киев Київ Kyiv Kiev', 'Одесса Одеса Odesa Odessa', 'Львов Львів Lviv', 'Ровно Рівне Rivne', 'Чернигов Чернігів Chernihiv', 'Рог Ріг Розі Rih Rig',
    'Новоград-Волынский Звягель Zviahel', 'Ленобласть Ленинградская Leningrad', 'Петербург Petersburg', 'Подмосковье Moscow',
    'Донетчина Донеччина Донецк Донецьк Donetsk', 'Луганщина Луганск Луганськ Luhansk', 'Хмельнитчина Хмельниччина Хмельницкий Хмельницький Khmelnytskyi',
    'Николай Микола', 'Александр Олександр', 'Алексей Олексій', 'Елена Олена', 'Илья Ілля', 'Ольга Ольга', 'Евгений Євген', 'Екатерина Катерина',
    'Новости Новини News',
    'Германия Німеччина Germany German', 'Турция Туреччина Turkey Türkiye Turkish', 'Великобритания Британия Британія Britain British UK',
    'Венгрия Угорщина Hungary Hungarian', 'Словакия Словаччина Slovakia Slovak', 'Польша Польща Poland Polish', 'Финляндия Фінляндія Finland Finnish',
    'Нидерланды Нідерланди Netherlands Dutch', 'Дания Данія Denmark Danish', 'Швеция Швеція Sweden Swedish', 'Норвегия Норвегія Norway Norwegian',
    'Чехия Чехія Czech', 'Франция Франція France French', 'Италия Італія Italy Italian', 'Испания Іспанія Spain Spanish', 'Китай China Chinese',
    'Япония Японія Japan Japanese', 'Индия Індія India Indian', 'Черногория Чорногорія Montenegro', 'Молдавия Молдова Moldova', 'Грузия Грузія Georgia',
    'Люксембург Luxembourg', 'Вена Відень Vienna', 'Страсбург Strasbourg', 'Мюнхен Munich', 'Гамбург Hamburg', 'Брюссель Brussels', 'Варшава Warsaw',
    'Вашингтон Washington', 'Пекин Пекін Beijing', 'Париж Paris', 'Рим Rome', 'Прага Prague', 'Женева Geneva', 'Европа Європа Europe European',
    'Южный Південний Pivdennyi Yuzhne Yuzhny South Southern', 'Северный Північний North Northern', 'Черное Черным Чорне Чорним Black',
    'Североатлантический Північноатлантичний North Atlantic NATO', 'Санкт-Петербург Петербург Petersburg', 'Азово-Черноморский Азово-Чорноморський Azov Black',
    'Погранполиция Прикордонна Border', 'Кишинев Кишинів Chisinau Kishinev', 'Белый Білий White', 'Литва Lithuania Lithuanian', 'Креста Хреста Cross', 'Свято Святой Святий Holy Saint', 'Городской Міський City', 'Мазовецкое Мазовецком Мазовецьке Mazowieckie Masovia Masovian', 'Аугуст Август August', 'Красного Червоного Red', 'Одесщина Одещина Odesa', 'Латвия Латвія Latvia Latvian', 'Эстония Естонія Estonia Estonian', 'Белое Біле White', 'Черное Чорне Black', 'Черноморский Чорноморський Black', 'Дальний Далекий Far', 'Общественное Суспільне Suspilne', 'Би-би-си Бі-бі-сі BBC',
]
_ALIAS = {}
def _aliases(forms):
    if not _ALIAS:
        for g in ALIASES:
            sk = set()
            for w in g.split():
                for part in w.split('-') + [w]:
                    sk |= cyr_forms(part) if re.search(r'[А-Яа-яЁёІіЇїЄєҐґ]', part) else lat_forms(part)
            for x in sk: _ALIAS.setdefault(x, set()).update(sk)
    out = set(forms)
    for f in forms: out |= _ALIAS.get(f, set())
    return out

_GAZ = None
def gazetteer():
    global _GAZ
    if _GAZ is None:
        p = os.path.join(ROOT, 'data', 'gazetteer.json.gz')
        if os.path.exists(p):
            with gzip.open(p, 'rt', encoding='utf-8') as fh: g = json.load(fh)
            _GAZ = (g['places'], set(g['common']))
        else: _GAZ = ({}, set())
    return _GAZ

STOP = set('Telegram'.split())
DIR_PART = re.compile(r'^(северо|южно|западно|восточно|центрально|азово|северно|юго|північно|південно|західно|східно)$', re.I)
PREFIX_ORG = re.compile(r'^(Мин|Гос|Нац|Ген|Сов|Евро|Євро|Рос|Укр|Спец|Рос)[а-яёіїєґ]')
PROPER = re.compile(r"(?<![\w’'ʼ-])([А-ЯЁІЇЄҐ][а-яёіїєґ’'ʼ]+(?:-[А-ЯЁІЇЄҐа-яёіїєґ][а-яёіїєґ’'ʼ]+)*)")
SENT_START = re.compile(r'(?:^|[.!?…:;]\s+|[«"“(]\s*|—\s+|\n\s*)$')

def lev(a, b, cap=4):
    if abs(len(a) - len(b)) > cap: return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]

def _sk_match(a, b):
    if a == b: return True
    n = min(len(a), len(b))
    if n < 4: return False
    tol = 1 if n <= 6 else 2
    return lev(a, b, tol) <= tol

def candidates(text, initial=False):
    """Proper-name candidates: capitalised, not sentence-initial, not inside a quotation, len >= 4, not a frequent lowercase word."""
    out = []
    if not text: return out
    places, common = gazetteer()
    t = re.sub(r'«[^»]*»|“[^”]*”|"[^"]*"', lambda m: ' ' * len(m.group(0)), text)   # quoted titles/phrases
    for m in PROPER.finditer(t):
        w = m.group(1)
        if len(w.replace('-', '')) < 4 or w in STOP or PREFIX_ORG.match(w): continue
        at_start = bool(SENT_START.search(t[:m.start()]))
        if at_start and not initial: continue
        if w.lower().replace('ё', 'е') in common: continue
        if at_start and keys(w) & common_stems(): continue          # «Граница …», «Дальний Восток» at the start of the place field
        out.append(w)
    return list(dict.fromkeys(out))

_CST = None
def common_stems():
    global _CST
    if _CST is None:
        _CST = set()
        for w in gazetteer()[1]: _CST |= keys(w)
    return _CST

def is_place(w):
    places, _ = gazetteer()
    parts = [p for p in w.split('-') if not DIR_PART.match(p)] or [w]
    return all(any(k in places for k in keys(p)) for p in parts)

def place_latin(w):
    places, _ = gazetteer()
    out = set()
    for p in w.split('-'):
        for k in keys(p): out.update(places.get(k, []))
    return out

def target_skeletons(text, lang):
    out = set()
    if lang == 'en':
        for w in re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)*", text or ''):
            if len(w) >= 2 and not re.search(r'[А-Яа-яЁёІіЇїЄєҐґ]', w): out |= lat_forms(w)
    else:
        for w in re.findall(r"[А-Яа-яЁёІіЇїЄєҐґ’'ʼ]+", text or ''): out |= cyr_forms(w)
    return out

def _present(w, tsk, lang, bwords):
    parts = [p for p in w.split('-') if len(p) >= 3 and not DIR_PART.match(p)] or [w]
    for p in parts:
        sks = _aliases(cyr_forms(p))
        if lang == 'en':
            for ln in place_latin(p):
                if re.fullmatch(r"[A-Z][a-z'’]{3,}", ln): sks |= lat_forms(ln)     # single-word GeoNames spellings only (no "Raion", pinyin, codes)
        if any(_sk_match(x, t) for x in sks for t in tsk): continue
        if lang == 'en' and bwords:          # Ukrainian form of the same name (Вольногорск -> Вільногірськ -> Vilnohirsk)
            if any(any(_sk_match(x, y) for x in sks for y in _aliases(cyr_forms(bw))) and
                   any(_sk_match(x, t) for x in _aliases(cyr_forms(bw)) for t in tsk) for bw in bwords): continue
        return False
    return True

def missing_names(src, dst, lang, bridge=None, initial=False):
    """Names from the Russian `src` without a counterpart in `dst` (lang 'uk' or 'en'); for English only toponyms are checked."""
    tsk = target_skeletons(dst, lang)
    bwords = list(dict.fromkeys(re.findall(r"[А-ЯЁІЇЄҐ][а-яёіїєґ’'ʼ]+", bridge or '')))
    miss = []
    for w in candidates(src, initial):
        if lang == 'en' and not is_place(w): continue
        if not _present(w, tsk, lang, bwords): miss.append(w)
    return miss

# Fixed spellings of names the translator tends to garble (people / artists; places are covered by the gazetteer).
# Russian stem regex -> {lang: (required stem, regex of known wrong variants in that language)}
FIXED_NAMES = [
    (re.compile(r'Киевстонер|Київстонер|Kyivstoner', re.I),
     {'uk': ('Київстонер', re.compile(r'Ки[єе]встонер|Київтонер|Киівстонер|Київ-стонер')),
      'en': ('Kyivstoner', re.compile(r'\bK(?:iev|yiv|yyiv|iiv|yev)[- ]?s?t?on+e+r\b|\bKievstoner\b|\bKyivtoner\b', re.I))}),
]
def glossary_names(lang):
    """Line for the translation prompt with the fixed spellings."""
    return '; '.join('%s -> %s' % (rx.pattern.split('|')[0], req[lang][0]) for rx, req in FIXED_NAMES if lang in req)
def fix_names(src, dst, lang):
    """Deterministically repair known misspellings of FIXED_NAMES in a translation (dict of fields), in place."""
    for k, v in (dst or {}).items():
        if not isinstance(v, str) or not isinstance((src or {}).get(k), str): continue
        for rx, req in FIXED_NAMES:
            if lang in req and rx.search(src[k]):
                good, bad = req[lang]
                dst[k] = bad.sub(lambda m: good + (m.group(0)[len(m.group(0).rstrip('аиуоюяеі')):] if lang == 'uk' else ''), v); v = dst[k]
    return dst

def check_fields(src, dst, lang, bridge=None):
    """src/dst: dicts of fields (Russian / translation). Returns {key: [missing names]} (empty = ok)."""
    out = {}
    for k, v in src.items():
        if not v or not isinstance((dst or {}).get(k), str): continue
        m = missing_names(v, dst[k], lang, (bridge or {}).get(k) if bridge else None, initial=(k == 'p'))   # the place field starts with a name
        for rx, req in FIXED_NAMES:                         # fixed spellings must be kept exactly
            mm = rx.search(v)
            if mm and lang in req and req[lang][0].lower() not in dst[k].lower(): m = list(m) + [mm.group(0)]
        if m: out[k] = m
    return out

_UK = {'а':'a','б':'b','в':'v','г':'h','ґ':'g','д':'d','е':'e','є':'ie','ж':'zh','з':'z','и':'y','і':'i','ї':'i','й':'i','к':'k','л':'l','м':'m','н':'n','о':'o',
       'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ь':'','ю':'iu','я':'ia',"'":'','’':'','ʼ':''}
_UK0 = {'є':'ye','ї':'yi','й':'y','ю':'yu','я':'ya'}
def ukr_romanize(w):
    """Ukrainian national transliteration (KMU 2010)."""
    out = []
    for i, ch in enumerate(w.lower()):
        out.append((_UK0 if i == 0 or w[i - 1] == '-' else _UK).get(ch, _UK.get(ch, ch)))
    return '-'.join(p.capitalize() for p in ''.join(out).split('-'))

def hints(names, lang, bridge_text=''):
    """Lines for the retranslation prompt: name in the original -> expected spellings."""
    bwords = list(dict.fromkeys(re.findall(r"[А-ЯЁІЇЄҐ][а-яёіїєґ’'ʼ]+", bridge_text or '')))
    lines = []
    for w in names:
        fx = next((req[lang][0] for rx, req in FIXED_NAMES if lang in req and rx.search(w)), None)
        if fx: lines.append('- %s: write it exactly as %s (the established spelling of this name)' % (w, fx)); continue
        if lang == 'en':
            uk = next((b for b in bwords if any(_sk_match(x, y) for x in cyr_forms(w) for y in cyr_forms(b))), None)
            v = sorted({x for x in place_latin(w) if re.fullmatch(r"[A-Za-z' -]+", x) and "'" not in x})[:3]
            if uk: v.append(ukr_romanize(uk) + ' (romanised from the Ukrainian form %s; may be inflected)' % uk)
            v.append(translit(w).capitalize() + ' (letter-by-letter, inflected form)')
            lines.append('- %s: the SAME place; standard English spelling such as %s' % (w, '; '.join(dict.fromkeys(v))))
        else:
            lines.append('- %s: keep the same name (same place/person) in Ukrainian spelling; do not replace it with a different name' % w)
    return lines
