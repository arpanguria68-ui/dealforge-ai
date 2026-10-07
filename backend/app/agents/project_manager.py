"""
Project Manager / Scrum Master Agent — Reasoning-First Edition

3-Phase Workflow:
  Phase 1 — Data Requirements: Identify what data/files are needed. Check MCP
             for auto-fetchable items. Ask user only for what can't be self-fetched.
  Phase 2 — Clarifying Questions: Socratic questioning with visible reasoning
             before building the plan. Surface potential blockers upfront.
  Phase 3 — MECE Plan with Risk Flags: Structured task list, risks per task,
             always awaits user approval before any execution.
"""

from typing import Dict, Any, Optional, List
import json
import re
from datetime import datetime

from app.agents.base import BaseAgent, AgentOutput
from app.core.tasks.task_manager import get_task_manager, AGENT_CAPABILITIES

# Default task templates for common deal analysis scenarios
DEAL_ANALYSIS_TEMPLATE = [
    {
        "title": "Financial Statement Analysis",
        "description": "Analyze historical income statement, balance sheet, and cash flow for the target. Calculate key ratios (margins, leverage, coverage). Identify trends and red flags.",
        "assigned_agent": "financial_analyst",
        "priority": "critical",
    },
    {
        "title": "Company & Market Research",
        "description": "Research the target company background, industry dynamics, TAM/SAM/SOM, and competitive landscape. Identify key competitors and market trends.",
        "assigned_agent": "market_researcher",
        "priority": "high",
    },
    {
        "title": "DCF Valuation Model",
        "description": "Build a Discounted Cash Flow model for the target with 5-year projected free cash flows, WACC calculation, and terminal value. Apply mid-year convention.",
        "assigned_agent": "valuation_agent",
        "priority": "critical",
    },
    {
        "title": "Comparable Companies Analysis",
        "description": "Identify peer companies and calculate trading multiples (EV/EBITDA, EV/Revenue, P/E). Derive implied valuation range for the target.",
        "assigned_agent": "valuation_agent",
        "priority": "high",
    },
    {
        "title": "LBO Returns Analysis",
        "description": "Model leveraged buyout scenario for the target with debt waterfall, cash flow sweep, and equity returns (IRR, MOIC) at various exit multiples.",
        "assigned_agent": "dcf_lbo_architect",
        "priority": "high",
    },
    {
        "title": "Advanced Financial Modeling",
        "description": "Build rigorous 3-statement models, downside scenarios, and Monte Carlo simulations for the target.",
        "assigned_agent": "advanced_financial_modeler",
        "priority": "high",
    },
    {
        "title": "Risk Assessment & Sensitivity Analysis",
        "description": "Identify key risks for the target (market, operational, financial, regulatory). Run sensitivity analysis on critical assumptions. Build risk heatmap.",
        "assigned_agent": "risk_assessor",
        "priority": "high",
    },
    {
        "title": "Legal & Regulatory Review",
        "description": "Review legal structure, pending litigation, regulatory requirements for the target. Flag material legal risks and compliance gaps.",
        "assigned_agent": "legal_advisor",
        "priority": "medium",
    },
    {
        "title": "Commercial Due Diligence",
        "description": "Assess business model sustainability, customer concentration, revenue quality, and growth drivers for the target. Validate management projections.",
        "assigned_agent": "due_diligence_agent",
        "priority": "medium",
    },
    {
        "title": "Data Curation & Synthesis",
        "description": "Synthesize data from financial, legal, and risk agents for the target. Resolve any conflicting data points and query PageIndex for missing sector context.",
        "assigned_agent": "data_curator",
        "priority": "high",
    },
    {
        "title": "Complex Reasoning & Strategy",
        "description": "Apply Chain-of-Thought reasoning to the curated data bible for the target. Formulate strategic insights and deeper transaction rationale.",
        "assigned_agent": "complex_reasoning",
        "priority": "high",
    },
    {
        "title": "Bull vs Bear Debate",
        "description": "Present and debate bull case and bear case arguments for the investment in the target. Stress-test the investment thesis from both sides.",
        "assigned_agent": "debate_moderator",
        "priority": "medium",
    },
    {
        "title": "Final Scoring & Recommendation",
        "description": "Aggregate all analysis for the target into final deal score (1-10). Provide clear BUY/PASS/CONDITIONAL recommendation with key conditions.",
        "assigned_agent": "scoring_agent",
        "priority": "critical",
        "depends_on": [],
    },
    {
        "title": "Report Architecture & Theming",
        "description": "Select the appropriate report template, configure branding settings, and layout the final document structure.",
        "assigned_agent": "report_architect",
        "priority": "critical",
        "depends_on": [],
    },
    {
        "title": "Investment Memo & Report Generation",
        "description": "Compile all findings into McKinsey-style investment memo with executive summary, charts (football field, risk heatmap, radar), and appendices.",
        "assigned_agent": "investment_memo_agent",
        "priority": "high",
        "depends_on": [],
    },
]

SPEED_MODE_AGENTS = {"financial_analyst", "risk_assessor", "market_researcher"}


def wire_stage_dependencies(items: List[Any]) -> None:
    """Turn the approved task plan into an analysis-to-synthesis dependency DAG."""
    ids_by_agent: Dict[str, List[str]] = {}
    by_id = {item.id: item for item in items if getattr(item, "id", None)}
    for item in items:
        agent = str(getattr(item, "assigned_agent", "")).casefold()
        if getattr(item, "id", None):
            ids_by_agent.setdefault(agent, []).append(item.id)

    curator_agent_ids = ids_by_agent.get("data_curator", []) + ids_by_agent.get("data_curator_agent", [])
    # Data-curator tasks can either gather source inputs or synthesize completed
    # analyses. Only the latter belong at the end of the dependency graph.
    collection_terms = ("collect", "collection", "gather", "fetch", "retrieve", "ingest")
    source_terms = ("financial data", "financial statement", "financial records", "source data")
    data_collection_ids = []
    for item in items:
        title = str(getattr(item, "title", "")).casefold()
        description = str(getattr(item, "description", "")).casefold()
        task_text = f"{title} {description}"
        if any(term in task_text for term in collection_terms) and any(term in task_text for term in source_terms):
            data_collection_ids.append(item.id)
    curator = [item_id for item_id in curator_agent_ids if item_id not in data_collection_ids]
    reasoning = ids_by_agent.get("complex_reasoning", []) + ids_by_agent.get("complex_reasoning_agent", [])
    debate = ids_by_agent.get("debate_moderator", [])
    scoring = ids_by_agent.get("scoring_agent", [])
    architects = ids_by_agent.get("report_architect", []) + ids_by_agent.get("report_compiler", [])
    memos = ids_by_agent.get("investment_memo_agent", [])
    modeling_agents = {
        "valuation_agent", "dcf_lbo_architect", "advanced_financial_modeler",
        "due_diligence_agent", "treasury_cash",
    }
    modeling_ids = [item.id for item in items if item.id and str(item.assigned_agent).casefold() in modeling_agents]
    synthesis_ids = set(curator + reasoning + debate + scoring + architects + memos)
    foundation_ids = [item.id for item in items if item.id and item.id not in synthesis_ids and item.id not in modeling_ids]
    analysis_ids = foundation_ids + modeling_ids

    for item in items:
        agent = str(getattr(item, "assigned_agent", "")).casefold()
        if item.id in data_collection_ids:
            required = list(getattr(item, "depends_on", []) or [])
        elif agent in {"data_curator", "data_curator_agent"}:
            required = analysis_ids
        elif agent in {"complex_reasoning", "complex_reasoning_agent"}:
            required = analysis_ids + curator
        elif agent == "debate_moderator":
            required = analysis_ids + curator + reasoning
        elif agent == "scoring_agent":
            required = analysis_ids + curator + reasoning + debate
        elif agent in {"report_architect", "report_compiler"}:
            required = analysis_ids + curator + reasoning + debate + scoring
        elif agent == "investment_memo_agent":
            required = [candidate for candidate in by_id if candidate != item.id]
        elif item.id in modeling_ids:
            required = data_collection_ids or foundation_ids
        else:
            required = list(getattr(item, "depends_on", []) or [])
        item.depends_on = list(dict.fromkeys(
            dependency for dependency in required if dependency in by_id and dependency != item.id
        ))


def select_tasks_for_focus_mode(tasks: List[Dict], focus_mode: str) -> List[Dict]:
    """Apply UI focus limits before a task list is persisted for execution."""
    if focus_mode == "speed":
        selected = [
            task for task in tasks
            if task.get("assigned_agent") in SPEED_MODE_AGENTS
            or task.get("priority") == "critical"
        ][:3]
        return selected or tasks[:1]
    if focus_mode == "balanced":
        selected = [
            task for task in tasks
            if task.get("priority") in ("critical", "high")
        ][:12]
        return selected or tasks[:1]
    return tasks

DATA_REQUIREMENTS_BY_TASK_TYPE = {
    "deal_analysis": [
        {
            "item": "Target company financial statements (3 years)",
            "source": "user_or_mcp",
            "mcp_capability": "financial_statements",
        },
        {
            "item": "Target company pitch deck / CIM",
            "source": "user_only",
            "mcp_capability": None,
        },
        {
            "item": "Term sheet or LOI (if available)",
            "source": "user_only",
            "mcp_capability": None,
        },
        {
            "item": "Stock price & market data",
            "source": "mcp_auto",
            "mcp_capability": "stock_price",
        },
        {
            "item": "Comparable company multiples",
            "source": "mcp_auto",
            "mcp_capability": "sector_metrics",
        },
        {
            "item": "Recent company news & sentiment",
            "source": "mcp_auto",
            "mcp_capability": "company_news",
        },
        {
            "item": "Analyst ratings & price targets",
            "source": "mcp_auto",
            "mcp_capability": "analyst_ratings",
        },
        {
            "item": "Management team background",
            "source": "user_or_mcp",
            "mcp_capability": "leadership_data",
        },
    ],
    "general": [
        {
            "item": "Deal overview or brief",
            "source": "user_only",
            "mcp_capability": None,
        },
        {
            "item": "Target company name & ticker (if public)",
            "source": "user_only",
            "mcp_capability": None,
        },
    ],
}


class ProjectManagerAgent(BaseAgent):
    """
    Scrum Master / Project Manager agent with 3-phase reasoning workflow:
    1. Identify data/file requirements — ask user, offer MCP auto-fetch for gaps
    2. Ask clarifying questions with visible reasoning before planning
    3. Generate MECE task plan with per-task risk flags — always await user approval
    """

    name = "project_manager"
    description: str = "Reasoning-first PM — identifies data needs, asks smart questions, creates structured task plans with risk flags before executing anything"
    recommended_model: str = "Gemini 1.5 Pro (Complex Reasoning)"

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 1 + 2 entry point
    # ─────────────────────────────────────────────────────────────────────────

    async def generate_clarifying_questions(
        self,
        task: str,
        context: Optional[Dict] = None,
        user_skipped: bool = False,
        skipped_questions: List[Dict] = None,
        clarification_round: int = 0,
        max_rounds: int = 3,
    ) -> Dict[str, Any]:
        """
        Combined Phase 1 (data requirements) + Phase 2 (clarifying questions).
        Returns a structured response that the UI renders as an interactive checklist.
        """
        context = context or {}
        if self._is_focused_request(task):
            return {
                "phase": "clarification",
                "task_summary": task[:200],
                "data_requirements": {
                    "auto_fetchable": [],
                    "user_required": [],
                    "optional": [],
                },
                "clarifying_questions": [],
                "qa_controls": {
                    "current_round": clarification_round,
                    "max_rounds": max_rounds,
                    "can_skip": False,
                    "can_ask_more": False,
                },
            }

        # ── User-controlled QA depth ──
        # Use explicit args if provided, otherwise fallback to context
        max_rounds = max_rounds or context.get("max_clarification_rounds", 3)
        clarification_round = clarification_round or context.get(
            "clarification_round", 0
        )
        user_skipped = user_skipped or context.get("user_skipped_clarification", False)
        skipped_questions = skipped_questions or context.get("skipped_questions", [])

        if clarification_round >= max_rounds or user_skipped:
            # Self-research skipped questions before proceeding
            skipped_questions = context.get("skipped_questions", [])
            research_results = []
            if skipped_questions:
                research_results = await self._self_research_skipped(
                    task, skipped_questions, context
                )

            return {
                "phase": "clarification",
                "clarifying_questions": [],
                "skip_reason": (
                    f"Completed {clarification_round} round(s). Proceeding to planning."
                    if not user_skipped
                    else "User skipped remaining questions. Self-researching gaps."
                ),
                "self_researched": research_results,
                "assumptions_summary": await self._generate_assumptions_summary(
                    task, skipped_questions, research_results, context
                ),
            }

        available_mcp_tools = context.get("available_mcp_tools", [])

        # Determine which data requirements can be auto-fetched vs user-provided
        data_reqs = self._assess_data_requirements(task, [], available_mcp_tools)

        # Use LLM for high-quality clarifying questions if available
        if getattr(self, "llm", None):
            questions = await self._llm_clarifying_questions(task, context, data_reqs)
        else:
            questions = self._fallback_clarifying_questions(task, data_reqs)

        return {
            "phase": "clarification",
            "task_summary": task[:200],
            "data_requirements": {
                "auto_fetchable": data_reqs["auto"],
                "user_required": data_reqs["user_required"],
                "optional": data_reqs["optional"],
            },
            "mcp_note": (
                (
                    f"{len(available_mcp_tools)} live, read-only MCP tool(s) were discovered. "
                    "Matching data can be researched by the assigned specialist; verify the returned sources and period. "
                    "Discovery does not guarantee that a query will succeed."
                )
                if available_mcp_tools
                else (
                    "No live, read-only MCP tools were discovered. Provide required data or use the agent's available local/web tools."
                )
            ),
            "clarifying_questions": questions,
            "instruction": (
                "Please answer the questions above and confirm which files/data you can provide. "
                "Then call /api/v1/scrum/plan with your answers to get the full task plan.\n\n"
                "**Controls:** Reply with your answers to proceed, type `skip` to skip remaining "
                "questions (I'll self-research the gaps), or type `more` to request additional questions."
            ),
            "qa_controls": {
                "current_round": clarification_round + 1,
                "max_rounds": max_rounds,
                "can_skip": True,
                "can_ask_more": clarification_round + 1 < max_rounds,
            },
        }

    def _assess_data_requirements(
        self, task: str, mcp_ids: List[str], available_mcp_tools: List[Dict] = None
    ) -> Dict[str, List[Dict]]:
        """Classify required data into: auto_fetchable (MCP), user_required, optional."""
        task_lower = task.lower()
        available_mcp_tools = available_mcp_tools or []

        def matching_tools(capability: Optional[str]) -> List[Dict]:
            if not capability:
                return []
            terms = capability.lower().replace("_", " ").split()
            aliases = {
                "financial": ("financial", "finance"),
                "statements": ("statement", "statements", "financials"),
                "price": ("price", "quote", "stock"),
                "company": ("company", "issuer", "business"),
            }
            def matches(tool: Dict) -> bool:
                text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
                return all(
                    any(alias in text for alias in aliases.get(term, (term,)))
                    for term in terms
                )
            return [tool for tool in available_mcp_tools if matches(tool)]

        # Determine task type
        is_deal = any(
            kw in task_lower
            for kw in [
                "deal",
                "acquisition",
                "m&a",
                "invest",
                "due diligence",
                "valuation",
                "buyout",
                "lbo",
                "dcf",
                "company",
                "target",
            ]
        )
        reqs = DATA_REQUIREMENTS_BY_TASK_TYPE.get(
            "deal_analysis" if is_deal else "general"
        )

        auto = []
        user_required = []
        optional = []

        for req in reqs:
            source = req["source"]
            mcp_cap = req.get("mcp_capability")

            if source == "mcp_auto":
                # API keys are not evidence of MCP tool availability; require live discovery.
                can_fetch = bool(matching_tools(mcp_cap))
                if can_fetch:
                    auto.append(
                        {
                            "item": req["item"],
                            "fetched_by": (
                                matching_tools(mcp_cap)[0]["name"]
                            ),
                        }
                    )
                else:
                    optional.append(
                        {
                            "item": req["item"],
                            "note": "No matching live MCP tool was discovered; provide manually or use available research tools.",
                        }
                    )
            elif source == "user_or_mcp":
                can_fetch = bool(matching_tools(mcp_cap))
                if can_fetch:
                    auto.append(
                        {
                            "item": req["item"],
                            "fetched_by": (
                                matching_tools(mcp_cap)[0]["name"]
                            ),
                            "note": "A matching tool is available; successful retrieval and source validation are still required.",
                        }
                    )
                else:
                    user_required.append(
                        {
                            "item": req["item"],
                            "note": "Please provide this — MCP not configured for auto-fetch.",
                        }
                    )
            else:  # user_only
                user_required.append({"item": req["item"]})

        return {"auto": auto, "user_required": user_required, "optional": optional}

    async def _llm_clarifying_questions(
        self, task: str, context: Dict, data_reqs: Dict
    ) -> List[Dict]:
        """
        Use LLM to generate high-quality Socratic questions with reasoning.
        Injects prior memory context (Tier 2) and RL quality hints (Tier 3)
        to reduce noise and improve question selection.
        """
        from app.core.memory.clarification_memory import ClarificationMemory
        from app.core.memory.question_quality_store import QuestionQualityStore

        memory = ClarificationMemory()
        quality = QuestionQualityStore()
        deal_type = memory.detect_deal_type(task)

        # ── Tier 2: inject prior context so we don't re-ask settled questions ──
        prior_context = memory.get_context_for_prompt(deal_type, task)

        # ── Tier 3: inject historically high-quality question types ──
        quality_hints = quality.get_summary_for_prompt(deal_type)

        mcp_tools = context.get("available_mcp_tools", [])
        mcp_desc = json.dumps(mcp_tools) if mcp_tools else "None discovered"

        auto_items = [r["item"] for r in data_reqs["auto"]]
        user_items = [r["item"] for r in data_reqs["user_required"]]

        prompt = f"""You are a Senior Scrum Master and Investment Banking PM. A user has asked you to help with this task:

TASK: {task}

{prior_context}{quality_hints}
CONTEXT:
- Live read-only MCP tools (agent-scoped): {mcp_desc}
- Data I can auto-fetch: {json.dumps(auto_items)}
- Data user must provide: {json.dumps(user_items)}
- Agent capabilities: {list(AGENT_CAPABILITIES.keys())}

Before building a task plan, identify the 1-3 most critical unknowns that would materially change the plan design.
Do NOT ask about anything already covered in PRIOR CONTEXT above.

Return a JSON array of EXACTLY 1-3 question objects (NOT more than 3). Each must have:
- "question": The specific question to ask (1-2 sentences, crisp and focused)
- "reasoning": Why you are asking this (start with "I'm asking because...")
- "type": "data_availability" | "scope" | "constraint" | "risk" | "objective"
- "options": Optional list of 2-4 answer choices if applicable
- "potential_issue": A specific blocker this question helps surface (1 sentence)

CRITICAL: Return MAXIMUM 3 questions. Prefer 1-2 if context is already sufficient.
Return ONLY the JSON array, no other text."""

        try:
            from app.core.llm.model_router import get_model_router

            llm = self.llm or get_model_router().get_client_for_agent(self.name)
            response = await llm.generate(
                prompt=prompt,
                system_prompt="You are a senior Scrum Master and investment banking PM. Return ONLY valid JSON arrays. Maximum 3 questions.",
                temperature=0.3,
            )
            content = response.get("content", "[]")
            from app.core.json_helpers import extract_and_parse_json

            questions = extract_and_parse_json(content)

            # Handle cases where local LLMs wrap the array in a dict (e.g., {"questions": [...]})
            if isinstance(questions, dict):
                for key, value in questions.items():
                    if isinstance(value, list) and len(value) > 0:
                        questions = value
                        break

            if isinstance(questions, list) and len(questions) > 0:
                # Tier 1 hard cap: never return more than 3 questions
                return questions[:3]
        except Exception as e:
            self.logger.warning("llm_clarification_failed", error=str(e))

        return self._fallback_clarifying_questions(task, data_reqs)

    def _fallback_clarifying_questions(self, task: str, data_reqs: Dict) -> List[Dict]:
        """Deterministic fallback questions when LLM is unavailable. Max 3 total."""
        questions = []

        if data_reqs["user_required"]:
            items_str = ", ".join([r["item"] for r in data_reqs["user_required"]])
            questions.append(
                {
                    "question": f"Do you have the following available to share? {items_str}",
                    "reasoning": "I'm asking because without these documents, some analytical tasks will be based on estimates rather than actuals, which significantly reduces accuracy.",
                    "type": "data_availability",
                    "options": [
                        "Yes, I have all of them",
                        "I have some — will specify below",
                        "No, please research/estimate where possible",
                    ],
                    "potential_issue": "Missing source documents may force agents to rely on public estimates, reducing the reliability of the final recommendation.",
                }
            )

        questions.extend(
            [
                {
                    "question": "What is the primary objective of this analysis? (e.g., deciding whether to proceed with a bid, benchmarking against peers, internal board presentation)",
                    "reasoning": "I'm asking because the objective determines which agents to prioritize and how deep to go on each analysis track.",
                    "type": "objective",
                    "options": [
                        "Investment decision (buy/pass)",
                        "Valuation benchmarking",
                        "Board/LP presentation",
                        "Strategic review",
                        "Other",
                    ],
                    "potential_issue": "Unclear objective leads to wasted effort on irrelevant analysis and increases turnaround time.",
                },
                {
                    "question": "What is your timeline? When do you need the completed analysis?",
                    "reasoning": "I'm asking because timeline constraints affect which tasks can be run in parallel vs. sequentially, and whether we need to skip lower-priority tasks.",
                    "type": "constraint",
                    "options": [
                        "< 24 hours (urgent)",
                        "2-3 days",
                        "1 week",
                        "No hard deadline",
                    ],
                    "potential_issue": "Tight timelines may require deprioritising deep legal/compliance review, which could expose undiscovered risks.",
                },
                {
                    "question": "Are there specific areas or risk factors you are already concerned about that you want the analysis to focus on?",
                    "reasoning": "I'm asking because known concerns should be elevated to higher-priority tasks so agents investigate them thoroughly rather than treating them as routine.",
                    "type": "risk",
                    "options": [
                        "Financial leverage/debt levels",
                        "Regulatory or compliance risk",
                        "Market competition",
                        "Key-person or management risk",
                        "Integration complexity",
                        "Other",
                    ],
                    "potential_issue": "Uninvestigated known risks are the leading cause of post-close deal regret in M&A transactions.",
                },
            ]
        )
        return questions

    # ─────────────────────────────────────────────────────────────────────────
    # Phase 3 — Structured MECE Plan with Risk Flags
    # ─────────────────────────────────────────────────────────────────────────

    async def generate_plan_with_risks(
        self, task: str, context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Generate a MECE task plan with per-task risk flags. Never auto-executes."""
        start = datetime.utcnow()
        context = context or {}
        answers = context.get("user_answers", [])
        provided_data = context.get("provided_data", {})

        laya_triage = None
        try:
            from app.core.laya.client import get_laya_client

            laya_triage = await get_laya_client().triage_deal(task)
        except Exception as e:
            self.logger.warning("laya_agent_triage_failed", error=str(e))

        track_agents = {
            "financial": "financial_analyst",
            "legal": "legal_advisor",
            "risk": "risk_assessor",
            "market": "market_researcher",
            "tech": "ai_tech_diligence_agent",
            "esg": "esg_agent",
        }
        laya_agent = None
        if laya_triage:
            confidence_floor = 0.7 if laya_triage.get("backend") == "local" else 0.85
            candidate = track_agents.get(laya_triage.get("track"))
            if candidate and laya_triage.get("track_confidence", 0) >= confidence_floor:
                laya_agent = candidate

        multi_workstream_request = self._is_multi_workstream_request(task)
        broad_request = multi_workstream_request or any(marker in task.lower() for marker in (
            "full due diligence", "comprehensive due diligence", "end-to-end",
            "full m&a", "complete deal analysis", "investment committee",
        ))
        # Narrowing to one agent removes coverage, so (like the planner) it
        # needs a calibrated backend; LM Studio's self-reported confidence
        # can pick the agent for a focused task but not shrink the scope.
        laya_narrow = bool(
            laya_agent
            and not broad_request
            and laya_triage
            and laya_triage.get("needs_deep_dive") is False
            and laya_triage.get("backend") in ("local", "remote")
        )
        focused = not multi_workstream_request and (self._is_focused_request(task) or laya_narrow)
        if focused:
            # Explicit calculation requests need the financial specialist even
            # when a high-concentration/risk signal dominates Laya's top label.
            calculation_agent = (
                "financial_analyst" if self._is_financial_calculation_request(task) else laya_agent
            )
            tasks = [self._focused_task(task, context, assigned_agent=calculation_agent)]
        elif getattr(self, "llm", None):
            tasks = await self._llm_plan(task, context)
        else:
            tasks = self._get_template_tasks(context)

        if multi_workstream_request:
            tasks = self._ensure_explicit_workstream_coverage(task, tasks, context)

        # Extract metadata for the todo list
        meta = {} if focused else await self._extract_deal_metadata(task, answers)
        ticker = meta.get("ticker", context.get("ticker", ""))
        context_company = context.get("company_name")
        meta_company = meta.get("company_name")
        company_name = next(
            (name for name in (context_company, meta_company)
             if isinstance(name, str) and name.strip()
             and name.strip().lower() not in {"target company", "the target", "unknown"}),
            "Target Company",
        ).strip()

        # Create the todo list
        deal_id = context.get("deal_id", "scratch")
        tasks = select_tasks_for_focus_mode(tasks, context.get("focus_mode", "quality"))
        tm = get_task_manager()
        todo_list = await tm.create_todo_list(
            deal_id=deal_id,
            title=f"Deal Analysis: {company_name}",
            description=(
                f"MECE analysis pipeline for {company_name}. "
                "Review each task and its risk note before approving execution."
            ),
            items=tasks,
            ticker=ticker,
            company_name=company_name,
        )

        wire_stage_dependencies(todo_list.items)
        for item in todo_list.items:
            await tm.update_task(todo_list.id, item.id, {"depends_on": item.depends_on})

        elapsed = (datetime.utcnow() - start).total_seconds() * 1000

        return {
            "phase": "plan",
            "status": "awaiting_approval",
            "todo_list": todo_list.to_dict(),
            "task_count": len(todo_list.items),
            "message": (
                f"I've created {len(todo_list.items)} tasks for {company_name}. "
                "Each task includes a risk flag — please review carefully before approving. "
                "No work will start until you explicitly approve."
            ),
            "warnings": self._extract_warnings(tasks),
            "laya_decision": {
                **laya_triage,
                "selected_agent": laya_agent if focused else None,
                "scope_reduced": bool(laya_narrow and not self._is_focused_request(task)),
            } if laya_triage else None,
            "selected_agents": sorted({t.get("assigned_agent") for t in tasks if t.get("assigned_agent")}),
            "available_agents": {
                k: v["description"] for k, v in AGENT_CAPABILITIES.items()
            },
            "execution_time_ms": elapsed,
        }

    async def _llm_plan(self, task: str, context: Dict) -> List[Dict]:
        """Use LLM to build a MECE plan with per-task risk flags."""
        answers = context.get("user_answers", [])
        mcp_tools = context.get("available_mcp_tools", [])
        provided_data = context.get("provided_data", {})

        prompt = f"""You are a Senior Investment Banking Project Manager.

TASK: {task}

USER ANSWERS TO CLARIFYING QUESTIONS:
{json.dumps(answers, default=str)[:2000]}

PROVIDED DATA/FILES: {json.dumps(list(provided_data.keys())) if provided_data else "None uploaded yet"}

LIVE READ-ONLY MCP TOOLS (discovered just now; each lists the agents allowed to use it):
{json.dumps(mcp_tools) if mcp_tools else "None discovered"}

AVAILABLE AGENTS: {list(AGENT_CAPABILITIES.keys())}

Create a MECE task list sized to the user's requested scope. A focused request for one short assessment should produce one task for the best-matched specialist. Do not add market, legal, valuation, synthesis, or report tasks unless the user asks for them. A full M&A due-diligence request may use 10-14 tasks and should use the relevant specialists.

Return a JSON array of only the tasks needed for the requested scope. Each must have:
- "title": Concise task name
- "description": What this task does and the specific output it produces (2-3 sentences)
- "assigned_agent": One of the available agents listed above
- "priority": "critical" | "high" | "medium" | "low"
- "risk_flag": The most likely blocker or risk for THIS specific task (1 sentence). Be specific — not generic.
- "data_dependency": null | "requires_user_upload" | "mcp_auto_fetch" | "depends_on_prior_task"
- "mcp_source": If data_dependency is "mcp_auto_fetch", use the matching discovered tool's server_id. Otherwise null.

Only mark data_dependency as "mcp_auto_fetch" when a discovered tool directly supports the required data AND its allowed_agents includes the assigned_agent. Discovery is not a successful fetch; preserve a risk flag that source, period, and retrieval success must be verified. Otherwise use "requires_user_upload" or null.

Ordering rules:
1. Data-gathering tasks first
2. Analysis tasks second (financial, market, legal, risk)
3. Synthesis tasks last (scoring, memo)

Return ONLY the JSON array, no other text."""

        try:
            from app.core.llm.model_router import get_model_router

            llm = self.llm or get_model_router().get_client_for_agent(self.name)
            response = await llm.generate(
                prompt=prompt,
                system_prompt="You are a senior investment banking PM. Return only valid JSON.",
                temperature=0.3,
            )
            content = response.get("content", "[]")
            from app.core.json_helpers import extract_and_parse_json

            tasks = extract_and_parse_json(content)
            if isinstance(tasks, list) and len(tasks) > 0:
                return self._validate_mcp_plan_dependencies(tasks, mcp_tools)
        except Exception as e:
            self.logger.warning("llm_plan_failed", error=str(e))

        return self._get_template_tasks(context)

    @staticmethod
    def _validate_mcp_plan_dependencies(tasks: List[Dict], mcp_tools: List[Dict]) -> List[Dict]:
        """Reject planner claims unsupported by a live tool and its agent scope."""
        for task in tasks:
            if task.get("data_dependency") != "mcp_auto_fetch":
                continue
            assigned_agent = task.get("assigned_agent")
            text = f"{task.get('title', '')} {task.get('description', '')}".lower()
            compatible = [
                tool for tool in mcp_tools
                if assigned_agent in tool.get("allowed_agents", [])
                and len({
                    term for term in set(text.replace("-", " ").replace("/", " ").split())
                    if len(term) > 4 and term in f"{tool.get('name', '')} {tool.get('description', '')}".lower()
                }) >= 2
            ]
            source = task.get("mcp_source")
            if not compatible or (source and source not in {t.get("server_id") for t in compatible}):
                task["data_dependency"] = "requires_user_upload"
                task["mcp_source"] = None
                task["risk_flag"] = (
                    "No matching, agent-authorized live MCP tool was discovered; provide the source data before analysis."
                )
            elif not source:
                task["mcp_source"] = compatible[0].get("server_id")
        return tasks

    async def _extract_deal_metadata(
        self, task: str, answers: List[Dict]
    ) -> Dict[str, str]:
        """Extract ticker and company name from task or answers."""
        prompt = f"""Extract the target company name and ticker symbol (if public) from the following context:

TASK: {task}
USER ANSWERS: {json.dumps(answers)}

Return ONLY a JSON object:
{{"company_name": "...", "ticker": "..."}}

If missing, use "Target Company" and "" respectively.
"""
        try:
            from app.core.llm.model_router import get_model_router

            llm = self.llm or get_model_router().get_client_for_agent(self.name)
            response = await llm.generate(prompt=prompt, temperature=0.1)
            from app.core.json_helpers import extract_and_parse_json

            return extract_and_parse_json(response.get("content", "")) or {}
        except Exception as e:
            self.logger.warning("metadata_extraction_failed", error=str(e))
            return {}

    def _extract_warnings(self, tasks: List[Dict]) -> List[str]:
        """Extract risk flags from tasks that are marked critical/high priority with blockers."""
        warnings = []
        for t in tasks:
            if t.get("risk_flag") and t.get("priority") in ("critical", "high"):
                warnings.append(f"[{t.get('title', 'Task')}] {t['risk_flag']}")
        return warnings[:5]  # Top 5 warnings

    # ─────────────────────────────────────────────────────────────────────────
    # Original run() for backward compatibility with existing orchestrator
    # ─────────────────────────────────────────────────────────────────────────

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        """Create a structured todo list. Backward-compatible entrypoint."""
        start = datetime.utcnow()
        context = context or {}
        deal_id = context.get("deal_id", "unknown")
        company_name = context.get("company_name", "Target Company")

        try:
            if getattr(self, "llm", None):
                customized_tasks = await self._generate_custom_tasks(task, context)
            else:
                customized_tasks = self._get_template_tasks(context)

            tm = get_task_manager()
            todo_list = await tm.create_todo_list(
                deal_id=deal_id,
                title=f"Deal Analysis: {company_name}",
                description=(
                    f"Comprehensive deal analysis pipeline for {company_name}. "
                    "Review and edit tasks below, then approve to begin execution."
                ),
                items=customized_tasks,
            )

            wire_stage_dependencies(todo_list.items)
            for item in todo_list.items:
                await tm.update_task(todo_list.id, item.id, {"depends_on": item.depends_on})

            elapsed = (datetime.utcnow() - start).total_seconds() * 1000

            return AgentOutput(
                success=True,
                data={
                    "todo_list": todo_list.to_dict(),
                    "message": (
                        f"Created {len(todo_list.items)} tasks for {company_name}. "
                        "Please review and approve before execution."
                    ),
                    "available_agents": {
                        k: v["description"] for k, v in AGENT_CAPABILITIES.items()
                    },
                },
                reasoning=(
                    f"Generated structured analysis pipeline with {len(todo_list.items)} tasks "
                    f"assigned to {len(set(i.assigned_agent for i in todo_list.items))} specialist agents."
                ),
                confidence=0.9,
                execution_time_ms=elapsed,
            )

        except Exception as e:
            self.logger.error("project_manager_error", error=str(e))
            return AgentOutput(
                success=False,
                data={"error": str(e)},
                reasoning=f"Failed to create task list: {e}",
                confidence=0.0,
            )

    async def _generate_custom_tasks(self, task: str, context: Dict) -> List[Dict]:
        """Use LLM to generate customized task list based on deal specifics."""
        prompt = f"""You are a Senior Project Manager at a top-tier investment bank.
Given the following deal analysis request, create a structured task list.

DEAL REQUEST: {task}
CONTEXT: {json.dumps(context, default=str)[:2000]}

Return a JSON array of tasks. Each task object must have:
- "title": concise task name
- "description": 1-2 sentence description of what needs to be done
- "assigned_agent": one of {list(AGENT_CAPABILITIES.keys())}
- "priority": "critical" | "high" | "medium" | "low"
- "risk_flag": main risk or blocker for this task (1 sentence)

Create only tasks needed to satisfy the request. For a narrow request, create one task assigned to its closest specialist. For broad M&A diligence, include the relevant analysis and synthesis specialists.
Order tasks by logical execution sequence.

Return ONLY the JSON array, no other text."""

        try:
            from app.core.llm.model_router import get_model_router

            llm = self.llm or get_model_router().get_client_for_agent(self.name)
            response = await llm.generate(
                prompt=prompt,
                system_prompt="You are a deal analysis project manager. Return only valid JSON.",
                temperature=0.3,
            )
            content = response.get("content", "[]")
            from app.core.json_helpers import extract_and_parse_json

            tasks = extract_and_parse_json(content)
            if isinstance(tasks, list) and len(tasks) > 0:
                return tasks
        except Exception as e:
            self.logger.warning("llm_task_generation_failed", error=str(e))

        return self._get_template_tasks(context)

    @staticmethod
    def _is_focused_request(task: str) -> bool:
        text = task.lower()
        broad_scope = (
            "full due diligence", "comprehensive due diligence", "end-to-end",
            "full m&a", "complete deal analysis", "investment memo",
        )
        if any(marker in text for marker in broad_scope):
            return False
        narrow_scope = (
            "short", "brief", "quick", "focused", "just",
            "risk assessment", "financial analysis", "market research",
            "valuation", "legal review", "sensitivity analysis",
        )
        return any(marker in text for marker in narrow_scope)

    @staticmethod
    def _is_multi_workstream_request(task: str) -> bool:
        text = task.lower()
        explicit_multi_scope = (
            "workstream", "work stream", "multiple tasks", "separate tasks",
            "parallel tasks", "multi-stage", "three tasks", "3 tasks",
        )
        enumerated_items = len(re.findall(r"(?:^|\s)(?:\(\s*\d+\s*\)|\d+[.)])\s+", text))
        numbered_scope = re.search(
            r"\b(?:exactly\s+)?(?:two|three|four|2|3|4)\b.{0,48}\b(?:tasks|workstreams|work streams)\b",
            text,
        )
        return (
            any(marker in text for marker in explicit_multi_scope)
            or enumerated_items >= 2
            or bool(numbered_scope)
        )

    @staticmethod
    def _ensure_explicit_workstream_coverage(
        prompt: str, tasks: List[Dict[str, Any]], context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Restore clearly requested core workstreams omitted by a planner response."""
        text = prompt.casefold()
        requirements = [
            ("financial", ("financial records", "financial statements", "financial data", "financial analysis", "financials"), "Financial Data Review", "financial_analyst"),
            ("market", ("market research", "market evidence", "market assessment", "industry research", "market"), "Market and Industry Research", "market_researcher"),
            ("valuation", ("valuation", "dcf", "comparable companies"), "Valuation Framework", "valuation_agent"),
            ("legal", ("legal review", "legal", "regulatory"), "Legal and Regulatory Review", "legal_advisor"),
            ("risk", ("risk assessment", "risk review", "risk analysis"), "Risk Assessment", "risk_assessor"),
        ]
        expected = re.search(r"\bexactly\s+(two|three|four|2|3|4)\s+(?:separate\s+)?(?:workstreams?|tasks?)\b", text)
        if not expected:
            return tasks
        expected_count = {"two": 2, "three": 3, "four": 4}.get(expected.group(1), int(expected.group(1)) if expected.group(1).isdigit() else 0)
        detected = [item for item in requirements if any(term in text for term in item[1])]
        covered = " ".join(
            f"{item.get('title', '')} {item.get('description', '')} {item.get('assigned_agent', '')}"
            for item in tasks
        ).casefold()
        company = context.get("company_name") or "the target"
        for key, terms, title, agent in detected:
            if len(tasks) >= expected_count:
                break
            if key in covered or any(term in covered for term in terms):
                continue
            tasks.append({
                "title": title,
                "description": (
                    f"Complete the explicitly requested {title.casefold()} workstream for {company}. "
                    "Use recorded source evidence, cite sources, and label missing information as unknown."
                ),
                "assigned_agent": agent,
                "priority": "high",
                "risk_flag": "This workstream was restored because the generated plan omitted an explicit user requirement.",
            })
            covered += f" {key} {title.casefold()} {agent}"
        return tasks

    @staticmethod
    def _is_financial_calculation_request(task: str) -> bool:
        """Detect explicit financial metric/calculation work that must not be risk-routed."""
        text = task.lower()
        calculation_verbs = ("calculate", "compute", "derive", "formula", "work out")
        financial_metrics = (
            "cagr", "ebitda", "gross margin", "net debt", "equity value",
            "ev/ebitda", "enterprise value", "revenue growth", "leverage ratio",
        )
        return (
            any(verb in text for verb in calculation_verbs)
            and any(metric in text for metric in financial_metrics)
        ) or any(metric in text for metric in ("calculate cagr", "calculate ebitda", "calculate net debt"))

    @staticmethod
    def _focused_task(
        task: str, context: Dict, assigned_agent: Optional[str] = None
    ) -> Dict[str, Any]:
        text = task.lower()
        if any(term in text for term in ("financial", "ebitda", "arr", "churn", "margin")):
            agent, title = "financial_analyst", "Focused Financial Assessment"
        elif any(term in text for term in ("valuation", "dcf", "comparable")):
            agent, title = "valuation_agent", "Focused Valuation"
        elif any(term in text for term in ("market", "tam", "competitor")):
            agent, title = "market_researcher", "Focused Market Research"
        elif "legal" in text or "regulatory" in text:
            agent, title = "legal_advisor", "Focused Legal Review"
        else:
            agent, title = "risk_assessor", "Focused Risk Assessment"

        company = context.get("company_name", "the target")
        agent = assigned_agent or agent
        return {
            "title": title,
            "description": task.replace("the target", company),
            "assigned_agent": agent,
            "priority": "critical",
            "risk_flag": "The assessment is limited to supplied facts; missing source data may constrain conclusions.",
            "data_dependency": None,
            "mcp_source": None,
        }

    def _get_template_tasks(self, context: Dict) -> List[Dict]:
        """Return default template tasks with context-specific adjustments."""
        tasks = []
        for t in DEAL_ANALYSIS_TEMPLATE:
            task = {**t}
            company = context.get("company_name", "the target")
            task["description"] = task["description"].replace("the target", company)
            if "risk_flag" not in task:
                task["risk_flag"] = (
                    "Ensure all input data is available before starting this task."
                )
            tasks.append(task)
        return tasks

    # ─────────────────────────────────────────────────────────────────────────
    # Task execution (unchanged)
    # ─────────────────────────────────────────────────────────────────────────

    async def execute_task(
        self, list_id: str, task_id: str, agent_registry=None
    ) -> Dict[str, Any]:
        """Execute a single task by routing to the assigned agent."""
        tm = get_task_manager()
        todo = await tm.get_todo_list(list_id)
        if not todo:
            return {"error": f"Todo list {list_id} not found"}

        task_item = next((i for i in todo.items if i.id == task_id), None)
        if not task_item:
            return {"error": f"Task {task_id} not found"}

        done_ids = {item.id for item in todo.items if item.status == "done"}
        unmet = [dependency for dependency in task_item.depends_on if dependency not in done_ids]
        if unmet:
            return {"error": "dependencies_not_satisfied", "blocked_by": unmet}

        await tm.update_task(list_id, task_id, {"status": "in_progress"})

        try:
            if agent_registry:
                agent = agent_registry.get(task_item.assigned_agent)
            else:
                agent = None

            if agent:
                result = await agent.run(
                    task_item.description, context={"task_id": task_id}
                )
                await tm.mark_task_result(
                    list_id,
                    task_id,
                    result.data if result.success else {"error": result.reasoning},
                )
                return result.data
            else:
                await tm.mark_task_result(
                    list_id,
                    task_id,
                    {"note": f"Agent '{task_item.assigned_agent}' not available"},
                )
                return {"note": f"Agent '{task_item.assigned_agent}' not registered"}

        except Exception as e:
            await tm.update_task(list_id, task_id, {"status": "blocked"})
            return {"error": str(e)}

    async def execute_all(self, list_id: str, agent_registry=None) -> Dict[str, Any]:
        """Execute all pending tasks in order, respecting dependencies.

        Includes Scrum Master error resilience:
        - Detects failed/blocked tasks after a full pass
        - Retries them once, optionally re-assigning to a fallback agent
        - Generates a user-facing failure summary if tasks remain incomplete
        """
        tm = get_task_manager()
        todo = await tm.get_todo_list(list_id)
        if not todo:
            return {"error": "Todo list not found"}
        if todo.status not in ("approved", "in_progress"):
            return {"error": "Todo list must be approved before execution"}

        todo.status = "in_progress"
        results = {}
        max_iterations = len(todo.items) * 2

        # ── First pass: execute all ready tasks ──
        for _ in range(max_iterations):
            ready = await tm.get_next_tasks(list_id)
            if not ready:
                break
            for task_item in ready:
                result = await self.execute_task(list_id, task_item.id, agent_registry)
                results[task_item.id] = result

        # ── Error resilience: detect and retry failed tasks ──
        todo = await tm.get_todo_list(list_id)  # Refresh from cache/DB
        failed_tasks = [
            item for item in todo.items
            if item.status in ("blocked", "pending")
            and all(dependency in {task.id for task in todo.items if task.status == "done"}
                    for dependency in item.depends_on)
        ]

        retried = []
        if failed_tasks:
            self.logger.warning(
                "scrum_master_retry",
                list_id=list_id,
                failed_count=len(failed_tasks),
                failed_ids=[t.id for t in failed_tasks],
            )

            for task_item in failed_tasks:
                # Reset status to pending for retry
                await tm.update_task(list_id, task_item.id, {"status": "pending"})

                # Execute retry
                retry_result = await self.execute_task(
                    list_id, task_item.id, agent_registry
                )
                results[task_item.id] = retry_result
                retried.append(task_item.id)

        # ── Final status check ──
        todo = await tm.get_todo_list(list_id)
        still_failed = [item for item in todo.items if item.status not in ("done",)]

        failure_summary = None
        if still_failed:
            failure_summary = {
                "message": f"{len(still_failed)} task(s) could not be completed after retry.",
                "failed_tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "assigned_agent": t.assigned_agent,
                        "status": t.status,
                    }
                    for t in still_failed
                ],
            }
            self.logger.error(
                "scrum_master_unresolved_failures", summary=failure_summary
            )

        return {
            "list_id": list_id,
            "status": todo.status,
            "results": results,
            "retried_tasks": retried,
            "failure_summary": failure_summary,
            "summary": todo.to_dict()["summary"],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Self-Research & Assumptions Generation (Tier 0 — P0 Critical)
    # ─────────────────────────────────────────────────────────────────────────

    async def _self_research_skipped(
        self,
        task: str,
        skipped_questions: List[Dict],
        context: Dict,
    ) -> List[Dict[str, Any]]:
        """
        When user skips clarifying questions, self-research the gaps.

        Uses web_search and data_curator to find answers to skipped questions
        so the system can proceed with informed assumptions rather than blind guesses.

        Args:
            task: The original user task
            skipped_questions: List of questions that were skipped with their metadata
            context: Full context including deal_id, available agents, etc.

        Returns:
            List of research results with source and confidence for each skipped question
        """
        if not skipped_questions:
            return []

        research_results = []
        deal_id = context.get("deal_id", "unknown")

        self.logger.info(
            "scrum_self_research_start",
            deal_id=deal_id,
            skipped_count=len(skipped_questions),
        )

        for skipped in skipped_questions:
            question_text = skipped.get("question", "")
            question_type = skipped.get("type", "general")

            result = {
                "question": question_text,
                "type": question_type,
                "research_method": None,
                "finding": None,
                "confidence": 0.0,
                "sources": [],
                "assumption_made": None,
            }

            try:
                # Determine best research approach based on question type
                if question_type in ["data_availability", "scope"]:
                    # Try web search for company/industry data
                    result["research_method"] = "web_search"

                    # Extract company name from task if possible
                    company_name = self._extract_company_name_simple(task)

                    if company_name and company_name != "Target Company":
                        search_query = f"{company_name} {question_text}"

                        # Use ToolRouter to perform web search
                        from app.core.memory.pageindex_client import (
                            get_pageindex_client,
                        )
                        from app.core.tools.tool_router import ToolRouter

                        tool_router = ToolRouter()
                        tool_router.register_default_tools(get_pageindex_client())
                        tool_router.set_provenance_context(
                            deal_id=deal_id,
                            agent_name=self.name,
                            execution_round=1,
                        )

                        tool_result = await tool_router.execute(
                            "web_search",
                            {"query": search_query, "num_results": 5},
                        )

                        if tool_result.success:
                            results = (tool_result.data or {}).get("results", [])
                            if results:
                                # Summarize findings
                                finding_parts = []
                                sources = []

                                for r in results[:3]:
                                    title = r.get("title", "")
                                    snippet = r.get("snippet", "")
                                    link = r.get("url", "")
                                    if snippet:
                                        finding_parts.append(snippet)
                                    if link:
                                        sources.append(link)

                                result["finding"] = (
                                    " | ".join(finding_parts)
                                    if finding_parts
                                    else "No specific data found"
                                )
                                result["sources"] = sources
                                result["confidence"] = min(
                                    0.7, 0.4 + (len(results) * 0.1)
                                )  # Cap at 0.7 for web data

                elif question_type == "risk":
                    # Use risk_assessor patterns from memory or knowledge base
                    result["research_method"] = "knowledge_base"

                    # Query ClarificationMemory for similar risks
                    from app.core.memory.clarification_memory import ClarificationMemory

                    memory = ClarificationMemory()

                    deal_type = memory.detect_deal_type(task)
                    prior_context = memory.get_context_for_prompt(deal_type, task)

                    if prior_context and "No prior context" not in prior_context:
                        result["finding"] = (
                            f"Based on historical patterns for {deal_type}: {prior_context[:500]}"
                        )
                        result["confidence"] = 0.6
                        result["sources"].append("clarification_memory")
                    else:
                        result["finding"] = (
                            "No prior risk patterns available. Will use standard risk framework."
                        )
                        result["confidence"] = 0.5

                elif question_type == "constraint":
                    # Timeline constraints - use standard frameworks
                    result["research_method"] = "standard_framework"

                    if (
                        "timeline" in question_text.lower()
                        or "deadline" in question_text.lower()
                    ):
                        result["finding"] = (
                            "Standard M&A analysis timeline: 2-3 weeks for comprehensive due diligence"
                        )
                        result["confidence"] = 0.8
                        result["assumption_made"] = (
                            "Assuming standard timeline unless specified otherwise"
                        )
                    else:
                        result["finding"] = (
                            "Constraint information not available via research"
                        )
                        result["confidence"] = 0.4

                else:
                    # General questions - try to infer from task
                    result["research_method"] = "task_inference"
                    result["finding"] = f"Inferring from task description: {task[:200]}"
                    result["confidence"] = 0.5
                    result["assumption_made"] = (
                        "Assuming standard parameters based on task context"
                    )

                # Generate assumption statement based on research
                if not result["assumption_made"]:
                    result["assumption_made"] = self._generate_assumption_from_research(
                        question_text, result["finding"], result["confidence"]
                    )

            except Exception as e:
                self.logger.warning(
                    "self_research_failed",
                    question=question_text[:100],
                    error=str(e),
                )
                result["finding"] = f"Research failed: {str(e)}"
                result["confidence"] = 0.3
                result["assumption_made"] = (
                    "Conservative assumption due to research failure"
                )

            research_results.append(result)

        self.logger.info(
            "scrum_self_research_complete",
            deal_id=deal_id,
            results_count=len(research_results),
            avg_confidence=sum(r["confidence"] for r in research_results)
            / len(research_results)
            if research_results
            else 0,
        )

        return research_results

    def _extract_company_name_simple(self, task: str) -> str:
        """Quick company name extraction for self-research."""
        import re

        patterns = [
            r"(?:acquire|acquisition of|merge with|analyze|buy|evaluate|target)\s+([A-Z][a-zA-Z\s]+(?:Corp|Inc|LLC|Ltd|Co|Corporation|Company)?)",
            r"(?:company|target)\s+(?:named?|called)\s+([A-Z][a-zA-Z\s]+)",
            r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\s+(?:M&A|acquisition|merger|deal)",
        ]
        for pattern in patterns:
            match = re.search(pattern, task, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return "Target Company"

    def _generate_assumption_from_research(
        self,
        question: str,
        finding: str,
        confidence: float,
    ) -> str:
        """Generate a clear assumption statement from research findings."""
        if confidence >= 0.7:
            return f"High-confidence assumption: Based on available data ({finding[:100]}...), proceeding with informed estimate"
        elif confidence >= 0.5:
            return f"Moderate-confidence assumption: Limited data available ({finding[:80]}...), using industry standard approach"
        else:
            return f"Low-confidence assumption: Insufficient data ({finding[:60]}...), using conservative estimates with appropriate risk flags"

    async def _generate_assumptions_summary(
        self,
        task: str,
        skipped_questions: List[Dict],
        research_results: List[Dict],
        context: Dict,
    ) -> Dict[str, Any]:
        """
        Generate an assumptions summary for user confirmation before proceeding.

        This creates a clear summary of what was skipped, what was researched,
        and what assumptions will be used. The user must explicitly confirm
        before the system proceeds with planning.

        Args:
            task: The original user task
            skipped_questions: Questions that were skipped
            research_results: Results from self-research
            context: Full context

        Returns:
            Assumptions summary dict with formatted message and confirmation_required flag
        """
        deal_id = context.get("deal_id", "unknown")

        # Build the assumptions summary
        summary = {
            "confirmation_required": True,
            "skipped_count": len(skipped_questions),
            "researched_count": len([r for r in research_results if r.get("finding")]),
            "assumptions": [],
            "risk_flags": [],
            "formatted_message": "",
        }

        # Compile assumptions from research
        for result in research_results:
            assumption = {
                "original_question": result.get("question", ""),
                "assumption": result.get("assumption_made", "No assumption available"),
                "confidence": result.get("confidence", 0),
                "research_method": result.get("research_method", "none"),
                "sources": result.get("sources", []),
            }
            summary["assumptions"].append(assumption)

            # Flag low-confidence assumptions
            if result.get("confidence", 0) < 0.5:
                summary["risk_flags"].append(
                    f"Low confidence on: {result.get('question', 'Unknown')[:60]}"
                )

        # Generate formatted message for UI
        lines = [
            "## 📋 Assumptions Summary",
            "",
            f"You skipped {summary['skipped_count']} clarifying question(s). Based on self-research,",
            "here are the assumptions I'll use for the analysis plan:",
            "",
        ]

        for i, assumption in enumerate(summary["assumptions"], 1):
            conf = assumption["confidence"]
            conf_emoji = "🟢" if conf >= 0.7 else ("🟡" if conf >= 0.5 else "🔴")

            lines.append(f"**{i}. {assumption['original_question'][:80]}...**")
            lines.append(f"   {conf_emoji} Assumption: {assumption['assumption']}")
            lines.append(
                f"   Confidence: {round(conf * 100)}% | Method: {assumption['research_method']}"
            )
            if assumption["sources"]:
                lines.append(f"   Sources: {', '.join(assumption['sources'][:2])}")
            lines.append("")

        # Add risk flags if any
        if summary["risk_flags"]:
            lines.extend(
                [
                    "⚠️ **Risk Flags**",
                    "The following assumptions have low confidence and may impact analysis quality:",
                    "",
                ]
            )
            for flag in summary["risk_flags"]:
                lines.append(f"- {flag}")
            lines.append("")

        # Add confirmation prompt
        lines.extend(
            [
                "---",
                "",
                "**Please confirm:**",
                "- [ ] I understand these assumptions",
                "- [ ] I want to proceed with the analysis plan based on these assumptions",
                "",
                "**Controls:** Type `confirm` to proceed, or `add` to provide missing information.",
            ]
        )

        summary["formatted_message"] = "\n".join(lines)

        self.logger.info(
            "assumptions_summary_generated",
            deal_id=deal_id,
            assumptions_count=len(summary["assumptions"]),
            risk_flags_count=len(summary["risk_flags"]),
        )

        return summary
