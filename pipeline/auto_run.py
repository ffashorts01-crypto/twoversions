#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autonomous every-3-hours run (no human / no Grok Bot):
   collect -> LLM cluster / relevance / write+extend cards (default Qwen: qwen3.7-flash, qwen3.7-plus for hard cases; LLM_PROVIDER=openai -> gpt-6-luna / gpt-6.1-sol) -> code checks + quality gates
   -> editor_import --check (all editor cards still valid) -> build.py (BASE_URL) -> deploy to Cloudflare Pages -> IndexNow (changed URLs only)
   -> cost log (logs/cost.csv).

    python3 pipeline/auto_run.py                 # full run
    python3 pipeline/auto_run.py --no-deploy     # everything except deploy + IndexNow (local test)
Env: LLM_PROVIDER (qwen|openai), DASHSCOPE_API_KEY (or OPENAI_API_KEY), LLM_BASE_URL, MODEL_BULK, MODEL_HARD, CLOUDFLARE_API_TOKEN (never printed), LLM_DAILY_BUDGET_USD (default 1.0 ≈ $30/mo), LLM_RUN_BUDGET_USD (default 0.45),
     LLM_BUDGET_USD (lifetime cap, default 1000), CLUSTER_SINCE (default: 3 days ago), BASE_URL (default https://twoversions.com),
     Importance: high-importance clusters first; low deferred when remaining budget is tight (see pipeline/importance.py).
     CF_ACCOUNT_ID, CF_PROJECT (twoversions-site), WRANGLER (command, default 'npx --yes wrangler@3').
Exit: 0 ok, 1 lock busy / config error, 2 build or check failed (nothing deployed), 3 budget cap hit (partial results still published), 4 deploy failed."""
import os, sys, fcntl, time, json, re, hashlib, argparse, subprocess, datetime as dt, urllib.request, shlex
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data'); PUB = os.path.join(ROOT, 'public'); LOGS = os.path.join(ROOT, 'logs')
os.makedirs(LOGS, exist_ok=True)

def say(*a):
    line = '%s auto_run: %s' % (dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ' '.join(str(x) for x in a))
    line = re.sub(r'sk-[A-Za-z0-9_-]{8,}', 'sk-***', line)
    print(line, flush=True)
    with open(os.path.join(LOGS, 'auto_run.log'), 'a', encoding='utf-8') as f: f.write(line + '\n')

def run(cmd, env=None, cwd=ROOT, timeout=3600):
    say('$', cmd if isinstance(cmd, str) else ' '.join(cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
    out = (r.stdout + r.stderr)
    out = re.sub(r'sk-[A-Za-z0-9_-]{8,}', 'sk-***', out)
    for k in ('DASHSCOPE_API_KEY', 'OPENAI_API_KEY', 'CLOUDFLARE_API_TOKEN', 'TELEGRAM_BOT_TOKEN'):
        v = os.environ.get(k)
        if v: out = out.replace(v, '***')
    return r.returncode, out

def snapshot():
    h = {}
    for d, _, fs in os.walk(PUB):
        for f in fs:
            if f.endswith('.html'):
                p = os.path.join(d, f)
                with open(p, 'rb') as fh: h[os.path.relpath(p, PUB)] = hashlib.sha1(fh.read()).hexdigest()
    return h

def changed_urls(before, after, base):
    urls = []
    for rel, hv in after.items():
        if before.get(rel) == hv: continue
        if rel in ('404.html',): continue
        path = '/' + rel.replace(os.sep, '/')
        path = path[:-len('index.html')] if path.endswith('/index.html') or path == '/index.html' else path
        urls.append(base + path)
    return sorted(urls)

def indexnow(urls, base):
    key = open(os.path.join(DATA, 'indexnow.key')).read().strip()
    host = base.split('//')[1]
    ok = True
    for ep in ('https://api.indexnow.org/indexnow', 'https://yandex.com/indexnow'):
        for i in range(0, len(urls), 9000):
            body = json.dumps({'host': host, 'key': key, 'keyLocation': '%s/%s.txt' % (base, key), 'urlList': urls[i:i + 9000]}).encode()
            for attempt in range(3):
                try:
                    r = urllib.request.urlopen(urllib.request.Request(ep, data=body, headers={'Content-Type': 'application/json; charset=utf-8'}), timeout=60)
                    say('indexnow', ep, len(urls[i:i + 9000]), 'urls ->', r.status); break
                except Exception as e:
                    say('indexnow', ep, 'attempt', attempt + 1, 'error', getattr(e, 'code', type(e).__name__)); time.sleep(5)
            else: ok = False
    return ok

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--no-deploy', action='store_true'); ap.add_argument('--skip-collect', action='store_true')
    ap.add_argument('--no-indexnow', action='store_true')
    a = ap.parse_args()
    base = os.environ.get('BASE_URL', 'https://twoversions.com').rstrip('/')
    env = dict(os.environ, BASE_URL=base, UPDATE_LOCK_HELD='1')
    env.setdefault('LLM_DAILY_BUDGET_USD', '1'); env.setdefault('LLM_RUN_BUDGET_USD', '0.45'); env.setdefault('LLM_BUDGET_USD', '1000')
    if not env.get('CLUSTER_SINCE'):
        env['CLUSTER_SINCE'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%S')
    prov = (env.get('LLM_PROVIDER') or 'qwen').strip().lower()
    kenv = {'qwen': 'DASHSCOPE_API_KEY', 'openai': 'OPENAI_API_KEY'}.get(prov)
    if not kenv: say('LLM_PROVIDER must be qwen or openai'); return 1
    if not env.get(kenv): say('%s is not set (LLM_PROVIDER=%s)' % (kenv, prov)); return 1
    if not a.no_deploy and not env.get('CLOUDFLARE_API_TOKEN'): say('CLOUDFLARE_API_TOKEN is not set'); return 1
    # --- coordination with the Grok Bot editor routine: same flock + its RUNNING marker
    marker = os.path.join(DATA, 'editor', 'RUNNING')
    if os.path.exists(marker) and time.time() - os.path.getmtime(marker) < 3 * 3600:
        say('editor routine is running (data/editor/RUNNING) -- skipping this run'); return 0
    lock = open(os.path.join(DATA, 'update.lock'), 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: say('data/update.lock is held by another run -- skipping this run'); return 0
    t0 = time.time(); code = 0
    manifest = os.path.join(DATA, 'page_hashes.json')
    before = snapshot()
    if not before and os.path.exists(manifest):          # fresh checkout (GitHub Actions): compare with the last deployed build
        before = json.load(open(manifest))
    args = [sys.executable, 'update.py', '--no-build']
    if a.skip_collect: args.append('--skip-collect')
    rc, out = run(args, env=env, timeout=80 * 60)
    tail = out.strip().splitlines()
    for l in tail[-25:]: say('  |', l)
    if rc == 3: code = 3; say('budget cap reached -- publishing partial results')
    elif rc not in (0,): say('update.py failed rc=%d -- nothing deployed' % rc); return 2
    spend = next((l for l in reversed(tail) if 'LLM spend this run' in l), '')
    m = re.search(r'\$([0-9.]+) \((\d+) calls\)', spend); run_cost = float(m.group(1)) if m else 0.0; calls = int(m.group(2)) if m else 0
    cards_line = next((l for l in reversed(tail) if ' cards: {' in l), '')
    # --- weekly review: generated once per closed ISO week (no-op on most runs; tools/weekly.py caps it at WEEKLY_MAX_USD)
    if rc != 3:
        wrc, wout = run([sys.executable, 'tools/weekly.py', '--auto'], env=env, timeout=15 * 60)
        for l in wout.strip().splitlines()[-3:]: say('  |', l)
        wm = re.search(r'LLM spend this run: \$([0-9.]+) \((\d+) calls\)', wout)
        if wm: run_cost += float(wm.group(1)); calls += int(wm.group(2))
        if wrc != 0: say('weekly.py rc=%d (ignored)' % wrc)
    # --- all editor cards must still pass their checks (an article stolen by the auto path etc.)
    rc, out = run([sys.executable, 'tools/editor_import.py', '--check', '--quiet'], env=env)
    say('  |', out.strip().splitlines()[-1] if out.strip() else '')
    if rc != 0: say('editor_import --check failed -- nothing deployed'); return 2
    rc, out = run([sys.executable, 'build.py'], env=env, timeout=1800)
    for l in out.strip().splitlines()[-4:]: say('  |', l)
    if rc != 0: say('build.py failed -- nothing deployed'); return 2
    after = snapshot()
    urls = changed_urls(before, after, base)
    say('changed pages: %d' % len(urls))
    deployed = False
    if not a.no_deploy and not urls and not os.path.exists(manifest): urls = ['%s/' % base]   # first run: make sure the site is deployed once
    if not a.no_deploy and urls:
        _cfd = '/workspace/cfdeploy'
        _cfw = _cfd + '/node_modules/.bin/wrangler'
        wr = os.environ.get('WRANGLER', _cfw if os.path.exists(_cfw) else 'npx --yes wrangler@3')
        cmd = '%s pages deploy %s --project-name %s --branch main --commit-dirty=true' % (wr, shlex.quote(PUB), os.environ.get('CF_PROJECT', 'twoversions-site'))
        denv = dict(env, CLOUDFLARE_ACCOUNT_ID=os.environ.get('CF_ACCOUNT_ID', 'c8acd4ec6b566fc03896b8a8efb8b1f4'))
        for attempt in range(3):
            rc, out = run(cmd, env=denv, cwd=os.environ.get('WRANGLER_CWD', _cfd if os.path.isdir(_cfd) else ROOT), timeout=1200)
            for l in out.strip().splitlines()[-3:]: say('  |', l)
            if rc == 0: deployed = True; break
            time.sleep(20)
        if not deployed: say('deploy failed'); code = 4
        else:
            json.dump(after, open(manifest, 'w'))
            time.sleep(10)
            try: st = urllib.request.urlopen(urllib.request.Request(base + '/ru/', headers={'User-Agent': 'Mozilla/5.0 (twoversions auto_run live check)'}), timeout=30).status   # CF blocks the Python-urllib UA (403)
            except Exception as e: st = getattr(e, 'code', type(e).__name__)
            say('live check %s/ru/ -> %s' % (base, st))
            if not a.no_indexnow: indexnow(urls, base)
            if st == 200 and not a.no_deploy:          # Telegram channel posts only once the new pages are live; never fails the run
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    import telegram_post
                    telegram_post.safe_run(fast=os.environ.get('TG_FAST') == '1')
                except Exception as e: say('telegram: ERROR (ignored): %s' % type(e).__name__)
    elif not urls: say('no page changed -- deploy skipped')
    if a.no_deploy: json.dump(after, open(manifest + '.nodeploy', 'w'))
    with open(os.path.join(LOGS, 'cost.csv'), 'a', encoding='utf-8') as f:
        f.write('%s,%.5f,%d,%d,%d,%d,%s\n' % (dt.datetime.now().isoformat(timespec='seconds'), run_cost, calls, len(urls), int(deployed), int(time.time() - t0), json.dumps(cards_line.split('cards: ')[-1]) if cards_line else ''))
    say('done code=%d run_cost=$%.4f calls=%d changed=%d deployed=%s %.0fs' % (code, run_cost, calls, len(urls), deployed, time.time() - t0))
    return code

if __name__ == '__main__':
    sys.exit(main())
