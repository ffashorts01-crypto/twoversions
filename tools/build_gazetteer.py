#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Builds data/gazetteer.json.gz for pipeline/places.py (run once; the output is committed with the data):
  - 'places': Cyrillic stem of a Ukrainian / Russian place name -> Latin spellings (GeoNames UA.txt + RU.txt, CC BY 4.0, download.geonames.org/export/dump/)
  - 'common': lowercase Russian/Ukrainian words that occur often in lowercase in the article corpus (capitalised mid-sentence they are usually
    not names: «Северный мост», «Оперативное командование»)
    python3 tools/build_gazetteer.py /path/to/geonames_dir"""
import sys, os, re, gzip, json, sqlite3, collections
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pipeline.places import keys
CYR = re.compile(r'[А-Яа-яЁёІіЇїЄєҐґ]')
RU_KEEP_ADM1 = {'09', '41', '10', '86', '61', '38', '47'}     # border / frequently mentioned regions: keep all villages

def main(gdir):
    places = collections.defaultdict(set)
    for cc in ('UA', 'RU'):
        for line in open(os.path.join(gdir, cc + '.txt'), encoding='utf-8'):
            f = line.rstrip('\n').split('\t')
            fc, code, pop, adm1 = f[6], f[7], int(f[14] or 0), f[10]
            if fc not in ('P', 'A', 'H', 'L', 'T', 'S'): continue
            if fc == 'S' and code not in ('NPP', 'PS', 'AIRP', 'AIRB'): continue
            if cc == 'RU' and fc == 'P' and pop < 500 and adm1 not in RU_KEEP_ADM1: continue
            if cc == 'RU' and fc in ('H', 'L', 'T', 'S') : continue
            if cc == 'UA' and fc in ('H', 'T') and code not in ('STM', 'RSV', 'LK', 'BAY', 'SPIT', 'ISL', 'CAPE', 'MT', 'HLL'): continue
            names = [f[1], f[2]] + f[3].split(',')
            lat = {n for n in names if n and not CYR.search(n)}
            for n in names:
                if not n or not CYR.search(n): continue
                for tok in re.findall(r"[А-ЯЁІЇЄҐ][а-яёіїєґ’'ʼ]+", n):
                    if len(tok) >= 4: [places[k].update(lat) for k in keys(tok)]
    common = collections.Counter(); seen = collections.Counter()
    db = sqlite3.connect(os.path.join(ROOT, 'data', 'articles.db'))
    for (t,) in db.execute("select title || ' ' || coalesce(text,'') from articles where lang in ('ru','uk') or grp in ('ru','ua')"):
        for w in re.findall(r"(?<![\w’'ʼ-])[а-яёіїєґ’'ʼ]{4,}", t or ''): common[w.replace('ё', 'е')] += 1
    com = sorted(w for w, n in common.items() if n >= 3)
    out = dict(source='GeoNames (CC BY 4.0) UA+RU; common words from the article corpus', places={k: sorted(v)[:12] for k, v in places.items()}, common=com)
    with gzip.open(os.path.join(ROOT, 'data', 'gazetteer.json.gz'), 'wt', encoding='utf-8') as fh: json.dump(out, fh, ensure_ascii=False)
    print('places', len(out['places']), 'common', len(com))

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '/workspace/geonames')
