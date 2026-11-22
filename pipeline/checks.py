# -*- coding: utf-8 -*-
"""Deterministic quality checks in code: verbatim quotes, number grounding, numeric-conflict detection, translation sanity."""
import re

def ws_regex(q):
    words = q.split()
    return re.compile(r'\s+'.join(re.escape(w) for w in words)) if words else None

def verify_quote(q, text):
    """Return the exact verbatim substring of text matching q (whitespace differences repaired), or None."""
    q = (q or '').strip()
    if len(q) < 12: return None
    if q in text: return q
    rx = ws_regex(q)
    m = rx.search(text) if rx else None
    return m.group(0) if m else None

NUM = re.compile(r'\d[\d\s\u00a0.,]*\d|\d')
def numbers(s):
    out = set()
    for m in NUM.finditer(s or ''):
        raw = m.group(0)
        out.add(re.sub(r'[\s\u00a0.,]', '', raw))                      # 100 000 / 100,000 / 100.000 -> 100000
        for part in re.split(r'[\s\u00a0]+', raw.strip()):             # also each space-separated token (dates like "25 30")
            if part: out.add(re.sub(r'[.,]', '', part))
        if re.fullmatch(r'\d+[.,]\d+', raw): out.add(raw.replace(',', '.'))   # decimals like 4.5
    return out

def number_tokens_in_summary(s):
    toks = []
    for m in re.finditer(r'(?<![\w.])(\d{1,3}(?:[\u00a0 ]\d{3})+|\d+(?:[.,]\d+)?)(?!\d)', s or ''):
        raw = m.group(1)
        key = re.sub(r'[\u00a0 ]', '', raw)
        if re.fullmatch(r'\d+,\d+', key): key = key.replace(',', '.')   # decimal comma (ru/uk) == decimal point (en)
        toks.append((key, raw))
    return toks

def ungrounded_numbers(summary, source_texts):
    """Numbers in `summary` that do not occur in any of the `source_texts` (allowing x1000 / x1e6 scale words: тыс., млн, thousand, million)."""
    allowed = set()
    for t in source_texts:
        for n in numbers(t):
            allowed.add(n)
            if n.isdigit() and len(n) < 7: allowed.add(n + '000'); allowed.add(n + '000000')
    bad = []
    for key, raw in number_tokens_in_summary(summary):
        k = key.replace(',', '.')
        if k in allowed or key.replace('.', '').replace(',', '') in allowed: continue
        if re.fullmatch(r'20\d\d', key): continue                       # years
        bad.append(raw)
    return bad

CAS = re.compile(r'(\d[\d\s\u00a0]*)\s+(?:\w+\s+){0,2}?(?:погиб\w*|загин\w*|убит\w*|уби\w*|ранен\w*|поранен\w*|постраждал\w*|пострадав\w*|killed|dead|died|injured|wounded|casualties)', re.I)
CAS2 = re.compile(r'(?:killed|injured|wounded|died|погибли|загинули|ранены|постраждали|погибших|загиблих|раненых|поранених)\s+(?:at least\s+|не менее\s+|щонайменше\s+|как минимум\s+)?(\d+)', re.I)
def casualty_numbers(text):
    out = set()
    for m in CAS.finditer(text or ''):
        n = re.sub(r'\D', '', m.group(1))
        if n and 0 < int(n) < 100000 and not re.fullmatch(r'20\d\d', n): out.add(int(n))
    for m in CAS2.finditer(text or ''):
        if m.group(1): out.add(int(m.group(1)))
    return out

CAS_WORD = re.compile(r'потерпіл|потерпевш|погиб|загин|убит|уби[лт]|вби[лтв]|медик|медиц|врач|лікар|medic|смерт|ранен|ранил|поранен|постраждал|пострадав|пострадал|травм|госпитал|жертв|тел[оа]|killed|kill|dead|death|died|injur|wound|casualt|hospital|victim', re.I)
DATE_AFTER = re.compile(r'\s*(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня|January|February|March|April|May|June|July|August|September|October|November|December|год|рок|year|час|годин|hour|минут|хвилин|minute|км|km|дрон|БПЛА|drone|ракет|missile)', re.I)
def _num_near_cas(n, text, win=90):
    """True if the number n occurs in `text` within `win` characters of a casualty word."""
    ds = str(n); groups = []
    while ds: groups.insert(0, ds[-3:]); ds = ds[:-3]
    pat = r'(?<![\d])' + r'[\s\u00a0\u202f,.]?'.join(groups) + r'(?![\d])'
    for m in re.finditer(pat, text or ''):
        if DATE_AFTER.match(text, m.end()): continue                     # '12 листопада', '12 drones', '12 km' are not casualty figures
        if CAS_WORD.search(text[max(0, m.start() - win): m.end() + win]): return True
    return False
def casualty_mismatch(summary, source_texts):
    """Casualty figures in the summary ('12 погибших', 'ранены 5') that no source gives in a casualty context.
    The plain number check (ungrounded_numbers) accepts '12' if it appears anywhere (e.g. '12 ноября'); this one requires the
    figure to stand next to a casualty word in at least one source (any language)."""
    bad = []
    nums = set()
    for m in list(CAS.finditer(summary or '')) + list(CAS2.finditer(summary or '')):
        raw = m.group(1); n = re.sub(r'\D', '', raw)
        if not n or DATE_AFTER.match(summary, m.start(1) + len(raw.rstrip())): continue      # '1 октября погиб' is a date
        if 0 < int(n) < 100000 and not re.fullmatch(r'20\d\d', n): nums.add(int(n))
    for n in sorted(nums):
        if any(n in casualty_numbers(t) or _num_near_cas(n, t) for t in source_texts): continue
        bad.append(str(n))
    return bad

def strip_sentences_with(text, bad_numbers, min_left=60):
    """Remove the sentences of `text` that contain any of `bad_numbers`. Returns the shortened text, or None when too little would remain."""
    if not text or not bad_numbers: return text
    sents = re.split(r'(?<=[.!?…])\s+(?=[«"А-ЯЁA-Z0-9])', text.strip())
    keys = {re.sub(r'[\u00a0 ]', '', b).replace(',', '.') for b in bad_numbers}
    keep = [x for x in sents if not ({k for k, _ in number_tokens_in_summary(x)} & keys)]
    out = ' '.join(keep).strip()
    if len(keep) == len(sents): return text
    return out if len(out) >= min_left else None

def prelim_numeric_conflict(arts):
    """Cheap pre-check (before summarising): casualty figures that differ between groups -> route the event straight to the stronger model."""
    per = {}
    for a in arts:
        per.setdefault(a['grp'], set()).update(casualty_numbers(a['title'] + ' ' + a['text']))
    sets = [s for s in per.values() if s]
    if len(sets) < 2: return False
    union = set().union(*sets); inter = set.intersection(*sets)
    return len(union - inter) > 0

# ---------------------------------------------------------------------------------------------- translation sanity
LAT = re.compile(r'[A-Za-z]'); CYR = re.compile(r'[А-Яа-яЁёІіЇїЄєҐґ]')
def script_ok(text, lang):
    if not text: return True
    lat, cyr = len(LAT.findall(text)), len(CYR.findall(text))
    if lang == 'en': return lat >= 0.8 * (lat + cyr) if (lat + cyr) else True
    if lang == 'uk':
        if cyr < 0.55 * (lat + cyr): return False
        return len(re.findall(r'[ыэъЫЭЪ]', text)) <= 1               # Russian-only letters should not appear in Ukrainian text
    if lang == 'ru': return cyr >= 0.7 * (lat + cyr) if (lat + cyr) else True
    return True

def _thousands(k):
    """'5.000' (from English '5,000') -> '5000'; decimals like '4.5' stay."""
    return k.replace('.', '') if re.fullmatch(r'\d{1,3}(?:\.\d{3})+', k) else k
def numbers_equal(src, dst):
    a = sorted(_thousands(k) for k, _ in number_tokens_in_summary(src)); b = sorted(_thousands(k) for k, _ in number_tokens_in_summary(dst))
    if a == b: return True
    # scale words may change the representation (e.g. "100 тысяч" vs "100 thousand") but digits stay equal; allow subset differences only for 1-digit numbers
    return set(a) - set(b) <= {x for x in a if len(x) == 1} and set(b) - set(a) <= {x for x in b if len(x) == 1}
