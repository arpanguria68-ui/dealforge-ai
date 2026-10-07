# DealForge AI — V2.0 Feature Enhancement PRD
**Spec Level: Implementation-Ready**
**Version:** 2.0.0
**Date:** 2026-03-26
**Prepared For:** AI Coding Assistant (Claude / Gemini Flash)
**Target Codebase:** `F:/code project/Kimi_Agent_DealForge AI PRD/backend/`

---

## 📋 Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current State Assessment](#current-state-assessment)
3. [Master Feature List & Priority Matrix](#master-feature-list)
4. [Phase 1 — Critical Hotfixes](#phase-1-critical-hotfixes) *(Week 1 — Do First)*
5. [Phase 2 — Research Quality](#phase-2-research-quality) *(Week 2–3)*
6. [Phase 3 — Live Intelligence](#phase-3-live-intelligence) *(Week 3–4)*
7. [Phase 4 — Knowledge Graph](#phase-4-knowledge-graph) *(Week 4–6)*
8. [Phase 5 — Advanced Orchestration](#phase-5-advanced-orchestration) *(Week 6–8)*
9. [Phase 6 — Production Hardening](#phase-6-production-hardening) *(Week 8–10)*
10. [Global Architecture Diagram](#global-architecture-diagram)
11. [Data Schemas](#data-schemas)
12. [Implementation Rules](#implementation-rules)

---

## Executive Summary

DealForge AI is an MBB-grade M&A due diligence platform using a 17-node LangGraph pipeline with 25 specialized agents. This PRD specifies **35 features** derived from deep analysis of three reference repositories:

| Source | Key Patterns Borrowed |
|---|---|
| **MiroFish (Swarm)** | Neo4j Knowledge Graph, GraphRAG, Hybrid Search, Stakeholder Simulation, Temporal Facts, Agent Write-Back |
| **Local Deep Researcher** | IterDRAG research loop, Running Summary, Token Budgeting, Live Web Search, Thinking Token Stripping, Dual Structured Output |
| **Internal Audit** | Context passthrough bugs, hard-coded agent tasks, MessageBus sync gap, model capability registry, convergence guards |

**Current readiness:** 87% production-ready (all prior critical bugs resolved)
**Target after V2.0:** 97% production-ready, adaptive orchestration, GraphRAG, live intelligence

---

## Current State Assessment

### Architecture Type (BEFORE)
```
75% Hard-Structured Pipeline / 25% Adaptive
- Fixed 17-node LangGraph sequence (always same order)
- Fixed 4 agents in parallel_analysis (21 of 25 agents never reached)
- Fixed generic task strings ("Analyze financial analyst aspects")
- No model capability awareness
- No live web search
- No knowledge graph (flat vector RAG only)
- <think> tokens not stripped → JSON parse failures on reasoning models
- max_tokens / temperature not forwarded to LLM clients
```

### Architecture Type (AFTER V2.0)
```
40% Fixed Core / 60% Adaptive
- Fixed: verification chain order (debate→red_team→halugate→report)
- Adaptive: agent selection, task generation, loop targets, quality gates,
            cross-agent sync, knowledge graph, iterative research loops
```

---

## Master Feature List

| ID | Feature | Phase | Source | Priority | Effort |
|---|---|---|---|---|---|
| F-001 | Thinking token stripping | 1 | Local-DR + Internal | 🔴 P0 | XS |
| F-002 | max_tokens + temperature passthrough | 1 | Internal Audit | 🔴 P0 | XS |
| F-003 | Ollama native JSON mode | 1 | Local-DR | 🔴 P0 | XS |
| F-004 | Context window enforcement + truncation | 1 | Internal Audit | 🔴 P0 | S |
| F-005 | LLM-based deal name extraction | 1 | Internal Audit | 🔴 P0 | XS |
| F-006 | Fix ComplexReasoningAgent context | 1 | Internal Audit | 🔴 P0 | XS |
| F-007 | Fix ReportArchitectAgent context | 1 | Internal Audit | 🔴 P0 | XS |
| F-008 | Loop-back convergence guard | 1 | Internal Audit | 🟠 P1 | XS |
| F-009 | Model capability registry | 2 | Internal Audit | 🔴 P0 | M |
| F-010 | Pre-flight capability probe | 2 | Internal Audit | 🟠 P1 | M |
| F-011 | Per-source token budgeting | 2 | Local-DR | 🟠 P1 | S |
| F-012 | IterDRAG per-MECE-branch research loop | 2 | Local-DR | 🔴 P0 | M |
| F-013 | Running summary pattern | 2 | Local-DR | 🟠 P1 | S |
| F-014 | Source registry in DealState | 2 | Local-DR | 🟠 P1 | S |
| F-015 | Smart targeted loop-back | 2 | Internal Audit | 🟠 P1 | S |
| F-016 | MessageBus post-parallel sync | 2 | Internal Audit | 🟠 P1 | S |
| F-017 | Live web search tool | 3 | Local-DR | 🔴 P0 | M |
| F-018 | Hybrid search (Vector + BM25) | 3 | MiroFish | 🟠 P1 | M |
| F-019 | Dynamic deal-specific task generation | 3 | Internal Audit | 🔴 P0 | M |
| F-020 | WorkflowConfig from_runnable_config | 3 | Local-DR | 🟡 P2 | S |
| F-021 | Deal knowledge graph (Neo4j) | 4 | MiroFish | 🔴 P0 | L |
| F-022 | Dynamic deal ontology generation | 4 | MiroFish | 🟠 P1 | M |
| F-023 | Agent write-back to knowledge graph | 4 | MiroFish | 🟠 P1 | M |
| F-024 | Temporal facts (valid_at / expired_at) | 4 | MiroFish | 🟡 P2 | M |
| F-025 | InsightForge GraphRAG | 4 | MiroFish | 🔴 P0 | L |
| F-026 | Dynamic agent selection planner | 5 | Internal Audit | 🟠 P1 | L |
| F-027 | Per-stage quality gates | 5 | Memento / Local-DR | 🟠 P1 | M |
| F-028 | Stakeholder reaction simulation | 5 | MiroFish | 🟡 P2 | L |
| F-029 | Anthropic Claude client | 5 | Internal Audit | 🟠 P1 | M |
| F-030 | Groq client | 5 | Internal Audit | 🟡 P2 | S |
| F-031 | Deal-specific task map at screening | 5 | Internal Audit | 🔴 P0 | M |
| F-032 | LangSmith @traceable observability | 6 | Local-DR | 🟡 P2 | S |
| F-033 | Subprocess isolation for report gen | 6 | MiroFish | 🟡 P2 | L |
| F-034 | strip_thinking in all clients | 6 | Local-DR | 🔴 P0 | XS |
| F-035 | OpenRouter unified client | 6 | Internal Audit | 🟡 P2 | M |

**Effort key:** XS = <30 min | S = 1–2 hrs | M = half day | L = 1–2 days

---

## Phase 1 — Critical Hotfixes

> **Goal:** Fix silent failures affecting every single agent run today.
> **Rule:** Implement ALL of Phase 1 before starting Phase 2.

---

### F-001 · Thinking Token Stripping
**Priority:** P0 · **Effort:** XS · **Source:** Local Deep Researcher + Internal Audit

#### Problem
When DeepSeek-R1, QwQ-32B, GLM5, or any reasoning model is used, the LLM output contains `<think>reasoning...</think>` blocks that contaminate `json.loads()` calls, causing `JSONDecodeError` across all agents. Currently only NVIDIA's GLM5 has thinking enabled but `clear_thinking: False` so the tags stay in the output and nothing strips them.

#### Files to Create
```
backend/app/core/llm/thinking_utils.py   ← NEW
```

#### Files to Modify
```
backend/app/core/llm/llm_gateway.py      ← call() response processing
backend/app/core/llm/local_llm_client.py ← OllamaClient.generate()
backend/app/core/llm/nvidia_client.py    ← NvidiaClient.generate()
backend/app/core/llm/gemini_client.py    ← GeminiClient.generate()
```

#### Implementation Spec

**`thinking_utils.py` — create this file:**
```python
"""Utility functions for handling thinking/reasoning model outputs."""

def strip_thinking_tokens(text: str, tag: str = "<think>") -> str:
    """
    Remove <think>...</think> blocks (and variants) from model output.
    Handles nested blocks and multiple occurrences.
    Supported tags: <think>, <thinking>, <reasoning>
    """
    if not text:
        return text
    close_tag = tag.replace("<", "</")
    result = text
    max_iter = 20  # safety cap for pathological inputs
    iterations = 0
    while tag in result and close_tag in result and iterations < max_iter:
        start = result.find(tag)
        end = result.find(close_tag)
        if end == -1:
            break
        end += len(close_tag)
        result = result[:start] + result[end:]
        iterations += 1
    return result.strip()

def extract_thinking(text: str, tag: str = "<think>") -> tuple[str, str]:
    """
    Returns (clean_output, thinking_content) as separate strings.
    Use this when you want to log/store the reasoning separately.
    """
    if not text:
        return text, ""
    close_tag = tag.replace("<", "</")
    thinking_parts = []
    result = text
    max_iter = 20
    iterations = 0
    while tag in result and close_tag in result and iterations < max_iter:
        start = result.find(tag)
        end_tag_start = result.find(close_tag)
        if end_tag_start == -1:
            break
        end = end_tag_start + len(close_tag)
        thinking_parts.append(result[start + len(tag):end_tag_start])
        result = result[:start] + result[end:]
        iterations += 1
    return result.strip(), "\n".join(thinking_parts)

def detect_thinking_tag(text: str) -> str | None:
    """Auto-detect which thinking tag style a model uses."""
    for tag in ["<think>", "<thinking>", "<reasoning>"]:
        if tag in text:
            return tag
    return None

def has_thinking_tokens(text: str) -> bool:
    return detect_thinking_tag(text) is not None
```

**In `llm_gateway.py` — add to `call()` after `content = result.get("content", "")`:**
```python
# Strip thinking tokens if present (DeepSeek-R1, QwQ, GLM5, etc.)
from app.core.llm.thinking_utils import strip_thinking_tokens, detect_thinking_tag
if content:
    detected_tag = detect_thinking_tag(content)
    if detected_tag:
        clean_content, thinking_content = extract_thinking(content, detected_tag)
        result["content"] = clean_content
        result["thinking_content"] = thinking_content   # preserve for logging
        result["thinking_stripped"] = True
        logger.debug("thinking_tokens_stripped",
                     provider=actual_provider,
                     tag=detected_tag,
                     thinking_length=len(thinking_content))
```

#### Acceptance Criteria
- [ ] `strip_thinking_tokens("<think>reasoning</think>\n{\"answer\": 42}")` returns `'{"answer": 42}'`
- [ ] Works for `<thinking>` and `<reasoning>` tags too
- [ ] Handles multiple `<think>` blocks in one response
- [ ] Preserves `thinking_content` in result dict (for debug logging)
- [ ] All existing agents continue to work (no regression)
- [ ] `json.loads()` succeeds on outputs from DeepSeek-R1 8B loaded in Ollama

---

### F-002 · `max_tokens` and `temperature` Passthrough
**Priority:** P0 · **Effort:** XS · **Source:** Internal Audit

#### Problem
`LLMGateway.call()` accepts `max_tokens=1024` and `temperature=0.7` as parameters but does NOT forward them to `client.generate()`. Every LLM call runs at each client's hardcoded defaults. Temperature-controlled generation (e.g., `temperature=0.0` for deterministic MECE trees) is silently ignored.

#### Files to Modify
```
backend/app/core/llm/llm_gateway.py      ← call() → do_call()
backend/app/core/llm/local_llm_client.py ← OllamaClient.generate(), LMStudioClient.generate()
backend/app/core/llm/gemini_client.py    ← GeminiClient.generate(), OpenAIClient.generate(), MistralClient.generate()
backend/app/core/llm/nvidia_client.py    ← NvidiaClient.generate()
```

#### Implementation Spec

**In `llm_gateway.py` — fix `do_call()` inside `call()`:**
```python
# BEFORE (broken):
async def do_call():
    return await client.generate(
        prompt=prompt,
        system_prompt=system_prompt,
        tools=tools,
        # max_tokens and temperature NOT PASSED
    )

# AFTER (fixed):
async def do_call():
    return await client.generate(
        prompt=prompt,
        system_prompt=system_prompt,
        tools=tools,
        temperature=temperature,    # ← ADD
        max_tokens=max_tokens,      # ← ADD
    )
```

**In each client's `generate()` method — use kwargs instead of hardcoded values:**

For `OllamaClient.generate()`:
```python
# BEFORE:
payload = {
    "model": self.model,
    "messages": messages,
    "stream": False,
    "options": {"temperature": kwargs.get("temperature", 0.7)},
}

# AFTER:
payload = {
    "model": self.model,
    "messages": messages,
    "stream": False,
    "options": {
        "temperature": kwargs.get("temperature", 0.7),
        "num_predict": kwargs.get("max_tokens", 4096),  # Ollama's max_tokens param
    },
}
```

For `LMStudioClient.generate()`:
```python
# BEFORE:
params = {
    "model": self.model,
    "messages": messages,
    "temperature": kwargs.get("temperature", 0.7),
    "max_tokens": kwargs.get("max_tokens", 12000),
}

# AFTER — same pattern but ensure kwargs are passed from generate() signature:
async def generate(self, prompt, system_prompt=None, tools=None, temperature=0.7, max_tokens=4096, **kwargs):
    params = {
        "model": self.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
```

**Apply same pattern to:** `GeminiClient`, `OpenAIClient`, `MistralClient`, `NvidiaClient`
Each client's `generate()` must accept `temperature` and `max_tokens` as explicit named parameters (not just `**kwargs`).

#### Acceptance Criteria
- [ ] `gateway.call(provider="ollama", prompt="...", temperature=0.0)` produces deterministic output
- [ ] `gateway.call(provider="gemini", prompt="...", max_tokens=512)` respects the 512 limit
- [ ] MECE tree generation at `temperature=0.0` is fully deterministic
- [ ] All 7 client classes accept `temperature` and `max_tokens` as explicit params
- [ ] No hardcoded `temperature=0.7` or `max_tokens=4000` remain in client code

---

### F-003 · Ollama Native JSON Mode
**Priority:** P0 · **Effort:** XS · **Source:** Local Deep Researcher

#### Problem
Ollama has a reliable native `format: "json"` parameter that forces valid JSON output from any model. DealForge uses 60+ lines of fragile regex + JSON repair to parse Ollama responses instead.

#### Files to Modify
```
backend/app/core/llm/local_llm_client.py ← OllamaClient.generate()
backend/app/agents/base.py               ← generate_with_tools() local model branch
```

#### Implementation Spec

**In `OllamaClient.generate()` — add `json_mode` parameter:**
```python
async def generate(
    self,
    prompt: str,
    system_prompt: Optional[str] = None,
    tools: Optional[List[Dict]] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    json_mode: bool = False,   # ← NEW
    **kwargs,
) -> Dict[str, Any]:
    payload = {
        "model": self.model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if json_mode:
        payload["format"] = "json"   # ← Ollama native JSON guarantee
    if tools:
        payload["tools"] = tools
```

**In `llm_gateway.py` — pass `json_mode` flag:**
```python
# In call(): detect when JSON mode should be used
# Use JSON mode when: no tools requested, temperature=0, and local model
use_json_mode = (
    tools is None
    and temperature == 0.0
    and actual_provider == "ollama"
)

async def do_call():
    return await client.generate(
        prompt=prompt,
        system_prompt=system_prompt,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=use_json_mode,   # ← NEW
    )
```

**In `base.py` `generate_with_tools()` — REMOVE the 60-line regex block for local models:**
```python
# DELETE everything from:
#   json_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
# to the end of the regex repair block (~lines 462-526)
#
# REPLACE WITH:
if is_local_model and tools:
    # For tool-calling: still use ReAct text format
    # (JSON mode only for non-tool generation)
    pass  # Tool calls handled by Ollama native tool_calls response
```

#### Acceptance Criteria
- [ ] Ollama responses with `json_mode=True` are always valid JSON (no regex needed)
- [ ] `json.loads(response["content"])` never raises `JSONDecodeError` for deterministic calls
- [ ] The 60-line regex + repair block is removed from `base.py`
- [ ] Tool calling still works (native Ollama tool_calls format)
- [ ] Regression test: existing agent outputs unchanged in structure

---

### F-004 · Context Window Enforcement + Prompt Truncation
**Priority:** P0 · **Effort:** S · **Source:** Internal Audit

#### Problem
Every client declares `max_context` (Ollama=8000, LMStudio=12000) but this is NEVER enforced. Large deal prompts (brief + system prompt + 5 chunks) routinely hit 15K–25K tokens, silently overflowing 8K models and producing garbage output.

#### Files to Create
```
backend/app/core/llm/context_guard.py    ← NEW
```

#### Files to Modify
```
backend/app/core/llm/llm_gateway.py      ← call() — add guard before client.generate()
```

#### Implementation Spec

**`context_guard.py` — create this file:**
```python
"""
Context window guard — truncates prompts that exceed model limits.
Uses middle-truncation: preserves start (instructions) + end (question/task).
"""
import structlog

logger = structlog.get_logger()

# Conservative chars-per-token ratio (English financial text)
CHARS_PER_TOKEN = 3.5

# Provider → default context window (tokens) if model unknown
PROVIDER_DEFAULTS = {
    "ollama":    8_192,
    "lmstudio":  12_288,
    "gemini":    1_048_576,
    "openai":    128_000,
    "mistral":   32_768,
    "nvidia":    16_384,
    "vertex":    1_048_576,
}

def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))

def enforce_context_limit(
    prompt: str,
    system_prompt: str | None,
    provider: str,
    context_window: int | None = None,
    reserve_output_tokens: int = 2048,
) -> tuple[str, str | None, bool]:
    """
    Truncates prompt/system_prompt if combined token estimate exceeds limit.

    Args:
        prompt: User prompt
        system_prompt: System instructions
        provider: LLM provider name (for default lookup)
        context_window: Override context window (from capability registry)
        reserve_output_tokens: Tokens to reserve for model output

    Returns:
        (prompt, system_prompt, was_truncated)
    """
    limit = context_window or PROVIDER_DEFAULTS.get(provider, 8192)
    usable = limit - reserve_output_tokens

    sys_tokens = estimate_tokens(system_prompt or "")
    prompt_tokens = estimate_tokens(prompt)
    total_tokens = sys_tokens + prompt_tokens

    if total_tokens <= usable:
        return prompt, system_prompt, False

    # Calculate how many tokens we need to trim
    excess_tokens = total_tokens - usable
    excess_chars = int(excess_tokens * CHARS_PER_TOKEN)

    logger.warning(
        "context_limit_exceeded",
        provider=provider,
        estimated_tokens=total_tokens,
        limit=usable,
        excess_tokens=excess_tokens,
    )

    # Middle-truncation on prompt (preserve first 60% + last 20%)
    if len(prompt) > excess_chars:
        keep_start = int(len(prompt) * 0.60)
        keep_end   = int(len(prompt) * 0.20)
        truncation_notice = "\n\n[...CONTEXT TRUNCATED TO FIT MODEL CONTEXT WINDOW...]\n\n"
        prompt = prompt[:keep_start] + truncation_notice + prompt[-keep_end:]

    return prompt, system_prompt, True
```

**In `llm_gateway.py` `call()` — add guard BEFORE `do_call()`:**
```python
from app.core.llm.context_guard import enforce_context_limit

# Get context window from capability registry (Phase 2, F-009)
# For now use provider default
prompt, system_prompt, was_truncated = enforce_context_limit(
    prompt=prompt,
    system_prompt=system_prompt,
    provider=actual_provider,
)
if was_truncated:
    logger.warning("prompt_was_truncated", provider=actual_provider)
```

#### Acceptance Criteria
- [ ] Prompts >8K tokens to Ollama are truncated to fit with a clear notice marker
- [ ] System prompt is preserved in full (only user prompt is truncated)
- [ ] `was_truncated` is logged as a warning
- [ ] Gemini/OpenAI with large context windows are NOT truncated unnecessarily
- [ ] Agent output quality is maintained (truncation targets middle of prompt)

---

### F-005 · LLM-Based Deal Name Extraction
**Priority:** P0 · **Effort:** XS · **Source:** Internal Audit

#### Problem
`_node_init()` in `graph.py` uses the first line of the user's brief as the deal name:
```python
first_line = brief.split("\n")[0].strip()
context["deal_name"] = first_line  # → "We've been approached regarding Skybound CRM..."
```
This produces unusable deal names in every report header and file name.

#### Files to Modify
```
backend/app/orchestrator/graph.py    ← _node_init()
```

#### Implementation Spec

**In `_node_init()` — replace heuristic with LLM extraction:**
```python
async def _node_init(self, state: DealState) -> DealState:
    """Initialize the workflow"""
    self.logger.info("Initializing workflow", deal_id=state["deal_id"])

    context = state.get("context", {})
    updates: Dict[str, Any] = {
        "current_stage": DealStage.INIT,
        "started_at": datetime.utcnow().isoformat(),
    }

    # Extract deal name using LLM — NOT first-line heuristic
    if not context.get("deal_name") and context.get("deal_brief"):
        brief = str(context["deal_brief"])[:1000]
        try:
            gateway = get_llm_gateway()
            response = await gateway.call(
                provider="gemini",   # Fast, cheap extraction
                prompt=(
                    f"Extract ONLY the company or deal name from this text. "
                    f"Return just the name, nothing else. No punctuation.\n\n"
                    f"Text: {brief}"
                ),
                system_prompt="You are a name extraction tool. Reply with only the company/deal name.",
                temperature=0.0,
                max_tokens=30,
            )
            deal_name = response.get("content", "").strip().strip('"').strip("'")
            # Validate: name should be short and not the full brief
            if deal_name and len(deal_name) < 100 and deal_name.lower() != brief[:len(deal_name)].lower():
                context = dict(context)
                context["deal_name"] = deal_name
                updates["context"] = context
                updates["deal_name"] = deal_name
                self.logger.info("deal_name_extracted_by_llm", deal_name=deal_name)
            else:
                raise ValueError(f"Extraction returned implausible name: {deal_name}")
        except Exception as e:
            self.logger.warning("deal_name_llm_extraction_failed", error=str(e))
            # Fallback: take first meaningful word cluster
            words = brief.split()[:5]
            fallback_name = " ".join(w for w in words if len(w) > 3)[:60]
            context = dict(context)
            context["deal_name"] = fallback_name
            updates["context"] = context
            updates["deal_name"] = fallback_name

    return update_state(state, updates)
```

#### Acceptance Criteria
- [ ] Input: `"We've been approached regarding Skybound CRM acquisition..."` → Output: `"Skybound CRM"`
- [ ] Input: `"TechCorp wants to acquire Vantage Software for $850M"` → Output: `"Vantage Software"`
- [ ] Deal name appears correctly in report headers and file names
- [ ] Fallback works if LLM call fails
- [ ] LLM extraction uses `temperature=0.0` (deterministic)

---

### F-006 · Fix ComplexReasoningAgent Context Starvation
**Priority:** P0 · **Effort:** XS · **Source:** Internal Audit

#### Problem
`_node_complex_reasoning()` passes ONLY `curated_data` to the agent:
```python
ctx = {"curated_data": state["curated_data"]}   # ← Only this
```
The agent doing the deepest reasoning has no access to: `debate_output`, `red_team_flags`, `consistency_warnings`, `advanced_financial_output`, `scoring_output`, `halugate_results`.

#### Files to Modify
```
backend/app/orchestrator/graph.py    ← _node_complex_reasoning()
```

#### Implementation Spec
```python
async def _node_complex_reasoning(self, state: DealState) -> DealState:
    """Run Complex Reasoning Agent with FULL context"""
    self.logger.info("Running Complex Reasoning", deal_id=state["deal_id"])

    agent = self.agent_registry.get("complex_reasoning")
    if not agent:
        return state

    try:
        # Build comprehensive context — reasoning needs everything
        ctx = {
            # Primary data
            "curated_data":              state.get("curated_data", {}),
            "fact_base":                 state.get("fact_base", {}),

            # Agent outputs
            "financial_output":          state.get("financial_output", {}),
            "legal_output":              state.get("legal_output", {}),
            "risk_output":               state.get("risk_output", {}),
            "market_output":             state.get("market_output", {}),
            "advanced_financial_output": state.get("advanced_financial_output", {}),

            # Synthesis outputs
            "debate_output":             state.get("debate_output", {}),
            "red_team_flags":            state.get("red_team_flags", []),
            "consistency_warnings":      state.get("consistency_warnings", []),

            # Deal metadata
            "deal_name":                 state.get("deal_name", ""),
            "deal_stage":                state.get("deal_stage", "deep_dive"),
            "buyer_thesis":              state.get("context", {}).get("buyer_thesis"),
            "deal_goal":                 state.get("context", {}).get("deal_goal"),
            "industry":                  state.get("context", {}).get("industry"),
            "deal_id":                   state["deal_id"],
        }

        result = await agent.run(
            f"Execute comprehensive Chain-of-Thought reasoning for {state.get('deal_name', 'deal')}. "
            f"Synthesize all agent findings, resolve conflicts from debate, and address red team flags.",
            context=ctx,
        )
        if result.success:
            state = update_state(state, {"reasoning_trace": result.data})
    except Exception as e:
        self.logger.error("Complex Reasoning failed", error=str(e))

    return state
```

#### Acceptance Criteria
- [ ] ComplexReasoningAgent receives all agent outputs + debate + red team data
- [ ] Agent's reasoning trace references debate consensus points
- [ ] Agent's reasoning trace references red team flags
- [ ] Report quality improves — reasoning section is no longer generic

---

### F-007 · Fix ReportArchitectAgent Context
**Priority:** P0 · **Effort:** XS · **Source:** Internal Audit

#### Problem
`_node_report_architect()` passes only `state.get("context", {})` — raw user input. The agent designing the report blueprint has no access to actual analysis outputs.

#### Files to Modify
```
backend/app/orchestrator/graph.py    ← _node_report_architect()
```

#### Implementation Spec
```python
async def _node_report_architect(self, state: DealState) -> DealState:
    """Run Report Architect Agent with full analysis outputs"""
    self.logger.info("Running Report Architect", deal_id=state["deal_id"])

    agent = self.agent_registry.get("report_architect")
    if not agent:
        return state

    try:
        # Build blueprint context from all completed analysis
        ctx = state.get("context", {}).copy()
        ctx.update({
            "deal_name":          state.get("deal_name", ""),
            "deal_stage":         state.get("deal_stage", "deep_dive"),

            # All analysis outputs for blueprint planning
            "financial_summary":  _safe_summary(state.get("financial_output")),
            "legal_summary":      _safe_summary(state.get("legal_output")),
            "risk_summary":       _safe_summary(state.get("risk_output")),
            "market_summary":     _safe_summary(state.get("market_output")),

            # Quality signals — blueprint should highlight flagged areas
            "debate_consensus":   state.get("debate_output", {}).get("consensus_points", []),
            "debate_conflicts":   state.get("debate_output", {}).get("conflicts", []),
            "red_team_flags":     state.get("red_team_flags", []),
            "consistency_warnings": state.get("consistency_warnings", []),
            "halugate_blocked":   state.get("context", {}).get("halugate_results", {}).get("blocked", False),

            # Scoring
            "final_score":        state.get("final_score"),
            "scoring_breakdown":  state.get("scoring_output", {}).get("scoring_breakdown"),
        })

        result = await agent.run(
            f"Configure report blueprint for {state.get('deal_name', 'deal')} analysis",
            context=ctx,
        )
        if result.success:
            state = update_state(state, {"report_blueprint": result.data})
    except Exception as e:
        self.logger.error("Report Architect failed", error=str(e))

    return state

def _safe_summary(output: dict | None) -> dict:
    """Extract top-level summary fields from agent output (avoid huge nested dicts)."""
    if not output:
        return {}
    # Return top 5 keys only — blueprint doesn't need raw details
    return {k: v for i, (k, v) in enumerate(output.items()) if i < 5}
```

#### Acceptance Criteria
- [ ] Blueprint includes sections for areas flagged by red team
- [ ] Blueprint prioritizes unresolved debate conflicts
- [ ] Blueprint reflects deal stage (screening = 1-pager, ic_memo = full pyramid)
- [ ] `halugate_blocked=True` triggers a "Data Reliability Warning" section in blueprint

---

### F-008 · Loop-Back Convergence Guard
**Priority:** P1 · **Effort:** XS · **Source:** Internal Audit

#### Problem
Both `_should_continue_after_debate()` and `_should_continue_after_red_team()` can loop back to `parallel_analysis`. There is no counter — the system could theoretically loop indefinitely if the debate engine keeps flagging revisions.

#### Files to Modify
```
backend/app/orchestrator/state.py    ← DealState TypedDict — add loop_count
backend/app/orchestrator/graph.py    ← _should_continue_after_debate(), _should_continue_after_red_team()
```

#### Implementation Spec

**In `state.py` — add `loop_count` to `DealState`:**
```python
class DealState(TypedDict, total=False):
    # ... existing fields ...
    loop_count: int          # ← ADD: tracks number of loop-backs
    revision_targets: List[str]  # ← ADD: which agents need revision (F-015)
```

**In `create_initial_state()` — initialize:**
```python
"loop_count": 0,
"revision_targets": [],
```

**In `graph.py` — update both conditional edge functions:**
```python
def _should_continue_after_debate(self, state: DealState) -> str:
    debate_output = state.get("debate_output", {})
    loop_count = state.get("loop_count", 0)

    # Hard cap: max 2 loop-backs total
    if loop_count >= 2:
        self.logger.warning("debate_loop_cap_reached", loop_count=loop_count)
        return "red_team"

    if debate_output.get("requires_revision", False):
        # Extract which agents need revision (for F-015)
        revision_requests = debate_output.get("revision_requests", [])
        revision_targets = [req["agent"] for req in revision_requests if "agent" in req]
        # Will be set by the node function before returning
        return "loop_back"

    return "red_team"

# Same pattern for _should_continue_after_red_team()
def _should_continue_after_red_team(self, state: DealState) -> str:
    red_team_output = state.get("red_team_output", {})
    loop_count = state.get("loop_count", 0)

    if loop_count >= 2:
        self.logger.warning("red_team_loop_cap_reached", loop_count=loop_count)
        return "scoring"

    max_severity = red_team_output.get("max_severity", 0)
    if max_severity >= 9:   # Only loop back for critical-severity flags
        return "loop_back"

    return "scoring"
```

**In `_node_debate()` and `_node_red_team()` — increment counter on loop-back:**
```python
# At the start of _node_parallel_analysis():
loop_count = state.get("loop_count", 0)
if state.get("revision_targets"):  # We're in a loop-back pass
    state = update_state(state, {"loop_count": loop_count + 1})
```

#### Acceptance Criteria
- [ ] Maximum 2 loop-backs total before forcing forward progression
- [ ] `loop_count` is incremented on each loop-back
- [ ] At `loop_count >= 2`, workflow always proceeds to next stage
- [ ] Warning logged when cap is reached
- [ ] No infinite loops possible

---

## Phase 2 — Research Quality

> **Goal:** Transform agent research from single-shot retrieval to multi-round gap-aware investigation.

---

### F-009 · Model Capability Registry
**Priority:** P0 · **Effort:** M · **Source:** Internal Audit

#### Problem
System has zero structured knowledge of what individual models support (tool calling, thinking tokens, JSON mode, context window). Incapable models receive tool call payloads and fail silently.

#### Files to Create
```
backend/app/core/llm/model_registry.py   ← NEW
```

#### Files to Modify
```
backend/app/core/llm/llm_gateway.py      ← call() — capability-aware routing
backend/app/core/llm/local_llm_client.py ← use registry for context window
```

#### Implementation Spec

**`model_registry.py` — full implementation:**
```python
"""
Model Capability Registry — Static knowledge of LLM capabilities.
Used by LLMGateway to auto-adapt requests (tool format, JSON mode, context limits).
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelCapabilities:
    """Capabilities of a specific LLM model."""
    tool_calling: bool = False           # Native function/tool calling support
    json_mode: bool = False              # Reliable JSON output mode
    thinking: bool = False               # Explicit chain-of-thought tokens
    think_tag: Optional[str] = None      # Tag used: "<think>", "<thinking>", etc.
    vision: bool = False                 # Multimodal image input
    context_window: int = 8_192          # Max input + output tokens
    max_output_tokens: int = 4_096       # Max generation length
    supports_system_prompt: bool = True  # Some models (o1) don't support system
    tier: str = "general"                # "fast", "general", "reasoning", "embedding"
    streaming: bool = True               # Supports streaming output


# ── Ollama local models ─────────────────────────────────────────────────────
OLLAMA_REGISTRY: dict[str, ModelCapabilities] = {
    "llama3":             ModelCapabilities(tool_calling=True,  json_mode=True, context_window=8_192,   tier="general"),
    "llama3.1":           ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="general"),
    "llama3.1:8b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="fast"),
    "llama3.1:70b":       ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="general"),
    "llama3.2":           ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="fast"),
    "llama3.2:3b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=131_072, tier="fast"),
    "qwen2.5":            ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5:7b":         ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="fast"),
    "qwen2.5:14b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5:32b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5:72b":        ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5-coder":      ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "qwen2.5-coder:32b":  ModelCapabilities(tool_calling=True,  json_mode=True, context_window=32_768,  tier="general"),
    "deepseek-r1":        ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:7b":     ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:8b":     ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:14b":    ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:32b":    ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-r1:70b":    ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=65_536,  tier="reasoning"),
    "deepseek-v3":        ModelCapabilities(tool_calling=True,  json_mode=True,  thinking=False, context_window=65_536, tier="general"),
    "qwq":                ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=32_768,  tier="reasoning"),
    "qwq:32b":            ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=32_768,  tier="reasoning"),
    "phi4":               ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=16_384, tier="general"),
    "phi4:14b":           ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=16_384, tier="general"),
    "mistral":            ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, tier="general"),
    "mistral:7b":         ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, tier="fast"),
    "mixtral":            ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, tier="general"),
    "mixtral:8x7b":       ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, tier="general"),
    "gemma2":             ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="fast"),
    "gemma2:9b":          ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="fast"),
    "gemma2:27b":         ModelCapabilities(tool_calling=False, json_mode=True,  context_window=8_192,  tier="general"),
    "gemma3":             ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000,tier="general"),
    "gemma3:27b":         ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000,tier="general"),
    "phi3":               ModelCapabilities(tool_calling=False, json_mode=True,  context_window=4_096,  tier="fast"),
    "phi3.5":             ModelCapabilities(tool_calling=False, json_mode=True,  context_window=128_000,tier="fast"),
    "llava":              ModelCapabilities(tool_calling=False, json_mode=False, vision=True,  context_window=4_096,  tier="fast"),
    "llava:13b":          ModelCapabilities(tool_calling=False, json_mode=False, vision=True,  context_window=4_096,  tier="general"),
    "nomic-embed-text":   ModelCapabilities(tool_calling=False, json_mode=False, context_window=8_192,  tier="embedding"),
}

# ── Cloud models ─────────────────────────────────────────────────────────────
CLOUD_REGISTRY: dict[str, ModelCapabilities] = {
    # Google Gemini
    "gemini-1.5-flash":           ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=1_048_576, max_output_tokens=8_192,  tier="fast"),
    "gemini-1.5-pro":             ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=1_048_576, max_output_tokens=8_192,  tier="general"),
    "gemini-2.0-flash":           ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=1_048_576, max_output_tokens=8_192,  tier="fast"),
    "gemini-2.0-flash-thinking":  ModelCapabilities(tool_calling=False, json_mode=True,  thinking=True, think_tag="<think>", context_window=32_000, tier="reasoning"),
    "gemini-2.5-pro":             ModelCapabilities(tool_calling=True,  json_mode=True,  thinking=True, context_window=1_048_576, max_output_tokens=16_384, tier="reasoning"),

    # OpenAI
    "gpt-4o":                     ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000, max_output_tokens=16_384, tier="general"),
    "gpt-4o-mini":                ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000, max_output_tokens=16_384, tier="fast"),
    "o1":                         ModelCapabilities(tool_calling=False, json_mode=False, thinking=True, supports_system_prompt=False, context_window=200_000, tier="reasoning"),
    "o1-mini":                    ModelCapabilities(tool_calling=False, json_mode=False, thinking=True, supports_system_prompt=False, context_window=128_000, tier="reasoning"),
    "o3":                         ModelCapabilities(tool_calling=True,  json_mode=True,  thinking=True, context_window=200_000, max_output_tokens=100_000, tier="reasoning"),
    "o3-mini":                    ModelCapabilities(tool_calling=True,  json_mode=True,  thinking=True, context_window=200_000, tier="reasoning"),
    "gpt-4-turbo":                ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=128_000, tier="general"),

    # Anthropic Claude
    "claude-3-5-sonnet":          ModelCapabilities(tool_calling=True,  json_mode=False, context_window=200_000, max_output_tokens=8_192, tier="general"),
    "claude-3-5-haiku":           ModelCapabilities(tool_calling=True,  json_mode=False, context_window=200_000, max_output_tokens=8_192, tier="fast"),
    "claude-3-7-sonnet":          ModelCapabilities(tool_calling=True,  json_mode=False, thinking=True, think_tag="<thinking>", context_window=200_000, max_output_tokens=16_000, tier="reasoning"),
    "claude-opus-4":              ModelCapabilities(tool_calling=True,  json_mode=False, thinking=True, context_window=200_000, max_output_tokens=32_000, tier="reasoning"),

    # Mistral
    "mistral-large":              ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, max_output_tokens=8_192, tier="general"),
    "mistral-medium":             ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, max_output_tokens=8_192, tier="general"),
    "mistral-small":              ModelCapabilities(tool_calling=True,  json_mode=True,  context_window=32_768, max_output_tokens=8_192, tier="fast"),
}

MODEL_REGISTRY: dict[str, ModelCapabilities] = {**OLLAMA_REGISTRY, **CLOUD_REGISTRY}

def get_capabilities(model: str, provider: str = "") -> ModelCapabilities:
    """Look up model capabilities. Returns safe defaults if unknown."""
    caps = MODEL_REGISTRY.get(model)
    if caps:
        return caps
    # Provider-based fallbacks
    if provider in ("gemini", "vertex"):
        return ModelCapabilities(tool_calling=True, json_mode=True, context_window=1_048_576, tier="general")
    if provider == "openai":
        return ModelCapabilities(tool_calling=True, json_mode=True, context_window=128_000, tier="general")
    if provider in ("claude", "anthropic"):
        return ModelCapabilities(tool_calling=True, json_mode=False, context_window=200_000, tier="general")
    if provider == "mistral":
        return ModelCapabilities(tool_calling=True, json_mode=True, context_window=32_768, tier="general")
    return ModelCapabilities()  # Safe defaults
```

**In `llm_gateway.py` — use registry in `call()`:**
```python
from app.core.llm.model_registry import get_capabilities

async def call(self, provider: str, model: str, prompt: str, ...):
    actual_provider = self._resolve_provider(provider)
    caps = get_capabilities(model, actual_provider)

    # Route by capability:
    if tools and not caps.tool_calling:
        self.logger.warning("model_lacks_tool_support", model=model)
        # Fall back to text-based tool format or skip tool calling

    if tools and caps.think_tag:
        # Can use reasoning before tools
        pass

    # Enforce context limit using capability
    prompt, system_prompt, was_truncated = enforce_context_limit(
        prompt=prompt,
        system_prompt=system_prompt,
        provider=actual_provider,
        context_window=caps.context_window,
    )
```

#### Acceptance Criteria
- [ ] `get_capabilities("llama3.1:70b", "ollama")` returns correct tool_calling, json_mode, context_window
- [ ] `get_capabilities("unknown-model", "openai")` returns OpenAI defaults (not Ollama defaults)
- [ ] LLMGateway routes tool-calling requests away from models that don't support it (like Gemma2)
- [ ] No `JSONDecodeError` from models forced into JSON mode that don't support it
- [ ] `context_window` is used by context_guard to enforce limits

---

### F-010 · Pre-Flight Capability Probe
**Priority:** P1 · **Effort:** M · **Source:** Internal Audit

#### Problem
DealForge initializes without checking if the configured LLMs are actually available and capable. A misconfigured Ollama host, missing API key, or incompatible model leads to silent failures halfway through a deal run.

#### Files to Create
```
backend/app/core/llm/capability_probe.py   ← NEW
```

#### Files to Modify
```
backend/app/main.py                       ← FastAPI startup
backend/app/orchestrator/graph.py         ← Optional: log probe results
```

#### Implementation Spec

**`capability_probe.py` — full implementation:**
```python
"""Pre-flight validation of LLM capabilities before deal workflows start."""
import asyncio
import structlog
from app.core.llm.llm_gateway import get_llm_gateway
from app.core.llm.model_registry import get_capabilities

logger = structlog.get_logger()

async def probe_llm_health(
    provider: str,
    model: str,
    timeout_sec: int = 5,
) -> dict:
    """
    Test if an LLM endpoint is reachable and can handle a basic request.
    Returns: {"healthy": bool, "latency_ms": float, "error": str | None, "capabilities": ModelCapabilities}
    """
    gateway = get_llm_gateway()

    try:
        import time
        start = time.time()

        response = await asyncio.wait_for(
            gateway.call(
                provider=provider,
                model=model,
                prompt="Respond with: OK",
                temperature=0.0,
                max_tokens=10,
            ),
            timeout=timeout_sec,
        )

        latency_ms = (time.time() - start) * 1000
        caps = get_capabilities(model, provider)

        return {
            "healthy": True,
            "latency_ms": round(latency_ms, 2),
            "error": None,
            "capabilities": caps.__dict__,
        }
    except asyncio.TimeoutError:
        return {"healthy": False, "latency_ms": None, "error": f"Timeout after {timeout_sec}s"}
    except Exception as e:
        return {"healthy": False, "latency_ms": None, "error": str(e)}

async def probe_fallback_chain() -> dict:
    """Test the full LLM fallback chain (Gemini → Mistral → OpenAI → Ollama)."""
    chain = [
        ("gemini", "gemini-1.5-flash"),
        ("mistral", "mistral-small"),
        ("openai", "gpt-4o-mini"),
        ("ollama", "llama3.1"),
    ]

    results = {}
    for provider, model in chain:
        results[f"{provider}:{model}"] = await probe_llm_health(provider, model)

    # Find first healthy provider
    healthy = [k for k, v in results.items() if v["healthy"]]
    if healthy:
        logger.info("llm_fallback_chain_healthy", first_healthy=healthy[0])
    else:
        logger.error("llm_fallback_chain_all_down", results=results)

    return results
```

**In `main.py` — add startup probe:**
```python
@app.on_event("startup")
async def startup_checks():
    """Run pre-flight checks before accepting requests."""
    from app.core.llm.capability_probe import probe_fallback_chain

    logger.info("Running pre-flight LLM capability probe...")
    results = await probe_fallback_chain()

    healthy_count = sum(1 for r in results.values() if r["healthy"])
    if healthy_count == 0:
        logger.error("CRITICAL: No LLM providers are available. Startup aborted.")
        raise RuntimeError("No LLM providers available")
    else:
        logger.info(f"LLM health check passed. {healthy_count}/{len(results)} providers healthy.")
```

#### Acceptance Criteria
- [ ] Startup logs which LLMs are reachable and latency for each
- [ ] If ALL LLMs are unreachable, startup fails with clear error message
- [ ] If primary (Gemini) is down but fallback (Ollama) is up, startup succeeds with warning
- [ ] Probe completes within 30 seconds total (5s timeout × 6 models)
- [ ] Latency data is logged for monitoring

---

### F-011 · Per-Source Token Budgeting
**Priority:** P1 · **Effort:** S · **Source:** Local Deep Researcher

#### Problem
When retrieving documents from multiple sources (SEC filings, Crunchbase, financial databases), DealForge has no way to limit tokens-per-source. A single source's retrieval can consume 80% of the context window, starving other sources of representation.

#### Files to Modify
```
backend/app/core/document_store.py   ← add token budget tracking
backend/app/orchestrator/graph.py    ← _node_retrieval() — enforce per-source limits
```

#### Implementation Spec

**In `document_store.py` — add token budget class:**
```python
from dataclasses import dataclass

@dataclass
class TokenBudget:
    """Allocates tokens across document sources."""
    total_tokens: int
    source_budgets: dict[str, int]  # source_name → allocated tokens

    def __init__(self, total: int, sources: list[str]):
        self.total_tokens = total
        # Equal distribution across sources
        per_source = total // len(sources)
        self.source_budgets = {src: per_source for src in sources}

    def has_budget(self, source: str) -> bool:
        return self.source_budgets.get(source, 0) > 0

    def deduct(self, source: str, tokens: int) -> bool:
        """Returns True if deducted successfully, False if exceeds budget."""
        if self.source_budgets.get(source, 0) >= tokens:
            self.source_budgets[source] -= tokens
            return True
        return False
```

**In `graph.py` `_node_retrieval()` — enforce budgets:**
```python
from app.core.document_store import TokenBudget
from app.core.llm.thinking_utils import estimate_tokens

async def _node_retrieval(self, state: DealState) -> DealState:
    """Retrieve documents with per-source token limits"""
    brief = state.get("deal_brief", "")
    sources = ["sec_filings", "crunchbase", "bloomberg", "custom_docs"]

    # Budget: 8K tokens for retrieval phase
    budget = TokenBudget(total=8000, sources=sources)

    curated_data = {}
    for source in sources:
        if not budget.has_budget(source):
            self.logger.warning(f"Token budget exhausted for {source}")
            continue

        docs = await self.doc_store.retrieve(brief, source=source)

        tokens_used = sum(estimate_tokens(doc.content) for doc in docs)
        if budget.deduct(source, tokens_used):
            curated_data[source] = [d.dict() for d in docs]
        else:
            # Truncate to fit budget
            kept_docs = []
            for doc in docs:
                doc_tokens = estimate_tokens(doc.content)
                if budget.deduct(source, doc_tokens):
                    kept_docs.append(doc.dict())
            curated_data[source] = kept_docs
            self.logger.info(f"Truncated {source} to fit token budget")

    return update_state(state, {"curated_data": curated_data})
```

#### Acceptance Criteria
- [ ] Per-source token budget is enforced (no source exceeds allocation)
- [ ] When a source is exhausted, retrieval stops for that source (not skipped globally)
- [ ] Budget allocation is logged with actual tokens used per source
- [ ] Total tokens across all sources stays within limit

---

### F-012 · IterDRAG: Multi-Turn Research Loops per MECE Branch
**Priority:** P0 · **Effort:** M · **Source:** Local Deep Researcher

#### Problem
Each agent runs once and returns. If a financial analyst needs data on "supply chain risk," it must guess all relevant questions upfront. No follow-up, no refinement. Real researchers iterate: initial findings → identify gaps → targeted re-retrieval → synthesis.

#### Files to Modify
```
backend/app/agents/base.py         ← BaseAgent.run() — add loop capability
backend/app/agents/financial_analyst.py  ← implement research_loop pattern
backend/app/orchestrator/graph.py  ← _node_parallel_analysis() — enable iterative agents
```

#### Implementation Spec

**In `base.py` — add research loop pattern:**
```python
class BaseAgent:
    async def run_iterative(
        self,
        initial_task: str,
        context: dict,
        max_iterations: int = 3,
        gap_detection_prompt: str = None,
    ) -> AgentResult:
        """
        Iterative research: task → findings → gap detection → re-retrieval → synthesis
        """
        findings = []
        for iteration in range(max_iterations):
            self.logger.info(f"Iteration {iteration + 1}/{max_iterations}")

            # Run task
            result = await self.run(initial_task, context)
            if not result.success:
                break

            findings.append(result.data)

            # Detect gaps if not final iteration
            if iteration < max_iterations - 1:
                gaps = await self._detect_research_gaps(
                    task=initial_task,
                    findings=result.data,
                    prompt=gap_detection_prompt,
                )
                if not gaps:
                    self.logger.info("No gaps detected, stopping iteration")
                    break

                # Re-query document store with gaps
                context["retrieval_hint"] = gaps
                self.logger.info(f"Gaps detected: {gaps}")

        return AgentResult(
            success=True,
            data={
                "iterations": len(findings),
                "findings": findings,
                "final_synthesis": findings[-1] if findings else {},
            }
        )

    async def _detect_research_gaps(self, task, findings, prompt=None):
        """Use LLM to identify unanswered questions in findings."""
        gap_prompt = prompt or (
            f"Task: {task}\n\n"
            f"Current findings:\n{json.dumps(findings, indent=2)}\n\n"
            f"What key questions remain unanswered? List as JSON array."
        )

        response = await self.gateway.call(
            provider="gemini",
            prompt=gap_prompt,
            temperature=0.0,
            max_tokens=256,
        )

        try:
            gaps = json.loads(response.get("content", "[]"))
            return gaps[:3]  # Top 3 gaps
        except:
            return []
```

**Usage in agent:**
```python
# In FinancialAnalystAgent.run():
result = await self.run_iterative(
    initial_task="Analyze supply chain risk",
    context=context,
    max_iterations=3,
    gap_detection_prompt="What supply chain risks remain unexplored?",
)
```

#### Acceptance Criteria
- [ ] IterDRAG completes within 2–3 iterations for supply chain deep-dives
- [ ] Gap detection prompt correctly identifies missing angles
- [ ] Final synthesis consolidates findings from all iterations
- [ ] Loop stops early if gaps are empty (e.g., after iteration 1 for simple topics)
- [ ] Token usage is logged per iteration

---

### F-013 · Running Summary Pattern
**Priority:** P1 · **Effort:** S · **Source:** Local Deep Researcher

#### Problem
As research loops progress, the context dict grows unbounded. A 3-iteration research loop on 5 agents produces enormous nested findings dicts, causing context overflow and poor reasoning quality.

#### Implementation Spec

**In `base.py` — add running summary:**
```python
async def _maintain_running_summary(self, iteration: int, findings: dict) -> str:
    """
    Compress prior findings into a concise summary.
    Stored in state["running_summary"] for subsequent iterations.
    """
    summary_prompt = (
        f"Iteration {iteration} findings:\n"
        f"{json.dumps(findings, indent=2)}\n\n"
        f"Summarize in 2-3 sentences: key facts, conclusions, open questions."
    )

    response = await self.gateway.call(
        provider="gemini",
        prompt=summary_prompt,
        temperature=0.0,
        max_tokens=150,
    )

    return response.get("content", "").strip()
```

**Usage:**
```python
for iteration in range(max_iterations):
    result = await self.run(task, context)
    summary = await self._maintain_running_summary(iteration, result.data)
    context["running_summary"] = summary  # ← Replace bulky prior iteration
    # Now context is lean; prior findings replaced by compressed summary
```

#### Acceptance Criteria
- [ ] Running summary is ≤200 tokens per iteration
- [ ] Key facts from all prior iterations are preserved in summary
- [ ] Context dict size is bounded (no quadratic growth)

---

### F-014 · Source Registry in DealState
**Priority:** P1 · **Effort:** S · **Source:** Local Deep Researcher

#### Problem
Agents retrieve documents but don't track where they came from, making audit trails impossible. Report readers can't verify findings.

#### Files to Modify
```
backend/app/orchestrator/state.py    ← DealState TypedDict
backend/app/agents/base.py           ← BaseAgent.run() — record sources
```

#### Implementation Spec

**In `state.py` — add source registry:**
```python
class DealState(TypedDict, total=False):
    # ... existing fields ...
    source_registry: dict[str, list[dict]]  # source_name → [{doc_id, url, retrieved_at, agent}]
```

**In `base.py` — record source access:**
```python
async def run(self, task: str, context: dict):
    """Run task and record all document sources accessed."""
    result = await self._execute_task(task, context)

    # Record which sources were used
    if "curated_data" in context:
        for source_name, docs in context["curated_data"].items():
            if source_name not in state.get("source_registry", {}):
                state["source_registry"][source_name] = []

            for doc in docs:
                state["source_registry"][source_name].append({
                    "doc_id": doc.get("id"),
                    "url": doc.get("url"),
                    "retrieved_at": datetime.utcnow().isoformat(),
                    "agent": self.__class__.__name__,
                })

    return result
```

#### Acceptance Criteria
- [ ] Report footer includes "Sources: [SEC filings, Crunchbase, Bloomberg]"
- [ ] Each claim in the report is traceable to a specific document
- [ ] Source URLs are clickable in PDF/PPTX output

---

### F-015 · Smart Targeted Loop-Back (Debate + Red Team)
**Priority:** P1 · **Effort:** S · **Source:** Internal Audit

#### Problem
When debate flags "Financial Analyst output is inconsistent," the system loops back and re-runs ALL 4 parallel agents. Only the Financial Analyst needed revision.

#### Files to Modify
```
backend/app/orchestrator/graph.py    ← _node_loop_back() — smart targeting
backend/app/orchestrator/state.py    ← revision_targets field
```

#### Implementation Spec

**In `graph.py` `_node_loop_back()`:**
```python
async def _node_loop_back(self, state: DealState) -> DealState:
    """Re-run only agents flagged for revision."""
    revision_targets = state.get("revision_targets", [])

    if not revision_targets:
        # Fallback: re-run all if not specified
        revision_targets = ["financial", "legal", "risk", "market"]

    self.logger.info(f"Loop-back targeting: {revision_targets}")

    # Selectively run flagged agents
    for agent_name in revision_targets:
        if agent_name in self.agent_registry:
            agent = self.agent_registry.get(agent_name)
            result = await agent.run(
                f"Revise analysis for {state.get('deal_name')}",
                context=state.get("context", {}),
            )
            if result.success:
                state = update_state(state, {
                    f"{agent_name}_output": result.data
                })

    return state
```

**Extraction in debate node:**
```python
# In _node_debate() or debate engine
if debate_output.get("requires_revision"):
    targets = [
        req["agent"] for req in debate_output.get("revision_requests", [])
    ]
    state = update_state(state, {"revision_targets": targets})
```

#### Acceptance Criteria
- [ ] Only revised agents are re-run (not all 4)
- [ ] Loop-back completes faster (1 agent vs 4)
- [ ] Report quality improves (targeted fixes vs generic re-run)

---

### F-016 · MessageBus Post-Parallel Sync
**Priority:** P1 · **Effort:** S · **Source:** Internal Audit

#### Problem
4 agents run in parallel. After all complete, there's no synchronization point for cross-agent communication. Agent A might have findings relevant to Agent B's output but no way to share.

#### Files to Create
```
backend/app/orchestrator/message_bus.py   ← NEW
```

#### Files to Modify
```
backend/app/orchestrator/graph.py    ← _node_parallel_analysis() — add sync point
```

#### Implementation Spec

**`message_bus.py` — publish-subscribe pattern:**
```python
from typing import Callable
import asyncio

class MessageBus:
    """Cross-agent communication bus for post-parallel synthesis."""

    def __init__(self):
        self.subscribers: dict[str, list[Callable]] = {}

    def subscribe(self, topic: str, handler: Callable):
        """Register a handler for a topic."""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        self.subscribers[topic].append(handler)

    async def publish(self, topic: str, message: dict):
        """Broadcast message to all subscribers."""
        if topic not in self.subscribers:
            return

        tasks = [h(message) for h in self.subscribers[topic]]
        await asyncio.gather(*tasks, return_exceptions=True)

# Example topics:
# "financial:supply_chain_risk" → risk agent subscribes
# "market:competitor_count" → financial agent subscribes
```

**Usage in `graph.py`:**
```python
async def _node_parallel_analysis(self, state: DealState) -> DealState:
    """Run 4 agents in parallel, then sync via message bus."""
    bus = MessageBus()

    # Register subscribers
    risk_agent.subscribe_to_bus(bus, "market:findings", "legal:findings")
    financial_agent.subscribe_to_bus(bus, "market:findings")

    # Run agents in parallel
    results = await asyncio.gather(
        self.agent_registry["financial"].run(...),
        self.agent_registry["legal"].run(...),
        self.agent_registry["risk"].run(...),
        self.agent_registry["market"].run(...),
    )

    # Sync phase: publish findings
    for i, agent_name in enumerate(["financial", "legal", "risk", "market"]):
        await bus.publish(f"{agent_name}:findings", results[i].data)

    return state
```

#### Acceptance Criteria
- [ ] Agents can discover cross-agent findings post-parallel
- [ ] Risk agent can reference market findings in its output
- [ ] No synchronous blocking (all parallel, then async publish)

---

## Phase 3 — Live Intelligence

> **Goal:** Connect DealForge to real-time market data, web search, and dynamic task generation.

---

### F-017 · Live Web Search Tool
**Priority:** P0 · **Effort:** M · **Source:** Local Deep Researcher

#### Problem
Agents analyze static documents. No access to latest news, earnings calls, analyst reports published today. A deal analysis from 6 months ago is stale.

#### Files to Create
```
backend/app/core/tools/web_search.py   ← NEW
```

#### Implementation Spec

```python
"""Live web search integration (SerpAPI / Google Custom Search)."""
import aiohttp
from typing import list

class WebSearchTool:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://serpapi.com/search"  # or Google CSE

    async def search(self, query: str, num_results: int = 5) -> list[dict]:
        """
        Search the web and return top results with snippets.
        """
        params = {
            "q": query,
            "api_key": self.api_key,
            "num": num_results,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(self.base_url, params=params) as resp:
                data = await resp.json()

                return [
                    {
                        "title": r.get("title"),
                        "url": r.get("link"),
                        "snippet": r.get("snippet"),
                        "published": r.get("date"),
                    }
                    for r in data.get("organic_results", [])
                ]

    async def search_news(self, query: str, days: int = 7) -> list[dict]:
        """Search recent news only (last N days)."""
        # Add news-specific query: "query after:2025-03-19"
        return await self.search(f"{query} after:{get_date_n_days_ago(days)}")
```

**Register in tool router:**
```python
# backend/app/core/tools/tool_router.py
web_search = WebSearchTool(os.getenv("SERPAPI_KEY"))
TOOLS = {
    "web_search": web_search.search,
    "web_search_news": web_search.search_news,
}
```

#### Acceptance Criteria
- [ ] `web_search("Skybound CRM acquisition")` returns top 5 news articles
- [ ] Results include publication date and snippet
- [ ] Agents can call this tool as part of their task

---

### F-018 · Hybrid Search (Vector + BM25)
**Priority:** P1 · **Effort:** M · **Source:** MiroFish

#### Problem
Vector-only RAG misses exact-match documents (e.g., "Skybound CRM" revenue). BM25 full-text search misses semantic matches (e.g., "aerial-view SaaS platform" vs "cloud software company").

#### Files to Create
```
backend/app/core/search/hybrid_search.py   ← NEW
```

#### Implementation Spec

```python
"""Hybrid search combining vector embeddings + BM25 full-text."""

class HybridSearch:
    def __init__(self, vector_store, bm25_index):
        self.vector_store = vector_store
        self.bm25 = bm25_index

    async def search(self, query: str, top_k: int = 10, alpha: float = 0.6) -> list[dict]:
        """
        Combined search with RRF (Reciprocal Rank Fusion).
        alpha=0.6 means 60% weight on vector, 40% on BM25.
        """
        vector_results = await self.vector_store.search(query, top_k=top_k)
        bm25_results = self.bm25.search(query, top_k=top_k)

        # Score by rank (reciprocal rank fusion)
        vector_scores = {r["id"]: 1/(i+1) for i, r in enumerate(vector_results)}
        bm25_scores = {r["id"]: 1/(i+1) for i, r in enumerate(bm25_results)}

        # Merge with weighted combination
        all_ids = set(vector_scores.keys()) | set(bm25_scores.keys())
        final_scores = {}
        for doc_id in all_ids:
            v_score = vector_scores.get(doc_id, 0) * alpha
            bm_score = bm25_scores.get(doc_id, 0) * (1 - alpha)
            final_scores[doc_id] = v_score + bm_score

        # Return sorted by final score
        ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
        return [{"id": doc_id, "score": score} for doc_id, score in ranked[:top_k]]
```

#### Acceptance Criteria
- [ ] Hybrid search returns both semantic + exact-match results
- [ ] Alpha parameter can be tuned (0.0 = pure BM25, 1.0 = pure vector)
- [ ] Performance: <200ms for 10K document corpus

---

### F-019 · Dynamic Deal-Specific Task Generation
**Priority:** P0 · **Effort:** M · **Source:** Internal Audit

#### Problem
Every agent gets the same hardcoded prompt: "Analyze financial aspects." Real analysis should vary by deal type. A tech acquisition needs product-market fit analysis. A manufacturing buyout needs supply chain risk.

#### Files to Modify
```
backend/app/agents/base.py          ← add task generation
backend/app/orchestrator/graph.py   ← _node_task_generation() — NEW stage
```

#### Implementation Spec

**New stage in graph (pre-parallel_analysis):**
```python
async def _node_task_generation(self, state: DealState) -> DealState:
    """
    Generate deal-specific tasks for each agent based on industry + deal type.
    """
    industry = state.get("context", {}).get("industry", "").lower()
    deal_type = state.get("context", {}).get("deal_type", "acquisition")
    deal_brief = state.get("deal_brief", "")

    prompt = f"""
    Deal: {state.get('deal_name')}
    Industry: {industry}
    Type: {deal_type}
    Brief: {deal_brief[:500]}

    Generate specific analytical tasks for:
    1. Financial Analyst — what metrics matter most?
    2. Legal Analyst — what regulatory risks?
    3. Risk Assessor — industry-specific risks?
    4. Market Analyst — market size / growth?

    Format as JSON with keys: financial_task, legal_task, risk_task, market_task.
    Each value is a specific, 2-3 sentence instruction.
    """

    response = await self.gateway.call(
        provider="gemini",
        prompt=prompt,
        temperature=0.7,  # Some creativity
        max_tokens=512,
    )

    tasks = json.loads(response.get("content", "{}"))
    return update_state(state, {"agent_tasks": tasks})
```

**Use in parallel_analysis:**
```python
agent_tasks = state.get("agent_tasks", {})
financial_task = agent_tasks.get("financial_task", "Analyze financials")

result = await self.agent_registry["financial"].run(
    financial_task,  # ← Dynamic, not hardcoded
    context=state.get("context", {}),
)
```

#### Acceptance Criteria
- [ ] Tech deal → tasks include "assess product-market fit, TAM expansion"
- [ ] Manufacturing deal → tasks include "supply chain resilience, capex requirements"
- [ ] Private equity deal → tasks emphasize "EBITDA, leverage ratios, synergy potential"

---

### F-020 · WorkflowConfig: Dynamic Agent Selection
**Priority:** P2 · **Effort:** S · **Source:** Local Deep Researcher

#### Problem
Every deal uses all 25 agents. A simple screening pass doesn't need deep DCF modeling or stakeholder simulation. Agents are redundant for early-stage deals.

#### Files to Create
```
backend/app/orchestrator/workflow_config.py   ← NEW
```

#### Implementation Spec

```python
from dataclasses import dataclass
from enum import Enum

class DealStage(str, Enum):
    SCREENING = "screening"
    PRELIMINARY = "preliminary"
    IC_MEMO = "ic_memo"
    DEEP_DIVE = "deep_dive"

@dataclass
class WorkflowConfig:
    """Defines which agents run at which deal stage."""
    stage: DealStage
    agents: list[str]
    enable_iteration: bool = False
    enable_debate: bool = False
    max_tokens: int = 16000

    @classmethod
    def from_stage(cls, stage: DealStage) -> "WorkflowConfig":
        """Preset configs for each stage."""
        configs = {
            DealStage.SCREENING: WorkflowConfig(
                stage=stage,
                agents=["financial", "legal", "risk"],
                enable_debate=False,
            ),
            DealStage.PRELIMINARY: WorkflowConfig(
                stage=stage,
                agents=["financial", "legal", "risk", "market"],
                enable_debate=True,
            ),
            DealStage.DEEP_DIVE: WorkflowConfig(
                stage=stage,
                agents=[...all 25...],
                enable_iteration=True,
                enable_debate=True,
                max_tokens=32000,
            ),
        }
        return configs.get(stage, configs[DealStage.PRELIMINARY])
```

**Usage in graph:**
```python
stage = state.get("deal_stage", "screening")
config = WorkflowConfig.from_stage(stage)

# Only run configured agents
for agent_name in config.agents:
    ...
```

#### Acceptance Criteria
- [ ] Screening config uses 3 agents (financial, legal, risk)
- [ ] Deep-dive config uses all 25 agents + iteration + debate
- [ ] Token allocation varies by stage

---

## Phase 4 — Knowledge Graph (Neo4j GraphRAG)

> **Goal:** Build a persistent, queryable knowledge graph of deal facts and relationships.

---

### F-021 · Deal Knowledge Graph (Neo4j)
**Priority:** P0 · **Effort:** L · **Source:** MiroFish

#### Problem
Agent findings exist only as isolated JSON blobs. No way to query "What are all risks related to supply chain?" across deals. No entity linking (e.g., recognizing "Skybound" and "Skybound CRM" as the same company).

#### Files to Create
```
backend/app/core/knowledge_graph/neo4j_client.py   ← NEW
backend/app/core/knowledge_graph/ontology.py       ← NEW
```

#### Implementation Spec

```python
"""Neo4j knowledge graph for deal intelligence."""

from neo4j import AsyncGraphDatabase

class DealKnowledgeGraph:
    def __init__(self, uri: str, auth: tuple):
        self.driver = AsyncGraphDatabase.driver(uri, auth=auth)

    async def create_deal_node(self, deal_id: str, deal_name: str, industry: str):
        """Create a DEAL node in the graph."""
        async with self.driver.session() as session:
            await session.run(
                """
                MERGE (d:Deal {id: $deal_id})
                SET d.name = $name, d.industry = $industry,
                    d.created_at = datetime()
                """,
                deal_id=deal_id,
                name=deal_name,
                industry=industry,
            )

    async def add_company_node(self, deal_id: str, company_name: str, entity_type: str):
        """Create a COMPANY node linked to a deal."""
        async with self.driver.session() as session:
            await session.run(
                """
                MATCH (d:Deal {id: $deal_id})
                MERGE (c:Company {name: $name})
                SET c.type = $type
                MERGE (d)-[:INVOLVES]->(c)
                """,
                deal_id=deal_id,
                name=company_name,
                type=entity_type,
            )

    async def add_risk_node(self, deal_id: str, risk_name: str, severity: int, category: str):
        """Create a RISK node."""
        async with self.driver.session() as session:
            await session.run(
                """
                MATCH (d:Deal {id: $deal_id})
                MERGE (r:Risk {name: $name})
                SET r.severity = $severity, r.category = $category
                MERGE (d)-[:HAS_RISK]->(r)
                """,
                deal_id=deal_id,
                name=risk_name,
                severity=severity,
                category=category,
            )

    async def query_risks_by_category(self, category: str) -> list:
        """SPARQL-like: Find all risks in a category."""
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (d:Deal)-[:HAS_RISK]->(r:Risk)
                WHERE r.category = $category
                RETURN d.name, r.name, r.severity
                """,
                category=category,
            )
            return [record for record in await result.data()]
```

#### Acceptance Criteria
- [ ] Neo4j instance is initialized on startup
- [ ] Each deal creates a DEAL node with linked COMPANY, RISK, PERSON, METRIC nodes
- [ ] Cross-deal queries work (e.g., "all deals with supply chain risks > severity 8")
- [ ] Entity deduplication: "Skybound CRM" and "Skybound" resolve to same node

---

### F-022 · Dynamic Deal Ontology Generation
**Priority:** P1 · **Effort:** M · **Source:** MiroFish

#### Problem
The knowledge graph uses fixed schema (DEAL, COMPANY, RISK). Each industry has unique entity types. Tech deals have "product_market_fit", but financial services deals have "regulatory_approval". No way to auto-extend schema.

#### Implementation Spec

```python
class DynamicOntology:
    """Auto-generates entity types and relationships based on deal context."""

    async def generate_ontology(self, industry: str, deal_brief: str) -> dict:
        """
        Use LLM to generate industry-specific ontology.
        Returns: {
            "entities": [{"type": "Product", "properties": [...]}, ...],
            "relationships": [{"from": "Company", "to": "Product", "label": "SELLS"}, ...],
        }
        """
        prompt = f"""
        Industry: {industry}
        Deal Brief: {deal_brief[:500]}

        Design a knowledge graph schema for {industry} deal analysis.
        Entity types: What entities matter? (e.g., Company, Product, Regulatory_Body)
        Relationships: How do they connect? (e.g., Company-MANUFACTURES->Product)

        Return as JSON with "entities" and "relationships" arrays.
        """

        response = await self.gateway.call(
            provider="gemini",
            prompt=prompt,
            temperature=0.7,
            max_tokens=1024,
        )

        return json.loads(response.get("content", "{}"))
```

#### Acceptance Criteria
- [ ] Tech deal ontology includes: Product, Technology_Stack, Customer_Segment
- [ ] Biotech deal ontology includes: Clinical_Trial, Regulatory_Approval, IP_Patent
- [ ] Relationships are automatically inferred

---

### F-023 · Agent Write-Back to Knowledge Graph
**Priority:** P1 · **Effort:** M · **Source:** MiroFish

#### Problem
Agents produce findings in JSON. These never touch the knowledge graph. The graph stays empty.

#### Implementation Spec

```python
# In each agent's finalize() step:
async def _write_findings_to_graph(self, findings: dict, deal_id: str, kb_graph: DealKnowledgeGraph):
    """Extract entities from findings and write to Neo4j."""

    # Example: Financial agent finds "Revenue: $50M"
    if "revenue" in findings:
        await kb_graph.add_metric_node(
            deal_id=deal_id,
            metric_name="Annual_Revenue",
            value=findings["revenue"],
            currency="USD",
        )

    # Example: Risk agent finds "Supply chain concentration"
    if "risks" in findings:
        for risk in findings["risks"]:
            await kb_graph.add_risk_node(
                deal_id=deal_id,
                risk_name=risk.get("name"),
                severity=risk.get("severity"),
                category=risk.get("category"),
            )
```

#### Acceptance Criteria
- [ ] Every agent's findings are parsed for entities and relationships
- [ ] Entities are deduplicated before writing (no duplicate nodes)
- [ ] Graph is queryable post-deal (e.g., "all acquisition targets in SaaS")

---

### F-024 · Temporal Facts (valid_at / expired_at)
**Priority:** P2 · **Effort:** M · **Source:** MiroFish

#### Problem
A deal is analyzed in March with market data from February. By June, that data is stale but still queries. No way to version facts or mark them obsolete.

#### Implementation Spec

```python
# In knowledge graph schema:
class Fact(BaseModel):
    entity: str
    property: str
    value: Any
    valid_from: datetime
    valid_until: datetime | None = None
    source_agent: str
    confidence: float = 1.0

# Query with temporal awareness:
async def query_current_facts(self, entity_id: str) -> list[Fact]:
    """Return only facts valid at current time."""
    async with self.driver.session() as session:
        result = await session.run(
            """
            MATCH (e)-[f:HAS_FACT]->(v)
            WHERE e.id = $entity_id
            AND f.valid_from <= datetime()
            AND (f.valid_until IS NULL OR f.valid_until > datetime())
            RETURN f
            """,
            entity_id=entity_id,
        )
        return [Fact(**record) for record in await result.data()]
```

#### Acceptance Criteria
- [ ] Facts have valid_from and valid_until timestamps
- [ ] Queries exclude expired facts by default
- [ ] Historical queries possible (e.g., "what was Q2 revenue at time X")

---

### F-025 · InsightForge: GraphRAG for Deal Intelligence
**Priority:** P0 · **Effort:** L · **Source:** MiroFish

#### Problem
With a knowledge graph, it's now possible to do graph-based reasoning: "What companies have unresolved supply chain risks?" or "Compare all SaaS acquisitions by revenue growth rate." This requires GraphRAG — retrieving not just documents but graph paths.

#### Files to Create
```
backend/app/core/search/graphrag.py   ← NEW
```

#### Implementation Spec

```python
class InsightForgeGraphRAG:
    """Graph-based RAG: traverse knowledge graph to answer complex questions."""

    async def answer_question(self, question: str, max_depth: int = 3) -> dict:
        """
        Example:
        Q: "What are the top 3 risks across all SaaS acquisitions?"

        1. Find all DEAL nodes with industry=SaaS
        2. Follow DEAL-[:HAS_RISK]->RISK edges
        3. Aggregate risks by severity
        4. Return top 3
        """

        # Decompose question into graph traversal
        traversal_plan = await self._decompose_question(question)

        # Execute Cypher queries per plan step
        results = await self._execute_traversal(traversal_plan, max_depth)

        # Synthesize into answer
        answer = await self._synthesize_answer(question, results)
        return answer

    async def _decompose_question(self, question: str) -> str:
        """Use LLM to convert NL question into Cypher traversal plan."""
        prompt = f"""
        Knowledge graph has these entity types: Deal, Company, Risk, Person, Metric
        Relationships: DEAL-[:INVOLVES]->COMPANY, DEAL-[:HAS_RISK]->RISK, etc.

        Question: {question}

        Generate a Cypher query to answer this. Return only Cypher code.
        """

        response = await self.gateway.call(
            provider="gemini",
            prompt=prompt,
            temperature=0.0,
            max_tokens=512,
        )

        return response.get("content", "")
```

#### Acceptance Criteria
- [ ] GraphRAG answers complex questions like "Which acquirers had unresolved legal risks?"
- [ ] Supports multi-hop queries (>2 relationship hops)
- [ ] Returns both entities and their paths for transparency

---

## Phase 5 — Advanced Orchestration

> **Goal:** Dynamic agent selection, per-stage quality gates, stakeholder simulation.

---

### F-026 · Dynamic Agent Selection Planner
**Priority:** P1 · **Effort:** L · **Source:** Internal Audit

#### Problem
17-node graph has every node wired. No way to skip an agent without modifying the graph. A deal that already has legal diligence might not need the LegalAnalystAgent.

#### Implementation Spec

```python
class AgentSelectionPlanner:
    """Determines which agents to run based on deal context."""

    async def plan_execution(self, state: DealState) -> list[str]:
        """
        Return list of agent names to execute.

        Example output:
        ["financial", "legal", "risk", "market", "advanced_financial"]
        """

        industry = state.get("context", {}).get("industry", "")
        deal_type = state.get("context", {}).get("deal_type", "")

        base_agents = ["financial", "legal", "risk", "market"]

        # Add specialized agents based on industry
        if industry.lower() in ["healthcare", "biotech"]:
            base_agents.append("clinical_analyst")

        if industry.lower() == "technology":
            base_agents.extend(["tech_analyst", "product_analyst"])

        if state.get("deal_size", 0) > 500_000_000:  # >$500M
            base_agents.append("advanced_financial")

        return base_agents
```

#### Acceptance Criteria
- [ ] Healthcare deals include clinical_analyst
- [ ] Large deals (>$500M) include advanced_financial
- [ ] Agent selection is logged with reasoning

---

### F-027 · Per-Stage Quality Gates
**Priority:** P1 · **Effort:** M · **Source:** Memento / Local Deep Researcher

#### Problem
Debate and red team flags should have different thresholds by stage. Screening can accept 1 critical risk. IC memo cannot.

#### Implementation Spec

```python
@dataclass
class QualityGate:
    stage: DealStage
    max_critical_risks: int
    max_unresolved_conflicts: int
    min_consensus: float  # e.g., 0.8 = 80% agreement required

    @classmethod
    def for_stage(cls, stage: DealStage) -> "QualityGate":
        gates = {
            DealStage.SCREENING: QualityGate(stage, max_critical_risks=3, max_unresolved_conflicts=2, min_consensus=0.5),
            DealStage.IC_MEMO: QualityGate(stage, max_critical_risks=1, max_unresolved_conflicts=0, min_consensus=0.9),
            DealStage.DEEP_DIVE: QualityGate(stage, max_critical_risks=0, max_unresolved_conflicts=0, min_consensus=0.95),
        }
        return gates.get(stage, gates[DealStage.PRELIMINARY])

async def _check_quality_gate(self, state: DealState) -> bool:
    """Return True if output passes quality gate."""
    stage = state.get("deal_stage")
    gate = QualityGate.for_stage(stage)

    red_team_critical = sum(
        1 for r in state.get("red_team_flags", [])
        if r.get("severity") >= 9
    )

    if red_team_critical > gate.max_critical_risks:
        self.logger.warning(f"Quality gate failed: {red_team_critical} critical risks > limit {gate.max_critical_risks}")
        return False

    return True
```

#### Acceptance Criteria
- [ ] Screening passes with 3 critical risks
- [ ] IC memo fails with >1 critical risk
- [ ] Quality gate decision is logged with threshold values

---

### F-028 · Stakeholder Reaction Simulation
**Priority:** P2 · **Effort:** L · **Source:** MiroFish

#### Problem
Deal analysis is one-dimensional (analyst view). Real decisions involve stakeholder sentiment (board, legal, finance, operations). A deal might be financially attractive but operationally risky from the CEO's perspective.

#### Implementation Spec

```python
class StakeholderSimulation:
    """Multi-stakeholder perspective on the deal."""

    async def simulate_reactions(self, deal: dict) -> dict:
        """
        Get perspectives from:
        - CFO: Is this accretive to EPS? Good leverage?
        - CTO/COO: Integration risks? Cultural fit?
        - General Counsel: Regulatory/litigation risks?
        - Board: Strategic fit? Value creation?
        """

        stakeholders = ["cfo", "cto", "general_counsel", "board"]
        reactions = {}

        for stakeholder in stakeholders:
            prompt = f"""
            Analyze this deal from the {stakeholder.upper()}'s perspective.
            Deal: {deal['name']}
            Industry: {deal['industry']}
            {stakeholder.upper()}'s priorities: [...]

            Rate deal sentiment: 1-10 (1=strongly against, 10=strongly favor)
            Top 3 concerns
            Top 3 value drivers
            Conditions for approval
            """

            response = await self.gateway.call(
                provider="gemini",
                prompt=prompt,
                temperature=0.7,
                max_tokens=400,
            )

            reactions[stakeholder] = json.loads(response.get("content", "{}"))

        return reactions
```

#### Acceptance Criteria
- [ ] Each stakeholder provides a 1-10 sentiment score
- [ ] Consensus score is the average (or weighted by seniority)
- [ ] Report includes a "Stakeholder Alignment" section

---

### F-029 · Anthropic Claude Client
**Priority:** P1 · **Effort:** M · **Source:** Internal Audit

#### Files to Create
```
backend/app/core/llm/claude_client.py   ← NEW
```

#### Implementation Spec

```python
"""Anthropic Claude API client."""
import anthropic

class ClaudeClient:
    def __init__(self, api_key: str, model: str = "claude-opus-4"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> dict[str, Any]:
        """Generate completion via Claude API."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        return {
            "content": response.content[0].text,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }
```

**Register in LLM Gateway:**
```python
# In llm_gateway.py
elif provider == "claude":
    return ClaudeClient(os.getenv("ANTHROPIC_API_KEY"), model)
```

#### Acceptance Criteria
- [ ] Claude can be selected as LLM provider
- [ ] Tool calling works with Claude
- [ ] Thinking tokens are handled (if model is claude-3-7-sonnet)

---

### F-030 · Groq Client
**Priority:** P2 · **Effort:** S · **Source:** Internal Audit

**Similar to F-029 but for Groq API:**
```python
class GroqClient:
    def __init__(self, api_key: str, model: str = "mixtral-8x7b-32768"):
        from groq import Groq
        self.client = Groq(api_key=api_key)
        self.model = model

    async def generate(self, prompt: str, system_prompt: str | None = None, **kwargs):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        return {"content": response.choices[0].message.content}
```

#### Acceptance Criteria
- [ ] Groq can be selected as fallback provider
- [ ] Groq is faster for simple completions (sub-1-second latency)

---

### F-031 · Deal-Specific Task Map at Screening
**Priority:** P0 · **Effort:** M · **Source:** Internal Audit

#### Problem
Screening uses hardcoded tasks. A screening analyst reviewing 20 deals/week wants: "Check growth trajectory" for SaaS, "Check supply chain resilience" for manufacturing, "Check patent strength" for biotech. Right now it's one template for all.

#### Implementation Spec

```python
class ScreeningTaskMap:
    """Pre-built task templates for each industry."""

    TEMPLATES = {
        "saas": {
            "financial": "Assess: (1) growth rate vs. industry, (2) CAC payback period, (3) gross margins, (4) cash burn vs. runway",
            "market": "Market size (TAM/SAM/SOM), competitive intensity, switching costs",
            "risk": "Customer concentration, platform dependency, churn trends, pricing power",
        },
        "manufacturing": {
            "financial": "EBITDA stability, capex requirements, working capital needs, inventory turns",
            "legal": "Supply chain contracts, union agreements, environmental liabilities",
            "risk": "Supply chain concentration, commodity price exposure, factory utilization",
        },
        # ... more industries ...
    }

    @classmethod
    def get_tasks(cls, industry: str) -> dict:
        return cls.TEMPLATES.get(industry.lower(), cls.TEMPLATES["default"])
```

**Usage in screening workflow:**
```python
industry = state["context"]["industry"]
tasks = ScreeningTaskMap.get_tasks(industry)
# Pass industry-specific tasks to agents
```

#### Acceptance Criteria
- [ ] SaaS screening emphasizes growth rate, CAC, gross margin
- [ ] Manufacturing screening emphasizes capex, supply chain, factory efficiency
- [ ] Task templates are stored in CLAUDE.md for easy customization

---

## Phase 6 — Production Hardening

> **Goal:** Observability, error recovery, security, performance optimization.

---

### F-032 · LangSmith @traceable Observability
**Priority:** P2 · **Effort:** S · **Source:** Local Deep Researcher

#### Implementation Spec

```python
from langsmith import traceable

@traceable(name="deal_workflow")
async def run_deal_analysis(deal_brief: str) -> dict:
    """All LLM calls, agents, and state transitions are traced in LangSmith."""

    @traceable(name="retrieval")
    async def retrieve_documents():
        return await doc_store.retrieve(deal_brief)

    @traceable(name="parallel_analysis")
    async def run_agents():
        return await graph.run(deal_brief)

    docs = await retrieve_documents()
    results = await run_agents()

    return results
```

**Setup:**
```python
# In main.py
import langsmith
langsmith.set_api_key(os.getenv("LANGSMITH_API_KEY"))

# All @traceable functions now auto-log to LangSmith dashboard
```

#### Acceptance Criteria
- [ ] Every agent run is logged to LangSmith with inputs/outputs
- [ ] Latency is tracked per node
- [ ] Costs are tracked per provider call

---

### F-033 · Subprocess Isolation for Report Generation
**Priority:** P2 · **Effort:** L · **Source:** MiroFish

#### Problem
Report generation (PPTX, PDF, XLSX) is CPU-bound and can block the API. A timeout in PowerPoint generation blocks the deal workflow.

#### Implementation Spec

```python
import subprocess
import json

async def generate_report_isolated(
    deal_data: dict,
    output_format: str = "pptx",
) -> str:
    """
    Spin up isolated subprocess to generate report.
    Prevents crashes from affecting the main workflow.
    """

    # Write deal data to temp file
    with open("/tmp/deal_data.json", "w") as f:
        json.dump(deal_data, f)

    # Run report generator as separate process
    process = await asyncio.create_subprocess_exec(
        "python", "backend/app/core/reports/pptx_generator.py",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(
        process.communicate(input=json.dumps(deal_data).encode()),
        timeout=120,  # 2-minute timeout
    )

    if process.returncode != 0:
        logger.error(f"Report generation failed: {stderr.decode()}")
        raise RuntimeError(f"Report generation failed")

    report_path = stdout.decode().strip()
    return report_path
```

#### Acceptance Criteria
- [ ] Report generation doesn't block deal analysis
- [ ] Subprocess timeout is enforced (max 120s)
- [ ] Failures in report generation don't crash the workflow

---

### F-034 · strip_thinking in All Clients
**Priority:** P0 · **Effort:** XS · **Source:** Local Deep Researcher

#### Problem
F-001 added thinking token stripping only in LLMGateway. Some clients call reasoning models directly (bypass gateway). Need stripping everywhere.

#### Implementation Spec

Apply `strip_thinking_tokens()` in:
- ✅ `llm_gateway.py` (already done in F-001)
- `claude_client.py` (F-029) — strip after response
- `groq_client.py` (F-030) — strip after response
- Any agent that calls an LLM directly (not via gateway)

**Template:**
```python
from app.core.llm.thinking_utils import strip_thinking_tokens, detect_thinking_tag

# After getting response from LLM:
if content and detect_thinking_tag(content):
    clean_content, thinking_content = extract_thinking(content)
    result["content"] = clean_content
    result["thinking_content"] = thinking_content
```

#### Acceptance Criteria
- [ ] No `<think>` blocks in agent outputs
- [ ] `json.loads()` succeeds on all reasoning model outputs
- [ ] Thinking content is preserved in logs for debugging

---

### F-035 · OpenRouter Unified LLM Client
**Priority:** P2 · **Effort:** M · **Source:** Internal Audit

#### Problem
DealForge now supports 7+ LLM providers. Maintaining each client is a burden. OpenRouter is a unified endpoint that supports 100+ models behind one API.

#### Files to Create
```
backend/app/core/llm/openrouter_client.py   ← NEW
```

#### Implementation Spec

```python
"""OpenRouter unified LLM client (fallback meta-provider)."""

class OpenRouterClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://openrouter.ai/api/v1"

    async def generate(
        self,
        prompt: str,
        model: str = "auto",  # Auto-select best model
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> dict:
        """Call OpenRouter's unified endpoint."""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://dealforge.ai",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                headers=headers,
            ) as resp:
                data = await resp.json()
                return {
                    "content": data["choices"][0]["message"]["content"],
                    "usage": data.get("usage", {}),
                }
```

**Use in fallback chain:**
```python
# In llm_gateway.py
fallback_chain = [
    ("gemini", self.gemini_client),
    ("mistral", self.mistral_client),
    ("openai", self.openai_client),
    ("openrouter", self.openrouter_client),  # ← Ultimate fallback
    ("ollama", self.ollama_client),
]
```

#### Acceptance Criteria
- [ ] OpenRouter is the last provider tried (slowest, most expensive, most reliable)
- [ ] Auto-selection chooses best available model from 100+
- [ ] Fallback chain never fails (always has a working provider)

---

## Global Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          DEALFORGE AI v2.0                              │
│                    Multi-Agent M&A Due Diligence                        │
└─────────────────────────────────────────────────────────────────────────┘

                              ┌──────────────┐
                              │  React UI    │
                              │  (Dashboard) │
                              └──────┬───────┘
                                     │ HTTP/WS
                              ┌──────▼───────┐
                              │   FastAPI    │
                              │   (Backend)  │
                              └──────┬───────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        │         ┌──────────────────▼──────────────────┐         │
        │         │     LangGraph Orchestrator (17-node)│         │
        │         │                                    │         │
        │         │  Init → Retrieval → Task Gen      │         │
        │         │  → Parallel Analysis → Debate     │         │
        │         │  → Red Team → Scoring →           │         │
        │         │  Complex Reasoning → Report       │         │
        │         └──────────────────┬──────────────────┘         │
        │                            │                            │
        │    ┌───────────────────────┼───────────────────────┐   │
        │    │                       │                       │   │
        │    ▼                       ▼                       ▼   │
        │ ┌─────────┐  ┌─────────┐ ┌─────────┐  ┌─────────┐    │
        │ │ Debate  │  │Red Team │ │Halugate │  │ Scoring │    │
        │ │ Engine  │  │ Engine  │ │ Filter  │  │ Engine  │    │
        │ └────┬────┘  └────┬────┘ └────┬────┘  └────┬────┘    │
        │      │            │           │             │         │
        │ ┌────▼────────────▼───────────▼─────────────▼────┐   │
        │ │         25 Specialized Agents (Pool)          │   │
        │ │ Financial | Legal | Risk | Market | Tech ...  │   │
        │ └──────────────────┬──────────────────────────┘   │
        │                    │                              │
        ├────────────────────┼──────────────────────────────┤
        │                    │                              │
        │   LLM Gateway (Fallback Chain)                    │
        │   Gemini → Mistral → OpenAI → OpenRouter → Ollama  │
        │                                                    │
        │   ┌─────────────────────────────────────────────┐ │
        │   │ Model Capability Registry (F-009)           │ │
        │   │ Token Budgeting (F-011)                     │ │
        │   │ Context Guard (F-004)                       │ │
        │   │ Thinking Token Stripper (F-001)             │ │
        │   └─────────────────────────────────────────────┘ │
        │                                                    │
        └────────────────────┬───────────────────────────────┘
                             │
        ┌────────────────────┼───────────────────────────────┐
        │                    │                               │
        │    ┌──────────────▼──────────────┐                 │
        │    │  Knowledge Graph (Neo4j)    │                 │
        │    │  + GraphRAG + Ontology      │                 │
        │    └──────────────┬──────────────┘                 │
        │                   │                                │
        │    ┌──────────────▼──────────────┐                 │
        │    │  Document Store (Redis)      │                 │
        │    │  + Hybrid Search (BM25+Vec)  │                 │
        │    └──────────────┬──────────────┘                 │
        │                   │                                │
        │    ┌──────────────▼──────────────┐                 │
        │    │  External Data Sources       │                 │
        │    │  SEC | Bloomberg | Web Search│                 │
        │    └──────────────────────────────┘                 │
        │                                                    │
        └────────────────────────────────────────────────────┘

                      Report Generation Pipeline
                    (Subprocess isolation, F-033)

            ┌─────────────────────────────────────────┐
            │ Reporting Engine (McKinsey-style)       │
            │                                         │
            │ PPTX Generator | PDF | XLSX | IC Memo  │
            │                                         │
            └─────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
            ┌──────────────┐          ┌──────────────┐
            │   Reports    │          │  Observability
            │  Archive     │          │  (LangSmith)
            └──────────────┘          └──────────────┘
```

---

## Data Schemas

### DealState (LangGraph State)
```python
class DealState(TypedDict, total=False):
    # Identifiers
    deal_id: str
    deal_name: str
    created_at: str
    updated_at: str

    # Input
    deal_brief: str
    context: dict[str, Any]

    # Workflow control
    current_stage: DealStage
    deal_stage: str  # screening, preliminary, ic_memo, deep_dive
    loop_count: int
    revision_targets: list[str]

    # Retrieval & Knowledge
    curated_data: dict[str, list[dict]]
    fact_base: dict
    source_registry: dict[str, list[dict]]

    # Agent outputs (parallel)
    financial_output: dict
    legal_output: dict
    risk_output: dict
    market_output: dict
    advanced_financial_output: dict

    # Synthesis
    debate_output: dict
    red_team_output: dict
    red_team_flags: list[dict]
    consistency_warnings: list[str]
    halugate_results: dict

    # Reasoning & Scoring
    reasoning_trace: dict
    final_score: float
    scoring_output: dict

    # Report
    report_blueprint: dict
    meeting_memo: dict
    pptx_path: str
    pdf_path: str

    # Metadata
    agent_tasks: dict[str, str]
    running_summary: str
```

---

## Implementation Rules

1. **Phase 1 First**: Complete all of Phase 1 (F-001–F-008) before starting Phase 2.
2. **Async Everywhere**: All LLM calls and I/O must be `async`.
3. **Error Handling**: Failures in one agent should not crash the workflow (use try/except in graph nodes).
4. **Logging**: Structlog with consistent field names (provider, model, latency_ms, error).
5. **Token Tracking**: Log input_tokens and output_tokens for every LLM call.
6. **Testing**: Unit tests for each feature's acceptance criteria before merge.
7. **Documentation**: Update CLAUDE.md with new config options and hooks.

---

## V2.1 Enhancements (Bonus, Post-MVP)

### F-036 · Pydantic Structured Output Validation
**Effort:** M | **Priority:** P2

Auto-validate agent outputs against Pydantic schemas before writing to graph.

### F-037 · Async Parallel Agent Execution Optimization
**Effort:** M | **Priority:** P2

Use `asyncio.gather()` with proper exception handling and timeout per agent.

### F-038 · Redis Result Caching with TTL
**Effort:** S | **Priority:** P2

Cache identical queries for 24h to avoid redundant LLM calls.

### F-039 · Rate Limit Handling with Exponential Backoff
**Effort:** S | **Priority:** P2

Gracefully handle 429 responses with jitter-based backoff.

### F-040 · Deal Similarity Clustering
**Effort:** L | **Priority:** P2

Use vector embeddings to cluster similar deals; recommend comparison insights.

---

**END OF PRD**

**Total Features:** 40 (35 core + 5 V2.1)
**Total Effort Estimate:** 10–12 weeks (1 week Phase 1, 2 weeks Phase 2, 2 weeks Phase 3, 2–3 weeks Phase 4, 2 weeks Phase 5, 1–2 weeks Phase 6)
**Target Completion:** 2026-05-30 (10 weeks from 2026-03-19)
**Quality Target:** 99% production-ready (vs. current 87%)