# Search-first tender discovery MVP

An independent prototype for Ripples Engineering. It does not change the existing
portal adapters, LangGraph, scoring, or download pipeline. Python 3.10+ and its
standard library are sufficient; there are no new pip dependencies.

## SQLite storage

SQLite is the backend source of truth for discovery runs, observations,
opportunities, sources, ranking assessments, tracking state and feedback.
Existing JSON files import once automatically and remain untouched as migration
backups. JSON/HTML run reports continue as exports; editing or removing an export
does not change the database after migration. The capability profile and API
credentials remain configuration files.

For the standard Windows live/demo workspaces, databases live outside OneDrive:
`%LOCALAPPDATA%/TenderAgent/<workspace-name-and-hash>/live.sqlite3` and
`demo.sqlite3`. The state API returns the actual path under `storage.path`.
`TENDER_AGENT_DB_DIR` can override the parent directory. A custom `--output`
directory gets its own `tenders.sqlite3` file, useful for portable copies/testing.

Run completion commits the snapshot, observations, tracking updates and refreshed
library together in one transaction. Ranking cache entries commit separately as
they finish, so interrupted discovery can reuse them. Feedback is written in a
short transaction and newer reviews survive a discovery run started earlier.
Database reads use snapshot transactions; connections enable foreign keys and a
20-second busy timeout. Default rollback journaling keeps the local deployment
simple, with brief writes outside model/search requests.

To migrate/check storage manually and create a consistent database backup:

```powershell
.venv/Scripts/python.exe -B -m discovery_mvp.storage --backup data/discovery_mvp/sqlite-migration-backup.sqlite3
```

The command reports table counts, integrity checks and any migration issues.
Keep the active database on local storage; back up a snapshot with this command.
Migration is idempotent and preserves original run IDs and assessment cache keys.

## Launch the frontend

```powershell
.venv/Scripts/python.exe -B -m discovery_mvp.server
```

Open [the local dashboard](http://127.0.0.1:8765). Use `--port 8766` if the default
port is occupied. Keep this command running while using the dashboard; Ctrl+C
stops the dashboard server. It binds only to the local computer.

The dashboard supports:

- Separate demo and live workspaces, with explicit synthetic-data labels.
- A discovery dialog with provider, ranker and query-budget controls.
- Background execution and a live activity log, with overlapping runs blocked.
- Opportunity search, status/provider filters, relevance sorting, and JSON export.
- A calendar picker for tenders discovered on a selected IST date, across all runs
  that day, including rediscoveries. Opportunities remain deduplicated. Select
  All dates to restore the full library. Date filtering uses saved run observations,
  not publication dates or deadlines; exports and counts reflect the selected date.
- Evidence, original-source links, search provenance, and relevant/irrelevant feedback.
- Capability groups, generated queries, saved run history, and key-configuration status.
- Daily scheduling instructions; the browser does not install schedules.

The backend reads credentials from `.env` at startup. Restart it after changing
credentials. Keys are never sent to the browser. Live runs incur provider charges.
The server is a local prototype, not a multi-user hosted application. Discovery
continues independently if the browser closes; stopping the dashboard does not
cancel an already launched discovery process. Run history remains on disk.

The interface refreshes from saved outputs, including runs created by Task
Scheduler. HTTP mutations require a local origin and a per-session token. Only
the three frontend assets are served, not workspace files or credential files.

Opportunity cards, detail views and exports include estimated tender values
explicitly stated in search text. Values normalize to INR and display as rupees,
lakh or crore. EMDs, fees and equipment unit prices are excluded. Missing or
conflicting values are labelled; they are never inferred. The default order is
match score descending, then tender value descending among equal matches.
Unknown values come last within the same match score. Saved history is enriched
when opened, and future discovery prompts request project values when available.

For price-only ordering, choose **Price: high to low** or **Price: low to high**
in the dashboard's sort menu. These modes ignore match score and place unknown
or conflicting values last. Sorting combines with the existing search, status
and provider filters and does not alter saved assessments.

In SQLite, the corresponding price-only query is:

```sql
SELECT * FROM opportunities ORDER BY value_inr DESC;
```

`ORDER BY` sorts rows. The dashboard sorts the loaded opportunities in JavaScript.
Run its price-sort regression tests with `node --test tests/test_price_sort.cjs`.

## Cumulative opportunity library

The dashboard defaults to **Opportunity library**, which retains discoveries
across every saved run. Switch to **Latest run** for that run's exact listings.
Run history still opens original snapshots. Exports follow the selected view.

Matching government tender IDs or GeM bid numbers connect different websites.
An exact departmental reference, or a sufficiently specific identical title and
deadline, can connect a notice missing its tender ID. Conflicting tender IDs
stay separate, as do similar projects in different wards. Uncertain matches
remain separate rather than being combined by fuzzy wording alone.

Each opportunity retains all source links, first/last discovery dates, source
record IDs and a status: new this run, rediscovered, or not seen this run.
Not seen does not mean closed, cancelled or withdrawn. The latest observed
assessment represents the opportunity. An earlier stated project value can be
retained when newer text omits it; the detail view labels it as previously stated.
Feedback follows merged opportunities across their source records.

Differently worded listings missing an ID can attach to a single identified
opportunity when their specific title details, deadline and stated value agree
(with up to 0.1% rounding). Routine design/supply/installation wording and word
order may differ; place, year, scope and ward/package markers remain significant.
Conflicting IDs, package details or values block title matching. Ambiguous
matches stay separate. This intentionally leaves uncertain paraphrases for review.
Existing exact-title matching remains available. All source records and review
history are retained, and a library-version change automatically rebuilds the
stored library from existing runs without new searches or model calls.

The library is refreshed in SQLite from stored run observations after each
discovery run. Existing history is backfilled without search or model calls.
Dashboard requests query the stored library rather than scanning JSON files.

## Ranking cache

Successful assessments persist in SQLite's `assessments` table (demo and live
databases are separate). Repeated proposal text reuses its
assessment without another ranking-model call, even when discovery queries,
source URLs or providers differ. The cache preserves the fresh source links and
search provenance. An expired cached deadline moves the proposal to review
without another model call. Price extraction and match-then-value sorting still
run on every result.

The key includes the title, unique snippets, company capability groups, shortlist
threshold, ranker/model name, server endpoint, ranking prompt, and implementation
version. A content or evaluator change triggers a new assessment. Snippet order
and duplicated snippets do not. Reworded search summaries do count as changed
content; matching only a tender ID could otherwise hide changed requirements.
When replacing model weights under the same tag, increment
`DISCOVERY_RANK_CACHE_VERSION` in `.env` and restart the dashboard.

Failed model calls and unsupported evidence are not cached. Successful entries
are saved immediately, so completed assessments survive an interrupted run.
Logs show `[cache]` reuse, and reports include hit, miss and write counts plus a
per-candidate cache marker. Keyword planning and web discovery still run to find
new or changed opportunities; this cache skips repeated ranking work.

## Present the offline demo

From the repository root in PowerShell:

```powershell
.venv/Scripts/python.exe -m discovery_mvp.run --demo
```

Open `data/discovery_mvp_demo/latest.html` in your browser. Four clearly synthetic
examples demonstrate a direct match, a bundled opportunity needing review, an
irrelevant tender, and a catalogue. These are not actual tender opportunities.
Demo output and state are separate from live output and state.

## Run live searches

### Recommended: Codex discovery + Codex ranking

Discovery, keyword planning and ranking default to Codex with ChatGPT sign-in.
On a new laptop, install Python 3.10+ and Codex (CLI or the Windows desktop app),
clone the repository, and sign in. No pip packages or local model downloads are
needed for this discovery workflow. From the repository root:

```powershell
codex login status
# If needed, run: codex login
python -B -m discovery_mvp.run --provider codex --ranker codex --query-budget 8
```

No OpenAI or Ollama API key is required for this combination. Search, planning
and ranking consume your Codex usage allowance. Qwen/Ollama is optional. The
command line, dashboard and scheduling script default to Codex for both discovery
and ranking. Restart an already-running dashboard to load backend changes.

To use a local model, explicitly choose `--ranker ollama` (or Local Ollama in the
dashboard), start Ollama, and install `OLLAMA_CHAT_MODEL`. GPT API ranking remains
available through `--ranker gpt` with `OPENAI_API_KEY`.

If Codex is missing from PATH, set `CODEX_CLI_PATH` in `.env` to its executable's
absolute path. On Windows the prototype also automatically checks the Codex
desktop app's versioned installation when it is absent from PATH. Restart the
server after updating the Python code. The scheduled task runs under your Windows account and needs that
account's Codex ChatGPT sign-in. `schedule.ps1` defaults to `-Provider codex -Ranker codex`.
No schedule is automatically registered or replaced.

Codex runs in an isolated temporary working folder, with read-only sandboxing,
shell tools disabled and API keys removed from its child environment. The adapter
requires ChatGPT sign-in and a completed native web-search event, then reads the
CLI's schema-constrained JSON output. Search summaries remain model-written and
their URLs are not independently checked against a full search-source list.
Verify original tender notices; mirrors and stale listings may appear.

Each query has a 240-second timeout and no automatic retry. Set
`CODEX_SEARCH_TIMEOUT` to adjust it. Results record per-query CLI usage and timing
in `codex_search_calls`, alongside planning, search, ranking and total processing
timings. Planning/ranking calls use supplied data only, with web search disabled,
a separate `CODEX_RANK_TIMEOUT` (default 180 seconds), and no automatic retry.
Their timing and usage are recorded in `codex_rank_calls`. `CODEX_RANK_MODEL`
optionally overrides the CLI default; increment `DISCOVERY_RANK_CACHE_VERSION`
if that default changes and you want fresh assessments. The ranking cache also
separates Codex assessments from Ollama/GPT assessments.

When Ollama is selected, short local JSON tasks disable thinking and cap output at 1,024 tokens;
failed local requests are not retried automatically. Check the report for any
partial result or model-output truncation.

An observed local-model error is guarded conservatively: notices for footpaths,
pavements, walkways or boundary walls around/near a fountain go to manual review
if the model tries to shortlist them. The original model score and reason remain
in JSON. This narrow check does not replace full-document scope validation.

See [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
and [CLI options](https://learn.chatgpt.com/docs/cli/reference).

### Alternative: API-based providers

1. Copy `discovery_mvp/.env.example` to `discovery_mvp/.env`.
2. Set `OPENAI_API_KEY` for GPT discovery and `OLLAMA_API_KEY` for Ollama web search.
   The latter uses Ollama's hosted search API, even when inference runs locally.
3. Start your local Ollama server and make sure `OLLAMA_CHAT_MODEL` is installed.
   The default matches this repository's existing model configuration.
4. Run:

```powershell
.venv/Scripts/python.exe -m discovery_mvp.run --provider both --ranker ollama --query-budget 6
```

Alternatives:

```powershell
# Only GPT: does not require a local Ollama server or Ollama key.
.venv/Scripts/python.exe -m discovery_mvp.run --provider gpt --ranker gpt

# Only Ollama: hosted web search plus local inference, no OpenAI key.
.venv/Scripts/python.exe -m discovery_mvp.run --provider ollama --ranker ollama
```

Live reports are `data/discovery_mvp/latest.html` and `latest.json`. Timestamped
copies are retained under `runs/`; SQLite retains previously seen URLs,
search allocation history, and reviewer feedback. Each report shows the sources,
queries, provider coverage, new/changed/unchanged status, and errors. JSON retains
all candidates including rejections. No messages are emailed automatically.

Environment variables take precedence over `.env`. The `.env` file is ignored by
Git; keep API credentials out of shared reports. Existing dependencies are not
needed for this module. The default OpenAI model is configurable using
`OPENAI_SEARCH_MODEL`; account access and billing must be configured separately.

## How keyword selection improves

`profile.json` groups specific company capabilities instead of searching generic
words such as "water" or "lighting". The selected ranker proposes up to two short
synonyms per group; original curated phrases remain in every query. Expansion is
prompt constrained, not a guarantee of business relevance, and is visible in the
report. An expansion failure uses curated phrases and marks the run partial.

Each run allocates at least half its queries to the least-searched groups. The
remaining allocation favors groups with useful results according to human
feedback. Synonyms within each group rotate across successful runs. This provides
basic coverage and feedback-based query optimization, not a trained keyword model.
The bundled-project group searches for relevant subsystems in wider procurements.

Record a review using the exact result URL from `latest.json`:

```powershell
.venv/Scripts/python.exe -m discovery_mvp.run --feedback 'https://SOURCE/notice' relevant
.venv/Scripts/python.exe -m discovery_mvp.run --feedback 'https://SOURCE/notice' irrelevant
```

Use `--demo` with feedback on demo URLs. Labels replace previous reviews for the
same URL rather than counting each command as a new review. Label genuine scope
matches as relevant; eligibility is a separate decision. Fewer irrelevant alerts
do not prove better recall: compare against your team's manual discoveries too.

## Schedule periodic updates through code

The Python command runs once and exits. An operating-system scheduler starts it
daily; this avoids keeping a Python process alive all day. This script creates
a Windows Task Scheduler task for 09:00 in your computer's local timezone:

```powershell
./discovery_mvp/schedule.ps1 -Python "$PWD/.venv/Scripts/python.exe" -At '09:00' -Provider both -Ranker ollama
```

The schedule is **not installed by running the prototype**. Run the command above
when ready. On an India-configured computer, 09:00 means 09:00 IST. Reports use
IST timestamps independently of the host timezone. The registered action is
equivalent to:

```python
# Conceptually, the scheduler invokes this daily from the repository root:
import subprocess
subprocess.run([
    r"C:\path\to\python.exe", "-m", "discovery_mvp.run",
    "--provider", "both", "--ranker", "ollama",
], cwd=r"C:\path\to\tender_agent", check=True)
```

Task Scheduler supplies the clock, while Python performs the searches, merges
results, records changes, and writes the new report. A daily run searches all its
selected queries, not just notices labelled "published today", allowing overlap
for search indexing delays. Query groups rotate, so this is not a complete daily
crawl of every group. Increase `--query-budget` to 8 for all eight groups per run
(pass `-QueryBudget 8` to `schedule.ps1` when scheduling this setting).

The task runs while the registering Windows user is logged on. Keep the computer
awake and connected, and keep Ollama running when using local inference.
`StartWhenAvailable` requests a missed run after the machine becomes available.
For unattended runs while logged off, configure a suitable service account and
credentials through your company's approved Task Scheduler setup.

There are two overlap protections: Task Scheduler ignores a new instance while
one runs, and Python takes an OS file lock. A crash releases the lock. Each API
request retries transient failures up to three attempts. Successful results
remain available if another provider fails. Exit codes: `0` clean run,
`2` partial run, `1` failed run. The scheduler has a two-hour execution limit.

Check or start the task manually:

```powershell
Get-ScheduledTaskInfo -TaskName 'Ripples-Tender-Discovery-MVP'
Start-ScheduledTask -TaskName 'Ripples-Tender-Discovery-MVP'
```

Change a schedule using Task Scheduler, or remove and recreate it:

```powershell
./discovery_mvp/schedule.ps1 -Python "$PWD/.venv/Scripts/python.exe" -Remove
```

There is intentionally no automatic retry of the entire daily job: repeating the
job repeats billable searches. Failed or partial runs have nonzero task results.
Notification delivery and external uptime monitoring are future additions.

## Bounds and known gaps

- Default: six distinct queries, two providers, up to five results per search,
  and one ranking call per deduplicated candidate, plus one keyword expansion call.
  GPT search allows at most two built-in tool calls per request. Retries can add
  requests/cost. Token usage is recorded, but no currency estimate is claimed.
- GPT URLs must match a returned search source or citation. This prevents an
  uncited model URL from entering the candidate list; it does not verify the
  underlying notice or make a generated summary verbatim source evidence.
- Ollama results contain provider snippets. Both providers' text is treated as
  untrusted input; supporting ranking quotes are checked against supplied text.
- Neither path downloads full tender documents or validates live deadlines,
  corrigenda, bidder eligibility, or whether a notice remains open. A possible past
  deadline goes to review. Unknown deadlines stay unknown. Search results can be stale.
- Deduplication uses normalized URLs, preserves portal IDs, and merges provenance.
  The same tender on different URLs/portals can still appear twice. Signed or
  session URLs can change. Changed snippets are search-text changes, not verified
  tender amendments. Notices absent from today's search are retained in state,
  but are not independently revisited. This MVP is not corrigendum monitoring.
- Ranker failures retain candidates for manual review. There is no silent
  substitution of synthetic data for failed live searches.
- SQLite and exported reports retain history without a retention limit; add retention before long use.
- Provider contracts are covered with mocked tests. Codex search has also been
  smoke-tested with ChatGPT sign-in. Original API-based providers require their
  own account access; an Ollama search key alone may not include hosted search credits.

Run the offline checks:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_discovery_mvp.py -v
```

API references: [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search),
[Ollama web search](https://docs.ollama.com/capabilities/web-search),
[Ollama chat](https://docs.ollama.com/api/chat).
