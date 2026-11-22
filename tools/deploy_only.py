#!/usr/bin/env python3
"""Rebuild + deploy + IndexNow WITHOUT collecting or calling the LLM (for site-code changes between routine runs).
Uses the same lock / RUNNING marker / wrangler / IndexNow code as pipeline/auto_run.py."""
import os, sys, fcntl, time, json, shlex, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'pipeline'))
import auto_run as A
def main():
    base = os.environ.get('BASE_URL', 'https://twoversions.com').rstrip('/')
    env = dict(os.environ, BASE_URL=base)
    if not env.get('CLOUDFLARE_API_TOKEN'): A.say('CLOUDFLARE_API_TOKEN is not set'); return 1
    marker = os.path.join(A.DATA, 'editor', 'RUNNING')
    if os.path.exists(marker) and time.time() - os.path.getmtime(marker) < 3 * 3600: A.say('editor RUNNING marker -- abort'); return 1
    lock = open(os.path.join(A.DATA, 'update.lock'), 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: A.say('update.lock held -- abort'); return 1
    before = A.snapshot()
    rc, out = A.run([sys.executable, 'build.py'], env=env, timeout=1800)
    for l in out.strip().splitlines()[-5:]: A.say('  |', l[:300])
    if rc != 0: A.say('deploy_only: build failed -- nothing deployed'); return 2
    after = A.snapshot(); urls = A.changed_urls(before, after, base)
    A.say('deploy_only: changed pages: %d' % len(urls))
    _cfd = '/workspace/cfdeploy'; wr = os.environ.get('WRANGLER', _cfd + '/node_modules/.bin/wrangler')
    cmd = '%s pages deploy %s --project-name %s --branch main --commit-dirty=true' % (wr, shlex.quote(A.PUB), os.environ.get('CF_PROJECT', 'twoversions-site'))
    denv = dict(env, CLOUDFLARE_ACCOUNT_ID=os.environ.get('CF_ACCOUNT_ID', 'c8acd4ec6b566fc03896b8a8efb8b1f4'))
    for attempt in range(3):
        rc, out = A.run(cmd, env=denv, cwd=_cfd, timeout=1200)
        for l in out.strip().splitlines()[-2:]: A.say('  |', l)
        if rc == 0: break
        time.sleep(20)
    else: A.say('deploy_only: deploy failed'); return 4
    json.dump(after, open(os.path.join(A.DATA, 'page_hashes.json'), 'w'))
    if urls and '--no-indexnow' not in sys.argv: time.sleep(10); A.indexnow(urls, base)
    A.say('deploy_only: done'); return 0
if __name__ == '__main__': sys.exit(main())
