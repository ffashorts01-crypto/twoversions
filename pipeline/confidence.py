# -*- coding: utf-8 -*-
"""Confidence scale per event, computed mostly in code from features. Labels: confirmed / contradict / both_sides / one_side / insufficient."""
import re
LABELS = {
 'confirmed':    {'icon': '✔', 'ru': 'подтверждено независимыми источниками', 'uk': 'підтверджено незалежними джерелами', 'en': 'confirmed by independent sources'},
 'contradict':   {'icon': '⚠', 'ru': 'стороны противоречат друг другу', 'uk': 'сторони суперечать одна одній', 'en': 'the sides contradict each other'},
 'both_sides':   {'icon': '⇄', 'ru': 'сообщают обе стороны, независимых источников нет', 'uk': 'повідомляють обидві сторони, незалежних джерел немає', 'en': 'both sides report it, no independent sources'},
 'one_side':     {'icon': '◐', 'ru': 'заявляет одна сторона', 'uk': 'заявляє одна сторона', 'en': 'claimed by one side only'},
 'insufficient': {'icon': '?', 'ru': 'недостаточно данных', 'uk': 'недостатньо даних', 'en': 'insufficient data'},
}
EXPLAIN = {
 'confirmed':    {'ru': 'Событие описывают международные или эмигрантские независимые издания, и между группами нет выявленных противоречий в фактах. Это не означает, что независимые СМИ проверили каждую деталь.',
                  'uk': 'Подію описують міжнародні або емігрантські незалежні видання, і між групами немає виявлених суперечностей у фактах. Це не означає, що незалежні медіа перевірили кожну деталь.',
                  'en': 'The event is reported by international or exile independent outlets and no contradictions in the facts between the groups were detected. This does not mean the independent outlets verified every detail.'},
 'contradict':   {'ru': 'Группы источников приводят несовместимые сведения (модель по тексту отметила несовместимые утверждения о фактах; различающиеся цифры показаны в карточке). Сайт не определяет, кто прав.',
                  'uk': 'Групи джерел наводять несумісні відомості (модель за текстом відзначила несумісні твердження про факти; різні цифри показано в картці). Сайт не визначає, хто правий.',
                  'en': 'The groups of sources give incompatible information (the model, reading the texts, flagged incompatible statements of fact; differing figures are shown in the card). The site does not decide who is right.'},
 'both_sides':   {'ru': 'Событие описывают и российские, и украинские источники, но международных независимых источников в подборке нет.',
                  'uk': 'Подію описують і російські, і українські джерела, але міжнародних незалежних джерел у добірці немає.',
                  'en': 'Both Russian and Ukrainian sources describe the event, but there are no international independent sources in the selection.'},
 'one_side':     {'ru': 'Событие описывает только одна сторона (без независимого международного источника); её утверждения приведены как утверждения.',
                  'uk': 'Подію описує лише одна сторона (без незалежного міжнародного джерела); її твердження наведено як твердження.',
                  'en': 'Only one side reports the event (no independent international source); its statements are presented as statements.'},
 'insufficient': {'ru': 'Материалов мало или автоматические проверки качества прошли не полностью (мало источников, неуверенность модели, часть цитат или цифр не подтвердилась).',
                  'uk': 'Матеріалів мало або автоматичні перевірки якості пройдено не повністю (мало джерел, невпевненість моделі, частину цитат або цифр не підтверджено).',
                  'en': 'Few materials, or the automatic quality checks passed only partly (few sources, model uncertainty, some quotes or figures not confirmed).'},
}
ORIGIN_PREFIX = [('РИА', 'ru'), ('ТАСС', 'ru'), ('Минобороны', 'ru'), ('Интерфакс', 'ru'), ('Суспільне', 'ua'), ('Українська', 'ua'), ('РБК', 'ua'), ('Kyiv Independent', 'ua'),
                 ('BBC', 'intl'), ('Associated', 'intl'), ('The Guardian', 'intl'), ('DW', 'intl'), ('Al Jazeera', 'intl'), ('Meduza', 'exile')]
def origin(source):
    for p, o in ORIGIN_PREFIX:
        if source.startswith(p): return o
    return 'intl'
LEAN = {'ru': 'RU', 'ua': 'UA', 'intl': 'IND', 'exile': 'IND'}

def features(arts, rel_agreement=None, model_uncertainty=None, model_contradiction=None, numeric_conflict=False, numeric_cross=False,
             quote_check='pass', numbers_ungrounded=0):
    """arts: list of dict(source, grp, text) -- the relevant articles of the event."""
    src = {a['source'] for a in arts}
    groups = sorted({a['grp'] for a in arts})
    leans = {LEAN[origin(a['source'])] for a in arts}
    ind_intl = {a['source'] for a in arts if a['grp'] == 'ind' and origin(a['source']) in ('intl', 'exile')}
    short_only = bool(arts) and all(len(a['text']) < 250 for a in arts)
    return dict(n_articles=len(arts), n_sources=len(src), groups=groups, n_groups=len(groups), leans=sorted(leans), n_leans=len(leans), ind_intl=len(ind_intl),
                numeric_conflict=bool(numeric_conflict), numeric_cross=bool(numeric_cross), quote_check=quote_check, rel_agreement=rel_agreement,
                model_uncertainty=model_uncertainty, model_contradiction=model_contradiction, short_only=short_only, numbers_ungrounded=numbers_ungrounded)

def label(f):
    reasons = []
    if f['n_sources'] < 2: reasons.append('few_sources')
    if f['rel_agreement'] is not None and f['rel_agreement'] < 0.6: reasons.append('low_relevance_agreement')
    if f['model_uncertainty'] == 'high': reasons.append('model_uncertain')
    if f['quote_check'] != 'pass': reasons.append('quote_check_partial')
    if f['short_only']: reasons.append('titles_only')
    if f['numbers_ungrounded']: reasons.append('numbers_ungrounded')
    if reasons: return 'insufficient', reasons
    # A contradiction label needs the model to report a real contradiction between sides (it reads the actual wording);
    # code-detected number differences alone are NOT enough: counts like 'launched' vs 'shot down' or 'injured' at different
    # times are different metrics. Numeric differences are still shown in the card and route the event to the stronger model.
    contra = bool(f['model_contradiction']) and f['n_leans'] >= 2
    if contra: return 'contradict', ['model_reported_contradiction'] + (['numeric_difference'] if (f['numeric_conflict'] or f['numeric_cross']) else [])
    if f['ind_intl'] >= 2 or (f['ind_intl'] >= 1 and f['n_leans'] >= 2): return 'confirmed', ['independent_sources:%d' % f['ind_intl']]
    if 'RU' in f['leans'] and 'UA' in f['leans']: return 'both_sides', []
    return 'one_side', ['single_lean:' + ','.join(f['leans'])]

def score(f, lab):
    """0..100, used for sorting / tooltips only."""
    s = 20 * min(f['n_groups'], 3) + 10 * min(f['ind_intl'], 2) + 5 * min(f['n_sources'], 4)
    if f['rel_agreement'] is not None: s += int(10 * f['rel_agreement'])
    if f['quote_check'] == 'pass': s += 10
    if f['numeric_conflict'] or f['numeric_cross']: s -= 10
    if f['model_uncertainty'] == 'medium': s -= 5
    if f['model_uncertainty'] == 'high': s -= 20
    if lab == 'insufficient': s = min(s, 40)
    return max(0, min(100, s))

def compute(arts, **kw):
    f = features(arts, **kw); lab, why = label(f)
    return dict(label=lab, why=why, score=score(f, lab), features=f)
