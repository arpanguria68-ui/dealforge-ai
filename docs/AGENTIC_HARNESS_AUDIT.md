# Agentic Harness, Laya Integration & Tool/Agent Readiness Audit

Scope: `backend/app/agents/base.py` (agent tool loop), `backend/app/core/tools/*`
(tool router + 38 registered tools), `backend/app/core/laya/*` (System-1
decision layer) and its call sites, `backend/app/core/mcp/external_client.py`.

Regression coverage for everything marked **Fixed** lives in
`backend/tests/test_agentic_harness.py` (10 of its 12 tests fail on the
pre-fix code).

## 1. Fixed in this change

### Laya decision layer (`core/laya/client.py`)

| Problem | Impact | Fix |
|---|---|---|
| No decision cache. The same brief/task/output is classified repeatedly per run (planner + project manager + graph triage node; model router `route_tier` before every LLM call; confidence gate + HaluGate + financial-analyst gate). | With `LAYA_MODE=lmstudio` every "~33ms decision" is a full chat completion (up to 120s timeout), repeated several times per graph step. | TTL + LRU cache keyed by backend, model, state and questions (`LAYA_CACHE_TTL_SECONDS`, `LAYA_CACHE_MAX_ENTRIES`); results are deep-copied so callers can't poison it. |
| Concurrent identical requests (e.g. guardrail and triage nodes gathered together, parallel agents) each hit the backend. | Duplicate inference. | Concurrent identical requests now share one in-flight call. |
| No circuit breaker. | A dead remote / LM Studio endpoint charged its full timeout (8s / 120s) on every call site, every step. | Per-backend breaker (`LAYA_BREAKER_THRESHOLD`, `LAYA_BREAKER_COOLDOWN_SECONDS`), half-open probe after cooldown. |
| New `httpx.AsyncClient` per decision. | TCP/TLS handshake on every call. | Pooled keep-alive client (bound to the running loop) closed on app shutdown. |
| LM Studio model discovery (`/api/v1/models`) on every decision. | Extra round trip per decision. | Cached for 60s per endpoint/model signature. |
| LM Studio transport failure retried the second "unconstrained" variant against an unreachable server. | Double timeout. | Stops on transport errors; failures now count toward the breaker. |
| `apredict_batch` (RAG rerank) re-scored every chunk each query. | Repeated reranks of the same chunks. | Batch serves cached items and only sends misses. |
| `noul` confidence defaulted to P(yes). | P(yes)=0.05 (a confident "no") was reported as 5% confidence. | Default confidence is now `max(p, 1-p)`. |
| No runtime visibility. | — | `GET /api/v1/laya/status` now returns `runtime`: calls, cache hit rate, in-flight joins, failures, abstentions, breaker state, avg backend latency. |

### Agent tool loop (`agents/base.py::generate_with_tools`)

| Problem | Fix |
|---|---|
| Always made an extra "final synthesis" LLM call whenever any tool ran, even when the model had already returned its final answer after seeing the results. Every tool-using agent call paid one wasted completion. | Synthesis only runs when the loop stops with unanswered tool results (round budget exhausted or duplicate-only round). |
| Identical tool calls re-executed across rounds (models often re-request the same search). | Memoized by `(name, args)`. A round with only repeated calls ends the loop. |
| Unbounded `json.dumps(indent=2)` of all tool results was re-sent every round. | Per-result (6k chars) and total (24k chars) budgets, compact JSON, newest results kept. |
| Eight agents read `result["tool_calls"]`, which was never set, so their recorded tool calls were always `None`. | `tool_calls` alias added alongside `function_calls`. |
| `run_with_structure` retrieved issue-tree branch context one branch at a time. | Branch retrievals run concurrently. |

### Tool router (`core/tools/tool_router.py`)

| Problem | Fix |
|---|---|
| **`peer_discovery`, `excel_model_populate`, `excel_export_tables` have sync `execute()`; the router `await`ed the result, raising a TypeError, so these three tools could never succeed through the agent loop.** | Sync tools run in a worker thread. |
| No per-tool timeout: one hung HTTP call stalled the whole agent. | `asyncio.wait_for` with `tool.timeout_seconds` / `TOOL_TIMEOUT_SECONDS` (default 90s). |
| Tool calls in a round ran sequentially. | Run concurrently, order preserved, bounded by `TOOL_MAX_CONCURRENCY` (default 4). |
| No argument validation: missing args surfaced as opaque `TypeError`; extra hallucinated args crashed tools without `**kwargs`; `null` args crashed. | Required-arg check with an actionable error listing expected params; unknown args dropped; `null`/empty string treated as `{}`; non-object args rejected. |
| `execution_time_ms` was usually unset. | Router measures it. |
| ~40 `info` log lines ("Registered tool") per agent instantiation. | Downgraded to `debug`. |

### MCP discovery (`core/mcp/external_client.py`)

`discover_tools()` opened a session to every configured server at the start of
**every** agent tool loop (10s timeout each when a server is down). It's now
cached per server config (`MCP_DISCOVERY_TTL_SECONDS`, default 60s), and an
unreachable server is re-probed after 15s.

### Individual tools

* `fetch_comparable_companies`: when live data failed, the tool **attached
  randomly jittered multiples to the requested real tickers** and returned
  `success=True`, which presented fabricated numbers as real peer data. It now
  returns one deterministic, clearly labelled `SECTOR_BENCHMARK` row plus
  `data_quality: "sector_default_estimate"` and a warning. yfinance calls
  moved off the event loop.
* `finance_analysis` (FinanceToolkit): blocking HTTP + pandas ran on the event
  loop. It now runs in a worker thread.
* `peer_discovery`: rebuilt the 300K-row FinanceDatabase table on every call.
  It's now loaded once per process.

## 2. Tool readiness (38 registered tools)

The contract harness checks every tool: snake_case name, object schema,
required ⊆ properties, `execute()` accepts every schema arg, no hidden
required args, and a usable description. It also checks that every
`AGENT_TOOL_MAP` / Laya-family entry exists. **All 38 pass.**

Readiness by behaviour (not covered by the contract check):

| Grade | Tools | Notes |
|---|---|---|
| Production-ready (real data or deterministic math, timeouts, fail-closed) | `web_search`, `web_scraper` (SSRF-guarded), `sec_filings`, `company_data`, `fetch_financial_statements`, `alpha_vantage`, `finnhub_data`, `financial_datasets`, `financial_calculator`, `antitrust_hhi_calculator`, `run_sensitivity_analysis`, `generate_football_field`, `document_search`, MCP tools | Depend on API keys; return `success=False` when they're unconfigured. |
| Usable with caveats | `fetch_comparable_companies` (benchmark fallback now labelled), `peer_discovery`, `finance_analysis` (FMP key), `run_monte_carlo_irr`, `churn_monte_carlo` (unseeded `random`, so not reproducible), `excel_*`, `generate_*` reporting tools | Fine as calculators/formatters. Outputs are only as good as the inputs. |
| **Heuristic / synthetic (now labelled `output_quality`; still not sourced analysis)** | `ai_stack_scanner` (keyword match, "Simplified mock logic"), `model_defensibility_scorer`, `ai_value_quantifier`, `carbon_footprint_extractor` ("mock logic" regex), `supply_chain_risk_flagger`, `esg_scorer` ("simulated_msci_rating"), `cyber_vuln_scanner`, `privacy_auditor`, `roadmap_generator`, `synergy_tracker`, `filing_due_diligence` (Kernel Ridge model trained on **synthetic** data at import time) | Labelled to the model and stamped on every result (`data_quality`, `method_note`). Replacing them with real data sources is still open. |

## 3. Agent readiness (24 agents)

All five cross-cutting issues found in the first pass are now fixed:

1. **Hardcoded confidence → evidence-based.** Eleven LLM-synthesis agents
   returned constant confidence (up to 0.95), which the confidence gate routes
   on, so weak outputs skipped peer review. They now use
   `BaseAgent._evidence_confidence(prior, response, data)`:
   - The prior is capped at 0.85.
   - It's discounted for empty or error output (×0.5), unparsed narrative
     output (×0.6), the failed-tool share (down to ×0.6), failed deterministic
     validation (×0.6), and no retrieved evidence (×0.85).
   - Output is marked `confidence_basis: "heuristic_signals"` and carries
     `confidence_signals`, so reports keep showing "Not calibrated" rather
     than a percentage.

   Agents covered: complex_reasoning, data_curator, dcf_lbo_architect,
   due_diligence, ingestion, investment_memo, market_researcher (debate),
   prospectus, and treasury (×3). Deterministic agents (compliance QA, red
   team, OFAS supervisor, report architect, compiler) keep rule-based values.
2. **Unguarded LLM calls:** `complex_reasoning_agent`, `data_curator_agent` and
   `compiler_agent` now return `AgentOutput(success=False)` on provider
   failure instead of raising.
3. **`ComplianceAgent` placeholder → real assessment.**
   - It retrieves deal-document excerpts per checklist item (concurrently) and
     assesses all items in one LLM call.
   - Any status other than `unknown` must cite a supplied excerpt id;
     otherwise it's downgraded to `unknown`.
   - `compliance_score` counts assessed items only (`None` when nothing
     could be assessed), with `coverage` and `unassessed` reported alongside.
     Previously "not assessed" scored as 0% compliant.
   - Confidence scales with coverage.
4. **Shared tool instances:** default tools are built once per process and
   shared by every agent's router. Only `document_search` is per agent. Router
   setup went from ~140 ms to ~0.06 ms per agent, which is ~3.5 s per
   orchestrator init.
5. **RL loop:** one `AgentQualityStore` per run instead of two, and no
   reflection LLM call for failed runs.

Tool labelling (see section 2):
- Tools now declare `output_quality`.
- The 8 keyword-screen tools are `heuristic`, `filing_due_diligence` is
  `synthetic_model`, and `roadmap_generator` / `synergy_tracker` are
  `template`.
- The model sees a `[HEURISTIC]` / `[SYNTHETIC_MODEL]` / `[TEMPLATE]` prefix
  in the tool description, and every result is stamped with `data_quality`
  plus a `method_note` instructing `[ESTIMATED]` citation.
- `churn_monte_carlo` is now seeded, so the same inputs give the same output.

### Text-reading tools: grounded extraction (`core/tools/grounded_extraction.py`)

Five tools that read free text now use LLM extraction instead of keyword
matching: `cyber_vuln_scanner`, `privacy_auditor`,
`carbon_footprint_extractor`, `supply_chain_risk_flagger` and
`ai_stack_scanner`.

- **Quote-verified findings:** every extracted finding must carry a verbatim
  quote. Quotes are checked against the input (normalizing whitespace, case
  and typographic punctuation), and unverifiable findings are dropped. Carbon
  figures must also appear inside their own quote. The model can classify and
  normalize what the document says, but can't add facts.
- **Better reading:** negation is handled ("no breaches in five years" is no
  longer a breach). Carbon figures are unit-normalized (t/kt/Mt CO2e), and
  the latest reported year per scope is used.
- **Privacy:** safeguards (SCCs, DPF) are reported separately from issues.
- **Stack scanner:** component names are canonicalized for the defensibility
  scorer.
- **Rule-based parts unchanged:** scores, severities and remediation costs
  are still deterministic rules.
- **Labelling:** results carry `data_quality: "extracted"` (schema prefix
  `[EXTRACTED]`). With no LLM, or with `TOOL_LLM_EXTRACTION=false`, they fall
  back to the keyword rules and say `data_quality: "heuristic"`.
- **Fallback bugs fixed:**
  - Supply-chain severity used `max()` on strings, so "High" plus cobalt
    became "Medium".
  - The carbon regex read the "2" in "scope 1 and scope 2…" as scope 1.
- **`esg_scorer`:** no longer emits a "simulated MSCI rating". It returns
  `indicative_rating_band` with `rating_basis` stating it's DealForge's own
  rule-based score.

Still `heuristic` (rule-based scoring over structured inputs):
`model_defensibility_scorer`, `ai_value_quantifier`, `esg_scorer`.

Still open, needing data or a decision rather than code:
- Retraining `filing_due_diligence` on observed filings (still
  `synthetic_model`).
- Real scanner/ESG-provider feeds, if wanted on top of document extraction.

### Build verification

- **OfficeCLI binaries:** the committed Windows binaries (~66 MB, including
  an `.exe.old`) are no longer tracked. OfficeCLI auto-downloads per platform
  (`OFFICECLI_AUTO_DOWNLOAD`), and `backend/data/officecli/` is ignored.
  Git history still contains them.
- **Backend:** fresh `pip install -r requirements.txt` on Python 3.13 failed
  at startup because SQLAlchemy's asyncio extra (`greenlet`) wasn't
  requested. It's now `sqlalchemy[asyncio]`. After the fix, uvicorn starts
  and `/health`, `/api/v1/laya/status` and
  `/api/v1/deals/{id}/knowledge-graph` respond.
- **Frontend:** `npm run build` failed from a clean checkout because
  `.gitignore`'s `*.json` rule had excluded `tsconfig.app.json` and
  `tsconfig.node.json`, which `tsconfig.json` references. Both are restored
  (Vite React-TS defaults, strict) and whitelisted. `tsc -b && vite build`
  passes.
- **Laya:** the first local decision builds the Router and downloads
  checkpoints (~54 s observed), and that cost landed on a user request. App
  startup now warms the backend in a background task (`LAYA_WARMUP`, default
  on; skipped for LM Studio). The sandbox blocks the checkpoint download
  (403), so a live decision couldn't be verified here. The failure handling
  (None results, breaker opening after 3 failures) was verified live.
- **Docker:** no daemon in the build sandbox, so the images themselves were
  not built.

Strongest agents: `financial_analyst` (grounded assessment, Laya output gate,
deterministic validation), `project_manager` (Laya triage with safety floors),
`red_team` (deterministic, no LLM), `treasury_agent`.
Weakest: `integration_planner_agent` (its tools are templates). `ai_tech_diligence`
and `esg_agent` now get quote-verified extraction from their text tools.

## 4. New configuration

| Env var | Default | Purpose |
|---|---|---|
| `LAYA_CACHE_TTL_SECONDS` | 300 | Decision cache TTL (0 disables caching) |
| `LAYA_CACHE_MAX_ENTRIES` | 2048 | LRU size |
| `LAYA_BREAKER_THRESHOLD` | 3 | Consecutive failures before the breaker opens |
| `LAYA_BREAKER_COOLDOWN_SECONDS` | 30 | Open-breaker duration |
| `TOOL_TIMEOUT_SECONDS` | 90 | Per-tool wall clock (overridable per tool via `timeout_seconds`) |
| `TOOL_MAX_CONCURRENCY` | 4 | Parallel tool calls per round |
| `MCP_DISCOVERY_TTL_SECONDS` | 60 | MCP tool-list cache |

## 5. Knowledge graph: Neo4j replaced with embedded SQLite

**How Neo4j was being used.**
- *Writes:* the orchestrator created a `Deal` node per run, and
  `BaseAgent._write_findings_to_graph` wrote numeric metrics, risks and
  entities after each successful agent run.
- *Reads:* none in production. `query_current_facts` had no callers, and
  `InsightForgeGraphRAG` was never instantiated.
- *Deployment:* neither docker-compose file runs a Neo4j service, so every
  deployment was offline. The client then re-dialled `bolt://localhost:7687`
  on **every write** and silently dropped the data.
- *Dependencies:* `requirements.txt` listed both `neo4j` and `neo4j-driver`.
  They install the same package name, and installing both breaks
  `import neo4j`.
- *Correctness:* nodes were MERGEd on `(label, name)` globally, so one deal's
  `financial_analyst_revenue` metric overwrote another deal's.
- *Wasted LLM call:* `OntologyService` made one LLM call per deal. Its output
  (`dynamic_tasks["_ontology"]`) was never read.
- *Security:* GraphRAG executed LLM-generated Cypher verbatim, with `deal_id`
  string-interpolated into the query.

**Replacement** (`core/knowledge_graph/graph_store.py`, selected in `service.py`):

| | Before | After |
|---|---|---|
| Backend | Neo4j server (never deployed) | Embedded SQLite file `$DATA_DIR/knowledge_graph.db` (WAL), stdlib only |
| Hard dependency | `neo4j` + `neo4j-driver` | none (`pip install neo4j` only if `KG_BACKEND=neo4j`) |
| Offline behaviour | reconnect attempt on every write, data dropped | always persists; Neo4j backend (if chosen) backs off 60s |
| Node scope | global `(label, name)`: cross-deal overwrites | per deal `(deal_id, label, name)` |
| Reads | none | `query_current_facts`, `get_risks`, `deal_summary`; `GET /api/v1/deals/{deal_id}/knowledge-graph` |
| GraphRAG | LLM-written Cypher executed verbatim | parameterized read of the deal's facts; LLM answers only from them |
| Ontology LLM call per deal | yes (output unused) | removed |

`KG_BACKEND=sqlite|neo4j|off` (default `sqlite`) and `KG_SQLITE_PATH`
configure it. Tests: `backend/tests/test_knowledge_graph.py`. The full suite
passes with the `neo4j` package uninstalled.

### Graph wired into the deal workflow

Before this change, the orchestrator ran agents via `agent.run()`, so
`_write_findings_to_graph` (only called from `run_with_structure`) never ran
during deal workflows. The graph only filled up from the chat-execute path.
Now:

- **Write-back:** after every successful agent in the analysis pass
  (parallel and sequential), the orchestrator writes its metrics, risks and
  entities. Red-team flags are recorded as risks (severity 1–5 → 2–10).
- **Read-back:** each analysis pass loads the deal's current facts once and
  passes them to every agent as `knowledge_graph_context`, which the tool
  loop appends to the system prompt. It's labelled as leads to verify, not
  evidence or instructions, and capped at 2.5k chars. The first pass sees
  nothing; loop-back passes see what earlier agents and the red team found.
- **Snapshot:** the completed state carries `knowledge_graph` (counts + top
  10 risks).
- **Stale-context fix:** the orchestrator now sets `agent._current_context`
  before `agent.run()`. Previously the tool loop read whatever context the
  previous run left behind (provider choice, the `deal_id` used to filter
  document retrieval).
- Every graph read and write fails soft; a graph error never fails an agent.

### Graph risks in the IC memo and compiled reports

- `knowledge_graph.service.risk_register(deal_id)` returns the deal's top
  current risks across all agents and the red team, ranked by severity,
  bounded, and fail-soft.
- **Investment memo agent:** the register is added to the prompt as a
  labelled "cross-agent risk register" that feeds the Risk Assessment
  section and the "Key risks" bullets. It also drives the risk-heatmap chart
  flag and is returned as `risk_register` in the memo output. Runtime objects
  (`kb_graph`) are no longer dumped into the memo's deal context.
- **Report compiler (orchestrator `compiler` node):** `deal_state` carries the
  register. The compiler is told to surface it in Key Takeaways and pass it to
  `generate_report` as `analyst_data.risk_matrix`, which the PPTX/Excel/DOCX
  generators already render.
- **Bug fixed:** the compiler read generated files from
  `tool_results[i]["result"]`, a key `generate_with_tools` never produces. It
  therefore always reported zero generated formats (confidence 0.0) even when
  `generate_report` succeeded.
- Deliberately **not** wired into the Reports Hub evidence pipeline
  (`document_planner`): that path only admits cited, curated evidence, and
  graph risks are agent-recorded findings, not sources. The report architect
  only plans sections from a capability inventory, so it needs no graph
  content.

### Final check

- **Fresh checkout:** full backend suite 261/261, the frontend builds, and the
  backend boots.
- **Per-task agent context:** agent instances are process-wide singletons,
  and `_current_context` was a plain attribute. Two deals running at once
  could overwrite each other's context mid-run, including the `deal_id` that
  filters document retrieval. It's now backed by a `ContextVar`, so each
  asyncio task (each agent run) sees its own context. There's a regression
  test that fails on the old code.

## 6. Data truncation, API I/O and tool arguments

**Prompt context:** 14 agents and the debate engine used
`json.dumps(context)[:2000]`.
- The cut left invalid JSON and kept whichever keys came first.
- With a realistic orchestrator context, the deal brief, Laya annotations,
  an object repr and the knowledge-graph block (already in the system
  prompt) filled the window. `fact_base` and `financial_data`, the numbers
  the agent was asked to analyse, were dropped.
- `app/core/prompt_context.render_context` now emits deal facts first,
  excludes runtime objects and blocks delivered elsewhere, truncates long
  values with explicit markers (valid JSON throughout), and lists
  `_omitted_for_length` keys.

**Ingestion:** knowledge-base parsing silently capped documents.
- The old limits were 10–15K chars per document, 2K per PDF page, 30 PDF
  pages, and 50 rows × 20 columns per sheet.
- Content is chunked downstream anyway, so the caps are now safety limits
  only: `INGEST_MAX_CHARS` 2M, `INGEST_MAX_PDF_PAGES` 1000,
  `INGEST_MAX_SHEET_ROWS` 5000, `INGEST_MAX_SHEET_COLS` 100.
- Any truncation is marked in the text and logged.

**Retrieval:** search returned the first 2000 chars of a matching node. With
pageindex section trees, that often wasn't the matched passage. It now
returns the 2000-char window with the most query-term hits.

**Uploads:** files were read whole into memory with no size or type check.
- They now stream to disk with `UPLOAD_MAX_MB` (default 50) and an extension
  allow-list (`UPLOAD_ALLOWED_EXTENSIONS`).
- Bulk uploads are capped at `UPLOAD_MAX_FILES` (default 50).
- Errors are 413, 415 and 400 (empty file); bulk uploads report them per
  file.

**JSON bodies:** a middleware rejects JSON bodies over `API_MAX_JSON_MB`
(default 5) with 413.

**Activity log:** the global feed stored full agent payloads, 1000 events
deep (including base64 report files). It now stores a slim copy. The
per-deal log keeps full events for report generation.

**Tool arguments:** primitive types are coerced per the tool's JSON schema
(`"1,000"` → 1000, `"true"` → True, JSON-string arrays and objects are
parsed) and `enum` is enforced. Uncoercible values return an actionable
error to the model instead of crashing inside the tool.
