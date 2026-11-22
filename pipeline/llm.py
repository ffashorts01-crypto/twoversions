# -*- coding: utf-8 -*-
"""LLM wrapper with spend tracking and hard budget caps. Provider is configurable:
  LLM_PROVIDER=qwen   (default) Alibaba Cloud Model Studio, OpenAI-compatible Chat Completions; key DASHSCOPE_API_KEY
                      base URL LLM_BASE_URL (default https://dashscope-intl.aliyuncs.com/compatible-mode/v1, Singapore / international)
  LLM_PROVIDER=openai OpenAI Responses API; key OPENAI_API_KEY (optional fallback)
Model names: MODEL_BULK / MODEL_HARD (defaults per provider, see common.PROVIDERS). Keys are never logged."""
import os, re, json, time, threading, requests
from .common import CFG, now_iso, log, DBLOCK, PROVIDERS

class BudgetExceeded(Exception): pass
class ContentFiltered(Exception):
    """Alibaba Model Studio input moderation rejected the request (HTTP 400 data_inspection_failed). Raised only with filter_raise=True."""

def provider():
    return CFG['PROVIDER']

def api_key():
    return os.environ.get(PROVIDERS[CFG['PROVIDER']]['key_env'], '')

def key_env():
    return PROVIDERS[CFG['PROVIDER']]['key_env']

def unit_price(model, in_tok):
    """USD per 1M (input, output) tokens for a request with in_tok input tokens (tiered: the tier is chosen by the request's input size
    and all tokens of the request are billed at that tier)."""
    tiers = CFG['PRICES'].get(model)
    if tiers is None: raise KeyError('no price configured for model %r (set PRICES in pipeline/common.py)' % model)
    for lim, pi, po in tiers:
        if in_tok <= lim: return pi, po
    return tiers[-1][1], tiers[-1][2]

def mask(s):
    s = re.sub(r'sk-[A-Za-z0-9_-]{6,}', 'sk-***', s or '')
    for k in ('DASHSCOPE_API_KEY', 'OPENAI_API_KEY', 'CLOUDFLARE_API_TOKEN'):
        v = os.environ.get(k)
        if v: s = s.replace(v, '***')
    return s

class LLM:
    def __init__(self, db, run_id, dry_run=False):
        self.db, self.run_id, self.dry = db, run_id, dry_run
        self.lock = threading.Lock()
        self.run_spend = 0.0
        self.total_before = db.execute('select coalesce(sum(cost),0) from llm_usage').fetchone()[0]
        from .common import now_local
        self.day_before = db.execute('select coalesce(sum(cost),0) from llm_usage where ts>=?', (now_local().strftime('%Y-%m-%d'),)).fetchone()[0]
        self.calls = 0
        self.by_model = {}
        self.filtered = 0          # requests rejected by the provider's content filter (data_inspection_failed)
        self.filter_probes = 0     # cheap probe calls used to isolate the offending article(s)
        self.filter_isolated = 0   # articles / lines isolated as filter triggers
    # ------------------------------------------------------------------ budget
    @property
    def total_spend(self): return self.total_before + self.run_spend
    def remaining(self): return min(CFG['BUDGET_USD'] - self.total_spend, CFG['RUN_BUDGET_USD'] - self.run_spend, CFG['DAILY_BUDGET_USD'] - self.day_before - self.run_spend)
    def _estimate(self, model, system, user, max_out):
        tin = (len(system) + len(user)) / 2.2          # conservative: ~2.2 chars/token for Cyrillic-heavy text
        pi, po = unit_price(model, tin)
        return (tin * pi + max_out * 0.6 * po) / 1e6
    def _record(self, stage, model, it, ot, ok):
        pi, po = unit_price(model, it); cost = (it * pi + ot * po) / 1e6
        with self.lock, DBLOCK:
            self.run_spend += cost; self.calls += 1
            m = self.by_model.setdefault(model, [0, 0, 0.0, 0]); m[0] += it; m[1] += ot; m[2] += cost; m[3] += 1
            self.db.execute('insert into llm_usage(ts,run_id,stage,model,in_tok,out_tok,cost,ok) values(?,?,?,?,?,?,?,?)', (now_iso(), self.run_id, stage, model, it, ot, cost, int(ok)))
            self.db.commit()
        return cost
    # ------------------------------------------------------------------ call
    def call(self, stage, model, system, user, max_out=12000, effort=None, retries=3, filter_raise=False):
        """Returns (parsed_json_or_None, raw_text). Raises BudgetExceeded when the cap would be crossed.
        effort: OpenAI reasoning effort for the hard model; for Qwen thinking stays OFF unless QWEN_THINKING_HARD=1 (then on for effort calls)."""
        if self.dry: raise RuntimeError('LLM call attempted in dry-run mode')
        est = self._estimate(model, system, user, max_out)
        with self.lock:
            if self.remaining() < est:
                raise BudgetExceeded('budget cap: remaining $%.3f < estimate $%.3f for %s/%s (total spent $%.3f, today $%.3f)' % (self.remaining(), est, stage, model, self.total_spend, self.day_before + self.run_spend))
        prov = PROVIDERS[CFG['PROVIDER']]
        key = os.environ.get(prov['key_env'])
        if not key: raise RuntimeError('%s is not set' % prov['key_env'])
        if prov['api'] == 'responses':
            url = CFG['BASE_URL'].rstrip('/') + '/responses'
            body = {'model': model, 'input': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                    'max_output_tokens': max_out, 'text': {'format': {'type': 'json_object'}}}
            if effort: body['reasoning'] = {'effort': effort}
        else:
            url = CFG['BASE_URL'].rstrip('/') + '/chat/completions'
            body = {'model': model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                    'max_tokens': max_out, 'response_format': {'type': 'json_object'}, 'temperature': CFG['TEMPERATURE']}
            think = bool(effort) and os.environ.get('QWEN_THINKING_HARD', '0') == '1'
            body['enable_thinking'] = think            # Qwen3.x hybrid models: thinking OFF (cheaper, JSON mode requires it off)
            if think: body.pop('response_format')
        last = None
        for a in range(retries):
            try:
                r = requests.post(url, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, json=body, timeout=600)
            except requests.RequestException as e:
                last = 'network: %s' % type(e).__name__; time.sleep(4 * (a + 1)); continue
            if r.status_code == 200: break
            last = 'http %s' % r.status_code
            if r.status_code in (429, 500, 502, 503, 504): time.sleep(5 * (a + 1)); continue
            if r.status_code == 400 and 'data_inspection_failed' in r.text:
                with self.lock: self.filtered += 1
                if filter_raise:
                    log.warning('LLM %s/%s: content filter (data_inspection_failed) -> isolating', stage, model)
                    raise ContentFiltered(stage)
            log.error('LLM %s/%s failed: %s %s', stage, model, r.status_code, mask(r.text[:300]))
            return None, ''
        else:
            log.error('LLM %s/%s failed after retries: %s', stage, model, last); return None, ''
        j = r.json(); u = j.get('usage', {}) or {}
        if prov['api'] == 'responses':
            txt = ''.join(c.get('text', '') for o in j.get('output', []) if o.get('type') == 'message' for c in o.get('content', []))
            done = j.get('status') == 'completed'; it, ot = u.get('input_tokens', 0), u.get('output_tokens', 0); why = (j.get('status'), j.get('incomplete_details'))
        else:
            ch = (j.get('choices') or [{}])[0]
            txt = (ch.get('message') or {}).get('content') or ''
            done = ch.get('finish_reason') in ('stop', None) and bool(txt)
            it, ot = u.get('prompt_tokens', 0), u.get('completion_tokens', 0); why = (ch.get('finish_reason'), None)
        obj = parse_json(txt) if done else None
        self._record(stage, model, it, ot, obj is not None)
        if not done: log.warning('LLM %s/%s incomplete: %s', stage, model, why)
        return obj, txt

    # ------------------------------------------------------------------ content-filter isolation
    PROBE_SYSTEM = 'Reply with the JSON object {"ok":1} and nothing else.'
    def probe(self, text):
        """True if `text` passes the provider's input filter. One tiny request on the bulk model (input-only cost, ~$0.00001 per 300 tokens)."""
        with self.lock: self.filter_probes += 1
        try:
            self.call('filter-probe', CFG['MODEL_BULK'], self.PROBE_SYSTEM, 'JSON check. Text:\n' + text, max_out=16, retries=2, filter_raise=True)
            return True
        except ContentFiltered:
            return False
    def isolate(self, items, render, max_probes=40):
        """Binary search for the items whose rendered text trips the content filter. Returns the list of offending items
        ([] if the items pass one by one -- then the trigger is elsewhere, e.g. in the shared context)."""
        bad = []; budget = [max_probes]
        def rec(group):
            if not group or budget[0] <= 0: return
            budget[0] -= 1
            if self.probe('\n'.join(render(x) for x in group)): return
            if len(group) == 1: bad.append(group[0]); return
            mid = len(group) // 2
            rec(group[:mid]); rec(group[mid:])
        rec(list(items))
        with self.lock: self.filter_isolated += len(bad)
        return bad

def parse_json(txt):
    t = txt.strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t)
    try: return json.loads(t)
    except ValueError:
        m = re.search(r'\{.*\}', t, re.S)
        if m:
            try: return json.loads(m.group(0))
            except ValueError: return None
    return None
