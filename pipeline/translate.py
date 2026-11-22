# -*- coding: utf-8 -*-
"""ru -> uk / en translation of title, place, diverge block and side summaries (Luna), with code checks; quotes are never translated."""
import re, json, sys, os
from .common import CFG, log, ROOT
from . import checks, places

LANG_NAME = {'uk': 'Ukrainian', 'en': 'English'}
SYSTEM = """You are a precise translator. Translate the Russian JSON values into %(lang)s. Keep it neutral and faithful: do not add, remove or soften any claim; keep every attribution ("the Russian Defense Ministry says", "according to..."); keep every number, date and name exactly (transliterate personal names letter by letter, never change a surname; e.g. Плугатарев -> Плугатарьов / Plugatarev); do not translate or alter quoted material inside « » or "" beyond translating the surrounding sentence. Null values stay null. Use these outlet names: %(glossary)s.
Return ONLY JSON with exactly the same keys as the input."""

def glossary(lang):
    sys.path.insert(0, os.path.join(ROOT, 'src')); import ui
    return '; '.join('%s' % v for k, v in sorted(ui.SRC_NAME[lang].items()) if not k.startswith('ТАСС (tass.ru'))

def check(src, dst, lang):
    problems = []
    if not isinstance(dst, dict): return ['not an object']
    for k, v in src.items():
        w = dst.get(k)
        if v is None:
            if w is not None: problems.append('%s should be null' % k)
            continue
        if not isinstance(w, str) or not w.strip(): problems.append('%s missing' % k); continue
        r = len(w) / max(1, len(v))
        if not (0.45 <= r <= 2.3): problems.append('%s length ratio %.2f' % (k, r))
        if not checks.script_ok(w, lang): problems.append('%s wrong script/language' % k)
        if not checks.numbers_equal(v, w): problems.append('%s numbers differ' % k)
    return problems

def _log_places(lang, src, miss, stage):
    try:
        with open(os.path.join(ROOT, 'logs', 'place_names.log'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(dict(ts=__import__('datetime').datetime.now().isoformat(timespec='seconds'), lang=lang, stage=stage, missing=miss,
                                    title=src.get('t')), ensure_ascii=False) + '\n')
    except OSError: pass

def translate(llm, src, lang, bridge=None):
    """src: dict(t,p,d,ru,ua,ind) in Russian. bridge: the accepted Ukrainian translation (used by the English place-name check).
    Returns (dict or None, path). Place names / surnames are checked in code (pipeline/places.py): on a mismatch one retranslation
    with explicit spellings; if names still do not match, the translation is withheld (None)."""
    user = json.dumps(src, ensure_ascii=False)
    system = SYSTEM % dict(lang=LANG_NAME[lang], glossary=glossary(lang))
    if places.glossary_names(lang): system += '\nFixed spellings of names: %s.' % places.glossary_names(lang)
    path = []
    fb = ''
    place_retry = False
    models = [CFG['MODEL_TRANSLATE'], CFG['MODEL_TRANSLATE'], CFG['MODEL_HARD']]
    i = 0
    while i < len(models):
        model = models[i]; i += 1
        path.append(model)
        obj, _ = llm.call('translate-' + lang, model, system, user + fb, max_out=6000, effort='low' if model == CFG['MODEL_HARD'] else None)
        probs = check(src, obj, lang)
        if not probs:
            out = {k: (obj[k].strip() if obj[k] is not None else None) for k in src}
            places.fix_names(src, out, lang)
            miss = places.check_fields(src, out, lang, bridge)
            if not miss:
                if place_retry: log.info('translation %s: place names fixed on retry', lang)
                return out, path
            flat = list(dict.fromkeys(w for v in miss.values() for w in v))
            if place_retry:
                log.warning('translation %s withheld: place names/surnames still do not match after retry: %s', lang, ', '.join(flat))
                _log_places(lang, src, miss, 'withheld'); return None, path + ['places']
            log.warning('translation %s: place names/surnames without counterpart (%s) -> retranslating once', lang, ', '.join(flat))
            _log_places(lang, src, miss, 'retry')
            place_retry = True
            btxt = ' '.join(v for v in (bridge or {}).values() if v)
            fb = ('\n\nThe previous translation changed or dropped these proper names (places / people). Each must stay the SAME place or person, '
                  'spelled in standard %s form; never substitute a different town or surname:\n%s\nPrevious (rejected) translation: %s\nReturn the corrected full JSON.'
                  % (LANG_NAME[lang], '\n'.join(places.hints(flat, lang, btxt)), json.dumps(out, ensure_ascii=False)))
            models = models[:i] + [CFG['MODEL_TRANSLATE']]          # exactly one retranslation for the names
            continue
        log.warning('translation %s rejected (%s): %s', lang, model, '; '.join(probs[:5]))
        fb = '\n\nThe previous translation was rejected: ' + '; '.join(probs[:8]) + '. Fix it.'
    return None, path
