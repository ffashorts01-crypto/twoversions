# -*- coding: utf-8 -*-
"""Shared config, SQLite store, logging for the auto-update pipeline."""
import os, sys, json, sqlite3, logging, datetime as dt, threading
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
DB_PATH = os.environ.get('PIPELINE_DB', os.path.join(DATA, 'articles.db'))
BERLIN = ZoneInfo('Europe/Berlin'); UTC = dt.timezone.utc
def now_local(): return dt.datetime.now(BERLIN)
def now_iso(): return now_local().isoformat(timespec='seconds')

# ---- settings (all overridable via environment) -----------------------------------------------------------------
def _new_only_since():
    """New-news-only cutoff: env NEW_ONLY_SINCE, else first line of data/new_only_since.txt; normalised to UTC ISO (comparable with articles.date_utc)."""
    v = os.environ.get('NEW_ONLY_SINCE', '').strip()
    if not v:
        try: v = open(os.path.join(DATA, 'new_only_since.txt'), encoding='utf-8').readline().strip()
        except OSError: v = ''
    if not v: return ''
    d = dt.datetime.fromisoformat(v)
    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat(timespec='seconds')
def _f(name, d): return float(os.environ.get(name, d))
def _i(name, d): return int(os.environ.get(name, d))
PROVIDERS = {
    'qwen':   dict(api='chat', key_env='DASHSCOPE_API_KEY', base_url='https://dashscope-intl.aliyuncs.com/compatible-mode/v1', bulk='qwen3.7-flash', hard='qwen3.7-plus', cluster='qwen3.7-plus', translate='qwen3.7-plus'),
    'openai': dict(api='responses', key_env='OPENAI_API_KEY', base_url='https://api.openai.com/v1', bulk='gpt-6-luna', hard='gpt-6.1-sol', cluster='gpt-6-luna', translate='gpt-6-luna'),
}
_PROV = os.environ.get('LLM_PROVIDER', 'qwen').strip().lower() or 'qwen'
if _PROV not in PROVIDERS: raise SystemExit('LLM_PROVIDER must be one of %s' % ', '.join(PROVIDERS))
CFG = dict(
    PROVIDER=_PROV,
    BASE_URL=os.environ.get('LLM_BASE_URL') or PROVIDERS[_PROV]['base_url'],
    MODEL_BULK=os.environ.get('MODEL_BULK') or PROVIDERS[_PROV]['bulk'],
    MODEL_HARD=os.environ.get('MODEL_HARD') or PROVIDERS[_PROV]['hard'],
    MODEL_CLUSTER=os.environ.get('MODEL_CLUSTER') or PROVIDERS[_PROV]['cluster'],       # clustering / orphan / merge calls (Qwen test: flash put 36% unrelated articles into clusters, plus 15%)
    MODEL_TRANSLATE=os.environ.get('MODEL_TRANSLATE') or PROVIDERS[_PROV]['translate'], # ru -> uk/en (Qwen test: flash altered surnames in Ukrainian)
    TEMPERATURE=_f('LLM_TEMPERATURE', 0.3),
    # USD per 1M tokens: model -> [(max input tokens of the request, input price, output price), ...] (tiered by the request's input size).
    # Qwen: Alibaba Model Studio, Singapore/international list prices (model-pricing page, 2026-11-07); promos and the 1M-token free quota are ignored (conservative).
    PRICES={'qwen3.7-flash': [(32000, 0.03, 0.13), (256000, 0.10, 0.40), (1000000, 0.20, 0.80)],
            'qwen3.7-plus':  [(256000, 0.40, 1.60), (1000000, 1.20, 4.80)],
            'qwen3.8-flash': [(1000000, 0.15, 0.47)],
            'qwen3.6-plus':  [(256000, 0.50, 3.00), (1000000, 2.00, 6.00)],
            'qwen3.7-max':   [(1000000, 2.50, 7.50)],
            'gpt-6-luna': [(10**9, 0.10, 0.50)], 'gpt-6.1-sol': [(10**9, 2.0, 10.0)]},
    BUDGET_USD=_f('LLM_BUDGET_USD', 7.0),         # HARD cap on cumulative spend recorded in the llm_usage table (all runs)
    RUN_BUDGET_USD=_f('LLM_RUN_BUDGET_USD', 0.45), # cap for a single run (~$30/mo with daily $1 and every-3h schedule)
    CLUSTER_BATCH=_i('CLUSTER_BATCH', 100),       # <= 100 articles per clustering call
    OPEN_DAYS=_i('OPEN_DAYS', 4),                 # events whose last article is newer than this are "open" (attach targets)
    MIN_REL_ARTICLES=_i('MIN_REL_ARTICLES', 2),   # quality gate: relevant articles
    MAX_UNGROUNDED=_i('MAX_UNGROUNDED', 1),       # quality gate: numbers absent from sources (editor rule: any -> withheld)
    MIN_REL_SOURCES=_i('MIN_REL_SOURCES', 2),     # quality gate: distinct sources among relevant articles
    MIN_REL_SHARE=_f('MIN_REL_SHARE', 0.5),       # quality gate: share of cluster members the relevance filter confirms
    SOL_MANY_SOURCES=_i('SOL_MANY_SOURCES', 9),   # events with >= this many relevant articles go straight to Sol
    WORKERS=_i('PIPELINE_WORKERS', 4),
    HTTP_DELAY=_f('HTTP_DELAY', 0.6),             # seconds between requests to the same host (per worker)
    DAILY_BUDGET_USD=_f('LLM_DAILY_BUDGET_USD', 1.0),   # HARD cap per calendar day Europe/Berlin (~$30/month)
    IMPORTANCE_HIGH=_i('IMPORTANCE_HIGH', 80),         # both sides / major topics: always prefer
    IMPORTANCE_MED=_i('IMPORTANCE_MED', 40),           # process when run budget is not tight
    IMPORTANCE_TIGHT_USD=_f('IMPORTANCE_TIGHT_USD', 0.20),  # below this remaining: skip score < MED
    IMPORTANCE_VERY_TIGHT_USD=_f('IMPORTANCE_VERY_TIGHT_USD', 0.10),  # below this: only HIGH
    NEW_ONLY_SINCE=_new_only_since(),                     # HARD floor (UTC ISO): older articles are never collected / clustered / carded (data/new_only_since.txt)
    CLUSTER_SINCE=os.environ.get('CLUSTER_SINCE', ''),     # ISO date/time (UTC compare on date_utc): only articles at/after this are clustered
    CLUSTER_UNTIL=os.environ.get('CLUSTER_UNTIL', ''),     # test/backfill only: upper bound for clustering
    EXTEND_LUNA_ONLY=os.environ.get('EXTEND_LUNA_ONLY', '1') == '1',
    CLUSTER_MAX=_i('CLUSTER_MAX', 400),             # max candidate articles per run (backlog drains over several runs)
    CONTACT_EMAIL=os.environ.get('CONTACT_EMAIL', 'feedback@example.com'),
)

DBLOCK = threading.RLock()   # serialises access to the shared sqlite connection between worker threads
_log_lock = threading.Lock()
def get_logger(run_id=None, to_file=True):
    lg = logging.getLogger('pipeline')
    if lg.handlers: return lg
    lg.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S')
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt); lg.addHandler(sh)
    if to_file:
        os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)
        fh = logging.FileHandler(os.path.join(ROOT, 'logs', 'update.log'), encoding='utf-8'); fh.setFormatter(fmt); lg.addHandler(fh)
    return lg
log = logging.getLogger('pipeline')

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY, source TEXT, grp TEXT, lang TEXT, title TEXT, url TEXT UNIQUE,
  date_utc TEXT, date_local TEXT, excerpt TEXT, text TEXT, note TEXT, military INTEGER);
CREATE TABLE IF NOT EXISTS art_state(article_id INTEGER PRIMARY KEY, state TEXT, attempts INTEGER DEFAULT 0, updated TEXT);
  -- state: pending (seen by clustering, not assigned yet) | clustered | hand (belongs to a hand-written card) | skipped
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, created TEXT, updated TEXT, last_art TEXT, first_art TEXT,
  status TEXT DEFAULT 'open', merged_into INTEGER, slug TEXT, dirty INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS event_articles(article_id INTEGER PRIMARY KEY, event_id INTEGER, hand_id INTEGER, rel TEXT, rel_note TEXT, rel_hash TEXT);
  -- exactly one event per article; hand_id set when attached to one of the 24 hand-written cards (then event_id is NULL)
CREATE TABLE IF NOT EXISTS cards(event_id INTEGER PRIMARY KEY, input_hash TEXT, status TEXT, reasons TEXT, card TEXT, confidence TEXT,
  built TEXT, first_published TEXT, model_path TEXT, slug TEXT);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, started TEXT, finished TEXT, args TEXT, spend REAL, stats TEXT);
CREATE TABLE IF NOT EXISTS llm_usage(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, stage TEXT, model TEXT, in_tok INTEGER, out_tok INTEGER, cost REAL, ok INTEGER);
CREATE TABLE IF NOT EXISTS source_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, source TEXT, new_articles INTEGER, urls_seen INTEGER, errors INTEGER, note TEXT);
CREATE INDEX IF NOT EXISTS ix_art_date ON articles(date_utc);
CREATE INDEX IF NOT EXISTS ix_ea_event ON event_articles(event_id);
"""
def connect(path=None):
    db = sqlite3.connect(path or DB_PATH, timeout=60, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db

def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError): return default
def save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
    os.replace(tmp, path)
