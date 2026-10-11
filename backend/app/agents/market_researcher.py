"""Market Researcher Agent"""

import asyncio
from typing import Dict, Any, Optional, List
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.agents.base import BaseAgent, AgentOutput


class MarketResearcherAgent(BaseAgent):
    """
    Agent for market research and competitive analysis

    Responsibilities:
    - Market sizing (TAM/SAM/SOM)
    - Competitive landscape analysis
    - Industry trend research
    - Customer segment analysis
    - Growth opportunity identification
    """

    name = "market_researcher"
    description: str = "Researches market opportunities and competitive landscape"
    recommended_model: str = "Gemini 1.5 Flash (Fast Web Search)"

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        """
        Execute market research task

        Args:
            task: Research task description
            context: Deal context with market data

        Returns:
            AgentOutput with market research
        """
        start_time = datetime.now()
        self.logger.info("Starting market research", task=task)
        self._current_context = context or {}

        deal_id = context.get("deal_id") if context else None
        industry = context.get("industry", "technology") if context else "technology"

        # Retrieve market-related documents (with deal_id filter)
        market_docs = []
        if deal_id:
            market_docs = await self.retrieve_context(
                f"market industry competition trend growth opportunity for {industry}",
                top_k=8,
                deal_id=deal_id,
            )

        # Retrieve evidence before synthesis. Tool-calling support varies by
        # provider/model; research must not depend on the LLM deciding to call
        # search correctly (or silently inventing an answer when it cannot).
        search = await self.tools.execute(
            "web_search", {"query": task, "num_results": 5}
        )
        search_data = search.data if isinstance(search.data, dict) else {}
        search_rows = search_data.get("results", []) if search.success else []
        sources = await self._collect_sources(search_rows)
        if not sources:
            detail = search.error or "No usable web results were returned."
            return AgentOutput(
                success=False,
                data={
                    "sources": [],
                    "findings": [],
                    "synthesis_status": "failed_retrieval",
                    "data_gaps": [detail],
                },
                reasoning=f"Web research could not collect evidence: {detail}",
                confidence=0.0,
                execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
            )

        evidence_findings = [
            {
                "source_id": source["id"],
                "text": source.get("excerpt") or source.get("snippet") or "",
                "verification": source["evidence_level"],
            }
            for source in sources
            if source.get("excerpt") or source.get("snippet")
        ]
        source_bundle = {
            "sources": [
                {
                    "id": source["id"],
                    "title": source["title"],
                    "publisher": source["publisher"],
                    "published": source.get("published"),
                    "url": source["url"],
                    "evidence_level": source["evidence_level"],
                    "text": (source.get("excerpt") or source.get("snippet") or "")[:1800],
                }
                for source in sources
            ]
        }

        # Build research prompt — LLM will call tools itself via generate_with_tools()
        prompt = self._build_research_prompt(
            task,
            context,
            market_docs,
            source_bundle,
            {},  # competitor_data — fetched by LLM via tools
            {},  # trends_data — fetched by LLM via tools
        )

        # RL Loop: Inject historically successful patterns
        from app.core.quality.agent_quality_store import AgentQualityStore

        quality_store = AgentQualityStore()
        await quality_store.initialize()
        best_practices = await quality_store.get_historical_best_practices(
            self.name, "deal_analysis"
        )

        system_prompt = self._build_system_prompt(best_practices, context)

        # Search has already run and source records are in the prompt. Keep
        # synthesis tool-free so models without function calling still work.
        try:
            response = await self.generate_with_routed_fallback(prompt, system_prompt)
        except Exception as exc:
            return self._evidence_only_output(
                sources, evidence_findings, start_time,
                f"Model synthesis unavailable: {str(exc)[:300]}",
            )

        try:
            research_data = self._parse_research_output(response["content"])
            valid_source_ids = {source["id"] for source in sources}
            source_by_id = {source["id"]: source for source in sources}
            findings = research_data.get("findings", [])
            evidence_gaps = research_data.get("evidence_gaps", [])
            evidence_gaps = [
                str(gap).strip()[:500]
                for gap in evidence_gaps
                if isinstance(gap, str) and gap.strip()
            ][:8] if isinstance(evidence_gaps, list) else []
            verified_findings = []
            unsupported_findings = []
            for finding in findings if isinstance(findings, list) else []:
                if not isinstance(finding, dict):
                    continue
                source_ids = finding.get("source_ids", [])
                source_ids = [source_ids] if isinstance(source_ids, str) else source_ids
                source_ids = [source_id for source_id in source_ids if source_id in valid_source_ids]
                if source_ids:
                    finding["source_ids"] = source_ids
                    finding["citations"] = [
                        {
                            "source_id": source_id,
                            "title": source_by_id[source_id]["title"],
                            "publisher": source_by_id[source_id]["publisher"],
                            "published": source_by_id[source_id].get("published"),
                            "url": source_by_id[source_id]["url"],
                            "evidence_level": source_by_id[source_id]["evidence_level"],
                        }
                        for source_id in source_ids
                    ]
                    verified_findings.append(finding)
                else:
                    unsupported_findings.append(finding)

            requested_count = self._requested_finding_count(task)
            sufficiently_supported = len(verified_findings) >= requested_count and all(
                source_by_id[source_id]["evidence_level"] == "page_text_retrieved"
                for finding in verified_findings
                for source_id in finding["source_ids"]
            )
            # Only source-linked findings cross the research-agent boundary. The
            # legacy broad market schema encouraged the model to invent metrics,
            # players, and recommendations without attaching citations.
            research_data = {
                "findings": verified_findings,
                "sources": sources,
                "evidence_findings": evidence_findings,
                "source_count": len(sources),
                "requested_finding_count": requested_count,
                "page_text_source_count": sum(
                    source["evidence_level"] == "page_text_retrieved" for source in sources
                ),
                "snippet_only_source_count": sum(
                    source["evidence_level"] == "search_snippet_only" for source in sources
                ),
                "synthesis_status": "complete" if sufficiently_supported else "partial_unverified_synthesis",
                "market_size": {"tam": None, "sam": None, "som": None, "growth_rate": None, "currency": "USD"},
                "competitive_landscape": {"market_structure": "unknown", "key_players": [], "competitive_intensity": "unknown"},
                "industry_trends": [],
                "customer_segments": [],
                "entry_barriers": [],
                "growth_opportunities": [],
                "market_risks": [],
                "investment_thesis": "Not assessed: no source-linked investment thesis was established.",
            }
            if unsupported_findings:
                research_data["unsupported_findings"] = unsupported_findings
            if not sufficiently_supported:
                research_data["provider_warning"] = (
                    f"The model returned {len(verified_findings)} source-linked findings for {requested_count} requested. "
                    f"{research_data['page_text_source_count']} of {len(sources)} sources had page text retrieved; "
                    "snippet-only sources are leads, not verified page evidence."
                )
                research_data["data_gaps"] = [
                    "Review source records and collect additional primary-source evidence before relying on this synthesis."
                ] + evidence_gaps
            elif evidence_gaps:
                research_data["data_gaps"] = evidence_gaps
            research_data["search_provider"] = search_data.get("provider", "unknown")
            research_data["reasoning"] = self._render_findings(verified_findings, sufficiently_supported)

            execution_time = (datetime.now() - start_time).total_seconds() * 1000

            return AgentOutput(
                success=True,
                data=research_data,
                reasoning=research_data.get("reasoning", ""),
                confidence=min(0.75, 0.25 + 0.1 * sum(
                    citation["evidence_level"] == "page_text_retrieved"
                    for finding in verified_findings
                    for citation in finding["citations"]
                )),
                execution_time_ms=execution_time,
                tool_calls=response.get("function_calls"),
            )

        except Exception as e:
            self.logger.error("Market research failed", error=str(e))
            return self._evidence_only_output(
                sources, evidence_findings, start_time,
                f"Model synthesis returned unusable output: {str(e)[:300]}",
            )

    async def _collect_sources(self, rows: Any) -> List[Dict[str, Any]]:
        """Normalize search hits and enrich a small, bounded set with page text."""
        candidates = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url.lower().startswith(("https://", "http://")):
                continue
            host = urlsplit(url).hostname or "Unknown source"
            reported_source = str(row.get("source") or "").strip()
            search_labels = {"ddg", "duckduckgo", "serper", "searxng", "search"}
            publisher = (
                reported_source
                if reported_source.casefold() not in search_labels
                else host.removeprefix("www.")
            )
            candidates.append({
                "title": str(row.get("title") or "Untitled search result")[:300],
                "url": url,
                "publisher": publisher[:160],
                "published": row.get("published") or None,
                "snippet": str(row.get("snippet") or "")[:1200],
                "search_provider": row.get("search_provider") or "unknown",
            })
        candidates = candidates[:5]
        if not candidates:
            return []

        async def scrape(source: Dict[str, Any]) -> Dict[str, Any]:
            try:
                result = await asyncio.wait_for(
                    self.tools.execute("web_scraper", {"url": source["url"], "max_chars": 5000}),
                    timeout=18,
                )
                data = result.data if result.success and isinstance(result.data, dict) else {}
                text = str(data.get("text") or "").strip()
                if text:
                    source["title"] = str(data.get("title") or source["title"])[:300]
                    source["excerpt"] = text[:4000]
                    source["evidence_level"] = "page_text_retrieved"
                else:
                    source["evidence_level"] = "search_snippet_only"
                    source["retrieval_note"] = (result.error or "Page text unavailable")[:300]
            except Exception as exc:
                source["evidence_level"] = "search_snippet_only"
                source["retrieval_note"] = str(exc)[:300]
            return source

        # Keep network fan-out bounded and preserve search ranking.
        enriched = await asyncio.gather(*(scrape(source) for source in candidates[:3]))
        sources = enriched + candidates[3:]
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        for index, source in enumerate(sources, start=1):
            source["id"] = f"W{index}"
            source["retrieved_at"] = retrieved_at
            source.setdefault("evidence_level", "search_snippet_only")
        return sources

    @staticmethod
    def _requested_finding_count(task: str) -> int:
        """Extract an explicitly requested finding count, defaulting to one."""
        text = str(task or "").lower()
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        match = re.search(
            r"\b(?:at least\s+)?(\d+|one|two|three|four|five)\s+"
            r"(?:(?:distinct|separate)[,\s]+)?"
            r"(?:(?:current|recent|major|key|latest)[,\s]+)?(?:market\s+)?"
            r"(?:developments?|findings?|updates?|trends?)\b",
            text,
        )
        if not match:
            return 1
        value = match.group(1)
        return min(10, int(value) if value.isdigit() else words[value])

    @staticmethod
    def _render_findings(findings: List[Dict[str, Any]], complete: bool) -> str:
        if not findings:
            return "No source-linked findings were produced. Review the retrieved web sources directly."
        lines = [
            f"Retrieved {len(findings)} source-linked finding(s). "
            + ("" if complete else "Synthesis is incomplete; the requested evidence threshold was not met."),
        ]
        for index, finding in enumerate(findings, start=1):
            source_refs = ", ".join(
                f"[{citation['source_id']}] {citation['title']} ({citation['publisher']})"
                for citation in finding.get("citations", [])
            )
            lines.append(f"{index}. {str(finding.get('text') or '').strip()} {source_refs}".strip())
        return "\n\n".join(lines)

    @staticmethod
    def _evidence_only_output(
        sources: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        start_time: datetime,
        warning: str,
    ) -> AgentOutput:
        data = {
            "sources": sources,
            "evidence_findings": findings,
            "source_count": len(sources),
            "synthesis_status": "partial_provider_unavailable",
            "provider_warning": warning,
            "data_gaps": ["No model synthesis was produced; review source excerpts directly."],
            "market_size": {"tam": None, "sam": None, "som": None, "growth_rate": None, "currency": "USD"},
            "competitive_landscape": {"market_structure": "unknown", "key_players": [], "competitive_intensity": "unknown"},
            "industry_trends": [],
            "customer_segments": [],
            "entry_barriers": [],
            "growth_opportunities": [],
            "market_risks": [],
            "investment_thesis": "Not assessed: model synthesis was unavailable.",
        }
        return AgentOutput(
            success=True,
            data=data,
            reasoning=warning,
            confidence=0.25,
            execution_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
        )

    def _build_research_prompt(
        self,
        task: str,
        context: Optional[Dict],
        market_docs: list,
        market_data: Dict,
        competitor_data: Dict,
        trends_data: Dict,
    ) -> str:
        """Build market research prompt"""
        evidence = market_data.get("sources", []) if isinstance(market_data, dict) else []
        requested_count = self._requested_finding_count(task)
        return f"""Task: {task}

Produce a concise research brief from the retrieved evidence only. Target {requested_count} distinct, decision-relevant developments; do not repeat the same event as separate findings. Each finding must state what happened, the date or period when available, and why it matters only when the source supports that implication. Attach exact source IDs. Never invent dates, figures, causal explanations, forecasts, or implications.

Evidence rules:
- `page_text_retrieved` means the page text was fetched; cite it only for statements supported by its supplied text.
- `search_snippet_only` is a lead, not verified page evidence. You may report it as an unverified lead, but do not present it as confirmed.
- If the evidence does not support the requested count, return fewer findings and state the specific evidence gap.
- Do not produce TAM/SAM/SOM, CAGR, a valuation, or an investment recommendation unless asked and directly supported.
- Return JSON only. No markdown fences or prose outside the JSON.

Retrieved web evidence (source IDs are the only permitted citations):
{json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}

JSON schema:
{{"findings":[{{"text":"concise factual development","source_ids":["W1"]}}],"evidence_gaps":["specific missing evidence"]}}"""

    def _build_system_prompt(
        self, best_practices: List[str] = None, context: Dict = None
    ) -> str:
        """Build system prompt for market research, inheriting stage-aware base prompt"""
        # Start with the base prompt (includes deal stage, thesis, citation discipline)
        base_prompt = self.build_system_prompt(context)

        prompt = (
            base_prompt
            + """

You are a careful market-research analyst. The web evidence has already been retrieved and is included in the user prompt. Do not claim to browse or call tools. Use only that evidence, preserve uncertainty, and return the requested compact JSON. Source-ID existence is checked by the application; your citations must also genuinely support each claim.
"""
        )
        if best_practices:
            prompt += (
                "\n\nHistorical Best Practices (Learn from past high-scoring deals):\n"
            )
            for bp in best_practices:
                prompt += f"- {bp}\n"

        return prompt

    def _parse_research_output(self, content: str) -> Dict[str, Any]:
        """Parse research output"""
        from app.core.json_helpers import extract_and_parse_json

        return extract_and_parse_json(content)

    def _calculate_confidence(self, research_data: Dict) -> float:
        """Calculate confidence score"""
        confidence = 0.5

        market_size = research_data.get("market_size", {})
        if market_size.get("tam") and market_size.get("sam"):
            confidence += 0.2

        if research_data.get("competitive_landscape", {}).get("key_players"):
            confidence += 0.15

        if research_data.get("industry_trends"):
            confidence += 0.15

        return min(1.0, confidence)

    async def analyze_competitor(
        self, competitor_name: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze a specific competitor using tools (not direct llm.generate)"""
        self._current_context = context
        prompt = f"""Analyze competitor: {competitor_name}

Context:
{json.dumps(context, indent=2)}

Use your tools (web_search, web_scraper) to find real data about this competitor.

Provide:
1. Company overview
2. Product/service offerings
3. Market position
4. Strengths and weaknesses
5. Strategic threats"""

        response = await self.generate_with_tools(
            prompt, self._build_system_prompt(context=context)
        )

        return {"competitor": competitor_name, "analysis": response["content"]}


class DebateModeratorAgent(BaseAgent):
    """
    Agent for moderating multi-agent debates

    Responsibilities:
    - Facilitate agent discussions
    - Synthesize conflicting viewpoints
    - Identify consensus and disagreements
    - Guide toward resolution
    """

    name = "debate_moderator"
    description = "Moderates multi-agent debates and synthesizes viewpoints"

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        """
        Execute debate moderation

        Args:
            task: Moderation task
            context: Contains agent outputs to synthesize

        Returns:
            AgentOutput with synthesized debate results
        """
        start_time = datetime.now()

        agent_outputs = context.get("agent_outputs", []) if context else []
        debate_topic = (
            context.get("topic", "Deal evaluation") if context else "Deal evaluation"
        )

        if not agent_outputs:
            return AgentOutput(
                success=False,
                data={},
                reasoning="No agent outputs to synthesize",
                confidence=0.0,
            )

        # Build debate synthesis prompt
        prompt = self._build_debate_prompt(debate_topic, agent_outputs)
        system_prompt = self._build_system_prompt()

        response = await self.llm.generate(prompt, system_prompt)

        try:
            synthesis_data = self._parse_synthesis_output(response["content"])

            # Check if any agent needs to redo their work based on the review
            requires_revision = False
            feedback = synthesis_data.get("reviewer_feedback", [])
            if feedback and len(feedback) > 0:
                requires_revision = True

            execution_time = (datetime.now() - start_time).total_seconds() * 1000

            debate_data = {**synthesis_data, "requires_revision": requires_revision}
            return AgentOutput(
                success=True,
                data=debate_data,
                reasoning=synthesis_data.get("reasoning", ""),
                confidence=self._evidence_confidence(0.75, None, debate_data if synthesis_data else {}),
                execution_time_ms=execution_time,
            )

        except Exception as e:
            self.logger.error("Debate synthesis failed", error=str(e))
            return AgentOutput(
                success=False,
                data={},
                reasoning=f"Synthesis failed: {str(e)}",
                confidence=0.0,
            )

    def _build_debate_prompt(self, topic: str, agent_outputs: Any) -> str:
        """Build debate synthesis prompt"""
        outputs_list = []
        
        # Handle both list and dict inputs for robustness
        if isinstance(agent_outputs, dict):
            for agent_name, output_data in agent_outputs.items():
                # If output_data is the full AgentOutput dict
                if isinstance(output_data, dict):
                    outputs_list.append({
                        "agent": agent_name,
                        "position": output_data.get("position", "Neutral"),
                        "key_points": output_data.get("key_points", output_data.get("findings", [])),
                        "confidence": output_data.get("confidence", 0.5)
                    })
                else:
                    outputs_list.append({"agent": agent_name, "data": str(output_data)})
        elif isinstance(agent_outputs, list):
            outputs_list = agent_outputs
        
        outputs_str = ""
        for output in outputs_list:
            if isinstance(output, dict):
                outputs_str += f"Agent: {output.get('agent', 'Unknown')}\n"
                outputs_str += f"Position: {output.get('position', 'Neutral')}\n"
                outputs_str += f"Key Points: {json.dumps(output.get('key_points', []))}\n"
                outputs_str += f"Confidence: {output.get('confidence', 0.5)}\n\n"
            else:
                outputs_str += f"Output raw: {str(output)}\n\n"

        return f"""Synthesize the following agent perspectives on: {topic}

Agent Outputs:
{outputs_str}

Provide:
1. Areas of consensus
2. Points of disagreement or logical flaws
3. Key insights from each perspective
4. Synthesized recommendation
5. Peer Review Feedback (If any agent failed to provide adequate analysis, hallucinated data, or made logical errors, list them here so they can be sent back to redo their work).

Respond with structured JSON:
{{
    "consensus_points": [string],
    "disagreements": [{{
        "topic": string,
        "positions": [string]
    }}],
    "key_insights": [string],
    "reviewer_feedback": [{{
        "agent": string,
        "feedback": string,
        "severity": string
    }}],
    "synthesized_recommendation": string,
    "confidence": number,
    "next_steps": [string],
    "reasoning": string
}}"""

    def _build_system_prompt(self) -> str:
        """Build system prompt for debate moderation"""
        return f"""You are {self.name}, {self.description}.

You are an expert facilitator with skills in:
- Synthesizing multiple viewpoints
- Identifying common ground
- Resolving conflicts
- Building consensus
- RIGOROUS PEER REVIEW of analytical work

Guidelines:
- Be objective and fair to all perspectives
- Highlight valid points from each side
- Identify underlying assumptions
- Distinguish between facts and opinions
- STRICTLY EVALUATE if an agent's logic is sound. If an output lacks data, has contradictions, or seems hallucinated, you MUST generate a `reviewer_feedback` item with "high" severity directing the agent to fix it.
- Provide clear, actionable synthesis
"""

    def _parse_synthesis_output(self, content: str) -> Dict[str, Any]:
        """Parse synthesis output"""
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        else:
            json_str = content.strip()

        return json.loads(json_str)


class ScoringAgent(BaseAgent):
    """
    Agent for final deal scoring

    Responsibilities:
    - Aggregate agent outputs
    - Calculate final deal score
    - Generate investment recommendation
    """

    name = "scoring_agent"
    description = "Calculates final deal scores and investment recommendations"

    async def run(self, task: str, context: Optional[Dict] = None) -> AgentOutput:
        """Execute deal scoring"""
        start_time = datetime.now()

        # Get inputs from other agents
        financial_output = context.get("financial_output", {}) if context else {}
        legal_output = context.get("legal_output", {}) if context else {}
        risk_output = context.get("risk_output", {}) if context else {}
        market_output = context.get("market_output", {}) if context else {}

        # Use DealScorer for calculation
        from app.core.scoring.deal_scorer import DealScorer

        scorer = DealScorer()

        # Extract data for scoring using the centralized OutputAdapter
        from app.core.output_adapter import get_output_adapter

        adapter = get_output_adapter()
        adapted = adapter.adapt(
            financial_output=financial_output,
            market_output=market_output,
            risk_output=risk_output,
            legal_output=legal_output,
        )

        team_data = context.get("team_data", {}) if context else {}
        traction_data = context.get("traction_data", {}) if context else {}
        strategic_data = context.get("strategic_data", {}) if context else {}

        # Calculate score
        score_result = scorer.score(
            market_data=adapted["market_data"],
            team_data=team_data,
            traction_data=traction_data,
            financial_data=adapted["financial_data"],
            risk_data=adapted["risk_data"],
            strategic_data=strategic_data,
        )

        # Safety Check: If data coverage is 0 and no agents returned data, fail the scoring
        agent_outputs = context.get("agent_outputs", {}) if context else {}
        if adapted.get("data_coverage", 0) < 0.05 and not any(agent_outputs.values()):
            return AgentOutput(
                success=False,
                data={},
                reasoning=f"Scoring aborted: insufficient data across all categories for {context.get('company_name', 'target company')}.",
                confidence=0.0,
            )


        # Attach data coverage internally for output
        data_coverage = adapted.get("data_coverage", 0.0)

        # RL Loop: Store agent quality signals
        from app.core.quality.agent_quality_store import AgentQualityStore

        quality_store = AgentQualityStore()
        await quality_store.initialize()

        # Reward sub-agents based on the final total_score vs target
        # A crude but effective signal: if the deal scores high, the analysis models were strong.
        # In a real system, we'd grade each agent's individual contribution.
        base_reward = 1.0 if score_result.total_score >= 70 else -0.5
        focus_agents = [
            "financial_analyst",
            "legal_advisor",
            "market_researcher",
            "risk_assessor",
        ]
        for ag in focus_agents:
            # We don't have the original action_id here without extensive state tracking,
            # so we log a synthetic completed action + reward immediately
            await quality_store.log_action(
                agent_name=ag,
                task_type="deal_analysis",
                deal_context=(
                    {
                        "industry": context.get("industry", "general"),
                        "type": context.get("type", "general"),
                    }
                    if context
                    else {}
                ),
                action_payload={
                    "final_recommendation_contribution": "See score context"
                },
            )
            # Find the most recently added for this agent and reward it
            import aiosqlite

            async with aiosqlite.connect(quality_store.db_path) as db:
                async with db.execute(
                    "SELECT id FROM agent_actions WHERE agent_name = ? ORDER BY id DESC LIMIT 1",
                    (ag,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        await quality_store.reward_action(
                            action_id=row[0],
                            score=base_reward,
                            feedback=f"Final deal score was {score_result.total_score}. Recommendations: {score_result.recommendations}",
                        )

            # Periodically process best practices
            await quality_store.update_best_practices(ag, "deal_analysis")

        execution_time = (datetime.now() - start_time).total_seconds() * 1000

        return AgentOutput(
            success=True,
            data={
                "total_score": score_result.total_score,
                "risk_level": score_result.risk_level.value,
                "data_coverage": data_coverage,
                "components": [
                    {
                        "name": c.name,
                        "score": round(c.score * 100, 2),
                        "weight": c.weight,
                        "factors": c.factors,
                    }
                    for c in score_result.components
                ],
                "recommendations": score_result.recommendations,
                "red_flags": score_result.red_flags,
                "green_flags": score_result.green_flags,
                "confidence": score_result.confidence,
            },
            reasoning=f"Deal scored at {score_result.total_score}/100 with {score_result.risk_level.value} risk",
            confidence=score_result.confidence,
            execution_time_ms=execution_time,
        )
