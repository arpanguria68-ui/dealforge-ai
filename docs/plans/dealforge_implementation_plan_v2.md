# DealForge AI — Implementation Plan v2
**Date**: 2026-03-16
**Author**: Claude Code — Full System Audit
**Standard**: MBB-Grade (McKinsey / BCG / Bain delivery quality)
**Scope**: Agent pipeline, output quality, UI/UX, knowledge base, LLM routing, report compilation

---

## Executive Diagnosis

The system ran a complete deal analysis (Skybound CRM, Project Aether) and produced a 16-page PDF where **every agent failed with the same Vertex AI authentication error**. The Executive Summary shows: Deal Score 49%, Recommendation: None. No financial analysis. No risk matrix. No valuation. No investment thesis.

**Root cause**: Invalid Vertex AI key (OAuth2 token entered instead of API Key) → LLM Gateway fallback only tries Ollama (offline) → all agents write raw error strings directly into deliverables.

**Underlying architecture verdict**: The system design is sound. LangGraph orchestration, HaluGate verification, debate engine, and multi-agent parallelism are well-built. Three layers of compounding failures prevent the design from producing output:

1. **Infrastructure layer**: Wrong key type, broken fallback routing, missing data plumbing
2. **Agent layer**: Data pipeline disconnect — no structured fact extraction before analysis
3. **Output layer**: Errors written into PDFs, report templates not consuming agent data

---

## Part 1 — Bugs Fixed (Already Applied)

These were applied in the current session. Listed here for tracking.

| # | File | Fix Applied | Status |
|---|------|-------------|--------|
| F1 | `meeting_memo.py:907` | Removed orphaned `s</th></tr>")` syntax error — Investment Memo Agent now loads | ✅ Done |
| F2 | `vertex_client.py` | Added `_validate_api_key()` — detects OAuth tokens, raises clear error before API call | ✅ Done |
| F3 | `main.py:_fetch_vertex_models` | Now makes real API test call instead of returning `"online"` for any key | ✅ Done |
| F4 | `main.py:get_available_models` | Reads from SettingsService first, not only `os.environ` — works on first load | ✅ Done |
| F5 | `settings_service.py` | All agent routing defaults changed from `ollama` → `gemini` | ✅ Done |
| F6 | `SettingsPage.tsx` | Vertex AI section: key type instructions, inline OAuth token warning, red border | ✅ Done |

---

## Part 2 — Sprint 0: Critical Path to Working Output

**Goal**: Get the system producing real analytical content on the Skybound case.
**Time estimate**: 1 day
**Blocker for all subsequent work**

### S0.1 — Fix LLM Gateway: Fallback on Auth Errors

**File**: `backend/app/core/llm/llm_gateway.py`, lines 383–407

**Problem**: On any exception (including auth failure), gateway tries Ollama as last resort only. If Ollama is offline, it returns the error string as content. It does not try the full fallback chain (Gemini → OpenAI → Mistral) for hard errors — only for rate-limit errors.

**Fix required**:
```python
# In LLMGateway.call(), replace the exception handler block:

except Exception as e:
    logger.error("llm_call_failed", provider=actual_provider, error=str(e))

    # Determine if this is an auth/config error vs a transient error
    err_str = str(e).lower()
    is_auth_error = any(x in err_str for x in ["api key", "401", "403", "invalid key", "unauthorized"])

    # For auth errors: try the full fallback chain, not just Ollama
    fallback_chain = FALLBACK_CHAIN.get(actual_provider, [])
    for fallback_provider in fallback_chain:
        if fallback_provider == actual_provider:
            continue
        try:
            fallback_client = get_llm_client(fallback_provider)
            result = await fallback_client.generate(prompt=prompt, system_prompt=system_prompt)
            actual_provider = fallback_provider
            fallback_used = True
            logger.info("fallback_succeeded", fallback=fallback_provider)
            break
        except Exception as fallback_err:
            logger.warning("fallback_failed", provider=fallback_provider, error=str(fallback_err))
            continue

    if result is None:
        return {
            "content": f"[Error] LLM call failed: {str(e)[:200]}",
            ...
        }
```

### S0.2 — Guard Errors from Entering Report Output

**File**: `backend/app/core/reports/report_generator.py`

**Problem**: Agent results containing `[Error] LLM call failed: ...` are written directly into PPTX slides and PDF sections. Client-facing deliverables show raw stack traces.

**Fix required**: Before writing any agent result into a slide or section, check for error prefix:

```python
def _safe_content(content: str, fallback: str = "Analysis pending — manual review required.") -> str:
    """Strip error strings before writing to deliverable."""
    if not content:
        return fallback
    if content.startswith("[Error]") or content.startswith("[Rate limited]"):
        return fallback
    return content
```

Apply `_safe_content()` to every field written into PPTX/PDF templates.

### S0.3 — Fix Deal Name Extraction

**File**: `backend/app/orchestrator/graph.py`, `_node_init()`

**Problem**: `deal.name` is set from the user's raw message ("We've been approached regarding Skybound CRM, a vertical SaaS platform focused on logistics"). The report title and target company field inherit this verbatim.

**Fix required**: Extract clean company name from context:

```python
def _extract_company_name(deal_name: str, context: dict) -> str:
    """Extract short company name for use in report headers."""
    # Prefer explicit target_company field
    if context.get("target_company"):
        return context["target_company"]
    # Truncate at common separators
    for sep in [",", " — ", " - ", " a ", " an ", " the ", "\n"]:
        if sep in deal_name:
            return deal_name.split(sep)[0].strip()
    # Max 60 chars
    return deal_name[:60]
```

---

## Part 3 — Sprint 1: Data Pipeline (The Most Important Sprint)

**Goal**: Every agent gets structured financial inputs, not raw text.
**Time estimate**: 3–4 days
**Impact**: Fixes valuation agent (currently always returns "insufficient data"), financial analyst (DCF never computed), data curator (always gets empty dict)

### S1.1 — Create `DealFactBase` Dataclass

**New file**: `backend/app/core/deal_fact_base.py`

A structured dataclass that is extracted ONCE from the deal brief and shared with all agents. This is the equivalent of a model workbook in a real deal process.

```python
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class DealFactBase:
    """Structured fact base extracted from deal brief. Single source of truth."""

    # ── Identity ──────────────────────────────────────────────────
    company_name: str
    industry: str
    deal_category: str          # growth_equity | buyout | venture | distressed
    deal_stage: str             # screening | deep_dive | ic_memo

    # ── Revenue & Growth ──────────────────────────────────────────
    arr_usd_m: Optional[float] = None           # Annual Recurring Revenue ($M)
    arr_growth_yoy_pct: Optional[float] = None  # ARR growth YoY (%)
    revenue_model: Optional[str] = None         # SaaS | transactional | services
    revenue_concentration: Optional[str] = None # top customer % of ARR

    # ── Unit Economics ────────────────────────────────────────────
    gross_margin_pct: Optional[float] = None    # Gross margin (%)
    ebitda_margin_pct: Optional[float] = None   # EBITDA margin (%)
    cac_payback_months: Optional[int] = None    # CAC payback period (months)
    ltv_cac_ratio: Optional[float] = None       # LTV/CAC ratio
    nrr_pct: Optional[float] = None             # Net Revenue Retention (%)
    gross_retention_pct: Optional[float] = None # Gross Revenue Retention (%)

    # ── Cash & Capital ────────────────────────────────────────────
    monthly_burn_usd_k: Optional[float] = None  # Monthly cash burn ($K)
    annualized_burn_usd_m: Optional[float] = None
    cash_balance_usd_m: Optional[float] = None  # Cash on hand ($M)
    runway_months: Optional[float] = None       # Derived: cash / burn

    # ── DCF Inputs (derived or estimated) ────────────────────────
    projected_fcf_5yr: List[float] = field(default_factory=list)
    wacc: Optional[float] = None
    terminal_growth_rate: float = 0.025

    # ── Market Context (from web search) ─────────────────────────
    sector_ev_arr_median: Optional[float] = None    # Comparable EV/ARR (x)
    sector_cac_payback_median: Optional[float] = None
    sector_nrr_p75: Optional[float] = None
    sector_gross_margin_median: Optional[float] = None

    # ── Data Quality Flags ────────────────────────────────────────
    fields_confirmed: List[str] = field(default_factory=list)   # from source docs
    fields_estimated: List[str] = field(default_factory=list)   # LLM-derived
    fields_missing: List[str] = field(default_factory=list)     # not available
```

### S1.2 — Create `IngestionAgent`

**New file**: `backend/app/agents/ingestion_agent.py`

Runs as the FIRST node in the LangGraph workflow, before `screening`. Extracts `DealFactBase` from the deal context. Uses the `financial_calculator` tool to derive downstream metrics from raw inputs.

**Prompt structure**:
```
You are extracting structured financial KPIs from a deal brief.

DEAL BRIEF:
{raw_deal_context}

Extract the following fields. For each field:
- Mark as "confirmed" if explicitly stated in the brief with a number
- Mark as "estimated" if you are deriving it from other stated numbers
- Mark as "missing" if there is no basis to populate it

DO NOT invent numbers. If a field is missing, leave it null and add to fields_missing.

DERIVATION RULES:
- annualized_burn_usd_m = monthly_burn_usd_k * 12 / 1000
- runway_months = cash_balance_usd_m * 1000 / monthly_burn_usd_k (if both known)
- ebitda_margin_pct ≈ gross_margin_pct - (opex_as_pct_revenue) if decomposable
- wacc = 0.12 to 0.18 for venture/growth, 0.08 to 0.12 for buyout (use midpoint if unknown)
```

**Integration into graph.py**:
```python
workflow.add_node("ingestion", self._node_ingestion)
workflow.set_entry_point("ingestion")
# ingestion → init → screening → ...
```

### S1.3 — Pass `DealFactBase` to All Financial Agents

**Files**: `valuation_agent`, `financial_analyst`, `dcf_lbo_architect`, `data_curator`

Add to `DealState`:
```python
@dataclass
class DealState:
    ...
    fact_base: Optional[DealFactBase] = None
```

Each financial agent reads from `state.fact_base` as structured input, not from freeform `context["raw_text"]`. The valuation agent's `_run_valuation_method("dcf")` check:

```python
# BEFORE (always fails):
cash_flows = financial_data.get("projected_cash_flows")  # always None

# AFTER (uses fact base):
fact_base = context.get("fact_base")
cash_flows = fact_base.projected_fcf_5yr if fact_base else None
wacc = fact_base.wacc if fact_base else None
```

### S1.4 — Fix DataCurator Context Key

**File**: `backend/app/agents/data_curator_agent.py`, line 90

```python
# BEFORE (always gets empty dict):
agent_outputs = context.get("agent_outputs", {})

# AFTER (correctly reads from state):
agent_results_list = context.get("agent_results", [])
agent_outputs = {
    r.get("agent_type", r.get("agent", f"agent_{i}")): r
    for i, r in enumerate(agent_results_list)
    if isinstance(r, dict)
}
```

Also replace the hardcoded `confidence=0.9` with a computed score based on how many fact_base fields are confirmed vs missing.

### S1.5 — Remove 500-char Truncation in InvestmentMemoAgent

**File**: `backend/app/agents/investment_memo_agent.py`, line 90

```python
# BEFORE:
data = json.dumps(r.get("data", {}), default=str)[:500]

# AFTER:
data = json.dumps(r.get("data", {}), default=str)[:4000]
```

Pass the full DataCurator output as primary input, not the raw agent results list.

---

## Part 4 — Sprint 2: Scrum Master UX

**Goal**: Surface the 3-phase ProjectManager flow in the Chat UI.
**Time estimate**: 2 days

### S2.1 — ChatWindow: Deal Intake Mode

**File**: `frontend/src/sections/ChatWindow.tsx`

Add a dedicated intake flow that triggers when a new deal is created. The UI shows three distinct phases with progress indicators:

**Phase 1 — Data Inventory** (triggered by ProjectManager agent, phase 1):
```
┌─────────────────────────────────────────────────────┐
│ 🧩 Scrum Master                              Phase 1/3│
│─────────────────────────────────────────────────────│
│ Before assembling the team, I need to understand     │
│ what data we're working with.                        │
│                                                      │
│ [Upload CIM / IM / Financial Model]  [Skip — brief only]│
│                                                      │
│ Deal category:                                       │
│ [Growth Equity] [Buyout] [Venture] [Distressed]      │
│                                                      │
│ Investment thesis (one sentence):                    │
│ [________________]                                   │
└─────────────────────────────────────────────────────┘
```

**Phase 2 — Clarifying Questions** (5 Socratic questions from PM agent):
- Questions rendered as individual clickable cards, not a wall of text
- Each answered question gets a checkmark
- "Ready to proceed" button activates after all answered

**Phase 3 — One Follow-Up Only**:
- After Phase 2, PM agent asks exactly one follow-up set (max 3 questions)
- This is enforced in the backend: `ProjectManagerAgent` has a `_followup_count` state, max = 1
- After follow-up, PM presents risk-annotated task plan

**Enforce single follow-up in ProjectManager agent**:
```python
# In project_manager.py:
async def run(self, task: str, context: dict) -> AgentOutput:
    phase = context.get("pm_phase", 1)
    followup_count = context.get("pm_followup_count", 0)

    if phase == 2 and followup_count >= 1:
        # Force move to Phase 3 — no more follow-ups
        phase = 3
    ...
```

### S2.2 — Render `followUps[]` and `missingData[]` in ChatWindow

**File**: `frontend/src/sections/ChatWindow.tsx`

These fields are defined in the `Message` interface but never rendered. Add below each agent message:

```tsx
{message.followUps && message.followUps.length > 0 && (
    <div className="mt-2 border-l-2 border-blue-400 pl-3 space-y-1">
        <p className="text-xs text-muted-foreground font-medium">Suggested follow-ups:</p>
        {message.followUps.map((q, i) => (
            <button
                key={i}
                onClick={() => handleSendMessage(q)}
                className="block text-xs text-blue-600 hover:underline text-left"
            >{q}</button>
        ))}
    </div>
)}
{message.missingData && message.missingData.length > 0 && (
    <div className="mt-2 border-l-2 border-amber-400 pl-3 space-y-1">
        <p className="text-xs text-amber-600 font-medium">Data needed for deeper analysis:</p>
        {message.missingData.map((item, i) => (
            <span key={i} className="inline-block mr-2 text-xs bg-amber-100 text-amber-800 rounded px-2 py-0.5">{item}</span>
        ))}
    </div>
)}
```

### S2.3 — Agent Thinking State Animation

Add a pulsing skeleton card while status is `"thinking"`:
```tsx
{message.status === 'thinking' && (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>{agentMeta.label} is analyzing...</span>
        <span className="text-xs opacity-60">~{agentMeta.estimatedSeconds}s</span>
    </div>
)}
```

---

## Part 5 — Sprint 3: Knowledge Base Integration

**Goal**: Deal-scoped document indexing with mandatory retrieval for financial agents.
**Time estimate**: 2–3 days

### S3.1 — Deal-Scoped Document Indexing

**File**: `backend/app/core/document_store.py`

Change all PageIndex queries to filter by `deal_id`. Each deal's documents exist in an isolated namespace.

```python
# Add deal_id to every index call:
await pageindex_client.index_document(
    content=text,
    metadata={"deal_id": deal_id, "source": filename, "agent_type": "ingestion"},
)

# Add deal_id filter to every search:
results = await pageindex_client.search(
    query=query,
    filters={"deal_id": deal_id},
    top_k=top_k,
)
```

### S3.2 — Document Upload UI in ChatWindow

Add paperclip/upload button in chat input area. On upload:
1. POST to `POST /documents/upload` with `deal_id`
2. Show indexing progress spinner
3. On complete: show "3 documents indexed — agents will use these for analysis"
4. Support PDF, DOCX, XLSX

### S3.3 — Mandatory Retrieval for Document-Heavy Agents

**File**: `backend/app/agents/base.py`, `run_with_structure()`

Tag agents as `document_dependent = True` in:
- `financial_analyst`, `legal_advisor`, `due_diligence_agent`, `prospectus_agent`

For these agents, force `retrieve_context()` before `run()` if documents are indexed:
```python
if self.document_dependent and deal_id:
    doc_context = await self.retrieve_context(self._build_retrieval_query(task), top_k=8, deal_id=deal_id)
    if doc_context:
        context["retrieved_documents"] = doc_context
        logger.info("mandatory_retrieval_complete", agent=self.name, chunks=len(doc_context))
```

### S3.4 — Expose Document Search in UI

Add `GET /documents/search?deal_id=X&q=...` endpoint. Show retrieved chunks as collapsed "Sources" cards below agent responses (Perplexity-style).

---

## Part 6 — Sprint 4: Report Quality (McKinsey Standard)

**Goal**: Every report section contains real analysis, not stubs.
**Time estimate**: 3–4 days

### S6.1 — SCQA Executive Summary with Real Data

**File**: `backend/app/agents/compiler_agent.py`, `_build_compilation_prompt()`

Current prompt passes only `deal_name`, `target_company`, `final_score`, `agents_completed`. Replace with structured data from `DealFactBase`:

```python
def _build_compilation_prompt(self, task, deal_state, formats, agent_results):
    fact_base = deal_state.get("fact_base", {})

    # Build structured financial context
    financial_context = {
        "arr_usd_m": fact_base.get("arr_usd_m"),
        "arr_growth_yoy_pct": fact_base.get("arr_growth_yoy_pct"),
        "gross_margin_pct": fact_base.get("gross_margin_pct"),
        "monthly_burn_usd_k": fact_base.get("monthly_burn_usd_k"),
        "cac_payback_months": fact_base.get("cac_payback_months"),
        "ltv_cac": fact_base.get("ltv_cac_ratio"),
        "nrr_pct": fact_base.get("nrr_pct"),
        "deal_score": deal_state.get("final_score"),
        "recommendation": deal_state.get("recommendation"),
    }

    # SCQA enforcement in prompt:
    return f"""
You are a McKinsey Senior Engagement Manager writing an Investment Committee memo.

DEAL FINANCIALS (verified):
{json.dumps(financial_context, indent=2)}

AGENT SYNTHESIS:
{json.dumps(agent_results, indent=2)}

Write an Executive Summary in strict SCQA format:
- SITUATION: What is {company_name} and why are we looking at it?
  (Use specific numbers: ARR, margin, growth rate)
- COMPLICATION: What is the core tension preventing a straightforward proceed?
  (Use specific: CAC payback vs sector median, burn vs runway)
- QUESTION: Given the complication, what is the investment decision?
- ANSWER: Our recommendation with exactly 3 supporting data points.

Every sentence must contain at least one specific number or named metric.
Do not write generic consulting boilerplate.
"""
```

### S6.2 — Financial Analysis with Benchmarks

**File**: `backend/app/agents/financial_analyst.py`, `_build_system_prompt()`

Inject sector benchmarks into every financial analysis prompt:

```python
VERTICAL_SAAS_BENCHMARKS = {
    "cac_payback_median_months": 14,
    "cac_payback_top_quartile_months": 8,
    "nrr_median_pct": 105,
    "nrr_top_quartile_pct": 120,
    "gross_margin_median_pct": 72,
    "arr_growth_top_quartile_pct": 40,
    "ev_arr_median_x": 5.5,
    "ev_arr_top_quartile_x": 8.0,
}

# Add to system prompt:
f"""
Sector Benchmarks (Vertical SaaS, 2024):
- CAC Payback: {VERTICAL_SAAS_BENCHMARKS['cac_payback_median_months']}mo median, {VERTICAL_SAAS_BENCHMARKS['cac_payback_top_quartile_months']}mo top quartile
- NRR: {VERTICAL_SAAS_BENCHMARKS['nrr_median_pct']}% median, {VERTICAL_SAAS_BENCHMARKS['nrr_top_quartile_pct']}% top quartile
- Gross Margin: {VERTICAL_SAAS_BENCHMARKS['gross_margin_median_pct']}% median
- EV/ARR: {VERTICAL_SAAS_BENCHMARKS['ev_arr_median_x']}x median, {VERTICAL_SAAS_BENCHMARKS['ev_arr_top_quartile_x']}x top quartile

When assessing Skybound:
- CAC payback 28 months = 2x sector median → quantify sales efficiency gap
- NRR 98% = gross retention, not net → flag absence of expansion revenue
- Gross margin 82% > sector median 72% → product-quality economics, a genuine moat
"""
```

### S6.3 — Valuation Football Field

**File**: `backend/app/agents/financial_analyst.py` and `report_generator.py`

The `ValuationAgent.calculate_comps_valuation()` static method exists and is mathematically correct. Wire it to actual data:

```python
# In ValuationAgent._run_valuation_method("comparable_companies"):
# Use fact_base and web_search tool to get sector multiples
comps_result = await self.tools.execute("web_search", {
    "query": "vertical SaaS logistics CRM EV/ARR acquisition multiple 2023 2024"
})
# Parse multiples from search results
# Pass to calculate_comps_valuation()
```

Render three valuation methods as a football field in the PPTX:
```
Method             Low      Mid      High
DCF               $62M     $72M     $83M
Comps (5-8x ARR)  $75M     $98M    $120M
Precedents        $68M     $88M    $105M
─────────────────────────────────────────
Recommended range          $72M–$98M
```

### S6.4 — Deal Score Waterfall in Report

**File**: `backend/app/core/reports/report_generator.py`

Replace single score display with a score waterfall showing exactly what drove the 53.75/100:

```
Score Drivers — Skybound CRM
─────────────────────────────────────────────────────
Base score                                        100
─────────────────────────────────────────────────────
Growth stall (5% vs 20% peer median)              -15
CAC payback (28mo vs 14mo median)                 -12
Cash burn ($2.4M/yr, ~6mo runway risk)             -8
Customer concentration (unknown)                   -5
─────────────────────────────────────────────────────
Gross retention (98%, top decile)                  +8
Gross margin (82%, 10pp above median)              +5
Sticky vertical product (logistics-specific)       +3
Founder credibility (unknown)                     ±0
─────────────────────────────────────────────────────
FINAL SCORE                                    53.75
RECOMMENDATION                     PROCEED WITH CAUTION
Structure: Revenue-based earnout tied to CAC improvement
```

### S6.5 — Risk Matrix with Evidence

**File**: `backend/app/agents/risk_assessor.py`, `_build_assessment_prompt()`

Add explicit requirement for evidence-backed risks with deal-breaker flags:

```
For each risk, provide:
1. Risk statement (specific to this company, not generic)
2. Evidence: cite the exact data point that signals this risk
3. Probability (0–1) and Impact (0–1)
4. Deal-breaker: YES/NO
5. Mitigation: specific, actionable (not "implement risk management")

Example of BAD risk: "Market competition may increase"
Example of GOOD risk: "28-month CAC payback suggests S&M efficiency ratio of ~0.4x,
  meaning Skybound spends $2.33 to acquire $1 of ARR — at current burn, any
  customer churn acceleration would trigger a liquidity event within 8 months."
```

---

## Part 7 — Sprint 5: Dashboard Overhaul

**Goal**: Real-time deal intelligence, not a static table.
**Time estimate**: 2–3 days

### S7.1 — Workflow Timeline Component

New component: `DealWorkflowTimeline.tsx`

Shows each LangGraph node as a stage with status:
```
init ✓ → screening ✓ → parallel_analysis ⟳ [2:34] → consistency_check ○ → ...
                            ↓
                     financial ✓  legal ⟳  risk ○  market ○
```

Poll `GET /deals/{id}/status` every 5 seconds while workflow is running.

### S7.2 — Agent Output Drill-Down

When clicking a deal, open a detail drawer with tabs per agent:

```
[Financial] [Legal] [Risk] [Market] [Red Team] [Score] [Report]
```

Each tab shows:
- Agent output (full, not truncated)
- Confidence score with trend
- Tool calls made
- Any debate challenges and responses

### S7.3 — Real-Time Activity Feed with Filters

Add filter chips to the agent activity feed:
- `[All]` `[Financial]` `[Legal]` `[Risk]` `[Market]` `[Errors Only]`

### S7.4 — Confidence Trend Sparkline

Add a small sparkline chart on each deal card showing confidence as agents complete:
```
Confidence: 53.75  ▁▂▃▅▆▇ (builds as agents finish)
```

---

## Part 8 — Sprint 6: Infrastructure Hardening

**Goal**: Production-ready error handling, rate limiting, observability.
**Time estimate**: 2 days

### S8.1 — Error Taxonomy

**New file**: `backend/app/core/errors.py`

```python
class AgentError(Exception):
    pass

class ProviderAuthError(AgentError):
    """Invalid or expired API key. User action required."""
    pass

class ProviderRateLimitError(AgentError):
    """Quota exhausted. Triggers full fallback chain."""
    pass

class AgentTimeoutError(AgentError):
    """Agent exceeded time budget. Skip and continue workflow."""
    pass

class InsufficientDataError(AgentError):
    """Not enough data to run. Request from user."""
    pass
```

Map each error class to:
- `ProviderAuthError` → emit SSE event → show "Fix API Key" prompt in Settings
- `ProviderRateLimitError` → trigger full fallback chain (not just Ollama)
- `AgentTimeoutError` → skip agent gracefully, mark as partial in report
- `InsufficientDataError` → surface in Phase 2 questions, request from user

### S8.2 — HTTP Rate Limiting

**File**: `backend/app/main.py`

Add `slowapi` or `fastapi-limiter`:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/agents/run")
@limiter.limit("10/second")
async def run_agent(...):
    ...
```

### S8.3 — API Health Check

Add `GET /health` returning:
```json
{
  "status": "healthy",
  "providers": {
    "gemini": {"status": "ok", "latency_ms": 234},
    "vertex": {"status": "auth_error", "error": "Invalid API key"},
    "ollama": {"status": "offline"}
  },
  "agents_registered": 24,
  "deals_active": 3
}
```

---

## Part 9 — Framework Decision: LangGraph vs Agno

**Decision: Keep LangGraph. Do not migrate.**

| Criterion | LangGraph | Agno |
|-----------|-----------|------|
| Complex branching / loop-back | Native conditional edges | Would need custom wiring |
| State persistence across steps | Built-in MemorySaver | Manual |
| Debate engine (multi-round) | Native node with loop | Would require custom framework |
| HaluGate verification as step | Natural node insertion | Plugin-style only |
| Human-in-the-loop (pauses) | Native interrupt support | Not built-in |
| 17-node workflow with 24 agents | Tested at this scale | Untested |
| Parallel agent execution | Native `add_node` + semaphore | Built-in but less control |

Agno is better for simple tool-using chatbots. DealForge requires a complex state machine with multi-round debates, adversarial red-teaming, and HaluGate cross-agent verification. **LangGraph is the correct choice.** Migrate to Agno would be a 3-week regression with no upside.

**Recommended upgrade**: LangGraph v0.2+ has improved support for:
- Streaming individual node outputs (enables real-time workflow timeline in UI)
- Persistent checkpoints (resume interrupted workflows)
- Human-in-the-loop pauses (IC approval gates)

---

## Part 10 — Agent-to-Provider Optimal Mapping

Configure this in Settings UI → Agent Routing per agent:

### Use Gemini Flash (`gemini-1.5-flash` or `gemini-2.0-flash-lite`)
Fast, cheap, high volume. Use for structured/lightweight tasks.

| Agent | Reason |
|-------|--------|
| `project_manager` | Intake questions, planning |
| `scoring_agent` | Structured formula scoring |
| `data_curator` | JSON normalization |
| `report_architect` | Blueprint config |
| `compliance_agent` | Checklist QA |
| `market_researcher` | Web search + TAM |
| `market_risk_agent` | Rule-based scoring |
| `esg_agent` | Checklist-based ESG scoring |

### Use Gemini Pro (`gemini-1.5-pro` or `gemini-2.5-flash`)
Deep reasoning, long context, nuanced judgment.

| Agent | Reason |
|-------|--------|
| `financial_analyst` | DCF narratives, benchmark comparisons |
| `legal_advisor` | Contract analysis, regulatory nuance |
| `risk_assessor` | Cross-domain synthesis |
| `investment_memo_agent` | Long-form writing, IC memo |
| `complex_reasoning` | Chain-of-Thought |
| `debate_moderator` | Multi-agent synthesis |
| `red_team_agent` | Adversarial stress-test |
| `business_analyst` | Unit economics, SCQA |

### Use Vertex AI (`gemini-2.0-flash-lite` via Vertex)
Enterprise quotas, data governance, no data retention. Use for production and sensitive data.

| Agent | Reason |
|-------|--------|
| `advanced_financial_modeler` | High-frequency DCF iterations |
| `valuation_agent` | Repeated comps queries |
| `due_diligence_agent` | Processes client CIM (sensitive) |
| `compiler_agent` | Full deal state synthesis |
| `dcf_lbo_architect` | Numerical precision, temp=0 |

---

## Priority Matrix

| Priority | Sprint | Item | Business Impact | Effort |
|----------|--------|------|-----------------|--------|
| **P0** | 0 | LLM Gateway fallback on auth errors | Blocking all output | 2h |
| **P0** | 0 | Guard errors from report output | Any deliverable is toxic | 1h |
| **P0** | 0 | Deal name extraction | Every report title is wrong | 1h |
| **P1** | 1 | `DealFactBase` + `IngestionAgent` | Valuation works for first time | 1 day |
| **P1** | 1 | Fix DataCurator context key | DataCurator works for first time | 30min |
| **P1** | 1 | Remove 500-char truncation | InvestmentMemo gets real data | 5min |
| **P1** | 4 | SCQA with real financial data | Executive Summary becomes usable | 3h |
| **P1** | 4 | Sector benchmarks in prompts | Analysis has context for comparison | 2h |
| **P2** | 2 | Scrum Master 3-phase UX | User sees the system is working | 1 day |
| **P2** | 2 | Render followUps[] in ChatWindow | Follow-up flow works | 2h |
| **P2** | 4 | Score waterfall in report | Score is explainable | 3h |
| **P2** | 4 | Risk matrix with evidence | Risks are specific not generic | 2h |
| **P2** | 5 | Workflow timeline on Dashboard | Visibility into deal progress | 1 day |
| **P3** | 3 | Deal-scoped PageIndex | Documents isolated per deal | 1 day |
| **P3** | 3 | Document upload UI | Users can add CIM/financials | 1 day |
| **P3** | 5 | Agent output drill-down | Analysts can inspect reasoning | 1 day |
| **P3** | 6 | Error taxonomy + HTTP rate limiting | Production hardening | 1 day |
| **P4** | 4 | Valuation football field in PPTX | Visual deliverable quality | 2h |
| **P4** | 6 | API health check endpoint | Operational visibility | 2h |

---

## Definition of Done — MBB Standard

A deal analysis is considered **deliverable** when it meets all of the following:

### Output Quality Gates
- [ ] Executive Summary uses SCQA format with at least 4 specific numbers
- [ ] Every risk has supporting evidence cited to a data source
- [ ] Valuation section shows 3 methods (DCF, comps, precedents) with ranges
- [ ] Score waterfall shows at least 5 named drivers with magnitudes
- [ ] Recommendation is one of: PROCEED / PROCEED WITH CONDITIONS / HOLD / PASS — with 3 stated conditions
- [ ] No `[Error]` strings appear anywhere in deliverable
- [ ] Report title uses company name, not raw user message

### Data Quality Gates
- [ ] `DealFactBase` has at least 8 confirmed fields (not estimated, not missing)
- [ ] At least one financial document indexed in PageIndex OR user confirmed "brief only"
- [ ] DCF model ran with explicit cash flows and WACC (even if estimated)
- [ ] Comparable companies query returned at least 3 named peers

### Process Quality Gates
- [ ] At least 4 agents completed successfully (not errored)
- [ ] HaluGate verification passed (narrative consistent with financial ground truth)
- [ ] Red Team agent ran and score did not decrease by >10 points after red team
- [ ] Confidence score is computed, not hardcoded

---

## Appendix: File Change Register

| File | Change Type | Sprint | Description |
|------|-------------|--------|-------------|
| `backend/app/core/llm/vertex_client.py` | Modified | 0 ✅ | Key format validation |
| `backend/app/core/reports/meeting_memo.py` | Modified | 0 ✅ | Syntax error removed |
| `backend/app/main.py` | Modified | 0 ✅ | Vertex test + SettingsService read |
| `backend/app/core/settings_service.py` | Modified | 0 ✅ | Agent routing defaults to gemini |
| `frontend/src/sections/SettingsPage.tsx` | Modified | 0 ✅ | Vertex instructions + OAuth warning |
| `backend/app/core/llm/llm_gateway.py` | Modified | 0 | Full fallback chain on auth errors |
| `backend/app/orchestrator/graph.py` | Modified | 0 | Deal name extraction; ingestion node |
| `backend/app/core/reports/report_generator.py` | Modified | 0 | Guard errors from slides/PDF |
| `backend/app/core/deal_fact_base.py` | New | 1 | DealFactBase dataclass |
| `backend/app/agents/ingestion_agent.py` | New | 1 | Structured KPI extraction |
| `backend/app/orchestrator/state.py` | Modified | 1 | Add fact_base to DealState |
| `backend/app/agents/financial_analyst.py` | Modified | 1 + 4 | Benchmarks; use fact_base |
| `backend/app/agents/valuation_agent.py` | Modified | 1 | Use fact_base for DCF inputs |
| `backend/app/agents/data_curator_agent.py` | Modified | 1 | Fix context key; computed confidence |
| `backend/app/agents/investment_memo_agent.py` | Modified | 1 | Remove 500-char truncation |
| `backend/app/agents/compiler_agent.py` | Modified | 4 | SCQA with real financial data |
| `backend/app/agents/risk_assessor.py` | Modified | 4 | Evidence-backed risks |
| `backend/app/core/reports/report_generator.py` | Modified | 4 | Score waterfall; football field |
| `frontend/src/sections/ChatWindow.tsx` | Modified | 2 | Scrum Master UX; followUps render |
| `frontend/src/sections/Dashboard.tsx` | Modified | 5 | Workflow timeline; drill-down |
| `backend/app/core/document_store.py` | Modified | 3 | Deal-scoped indexing |
| `backend/app/agents/base.py` | Modified | 3 | Mandatory retrieval for doc agents |
| `backend/app/core/errors.py` | New | 6 | Error taxonomy |
