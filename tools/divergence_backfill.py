#!/usr/bin/env python3
"""One-time (idempotent) backfill of the divergence label for already published cards that have >= 2 sides and no label yet.
qwen3.7-flash, 12 cards per call, hard cap (default $0.30) — on reaching it the rest stays unlabeled. Results are cached in
data/divergence_cache.json ({card_id: {label, h}}; h = hash of the card text, so an edited card is relabelled by the next backfill
only if it still has no label of its own). New / regenerated cards get the label from the card-writing call itself."""
import os, sys, json, hashlib, sqlite3, argparse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'src'))
os.environ.setdefault('LLM_BUDGET_USD', '1000')
from pipeline.llm import LLM, BudgetExceeded
from pipeline.common import CFG
CACHE = os.path.join(ROOT, 'data', 'divergence_cache.json')
SYSTEM = """You classify comparative news cards about the war in Ukraine. Each card summarises what three groups of outlets report about ONE event: "ru" (Russian state/affiliated), "ua" (Ukrainian), "ind" (independent/international), plus a "diverge" text describing where they differ. For EACH card return how far the groups' versions differ, judged only from the given text:
"agree" = the groups report the same core facts (they may stress different things);
"details" = same core event, but they differ in figures, details, wording of the target, attribution or omissions;
"contradict" = incompatible accounts of a core fact (who struck whom/what, opposite statements, clearly different figures for the same thing, one side denies what the other reports).
Return ONLY JSON: {"labels": {"<card id>": "agree"|"details"|"contradict", ...}} with every card id given."""

def card_text(c):
    return json.dumps(dict(title=c['title'], diverge=c['diverge'][:900], **{g: (c[g]['summary'] or None) and c[g]['summary'][:450] for g in ('ru', 'ua', 'ind')}), ensure_ascii=False)
def h(c): return hashlib.sha1(card_text(c).encode()).hexdigest()[:12]

def all_cards():
    from cards import CARDS
    out = []
    for c in CARDS:
        out.append(dict(id=str(c['id']), title=c['title'], diverge=c['diverge'], own=None, **{g: dict(summary=c[g]['summary']) for g in ('ru', 'ua', 'ind')}))
    d = json.load(open(os.path.join(ROOT, 'data', 'cards_auto.json'), encoding='utf-8'))
    for r in d['cards']:
        if r.get('status') != 'published': continue
        c = r['card']
        out.append(dict(id='a%d' % r['event_id'], title=c['title'], diverge=c['diverge'], own=c.get('divergence'), **{g: dict(summary=c['sides'][g]['summary']) for g in ('ru', 'ua', 'ind')}))
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--cap', type=float, default=0.30); ap.add_argument('--batch', type=int, default=12); ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    todo = [c for c in all_cards() if not c['own'] and sum(1 for g in ('ru', 'ua', 'ind') if c[g]['summary']) >= 2 and (cache.get(c['id']) or {}).get('h') != h(c)]
    print('cards to label: %d (cached: %d)' % (len(todo), len(cache)))
    if a.dry_run or not todo: return 0
    os.environ['LLM_RUN_BUDGET_USD'] = str(a.cap)
    CFG['RUN_BUDGET_USD'] = a.cap
    db = sqlite3.connect(os.path.join(ROOT, 'data', 'articles.db')); db.row_factory = sqlite3.Row
    llm = LLM(db, 'divergence-backfill')
    n = 0
    for i in range(0, len(todo), a.batch):
        chunk = todo[i:i + a.batch]
        user = '\n'.join('CARD id=%s: %s' % (c['id'], card_text(c)) for c in chunk)
        try: obj, _ = llm.call('divergence-backfill', CFG['MODEL_BULK'], SYSTEM, user, max_out=600)
        except BudgetExceeded as ex: print('cap reached: %s' % ex); break
        labels = (obj or {}).get('labels') or {}
        for c in chunk:
            lab = labels.get(c['id'])
            if lab in ('agree', 'details', 'contradict'): cache[c['id']] = dict(label=lab, h=h(c)); n += 1
        json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print('labelled %d cards; spend $%.4f in %d calls' % (n, llm.run_spend, llm.calls))
    return 0
if __name__ == '__main__': sys.exit(main())
