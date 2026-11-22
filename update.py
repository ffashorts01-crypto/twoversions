#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-command auto-update: collect -> cluster -> merge -> relevance filter -> draft/verify/translate cards -> confidence + quality gates -> export -> rebuild site.

    .venv/bin/python update.py                  # full incremental run (cron / GitHub Actions friendly; exit code 0 = ok, 3 = budget cap hit, 2 = build failed)
    .venv/bin/python update.py --dry-run        # no DB writes, no LLM calls: lists what would be collected / clustered / processed
Environment: LLM_PROVIDER (qwen|openai, default qwen), DASHSCOPE_API_KEY or OPENAI_API_KEY (never logged), LLM_BASE_URL, MODEL_BULK, MODEL_HARD, LLM_BUDGET_USD (hard cap over all recorded spend, default 7.0), LLM_RUN_BUDGET_USD, CONTACT_EMAIL, BASE_URL (see README)."""
import os, sys, argparse, json, subprocess, datetime as dt, time, fcntl
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pipeline import common, collect, cluster, relevance, cards, dedup
from pipeline.common import CFG, log, now_iso, BERLIN
from pipeline.llm import LLM, BudgetExceeded

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='no DB writes, no LLM calls, no build')
    ap.add_argument('--since', default='2026-09-25', help='collection start for sources that have no articles yet (YYYY-MM-DD, Europe/Berlin)')
    ap.add_argument('--sources', default='', help='comma-separated subset of sources to collect (default: all)')
    ap.add_argument('--backfill-since', default='', help='re-scan the selected sources from this date (YYYY-MM-DD) even if newer articles exist; known URLs are skipped')
    ap.add_argument('--skip-collect', action='store_true'); ap.add_argument('--skip-llm', action='store_true', help='collect + build only')
    ap.add_argument('--no-build', action='store_true'); ap.add_argument('--budget', type=float, help='hard cap (USD) for total recorded spend, overrides LLM_BUDGET_USD')
    ap.add_argument('--run-budget', type=float, help='cap (USD) for this run only')
    ap.add_argument('--only-events', default='', help='comma-separated event ids to (re)process in the card stage')
    a = ap.parse_args()
    if a.budget: CFG['BUDGET_USD'] = a.budget
    if a.run_budget: CFG['RUN_BUDGET_USD'] = a.run_budget
    lg = common.get_logger(to_file=not a.dry_run)
    run_id = dt.datetime.now(BERLIN).strftime('%Y%m%d-%H%M%S')
    lock = open(os.path.join(common.DATA, 'update.lock'), 'w')
    if os.environ.get('UPDATE_LOCK_HELD') != '1':          # pipeline/auto_run.py already holds the lock
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: log.error('another update.py run is in progress'); return 1
    db = common.connect()
    from pipeline import llm as llm_mod
    if not a.dry_run and not a.skip_llm and not llm_mod.api_key():
        log.error('%s is not set (LLM_PROVIDER=%s)', llm_mod.key_env(), CFG['PROVIDER']); return 1
    llm = LLM(db, run_id, dry_run=a.dry_run or a.skip_llm)
    log.info('=== update run %s %s | provider '+CFG['PROVIDER']+' '+CFG['MODEL_BULK']+'/'+CFG['MODEL_HARD']+' | spent so far $%.4f of cap $%.2f ===', run_id, '[DRY RUN]' if a.dry_run else '', llm.total_before, CFG['BUDGET_USD'])
    if CFG.get('NEW_ONLY_SINCE'): log.info('new-news-only cutoff %s: older articles are not collected, clustered or carded', CFG['NEW_ONLY_SINCE'])
    db.execute('insert or ignore into runs(id,started,args) values(?,?,?)', (run_id, now_iso(), json.dumps(vars(a)))); db.commit()
    stats = {}; code = 0
    try:
        if not a.skip_collect:
            since = dt.datetime.strptime(a.since, '%Y-%m-%d').replace(tzinfo=BERLIN)
            stats['collect'] = collect.run_collect(db, run_id, since, only=[x for x in a.sources.split(',') if x] or None, dry_run=a.dry_run,
                                           force_since=dt.datetime.strptime(a.backfill_since, '%Y-%m-%d').replace(tzinfo=BERLIN) if a.backfill_since else None)
        if not a.skip_llm:
            cluster.init_hand(db) if not a.dry_run else None
            if not a.dry_run: log.info('reopened %d recent editor events as attach targets', cluster.reopen_editor_events(db))
            # close events that have had no new articles for OPEN_DAYS (they stop being attach targets)
            if not a.dry_run:
                cutoff = (dt.datetime.now(BERLIN) - dt.timedelta(days=CFG['OPEN_DAYS'])).astimezone(dt.timezone.utc).isoformat()
                db.execute("update events set status='closed' where status='open' and last_art<?", (cutoff,)); db.commit()
            stats['cluster'] = cluster.run_cluster(db, llm, run_id, dry=a.dry_run)
            log.info('cluster: %s', stats['cluster'])
            last_merge = db.execute("select max(ts) from llm_usage where stage='merge'").fetchone()[0] or ''
            every = float(os.environ.get('MERGE_EVERY_HOURS', '4'))
            if last_merge and last_merge > (dt.datetime.now(BERLIN) - dt.timedelta(hours=every - 0.2)).isoformat(timespec='seconds'):
                log.info('merge: skipped (last merge pass %s, runs every %g h)', last_merge, every)
            else:
                stats['merge'] = cluster.run_merge(db, llm, run_id, dry=a.dry_run); log.info('merge: %s', stats['merge'])
            if not a.dry_run:
                stats['relevance_hand'] = relevance.run_hand_relevance(db, llm, cluster.hand_events())
            stats['relevance'] = relevance.run_relevance(db, llm, run_id, dry=a.dry_run); log.info('relevance: %s', stats['relevance'])
            if not a.dry_run and not a.only_events:
                try: stats['dedup'] = dedup.run_dedup(db, llm); log.info('dedup: %s', stats['dedup'])
                except BudgetExceeded: raise
                except Exception as ex: log.exception('dedup failed (ignored): %s', ex)
            only = {int(x) for x in a.only_events.split(',') if x} or None
            stats['cards'] = cards.run_cards(db, llm, run_id, dry=a.dry_run, only=only); log.info('cards: %s', stats['cards'])
    except BudgetExceeded as e:
        log.error('BUDGET CAP: %s -- stopping with partial results', e); code = 3
    finally:
        if not a.dry_run:
            cards.merged_cleanup(db)
            meta = cards.export(db); stats['export'] = meta
            log.info('export: %s', meta)
    if not a.dry_run and not a.no_build:
        env = dict(os.environ)
        r = subprocess.run([sys.executable, os.path.join(HERE, 'build.py')], cwd=HERE, env=env, capture_output=True, text=True)
        tail = r.stdout.strip().splitlines()[-6:] + r.stderr.strip().splitlines()[-6:]
        log.info('build exit=%d: %s', r.returncode, ' | '.join(tail)[:600])
        if r.returncode != 0: code = code or 2
    stats['llm'] = dict(run_spend=round(llm.run_spend, 5), total_spend=round(llm.total_spend, 5), calls=llm.calls, by_model=llm.by_model)
    if llm.filtered or llm.filter_probes:
        stats['content_filter'] = dict(rejected=llm.filtered, probes=llm.filter_probes, isolated=llm.filter_isolated)
        log.info('content filter: %d request(s) rejected (data_inspection_failed), %d probe(s), %d article(s)/line(s) isolated', llm.filtered, llm.filter_probes, llm.filter_isolated)
    log.info('LLM spend this run: $%.4f (%d calls); total recorded: $%.4f of cap $%.2f', llm.run_spend, llm.calls, llm.total_spend, CFG['BUDGET_USD'])
    if not a.dry_run:
        db.execute('update runs set finished=?, spend=?, stats=? where id=?', (now_iso(), llm.run_spend, json.dumps(stats, ensure_ascii=False, default=str), run_id)); db.commit()
    return code

if __name__ == '__main__':
    sys.exit(main())
