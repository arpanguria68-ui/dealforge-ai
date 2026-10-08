"""Base Agent Class for DealForge AI"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import structlog
import json
import re
import asyncio
from contextvars import ContextVar

from app.core.llm import get_llm_client
from app.core.llm.model_router import get_model_router
from app.core.llm.llm_gateway import get_llm_gateway
from app.core.memory.pageindex_client import PageIndexClient
from app.core.tools.tool_router import ToolRouter
from app.core.reflection.reflection_engine import ReflectionEngine, RewardEngine
from app.core.skills import build_skill_context, get_skill_for_task
from app.core.validation.output_validator import (
    validate_agent_output,
    format_validation_block,
)
from app.core.messaging.message_bus import get_message_bus, AgentMessage

logger = structlog.get_logger()

# Per-task agent run context. Agent instances are process-wide singletons, so a
# plain attribute let two concurrent deals overwrite each other's context
# (provider choice, deal_id used to filter document retrieval) mid-run.
# asyncio tasks copy the ContextVar on creation, so each run sees its own.
_AGENT_RUN_CONTEXT: ContextVar[Dict[int, Any]] = ContextVar("agent_run_context", default={})


@dataclass
class IssueTreeNode:
    """A node in a MECE issue tree"""

    id: str
    hypothesis: str
    sub_branches: List["IssueTreeNode"] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    status: str = "open"  # open, supported, refuted, needs_data
    confidence: float = 0.0


@dataclass
class AgentOutput:
    """Standardized agent output"""

    success: bool
    data: Dict[str, Any]
    reasoning: str
    confidence: float
    execution_time_ms: Optional[float] = None
    tool_calls: Optional[List[Dict]] = None
    reflection_score: Optional[float] = None
    issue_tree: Optional[Dict] = None
    action_id: Optional[int] = None


class BaseAgent(ABC):
    """Base class for all DealForge agents"""

    name: str = "base_agent"
    description: str = "Base agent class"
    recommended_model: str = ""

    @property
    def _current_context(self) -> Any:
        return _AGENT_RUN_CONTEXT.get().get(id(self), {})

    @_current_context.setter
    def _current_context(self, value: Any) -> None:
        contexts = dict(_AGENT_RUN_CONTEXT.get())
        contexts[id(self)] = value
        _AGENT_RUN_CONTEXT.set(contexts)

    def __init__(
        self,
        llm_client=None,
        pageindex_client=None,
        tool_router=None,
        reflection_engine=None,
        reward_engine=None,
    ):
        self._llm_client_injected = llm_client is not None
        # Use ModelRouter for mixed cloud+local LLM strategy
        if llm_client:
            self.llm = llm_client
        else:
            router = get_model_router()
            self.llm = router.get_client_for_agent(self.name)
        self.memory = pageindex_client or PageIndexClient()
        self.pageindex_client = self.memory  # Alias for legacy agent support
        self.reflection = reflection_engine or ReflectionEngine()
        self.reward = reward_engine or RewardEngine()

        # Fix: Register default tools if no custom router provided
        if tool_router:
            self.tools = tool_router
        else:
            self.tools = ToolRouter()
            self.tools.register_default_tools(self.memory)

        self.bus = get_message_bus()
        self.logger = structlog.get_logger(agent=self.name)

    async def generate_with_routed_fallback(self, prompt: str, system_prompt: Optional[str] = None, max_attempts: int = 3):
        """Use Laya's task-selected model and the gateway's guarded provider fallback chain."""
        local_only = bool(getattr(self, "_current_context", {}).get("local_only"))
        if self._llm_client_injected and not local_only:
            return await self.llm.generate(prompt, system_prompt)
        router = get_model_router()
        if local_only:
            provider = getattr(self, "_current_context", {}).get("routed_provider")
            if provider not in {"lmstudio", "ollama"}:
                raise RuntimeError("Local-only analysis requires an explicitly selected local provider.")
            model = None
        else:
            provider, model, _used_fallback = await router.get_model_route_for_text(
                self.name, f"{self.name}: {prompt[:1600]}",
                est_tokens=self._estimate_tokens((system_prompt or "") + prompt) + 2048,
            )
        result = await get_llm_gateway().call(
            provider=provider,
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=2048,
            temperature=0.2,
            use_cache=True,
            json_mode=True,
            allow_fallback=not local_only,
        )
        if result.get("error") or not str(result.get("content") or "").strip():
            raise RuntimeError(f"No configured LLM route returned a usable synthesis ({result.get('error', 'empty response')}).")
        self._last_llm_provider = result.get("provider_used", provider)
        return result

    @abstractmethod
    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        """
        Execute agent task

        Args:
            task: The task description
            context: Optional context data

        Returns:
            AgentOutput with results
        """
        pass

    async def emit_message(self, msg_type: str, payload: Dict[str, Any], deal_id: Optional[str] = None):
        """Helper to emit messages to the inter-agent bus"""
        msg = AgentMessage(
            sender=self.name,
            msg_type=msg_type,
            payload=payload,
            deal_id=deal_id or (self._current_context.get("deal_id") if hasattr(self, "_current_context") else None)
        )
        await self.bus.publish(msg)

    async def run_with_structure(
        self, task: str, context: Optional[Dict] = None
    ) -> AgentOutput:
        """
        DealForge 2.0 structured execution flow:
        0. [NEW] Inject Domain Skill for this task type
        0.1 [NEW] Inject historical best practices (closed RL loop)
        1. Generate MECE Issue Tree (hypothesis-first)
        2. Validate MECE completeness
        3. Retrieve context per branch
        4. Execute analysis with tools
        5. [NEW] Validate financial output for mathematical consistency
        6. Store learnings to memory
        7. [NEW] Reflect, reward, and update best practices
        """
        start_time = datetime.now()
        self.logger.info("Starting structured analysis", task=task, agent=self.name)

        # Step 0: DealForge 2.0 — Inject Domain Skill
        skill_context = build_skill_context(task, self.name)

        # [NEW] Step 0.1: Closed-loop RL — inject historical best practices
        best_practices_context = ""
        action_id = None
        quality_store = None
        try:
            from app.core.quality.agent_quality_store import AgentQualityStore
            quality_store = AgentQualityStore()
            await quality_store.initialize()
            practices = await quality_store.get_historical_best_practices(
                self.name, task[:50]
            )
            if practices:
                best_practices_context = (
                    "\n\n## Historical Best Practices (from prior high-scoring runs)\n"
                    + "\n".join(f"- {p}" for p in practices)
                )
                self.logger.info(
                    "RL best practices injected",
                    agent=self.name,
                    practice_count=len(practices),
                )
            # Log this action for the RL reward loop
            action_id = await quality_store.log_action(
                agent_name=self.name,
                task_type=task[:50],
                deal_context=context or {},
                action_payload={"task": task[:200], "has_best_practices": bool(practices)},
            )
        except Exception as e:
            self.logger.warning("RL best practices injection failed", error=str(e))

        # [NEW] Step 0.5: Sector Customization Framework
        from app.core.sector_loader import load_sector_config, build_sector_prompt

        sector_name = (context or {}).get("sector")
        sector_prompt = ""
        if sector_name:
            cfg = load_sector_config(sector_name)
            sector_prompt = build_sector_prompt(self.name, cfg)

        # [NEW] Step 0.7: Inter-Agent Messaging Context [Area 1]
        messaging_context = ""
        if (context or {}).get("deal_id"):
            try:
                past_msgs = await self.bus.get_messages(context["deal_id"])
                if past_msgs:
                    messaging_context = (
                        "\n\n## Inter-Agent Insights (Shared by other agents)\n"
                        + "\n".join([f"- [{m.sender}]: {m.payload.get('summary', str(m.payload))}" for m in past_msgs if m.msg_type == "insight_discovered"])
                    )
            except Exception as e:
                self.logger.warning("messaging_context_failed", error=str(e))

        if skill_context or sector_prompt or best_practices_context or messaging_context:
            self.logger.info(
                "Context injection",
                agent=self.name,
                has_skill=bool(skill_context),
                has_sector=bool(sector_prompt),
                has_best_practices=bool(best_practices_context),
                has_messages=bool(messaging_context),
            )
            # Pass injected traits to the LLM context via enriched_context
            context = {
                **(context or {}),
                "skill_context": (skill_context or "") + (best_practices_context or "") + (messaging_context or ""),
                "sector_prompt": sector_prompt,
            }

        self._current_context = context  # Store centrally for generate_with_tools

        # Emit SSE agent_starting event so frontend gets real-time updates
        deal_id = (context or {}).get("deal_id")
        task_id = (context or {}).get("task_id", f"{self.name}_{id(self)}")
        if deal_id:
            try:
                from app.api.stream import emit_agent_starting
                await emit_agent_starting(deal_id, self.name, task_id, task)
            except Exception:
                pass

        # Step 1: Generate MECE Issue Tree
        issue_tree = await self.generate_issue_tree(task, context)
        self.logger.info("Issue tree generated", branches=len(issue_tree.sub_branches))

        # Step 2: Validate MECE
        is_valid, gaps = self.validate_mece(issue_tree)
        if not is_valid:
            self.logger.warning("MECE validation failed", gaps=gaps)
            for gap in gaps:
                issue_tree.sub_branches.append(
                    IssueTreeNode(
                        id=f"auto_{len(issue_tree.sub_branches)}", hypothesis=gap
                    )
                )

        # Step 3: Retrieve context per branch
        # Branch retrievals are independent; run them concurrently.
        branch_results = await asyncio.gather(*(
            self.retrieve_context(branch.hypothesis, top_k=3)
            for branch in issue_tree.sub_branches
        ))
        branch_contexts = {
            branch.id: result
            for branch, result in zip(issue_tree.sub_branches, branch_results)
        }

        # Step 4: Execute the core run() with enriched context
        enriched_context = {
            **(context or {}),
            "issue_tree": self._serialize_tree(issue_tree),
            "branch_contexts": branch_contexts,
        }
        output = await self.run(task, enriched_context)

        # Step 5: DealForge 2.0 — Deterministic Output Validation
        output = await self._validate_and_annotate_output(task, output)

        # Step 6: Attach issue tree to output
        output.issue_tree = self._serialize_tree(issue_tree)

        # Step 7: Store learnings to memory
        if output.success:
            await self.store_to_memory(
                content=f"[{self.name}] {task}: {output.reasoning[:500]}",
                tags=[
                    self.name,
                    context.get("industry", "unknown") if context else "unknown",
                ],
            )

        # Step 8: Closed-loop RL — reflect, reward, update best practices
        try:
            if action_id is not None and quality_store is not None:
                # Reflection is an LLM call; a failed run has nothing worth
                # grading, so it is scored 0 without spending the call.
                reflection_score = await self.reflect(task, output) if output.success else 0.0
                reward = self.reward.compute_reward(
                    reflection_score=reflection_score,
                    task_completed=output.success,
                )
                output.reflection_score = reflection_score

                await quality_store.reward_action(
                    action_id, reward, f"reflection={reflection_score:.2f}"
                )
                if reward >= 0.7:
                    await quality_store.update_best_practices(
                        self.name, task[:50]
                    )
                self.logger.info(
                    "RL loop closed",
                    reflection=round(reflection_score, 3),
                )

                # Step 9: Emit Insight discovered message
                if output.success and output.confidence >= 0.8:
                    await self.emit_message(
                        msg_type="insight_discovered",
                        payload={
                            "task": task[:100],
                            "confidence": output.confidence,
                            "summary": output.reasoning[:200]
                        }
                    )
        except Exception as e:
            self.logger.warning("RL reward loop failed", error=str(e))

        execution_time_ms = (datetime.now() - start_time).total_seconds() * 1000
        output.execution_time_ms = execution_time_ms
        output.action_id = action_id

        # Emit SSE agent_completed / agent_error event for real-time frontend updates
        if deal_id:
            try:
                from app.api.stream import emit_agent_completed, emit_agent_error
                if output.success:
                    await emit_agent_completed(
                        deal_id=deal_id,
                        agent_type=self.name,
                        task_id=task_id,
                        result=output.data,
                        reasoning=output.reasoning[:500] if output.reasoning else "",
                        confidence=output.confidence,
                        execution_time_ms=execution_time_ms,
                    )
                else:
                    await emit_agent_error(
                        deal_id=deal_id,
                        agent_type=self.name,
                        task_id=task_id,
                        error=output.reasoning or "Agent returned failure",
                    )
            except Exception:
                pass

        # [NEW] Phase 4: Write findings to knowledge graph (F-023)
        if output.success and deal_id:
            try:
                kb_graph = (context or {}).get("kb_graph")
                if kb_graph:
                    await self._write_findings_to_graph(output.data, deal_id, kb_graph)
            except Exception as e:
                self.logger.warning("graph_write_back_failed", error=str(e))

        return output

    async def _write_findings_to_graph(self, findings: Dict[str, Any], deal_id: str, kb_graph: Any):
        """Extract entities/metrics from findings and persist to the deal knowledge graph (F-023)."""
        self.logger.info("writing_to_graph", deal_id=deal_id)
        
        # 1. Handle specialized metrics (Financial Analyst)
        metrics = findings.get("metrics", {}) or {}
        if not metrics and "valuation" in findings:
             metrics = findings["valuation"]
             
        for k, v in metrics.items():
            if isinstance(v, (int, float)):
                await kb_graph.add_entity(deal_id, f"{self.name}_{k}", "Metric", {"value": v, "agent": self.name})

        # 2. Handle Risks (Risk Assessor / Legal Advisor)
        risks = findings.get("risks", []) or []
        for risk in risks:
            if isinstance(risk, dict):
                await kb_graph.add_risk(
                    deal_id=deal_id,
                    risk_name=risk.get("name", "Unknown Risk"),
                    severity=risk.get("severity", 5),
                    category=risk.get("category", "General"),
                    description=risk.get("description", "")
                )
        
        # 3. Handle Companies/Entities (Market Researcher)
        entities = findings.get("entities", []) or []
        for ent in entities:
             if isinstance(ent, dict):
                 await kb_graph.add_entity(
                     deal_id=deal_id,
                     entity_name=ent.get("name"),
                     entity_label=ent.get("label", "Company"),
                     properties=ent
                 )

    async def _validate_and_annotate_output(
        self, task: str, output: "AgentOutput"
    ) -> "AgentOutput":
        """DealForge 2.0: Validate financial model outputs for math consistency."""
        try:
            task_lower = task.lower()
            output_data = output.data or {}

            # Determine what type of financial model was produced
            if any(
                k in task_lower for k in ["dcf", "wacc", "terminal value", "intrinsic"]
            ):
                result = validate_agent_output("dcf", output_data)
            elif any(
                k in task_lower for k in ["lbo", "leveraged buyout", "irr", "moic"]
            ):
                result = validate_agent_output("lbo", output_data)
            elif any(
                k in task_lower
                for k in ["income statement", "balance sheet", "3-statement"]
            ):
                result = validate_agent_output("financial_statement", output_data)
            else:
                return output  # No validation needed for non-numeric outputs

            # Append validation block to the reasoning
            validation_note = format_validation_block(result)
            output.reasoning = (output.reasoning or "") + validation_note
            output.data["_validation"] = result

        except Exception as e:
            self.logger.warning("output_validation_error", error=str(e))

        return output

    async def retrieve_context(
        self, query: str, top_k: int = 5, deal_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve context within the active deal whenever one is available."""
        try:
            if deal_id is None:
                active_context = getattr(self, "_current_context", None)
                if isinstance(active_context, dict):
                    deal_id = active_context.get("deal_id")
            kwargs = {"top_k": top_k}
            if deal_id:
                kwargs["filters"] = {"deal_id": deal_id}
            
            # Use TokenBudget if available in context (F-011)
            budget = self._current_context.get("token_budget") if hasattr(self, "_current_context") else None
            
            chunks = await self.memory.query(query, **kwargs)
            results = []
            for chunk in chunks:
                res = {
                    "content": chunk.content,
                    "page": chunk.page_number,
                    "relevance": chunk.relevance_score,
                    "source": chunk.metadata.get("filename") if hasattr(chunk, "metadata") else "unknown"
                }
                results.append(res)
                
                # Record in source registry if deal_id is present (F-014)
                if deal_id and hasattr(self, "_current_context"):
                    if "source_registry" not in self._current_context:
                        self._current_context["source_registry"] = {}
                    if self.name not in self._current_context["source_registry"]:
                        self._current_context["source_registry"][self.name] = []
                    
                    source_info = {
                        "title": res["source"],
                        "type": "document_chunk",
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    if source_info not in self._current_context["source_registry"][self.name]:
                        self._current_context["source_registry"][self.name].append(source_info)

            return results
        except Exception as e:
            self.logger.error("Context retrieval failed", error=str(e))
            return []

    async def generate_with_tools(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tool_rounds: int = 3,
        json_mode: bool = False,
    ) -> Dict[str, Any]:
        """Generate response with tool calls, routed through LLM Gateway.

        Supports iterative multi-round tool calling (up to max_tool_rounds).
        All LLM calls go through the gateway for rate limiting, caching, and fallback.
        """
        from app.core.llm.model_router import get_model_router

        # enforce deterministic default
        if temperature is None:
            temperature = 0.0

        # Laya shortlists task-relevant tools inside the agent's allow-list.
        task_tool_list = getattr(self.tools, "list_tools_for_task", None)
        if task_tool_list:
            tools = await task_tool_list(prompt, agent_name=self.name)
        else:
            tools = self.tools.list_tools(agent_name=self.name)
        allowed_tool_names = [
            t.get("function", {}).get("name") for t in tools
            if t.get("function", {}).get("name")
        ]

        # Inject Sector Prompt & Skill Context (Best Practices) dynamically
        ctx = getattr(self, "_current_context", {})
        sector_prompt = ctx.get("sector_prompt")
        skill_context = ctx.get("skill_context")
        
        if sector_prompt:
            system_prompt = (system_prompt or "") + "\n\n" + sector_prompt
        if skill_context:
            system_prompt = (system_prompt or "") + "\n\n" + skill_context
        kg_context = ctx.get("knowledge_graph_context")
        if kg_context:
            system_prompt = (system_prompt or "") + "\n\n" + kg_context
        deliverable_guidance = self._deliverable_guidance(allowed_tool_names)
        if deliverable_guidance:
            system_prompt = (system_prompt or "") + "\n\n" + deliverable_guidance

        # Keep the task-level Laya decision for every call in this tool loop.
        # Route on the real request size (system + prompt + tool schemas +
        # output + headroom for tool results), not on the 1600-char excerpt
        # Laya classifies; otherwise context-window fitting never triggered.
        request_tokens = self._estimate_tokens(
            (system_prompt or "") + prompt + (json.dumps(tools, default=str) if tools else "")
        )
        est_tokens = request_tokens + 1024 + (self._TOOL_RESULT_HEADROOM_TOKENS if tools else 0)
        model_router = get_model_router()
        selected_model = None
        if ctx.get("routed_provider"):
            provider = ctx["routed_provider"]
        else:
            provider, selected_model, _ = await model_router.get_model_route_for_text(
                self.name, f"{self.name}: {prompt[:1600]}", est_tokens=est_tokens
            )
        # Size the tool-result block to what the chosen model can hold.
        tool_context_chars = self._tool_context_budget(provider, selected_model, request_tokens)
        if selected_model:
            self.logger.info("laya_model_selected", agent=self.name, provider=provider, model=selected_model)
        is_local_model = provider in ["ollama", "lmstudio", "mistral"]

        gateway = get_llm_gateway()

        # For local models, inject ReAct instructions and disable native tools
        effective_tools = tools if (tools and not is_local_model) else None
        if is_local_model and tools:
            react_instructions = """
You have access to the following tools:
{}

To use a tool, you MUST output a JSON block wrapped in Markdown like this:
```json
{{
  "command": "tool_name",
  "args": {{"arg1": "value1"}}
}}
```
Do NOT wrap the JSON in any other formatting. Output only the JSON block to use a tool, or your final answer if no tools are needed.
""".format(
                json.dumps([t.get("function") for t in tools], indent=2)
            )
            system_prompt = (system_prompt or "") + "\n\n" + react_instructions

        # ── Multi-round tool calling loop (up to max_tool_rounds) ──
        accumulated_tool_results = []
        all_function_calls = []
        # Identical (tool, args) requests are answered from this memo instead
        # of re-executing, which models often do when they re-plan a round.
        call_memo: Dict[str, Dict[str, Any]] = {}
        current_prompt = prompt
        response = {}
        # True while the latest round executed tools the model has not yet seen.
        pending_tool_results = False

        for round_num in range(1, max_tool_rounds + 1):
            # Route through LLM Gateway (rate limit, cache, fallback)
            response = await gateway.call(
                provider=provider,
                prompt=current_prompt,
                system_prompt=system_prompt,
                tools=effective_tools,
                temperature=temperature,
                json_mode=json_mode,
                model=selected_model,
                allow_fallback=not bool(ctx.get("local_only")),
            )
            if response.get("error"):
                raise RuntimeError(
                    f"LLM provider call failed ({response['error']}): "
                    f"{response.get('content') or 'No usable response'}"
                )

            # Local models often represent ReAct calls as JSON text instead of
            # returning native function_calls.
            if is_local_model and not response.get("function_calls"):
                content = response.get("content", "")
                if '"command"' in content or "'command'" in content:
                    from app.core.json_helpers import extract_and_parse_json

                    parsed = extract_and_parse_json(content)
                    if isinstance(parsed, dict) and isinstance(parsed.get("command"), str):
                        args = parsed.get("args", {})
                        response["function_calls"] = [{
                            "name": parsed["command"],
                            "args": args if isinstance(args, dict) else {},
                        }]
                        response["content"] = ""

            # Check if tool calls were requested
            if not response.get("function_calls"):
                pending_tool_results = False
                break  # Model produced its final answer — exit loop

            self.logger.info(
                "Tool calls detected",
                round=round_num,
                calls=[c["name"] for c in response["function_calls"]],
            )

            calls = response["function_calls"]
            memo_keys = [self._tool_call_key(c) for c in calls]
            fresh, fresh_keys = [], []
            for call, key in zip(calls, memo_keys):
                if key not in call_memo and key not in fresh_keys:
                    fresh.append(call)
                    fresh_keys.append(key)
            fresh_results = await self.tools.execute_function_calls(
                fresh,
                allowed_tools=allowed_tool_names,
            ) if fresh else []
            for call, key, r in zip(fresh, fresh_keys, fresh_results):
                call_memo[key] = {
                    "name": call.get("name", ""),
                    "success": r.success,
                    "data": r.data,
                    "error": r.error,
                }
            round_results = []
            reported = set()
            for key in memo_keys:
                entry = dict(call_memo[key])
                if key not in fresh_keys or key in reported:
                    entry["note"] = "duplicate request; reused earlier result"
                reported.add(key)
                round_results.append(entry)
            accumulated_tool_results.extend(round_results)
            all_function_calls.extend(calls)
            pending_tool_results = True

            if not fresh:
                # The model only repeated earlier calls: it has all the data
                # it is going to get, so stop looping and synthesize.
                break

            # Build follow-up prompt with accumulated results
            tool_context = self._format_tool_results(accumulated_tool_results, tool_context_chars)
            current_prompt = (
                f"{prompt}\n\n--- TOOL EXECUTION RESULTS (Round {round_num}) ---\n"
                f"{tool_context}\n\n"
                f"Based on these results, either request additional tool calls if you need more data, "
                f"or provide your final comprehensive analysis in the requested JSON format."
            )

        # A final synthesis call is needed only when the loop stopped with
        # tool results the model has not answered yet (round budget exhausted
        # or duplicate-only round). When the model already returned a final
        # answer after seeing the results, a second call is pure waste.
        if accumulated_tool_results and pending_tool_results:
            tool_context = self._format_tool_results(accumulated_tool_results, tool_context_chars)
            final_prompt = (
                f"{prompt}\n\n--- ALL TOOL RESULTS ---\n{tool_context}\n\n"
                f"Based on all these results, provide your final comprehensive analysis "
                f"in the requested JSON format. Ensure your output is purely JSON."
            )
            final_response = await gateway.call(
                provider=provider,
                prompt=final_prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                json_mode=json_mode,
                model=selected_model,
                allow_fallback=not bool(ctx.get("local_only")),
            )
            if final_response.get("error"):
                raise RuntimeError(
                    f"LLM provider synthesis failed ({final_response['error']}): "
                    f"{final_response.get('content') or 'No usable response'}"
                )
            response["content"] = final_response.get("content", "")
            response["provider_used"] = final_response.get("provider_used", provider)
        if accumulated_tool_results:
            response["tool_results"] = accumulated_tool_results
            response["function_calls"] = all_function_calls
            # Alias kept for agents that read the older key.
            response["tool_calls"] = all_function_calls

        return response

    # Keys agents use when the model's output could not be parsed as JSON.
    _PARSE_FALLBACK_KEYS = frozenset({"raw_reasoning", "raw_synthesis", "raw_response", "raw"})

    @classmethod
    def _evidence_confidence(
        cls,
        prior: float,
        response: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        *,
        cap: float = 0.85,
    ) -> float:
        """Confidence from observable output signals instead of a fixed constant.

        Starts from the agent's prior (capped, so no LLM synthesis claims
        near-certainty by default) and discounts for: empty/error output,
        unstructured output (JSON parse fallback), failed tool calls, a failed
        deterministic validation, and analysis run with no retrieved evidence.
        The result is a heuristic, not a calibrated probability, and the
        output is marked that way (``confidence_basis``) so reports never
        render it as a percentage.
        """
        conf = min(float(prior), cap)
        signals: List[str] = []
        payload = data if isinstance(data, dict) else {}
        if not payload or payload.get("error"):
            conf *= 0.5
            signals.append("empty_or_error_output")
        elif cls._PARSE_FALLBACK_KEYS & set(payload) or payload.get("format") == "narrative":
            conf *= 0.6
            signals.append("unstructured_output")

        tool_results = (response or {}).get("tool_results") or []
        if tool_results:
            ok = sum(1 for t in tool_results if isinstance(t, dict) and t.get("success"))
            conf *= 0.6 + 0.4 * (ok / len(tool_results))
            if ok < len(tool_results):
                signals.append(f"tool_failures:{len(tool_results) - ok}/{len(tool_results)}")

        validation = payload.get("_validation")
        if isinstance(validation, dict) and validation.get("passed") is False:
            conf *= 0.6
            signals.append("validation_failed")

        rag = payload.get("_rag_context")
        if isinstance(rag, dict) and not rag.get("chunks_used"):
            conf *= 0.85
            signals.append("no_retrieved_evidence")

        if isinstance(data, dict):
            data.setdefault("confidence_basis", "heuristic_signals")
            data["confidence_signals"] = signals
        return round(max(0.0, min(conf, cap)), 3)

    @staticmethod
    def _tool_call_key(call: Dict[str, Any]) -> str:
        args = call.get("args", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (TypeError, ValueError):
                pass
        return json.dumps([call.get("name", ""), args], sort_keys=True, default=str)

    # Per-result and total character budgets (upper bounds) for tool output fed
    # back to the model; the effective total adapts to the model's window.
    _TOOL_RESULT_CHAR_LIMIT = 6000
    _TOOL_CONTEXT_CHAR_LIMIT = 24000
    _TOOL_CONTEXT_MIN_CHARS = 2000
    _TOOL_RESULT_HEADROOM_TOKENS = 4000
    _CHARS_PER_TOKEN = 3.5

    @classmethod
    def _estimate_tokens(cls, text: str) -> int:
        return max(1, int(len(text or "") / cls._CHARS_PER_TOKEN))

    _FILE_BUILDING_TOOLS = ("build_document", "generate_report", "generate_ic_memo",
                            "generate_deal_deck", "generate_meeting_memo")

    @classmethod
    def _deliverable_guidance(cls, tool_names: List[str]) -> str:
        """Tell the model that deliverables are files built by tools, not prose."""
        available = [name for name in cls._FILE_BUILDING_TOOLS if name in (tool_names or [])]
        if not available:
            return ""
        preferred = "build_document" if "build_document" in available else available[0]
        return (
            "DELIVERABLES: Any document, memo, report, spreadsheet or deck the user should "
            f"receive must be produced by calling a file-building tool ({', '.join(available)}); "
            f"prefer `{preferred}`. Pass the upstream agent results as evidence. Never present "
            "Markdown or prose as the deliverable file, and never claim a file exists unless a "
            "tool returned it."
        )

    @classmethod
    def _tool_context_budget(cls, provider: str, model: Optional[str], request_tokens: int) -> int:
        """Chars of tool results that fit beside the request in the model's window.

        A fixed 24K-char block is ~6.9K tokens, which on an 8K local model
        left no room for the prompt, so the gateway cut it blindly.
        """
        try:
            from app.config import get_settings
            from app.core.llm.model_registry import get_capabilities
            from app.core.llm.model_router import get_configured_model

            model_name = model or get_configured_model(provider, get_settings())
            window = get_capabilities(model_name, provider).context_window
        except Exception:
            return cls._TOOL_CONTEXT_CHAR_LIMIT
        reserve = max(int(window * 0.15), 1024)
        free_tokens = window - reserve - request_tokens
        budget = int(free_tokens * cls._CHARS_PER_TOKEN * 0.9)
        return max(cls._TOOL_CONTEXT_MIN_CHARS, min(cls._TOOL_CONTEXT_CHAR_LIMIT, budget))

    @classmethod
    def _format_tool_results(cls, results: List[Dict[str, Any]], total_limit: Optional[int] = None) -> str:
        total_limit = total_limit or cls._TOOL_CONTEXT_CHAR_LIMIT
        per_result = min(cls._TOOL_RESULT_CHAR_LIMIT, max(500, total_limit // max(1, min(len(results), 4))))
        rendered = []
        for item in results:
            text = json.dumps(item, default=str)
            if len(text) > per_result:
                text = text[:per_result] + f'... [truncated {len(text) - per_result} chars]'
            rendered.append(text)
        joined = "[\n" + ",\n".join(rendered) + "\n]"
        if len(joined) > total_limit:
            # Keep the most recent results: they reflect the model's latest plan.
            joined = "[... earlier tool results truncated ...]\n" + joined[-total_limit:]
        return joined

    # ═══════════════════════════════════════════════════════════
    #  Stage-Aware Prompt Injection (QA Flow 1 & 5)
    # ═══════════════════════════════════════════════════════════

    _STAGE_INSTRUCTIONS = {
        "screening": (
            "\n\n## Output Format (Screening Mode)\n"
            "Focus on go/no-go signals. Limit output to 1-page equivalent. "
            "Key metrics + 3 bullet risks + recommendation signal."
        ),
        "deep_dive": (
            "\n\n## Output Format (Deep-Dive Mode)\n"
            "Provide exhaustive evidence-backed analysis. Include data tables, "
            "sourced claims, risk matrix. Every quantitative claim must cite "
            "[Source: tool_name, date]."
        ),
        "ic_memo": (
            "\n\n## Output Format (IC Memo Mode)\n"
            "Structure using Pyramid Principle: lead with recommendation, then "
            "supporting arguments, then evidence. Use SCQA framework for "
            "executive summary (Situation → Complication → Question → Answer)."
        ),
    }

    _CITATION_DISCIPLINE = (
        "\n\n## Citation Discipline\n"
        "Every quantitative claim must be followed by [Source: tool_name, date]. "
        "If data is not available from tools, explicitly mark as [ESTIMATED] "
        "and state the estimation methodology."
    )

    def build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Build system prompt with deal-stage-aware formatting instructions."""
        base = f"""You are {self.name}, {self.description}.

Your task is to provide thorough, well-reasoned analysis with specific data points and actionable recommendations.

Evidence and decision discipline:
- Distinguish user-provided inputs, tool-sourced facts, deterministic calculations, and estimates.
- Treat missing, null, or failed tool data as unknown; never convert it to zero or fill it with a plausible value.
- Cite the tool/source and relevant period for externally sourced quantitative claims. Do not claim a lookup occurred unless a tool result is present.
- Preserve conflicting source values and describe the discrepancy; do not silently overwrite user inputs.
- State concise conclusions and material assumptions, not private chain-of-thought.
- Provide actionable recommendations proportional to the evidence, and state when evidence is insufficient.
- Format output as structured JSON when requested; represent unavailable numeric fields as null.
"""
        if not context:
            return base + self._CITATION_DISCIPLINE

        # Inject buyer thesis and deal goal
        thesis = context.get("buyer_thesis")
        goal = context.get("deal_goal")
        if thesis:
            base += f"\n\n## Investment Thesis\n{thesis}"
        if goal:
            base += f"\n\n## Deal Goal\n{goal}"

        # Inject stage-specific formatting
        stage = context.get("deal_stage", "deep_dive")
        stage_instruction = self._STAGE_INSTRUCTIONS.get(
            stage, self._STAGE_INSTRUCTIONS["deep_dive"]
        )
        base += stage_instruction

        # Always append citation discipline
        base += self._CITATION_DISCIPLINE

        return base

    def validate_output(
        self, output: Dict[str, Any], expected_schema: Optional[Dict] = None
    ) -> tuple[bool, Optional[str]]:
        """Validate agent output against expected schema"""
        if not expected_schema:
            return True, None

        required_fields = expected_schema.get("required", [])
        missing = [f for f in required_fields if f not in output]

        if missing:
            return False, f"Missing required fields: {missing}"

        return True, None

    async def reflect(
        self, task: str, output: AgentOutput, expected_schema: Optional[Dict] = None
    ) -> float:
        """Run reflection on agent output"""
        reflection_result = await self.reflection.evaluate(
            task=task, agent_output=output.data, expected_format=expected_schema
        )

        self.logger.info(
            "Reflection complete",
            score=reflection_result.score,
            grade=reflection_result.grade.value,
        )

        return reflection_result.score

    # ===== MECE Issue Tree Methods =====

    async def evaluate_replication(
        self, task: str, context: Optional[Dict] = None, runs: int = 3
    ) -> Dict[str, float]:
        """Execute the same task multiple times and return consistency metrics.

        This utility is intended for quality assessments and can optionally log
        the results to the AgentQualityStore. By default the underlying LLM is
        invoked with temperature=0 (deterministic), but stochastic models will
        still vary, allowing us to quantify variance.
        """
        from app.core.quality.replication import evaluate_outputs
        from app.core.quality.agent_quality_store import AgentQualityStore

        outputs: list[str] = []
        for _ in range(runs):
            result = await self.run(task, context)
            outputs.append(str(result.data or result.reasoning or result))

        stats = evaluate_outputs(outputs)

        # log into quality store for later inspection
        try:
            store = AgentQualityStore()
            await store.initialize()
            await store.log_replication_run(self.name, task, outputs)
        except Exception:
            # best-effort, do not fail entire evaluation
            self.logger.warning("replication_logging_failed")

        return stats

    async def generate_issue_tree(
        self, task: str, context: Optional[Dict] = None
    ) -> IssueTreeNode:
        """
        Generate a MECE (Mutually Exclusive, Collectively Exhaustive) issue tree.
        Every analysis starts with a hypothesis and branches into sub-questions.
        """
        tree_prompt = f"""You are generating a MECE issue tree for the following analysis task.

Task: {task}
Context: {json.dumps(context or {}, indent=2, default=str)}

Generate a hypothesis-first issue tree with 3-6 MECE branches. Each branch must be:
1. Mutually Exclusive: No overlap between branches
2. Collectively Exhaustive: Together they cover the entire problem space

Respond with JSON:
{{
    "hypothesis": "Main hypothesis statement",
    "branches": [
        {{
            "id": "branch_1",
            "hypothesis": "Sub-hypothesis",
            "key_questions": ["What data is needed?", "What metrics matter?"]
        }}
    ]
}}"""

        try:
            router = get_model_router()
            provider = (context or {}).get("routed_provider") or router.get_provider_for_agent(self.name)

            response = await get_llm_gateway().call(
                provider=provider,
                prompt=tree_prompt,
                system_prompt="You are a McKinsey-trained structured problem solver. Return only valid JSON.",
                temperature=0.0,
                allow_fallback=not bool((context or {}).get("local_only")),
            )
            tree_data = json.loads(
                response["content"].strip().strip("```json").strip("```").strip()
            )

            root = IssueTreeNode(
                id="root",
                hypothesis=tree_data.get("hypothesis", task),
            )
            for branch in tree_data.get("branches", []):
                root.sub_branches.append(
                    IssueTreeNode(
                        id=branch.get("id", f"branch_{len(root.sub_branches)}"),
                        hypothesis=branch.get("hypothesis", ""),
                        evidence=branch.get("key_questions", []),
                    )
                )
            return root

        except Exception as e:
            self.logger.error(
                "Issue tree generation failed, using fallback", error=str(e)
            )
            return IssueTreeNode(
                id="root",
                hypothesis=task,
                sub_branches=[
                    IssueTreeNode(
                        id="financial", hypothesis="Financial viability and valuation"
                    ),
                    IssueTreeNode(
                        id="strategic", hypothesis="Strategic fit and synergies"
                    ),
                    IssueTreeNode(id="risk", hypothesis="Risk factors and mitigation"),
                    IssueTreeNode(
                        id="operational",
                        hypothesis="Operational integration feasibility",
                    ),
                ],
            )

    async def run_iterative(
        self,
        task: str,
        context: Dict[str, Any],
        max_iterations: int = 3,
        gap_detection_prompt: Optional[str] = None,
    ) -> AgentOutput:
        """
        Iterative research (IterDRAG): task → findings → gap detection → re-retrieval → synthesis.
        Prevents context bloat via running summaries.
        """
        findings = []
        current_context = dict(context)
        final_reasoning = ""
        
        for iteration in range(max_iterations):
            self.logger.info("iterdrag_iteration_start", iteration=iteration+1, max=max_iterations)
            
            # Step 1: Run standard analysis
            result = await self.run(task, current_context)
            if not result.success:
                return result
                
            findings.append(result.data)
            final_reasoning = result.reasoning
            
            # Step 2: Stop early if final iteration or if no gaps detected
            if iteration == max_iterations - 1:
                break
                
            # Step 3: Detect research gaps
            gaps = await self._detect_research_gaps(task, result.data, gap_detection_prompt)
            if not gaps:
                self.logger.info("no_research_gaps_detected", iteration=iteration+1)
                break
                
            self.logger.info("research_gaps_identified", count=len(gaps))
            
            # Step 4: Maintain running summary (F-013) to prevent context explosion
            summary = await self._maintain_running_summary(iteration + 1, result.data)
            
            # Step 5: Update context for next iteration
            current_context["running_summary"] = summary
            current_context["retrieval_hint"] = "Focus on these unanswered gaps: " + "; ".join(gaps)
            
            # Trigger fresh retrieval based on hints? 
            # In simple impl, we just pass the hint to the next run.
            
        # Consolidate final output
        return AgentOutput(
            success=True,
            data={
                "iterations": len(findings),
                "all_findings": findings,
                "final_synthesis": findings[-1] if findings else {},
            },
            reasoning=final_reasoning,
            confidence=sum(f.get("confidence", 0.7) for f in findings) / len(findings) if findings else 0.0
        )

    async def _detect_research_gaps(self, task: str, findings: Dict, prompt: Optional[str] = None) -> List[str]:
        """Use LLM to identify unanswered questions or missing data in current findings (F-012)."""
        gap_prompt = prompt or f"""
        Original Task: {task}
        Current Findings: {json.dumps(findings, indent=2)}
        
        Identify the top 3 critical data points or questions that remain unanswered and are essential for a professional M&A analysis.
        Return ONLY a JSON array of strings. If no gaps exist, return [].
        """
        
        try:
            gateway = get_llm_gateway()
            # Fast model for gap detection
            response = await gateway.call(
                provider="gemini",
                model="gemini-3.8-flash",
                prompt=gap_prompt,
                temperature=0.0,
                max_tokens=256
            )
            content = response.get("content", "[]")
            clean_json = content.strip().strip("```json").strip("```").strip()
            gaps = json.loads(clean_json)
            return gaps[:3] if isinstance(gaps, list) else []
        except Exception as e:
            self.logger.warning("gap_detection_failed", error=str(e))
            return []

    async def _maintain_running_summary(self, iteration: int, findings: Dict) -> str:
        """Compress prior findings into a concise overview to keep context window clean (F-013)."""
        summary_prompt = f"""
        Iteration {iteration} Findings: {json.dumps(findings, indent=2)}
        
        Summarize the key facts, risks, and conclusions above in 3-5 high-density sentences.
        Maintain all specific numbers and dollar values.
        """
        
        try:
            gateway = get_llm_gateway()
            response = await gateway.call(
                provider="gemini",
                model="gemini-3.8-flash",
                prompt=summary_prompt,
                temperature=0.0,
                max_tokens=300
            )
            return response.get("content", "").strip()
        except Exception as e:
            self.logger.warning("summary_maintenance_failed", error=str(e))
            return "Summary unavailable."

    def validate_mece(self, tree: IssueTreeNode) -> tuple[bool, List[str]]:
        """Validate if the issue tree is MECE (F-015: Loop-back triggers)"""
        required_dimensions = {
            "financial": False,
            "strategic": False,
            "risk": False,
            "operational": False,
        }

        branch_texts = [b.hypothesis.lower() for b in tree.sub_branches]

        financial_keywords = [
            "financial",
            "valuation",
            "revenue",
            "cash flow",
            "dcf",
            "ebitda",
            "margin",
        ]
        strategic_keywords = ["strategic", "synergy", "market", "competitive", "growth"]
        risk_keywords = ["risk", "threat", "regulatory", "compliance", "legal"]
        operational_keywords = [
            "operational",
            "integration",
            "execution",
            "technology",
            "team",
        ]

        for text in branch_texts:
            if any(kw in text for kw in financial_keywords):
                required_dimensions["financial"] = True
            if any(kw in text for kw in strategic_keywords):
                required_dimensions["strategic"] = True
            if any(kw in text for kw in risk_keywords):
                required_dimensions["risk"] = True
            if any(kw in text for kw in operational_keywords):
                required_dimensions["operational"] = True

        gaps = [dim for dim, covered in required_dimensions.items() if not covered]
        return len(gaps) == 0, gaps

    def _serialize_tree(self, node: IssueTreeNode) -> Dict:
        """Serialize an IssueTreeNode to a dictionary."""
        return {
            "id": node.id,
            "hypothesis": node.hypothesis,
            "status": node.status,
            "confidence": node.confidence,
            "evidence": node.evidence,
            "sub_branches": [self._serialize_tree(b) for b in node.sub_branches],
        }

    async def store_to_memory(
        self, content: str, tags: Optional[List[str]] = None
    ) -> None:
        """
        Store learnings/insights to the MemoryEntry table for cross-deal intelligence.
        """
        try:
            await self.memory.ingest_text(
                content=content,
                metadata={
                    "agent_type": self.name,
                    "tags": tags or [],
                    "timestamp": datetime.now().isoformat(),
                },
            )
            self.logger.info("Memory stored", agent=self.name, tags=tags)
        except Exception as e:
            self.logger.warning("Failed to store memory", error=str(e))


from app.agents.compiler_agent import ReportCompilerAgent


class AgentRegistry:
    """Registry for managing agents"""

    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.logger = structlog.get_logger()

    def register(self, agent: BaseAgent):
        """Register an agent"""
        self.agents[agent.name] = agent
        self.logger.info("Agent registered", agent_name=agent.name)

    def get(self, name: str) -> Optional[BaseAgent]:
        """Get an agent by name"""
        return self.agents.get(name)

    def list_agents(self) -> List[str]:
        """List registered agent names"""
        return list(self.agents.keys())


# Singleton registry instance
_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    """Get the singleton agent registry"""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()

        # Import all agents here to avoid circular dependencies
        from app.agents.project_manager import ProjectManagerAgent
        from app.agents.market_researcher import (
            MarketResearcherAgent,
            DebateModeratorAgent,
            ScoringAgent,
        )
        from app.agents.financial_analyst import FinancialAnalystAgent, ValuationAgent
        from app.agents.risk_assessor import RiskAssessorAgent, MarketRiskAgent
        from app.agents.legal_advisor import LegalAdvisorAgent, ComplianceAgent
        from app.agents.dcf_lbo_architect import DCFLBOArchitectAgent
        from app.agents.red_team_agent import RedTeamAgent
        from app.agents.business_analyst import BusinessAnalystAgent

        # Extended agents
        from app.agents.due_diligence_agent import CommercialDueDiligenceAgent
        from app.agents.integration_planner_agent import IntegrationPlannerAgent

        # Removed missing or re-mapped agents
        from app.agents.esg_agent import ESGAgent
        from app.agents.prospectus_agent import ProspectusProcessingAgent
        from app.agents.treasury_agent import (
            TaxComplianceAgent,
            TreasuryCashAgent,
            FPAForecastingAgent,
        )
        from app.agents.ofas_supervisor import OFASSupervisorAgent
        from app.agents.ai_tech_diligence_agent import AITechDiligenceAgent
        from app.agents.compliance_qa_agent import ComplianceQAAgent
        from app.agents.investment_memo_agent import InvestmentMemoAgent
        from app.agents.compiler_agent import ReportCompilerAgent

        # New advanced agents
        from app.agents.advanced_financial_modeler import AdvancedFinancialModelerAgent
        from app.agents.data_curator_agent import DataCuratorAgent
        from app.agents.complex_reasoning_agent import ComplexReasoningAgent
        from app.agents.report_architect_agent import ReportArchitectAgent

        # Register core agents
        _registry.register(ProjectManagerAgent())
        _registry.register(DebateModeratorAgent())
        _registry.register(ScoringAgent())
        _registry.register(FinancialAnalystAgent())
        _registry.register(MarketResearcherAgent())
        _registry.register(LegalAdvisorAgent())
        _registry.register(RiskAssessorAgent())
        _registry.register(MarketRiskAgent())
        _registry.register(ComplianceAgent())
        _registry.register(DCFLBOArchitectAgent())

        # Register extended agents
        _registry.register(ValuationAgent())
        _registry.register(CommercialDueDiligenceAgent())
        _registry.register(IntegrationPlannerAgent())
        _registry.register(ESGAgent())
        _registry.register(ProspectusProcessingAgent())
        _registry.register(TaxComplianceAgent())
        _registry.register(TreasuryCashAgent())
        _registry.register(AITechDiligenceAgent())
        _registry.register(OFASSupervisorAgent())
        _registry.register(ComplianceQAAgent())
        _registry.register(InvestmentMemoAgent())
        _registry.register(ReportCompilerAgent())

        # Register advanced agents
        _registry.register(AdvancedFinancialModelerAgent())
        _registry.register(DataCuratorAgent())
        _registry.register(ComplexReasoningAgent())
        _registry.register(ReportArchitectAgent())

        # Register previously missing agents
        _registry.register(RedTeamAgent())
        _registry.register(BusinessAnalystAgent())
        _registry.register(FPAForecastingAgent())

    return _registry
