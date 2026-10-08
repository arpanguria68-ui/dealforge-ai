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
| **Not production-grade (heuristic/keyword stubs presented as analysis)** | `ai_stack_scanner` (keyword match, "Simplified mock logic"), `model_defensibility_scorer`, `ai_value_quantifier`, `carbon_footprint_extractor` ("mock logic" regex), `supply_chain_risk_flagger`, `esg_scorer` ("simulated_msci_rating"), `cyber_vuln_scanner`, `privacy_auditor`, `roadmap_generator`, `synergy_tracker`, `filing_due_diligence` (Kernel Ridge model trained on **synthetic** data at import time) | These should either be relabelled as heuristics in their descriptions and outputs (`data_quality: "heuristic"`), or replaced with LLM-extraction + rule scoring or real data sources. |

## 3. Agent readiness (24 agents)

Cross-cutting issues found (not changed in this pass, since they alter agent
behaviour):

1. **Hardcoded confidence on success**: 0.95 in `complex_reasoning_agent`,
   `ofas_supervisor`; 0.9 in `data_curator_agent`; 0.85 in `dcf_lbo_architect`,
   `investment_memo_agent`, `ingestion_agent`; 0.75 in `due_diligence_agent`,
   `market_researcher`. The confidence gate and Laya pre-gate route on this
   value, so a constant 0.95 bypasses peer review regardless of output quality.
   Derive it from validation results, tool success ratio, and the Laya
   `gate_confidence().combined` score.
2. **Unguarded LLM calls**: `complex_reasoning_agent`, `data_curator_agent` and
   `compiler_agent` call `generate_with_tools` outside their `try`, so a
   provider failure raises out of `run()` instead of returning
   `AgentOutput(success=False)`.
3. **Placeholder logic**: `legal_advisor._assess_compliance_item` always returns
   `status: "unknown"` ("For now, return placeholder").
4. **Per-agent `ToolRouter` construction**: each agent instantiates all 38
   tools. A shared registry plus per-agent filtering would cut startup cost
   and memory.
5. **RL loop overhead**: `run_with_structure` constructs and initializes
   `AgentQualityStore` twice per run and always runs `reflect()` (an LLM call)
   even for failed outputs.

Strongest agents: `financial_analyst` (grounded assessment, Laya output gate,
deterministic validation), `project_manager` (Laya triage with safety floors),
`red_team` (deterministic, no LLM), `treasury_agent`.
Weakest: `ai_tech_diligence`, `esg_agent`, `integration_planner_agent`. Their
tools are the stubs listed above.

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
