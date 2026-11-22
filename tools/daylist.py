#!/usr/bin/env python3
"""Editor helper: list unassigned war-related articles of one day (Europe/Berlin).  python3 tools/daylist.py 2026-10-05 [--all] [--grep REGEX] [--grp ru|ua|ind]"""
import sys, os, re, sqlite3, argparse
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser(); ap.add_argument('day'); ap.add_argument('--all', action='store_true'); ap.add_argument('--grep'); ap.add_argument('--grp'); ap.add_argument('--to'); ap.add_argument('--w', type=int, default=105)
a = ap.parse_args()
db = sqlite3.connect(os.path.join(HERE, 'data', 'articles.db'))
q = "select a.id,a.source,a.grp,a.date_local,a.title,length(a.text),a.text from articles a where substr(a.date_local,1,10)>=? and substr(a.date_local,1,10)<=?"
if not a.all: q += " and a.military=1 and a.id not in (select article_id from event_articles)"
rows = db.execute(q + " order by a.grp, a.source, a.date_local", (a.day, a.to or a.day)).fetchall()
SH = {'РИА Новости': 'RIA', 'ТАСС (tass.com, EN)': 'TASS', 'ТАСС (tass.ru, RU, RSS)': 'TASSr', 'Минобороны РФ (Telegram @mod_russia)': 'MoD', 'Интерфакс': 'IFX', 'Суспільне': 'SUS', 'Українська правда': 'UP',
      'РБК-Україна': 'RBC', 'Kyiv Independent': 'KI', 'BBC News': 'BBC', 'BBC News Русская служба': 'BBCr', 'Associated Press': 'AP', 'The Guardian': 'GU', 'DW': 'DW', 'DW (RU)': 'DWr', 'Al Jazeera': 'AJ', 'Meduza': 'MED', 'Meduza (EN)': 'MEDe'}
n = 0
for r in rows:
    if a.grp and r[2] != a.grp: continue
    if a.grep and not re.search(a.grep, r[4] + ' ' + r[6][:600], re.I): continue
    n += 1
    print('%d %s %s %s %s' % (r[0], SH.get(r[1], r[1][:6]), r[3][5:16].replace('T', ' ') if a.to else r[3][11:16], re.sub(r'\s+', ' ', r[4])[:a.w], '' if r[5] > 400 else '[%d]' % r[5]))
print('--', n, 'articles', file=sys.stderr)
