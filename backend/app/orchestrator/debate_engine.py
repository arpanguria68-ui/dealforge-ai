"""
Agent Debate Engine - True Multi-Round Agent-to-Agent Debates

This module implements a sentient debate system where agents actively 
challenge each other's conclusions before final synthesis.

Key Features:
- Multi-round structured debates
- Agents pose explicit challenges to each other
- Agents defend/revise their conclusions based on challenges
- Tracks debate history, consensus points, and unresolved conflicts
- Confidence-weighted voting on final recommendations
"""

import asyncio
import json
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import structlog

from app.agents.base import BaseAgent, AgentOutput, get_agent_registry
from app.orchestrator.state import DealState, update_state

logger = structlog.get_logger()


class DebateRoundStatus(str, Enum):
    """Status of a debate round"""
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    TIMEOUT = "timeout"


class ChallengeSeverity(str, Enum):
    """Severity of a challenge"""
    LOW = "low"        # Minor concern, advisory
    MEDIUM = "medium"  # Significant concern, needs addressing
    HIGH = "high"      # Critical flaw, must address or reject


@dataclass
class AgentChallenge:
    """A challenge posed by one agent to another"""
    id: str
    challenger: str          # Agent posing the challenge
    target: str              # Agent being challenged
    round_number: int
    challenge_text: str
    severity: ChallengeSeverity
    evidence: List[str]      # Supporting evidence for the challenge
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    response: Optional[str] = None
    resolved: bool = False
    resolution_type: Optional[str] = None  # "defended", "revised", "agreed", "disagreed"


@dataclass
class DebateRound:
    """A single round of debate"""
    round_number: int
    status: DebateRoundStatus = DebateRoundStatus.PENDING
    challenges: List[AgentChallenge] = field(default_factory=list)
    responses: Dict[str, str] = field(default_factory=dict)  # agent -> response
    consensus_points: List[str] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""


@dataclass
class DebateResult:
    """Final result of a multi-round debate"""
    total_rounds: int
    rounds: List[DebateRound]
    consensus_points: List[str]
    conflicts: List[Dict[str, Any]]
    unresolved_issues: List[Dict[str, Any]]
    revised_conclusions: Dict[str, Any]  # Agent -> revised conclusion
    final_synthesis: str
    confidence_adjustments: Dict[str, float]  # Agent -> confidence delta
    requires_revision: bool
    revision_requests: List[Dict[str, str]]  # agent -> feedback


class AgentDebateEngine:
    """
    Orchestrates true multi-round debates between agents.
    
    Unlike the simple synthesis in DebateModeratorAgent, this engine:
    1. Has each agent explicitly challenge others' conclusions
    2. Gives challenged agents opportunity to defend or revise
    3. Runs multiple rounds until consensus or max rounds
    4. Tracks confidence adjustments based on challenges
    """

    def __init__(
        self,
        max_rounds: int = 3,
        timeout_per_round: int = 60,
        min_challenges_per_round: int = 2,
    ):
        self.max_rounds = max_rounds
        self.timeout_per_round = timeout_per_round
        self.min_challenges_per_round = min_challenges_per_round
        self.logger = logger.bind(module="debate_engine")
        self.agent_registry = get_agent_registry()

    async def run_debate(
        self,
        agent_outputs: Dict[str, Dict[str, Any]],
        context: Dict[str, Any],
        deal_id: str,
    ) -> DebateResult:
        """
        Run a multi-round debate between agents.
        
        Args:
            agent_outputs: Dict of agent_name -> their output data
            context: Deal context
            deal_id: Deal identifier
            
        Returns:
            DebateResult with synthesis and revision requests
        """
        self.logger.info("Starting agent debate", deal_id=deal_id, 
                        agents=list(agent_outputs.keys()), max_rounds=self.max_rounds)
        
        agent_names = list(agent_outputs.keys())
        rounds: List[DebateRound] = []
        all_challenges: List[AgentChallenge] = []
        revised_conclusions: Dict[str, Any] = {}
        confidence_adjustments: Dict[str, float] = {agent: 0.0 for agent in agent_names}

        # Initialize with original outputs as baseline
        current_conclusions = {
            agent: self._extract_conclusion(agent, output)
            for agent, output in agent_outputs.items()
        }

        for round_num in range(1, self.max_rounds + 1):
            self.logger.info("Debate round starting", round=round_num, deal_id=deal_id)
            
            round_result = await self._run_single_round(
                round_number=round_num,
                agent_outputs=agent_outputs,
                current_conclusions=current_conclusions,
                prior_challenges=all_challenges,
                context=context,
                deal_id=deal_id,
            )
            
            rounds.append(round_result)
            all_challenges.extend(round_result.challenges)
            
            # Update conclusions based on this round's responses
            for challenge in round_result.challenges:
                if challenge.resolved and challenge.resolution_type in ["revised", "agreed"]:
                    # Update the target agent's conclusion
                    if challenge.response:
                        current_conclusions[challenge.target] = challenge.response
                        revised_conclusions[challenge.target] = challenge.response
            
            # Track confidence adjustments
            for agent, adjustment in round_result.confidence_adjustments.items():
                confidence_adjustments[agent] += adjustment
            
            self.logger.info("Debate round completed", 
                           round=round_num, 
                           challenges=len(round_result.challenges),
                           consensus=len(round_result.consensus_points),
                           deal_id=deal_id)

            # Check if we've reached consensus
            if len(round_result.conflicts) == 0 and len(round_result.consensus_points) >= len(agent_names):
                self.logger.info("Debate reached consensus", deal_id=deal_id)
                break

        # Generate final synthesis
        final_synthesis = await self._generate_final_synthesis(
            agent_outputs=agent_outputs,
            rounds=rounds,
            current_conclusions=current_conclusions,
            context=context,
            deal_id=deal_id,
        )

        # Determine if any agent needs to revise
        revision_requests = self._generate_revision_requests(rounds, agent_outputs)
        requires_revision = len(revision_requests) > 0

        # Collect unresolved issues
        unresolved_issues = self._collect_unresolved_issues(rounds)
        
        # Collect all consensus points
        all_consensus = []
        for r in rounds:
            all_consensus.extend(r.consensus_points)
        all_consensus = list(set(all_consensus))  # Deduplicate

        # Collect all conflicts
        all_conflicts = []
        for r in rounds:
            all_conflicts.extend(r.conflicts)

        result = DebateResult(
            total_rounds=len(rounds),
            rounds=rounds,
            consensus_points=all_consensus,
            conflicts=all_conflicts,
            unresolved_issues=unresolved_issues,
            revised_conclusions=revised_conclusions,
            final_synthesis=final_synthesis,
            confidence_adjustments=confidence_adjustments,
            requires_revision=requires_revision,
            revision_requests=revision_requests,
        )

        self.logger.info("Debate complete", 
                        deal_id=deal_id,
                        rounds=len(rounds),
                        consensus=len(all_consensus),
                        conflicts=len(all_conflicts),
                        requires_revision=requires_revision)

        return result

    async def _run_single_round(
        self,
        round_number: int,
        agent_outputs: Dict[str, Dict[str, Any]],
        current_conclusions: Dict[str, str],
        prior_challenges: List[AgentChallenge],
        context: Dict[str, Any],
        deal_id: str,
    ) -> DebateRound:
        """Run a single debate round"""
        
        round_result = DebateRound(
            round_number=round_number,
            status=DebateRoundStatus.ACTIVE,
        )
        
        agent_names = list(agent_outputs.keys())
        challenges: List[AgentChallenge] = []
        
        # Step 1: Generate challenges
        challenges = await self._generate_challenges(
            agent_outputs=agent_outputs,
            current_conclusions=current_conclusions,
            prior_challenges=prior_challenges,
            round_number=round_number,
            deal_id=deal_id,
        )
        
        round_result.challenges = challenges
        
        # Step 2: Get responses from challenged agents
        responses = await self._get_challenge_responses(
            challenges=challenges,
            agent_outputs=agent_outputs,
            round_number=round_number,
            deal_id=deal_id,
        )
        
        round_result.responses = responses
        
        # Update challenge responses
        for challenge in challenges:
            if challenge.target in responses:
                challenge.response = responses[challenge.target]
                challenge.resolved = True
                challenge.resolution_type = self._determine_resolution_type(challenge, responses[challenge.target])
        
        # Step 3: Analyze consensus and conflicts
        round_result.consensus_points = self._extract_consensus_points(responses, challenges)
        round_result.conflicts = self._extract_conflicts(responses, challenges)
        
        # Step 4: Generate round summary
        round_result.summary = self._generate_round_summary(
            round_number=round_number,
            challenges=challenges,
            responses=responses,
            consensus=round_result.consensus_points,
            conflicts=round_result.conflicts,
        )
        
        # Calculate confidence adjustments for this round
        round_result.confidence_adjustments = self._calculate_confidence_adjustments(
            challenges, responses
        )
        
        round_result.status = DebateRoundStatus.COMPLETED
        
        return round_result

    async def _generate_challenges(
        self,
        agent_outputs: Dict[str, Dict[str, Any]],
        current_conclusions: Dict[str, str],
        prior_challenges: List[AgentChallenge],
        round_number: int,
        deal_id: str,
    ) -> List[AgentChallenge]:
        """Generate challenges using a dedicated challenge generation agent"""
        
        # Use the LLM to generate structured challenges
        prompt = self._build_challenge_prompt(
            agent_outputs=agent_outputs,
            current_conclusions=current_conclusions,
            prior_challenges=prior_challenges,
            deal_id=deal_id,
        )
        
        system_prompt = """You are an Expert Debater. Your job is to generate STRUCTURAL CHALLENGES 
against other agents' conclusions. You are NOT being hostile - you are being rigorous.

CHALLENGE TYPES:
1. DATA CHALLENGE: "Your valuation assumes 15% growth, but your data shows only 8%"
2. LOGIC CHALLENGE: "Your conclusion that 'market is attractive' doesn't follow from the data you presented"
3. ASSUMPTION CHALLENGE: "Your DCF assumes WACC of 10%, but given the risk profile, 15% would be more appropriate"
4. CONTRADICTION: "Agent A says market is 'high growth' but Agent B says 'declining' - these contradict"
5. EVIDENCE GAP: "You claim 'strong competitive position' but provide no market share data"

You MUST generate at least 2 challenges. For each challenge, output JSON:
{
    "challenges": [
        {
            "challenger": "agent_name",
            "target": "agent_name", 
            "challenge_text": "specific challenge",
            "severity": "low|medium|high",
            "evidence": ["supporting evidence or data point"]
        }
    ]
}"""

        try:
            from app.core.llm.model_router import get_model_router
            from app.core.llm.llm_gateway import get_llm_gateway
            
            router = get_model_router()
            provider = router.get_provider_for_agent("debate_moderator")
            gateway = get_llm_gateway()
            
            response = await gateway.call(
                provider=provider,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.3,  # Lower temperature for structured output
            )
            
            content = response.get("content", "")
            
            # Parse JSON from response
            challenge_data = self._parse_challenge_json(content)
            
            if challenge_data and "challenges" in challenge_data:
                challenges = []
                for i, c in enumerate(challenge_data["challenges"]):
                    challenge = AgentChallenge(
                        id=f"challenge_{round_number}_{i}_{deal_id[:8]}",
                        challenger=c.get("challenger", ""),
                        target=c.get("target", ""),
                        round_number=round_number,
                        challenge_text=c.get("challenge_text", ""),
                        severity=ChallengeSeverity(c.get("severity", "medium")),
                        evidence=c.get("evidence", []),
                    )
                    # Only add valid challenges
                    if challenge.challenger and challenge.target:
                        challenges.append(challenge)
                
                self.logger.info("Generated challenges", 
                                count=len(challenges), 
                                deal_id=deal_id)
                return challenges
            
        except Exception as e:
            self.logger.error("Challenge generation failed", error=str(e), deal_id=deal_id)
        
        # Fallback: generate basic challenges programmatically
        return self._generate_fallback_challenges(
            agent_outputs, current_conclusions, round_number, deal_id
        )

    def _build_challenge_prompt(
        self,
        agent_outputs: Dict[str, Dict[str, Any]],
        current_conclusions: Dict[str, str],
        prior_challenges: List[AgentChallenge],
        deal_id: str,
    ) -> str:
        """Build the prompt for challenge generation"""
        
        agent_summaries = []
        for agent, output in agent_outputs.items():
            conclusion = current_conclusions.get(agent, "No explicit conclusion")
            summary = f"""
AGENT: {agent}
CONCLUSION: {conclusion}
KEY FINDINGS: {json.dumps(output.get('key_findings', []))[:500]}
"""
            agent_summaries.append(summary)
        
        prior_str = ""
        if prior_challenges:
            prior_str = "\n\nPRIOR CHALLENGES (from previous rounds):\n"
            for pc in prior_challenges[-5:]:  # Last 5
                prior_str += f"- {pc.challenger} challenged {pc.target}: {pc.challenge_text}\n"
        
        return f"""Analyze these agent conclusions and generate challenges:

{chr(10).join(agent_summaries)}
{prior_str}

Deal ID: {deal_id}

Generate structural challenges. Each challenge must:
1. Reference SPECIFIC data or logic from the target agent
2. State the nature of the inconsistency/gap/flaw
3. Be actionable (the agent can respond to it)

Output ONLY valid JSON with a "challenges" array.
"""

    def _parse_challenge_json(self, content: str) -> Optional[Dict]:
        """Parse challenge JSON from LLM response"""
        try:
            # Try to extract JSON from markdown code blocks
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                # Try to find JSON-like structure
                json_str = content
            
            return json.loads(json_str)
        except:
            return None

    def _generate_fallback_challenges(
        self,
        agent_outputs: Dict[str, Dict[str, Any]],
        current_conclusions: Dict[str, str],
        round_number: int,
        deal_id: str,
    ) -> List[AgentChallenge]:
        """Generate basic challenges when LLM fails"""
        
        challenges = []
        agents = list(agent_outputs.keys())
        
        # Generate simple cross-agent challenges based on data availability
        if len(agents) >= 2:
            # Challenge each agent on completeness
            for i, agent in enumerate(agents):
                target_idx = (i + 1) % len(agents)
                target_agent = agents[target_idx]
                
                # Check if target has complete data
                target_output = agent_outputs.get(target_agent, {})
                has_data = bool(target_output.get("revenue") or 
                              target_output.get("market_size") or 
                              target_output.get("risks"))
                
                if not has_data:
                    challenge = AgentChallenge(
                        id=f"fallback_{round_number}_{i}_{deal_id[:8]}",
                        challenger=agent,
                        target=target_agent,
                        round_number=round_number,
                        challenge_text=f"{target_agent} provided incomplete analysis with missing key metrics",
                        severity=ChallengeSeverity.MEDIUM,
                        evidence=["Data completeness check failed"],
                    )
                    challenges.append(challenge)
        
        return challenges[:self.min_challenges_per_round]

    async def _get_challenge_responses(
        self,
        challenges: List[AgentChallenge],
        agent_outputs: Dict[str, Dict[str, Any]],
        round_number: int,
        deal_id: str,
    ) -> Dict[str, str]:
        """Get responses from challenged agents"""
        
        responses = {}
        
        # Group challenges by target
        target_challenges: Dict[str, List[AgentChallenge]] = {}
        for challenge in challenges:
            if challenge.target not in target_challenges:
                target_challenges[challenge.target] = []
            target_challenges[challenge.target].append(challenge)
        
        # Get response from each challenged agent
        for target_agent, agent_challenges in target_challenges.items():
            response = await self._get_agent_response(
                target_agent=target_agent,
                challenges=agent_challenges,
                agent_output=agent_outputs.get(target_agent, {}),
                round_number=round_number,
                deal_id=deal_id,
            )
            responses[target_agent] = response
        
        return responses

    async def _get_agent_response(
        self,
        target_agent: str,
        challenges: List[AgentChallenge],
        agent_output: Dict[str, Any],
        round_number: int,
        deal_id: str,
    ) -> str:
        """Get a single agent's response to challenges"""
        
        challenge_texts = "\n".join([
            f"- [{c.severity.value.upper()}] {c.challenge_text}"
            for c in challenges
        ])
        
        prompt = f"""You are {target_agent}. You have been challenged by other agents in Round {round_number}.

CHALLENGES TO ADDRESS:
{challenge_texts}

YOUR ORIGINAL OUTPUT:
{json.dumps(agent_output, indent=2)[:2000]}

Your task is to RESPOND to these challenges. You can:
1. DEFEND: Provide evidence supporting your original conclusion
2. REVISE: Acknowledge the valid point and provide revised conclusion
3. CLARIFY: Provide additional context that addresses the challenge

Output JSON:
{{
    "response": "your detailed response",
    "resolution": "defended|revised|clarified",
    "revised_conclusion": "if you revised, what is the new conclusion?"
}}
"""
        
        try:
            from app.core.llm.model_router import get_model_router
            from app.core.llm.llm_gateway import get_llm_gateway
            
            router = get_model_router()
            provider = router.get_provider_for_agent(target_agent)
            gateway = get_llm_gateway()
            
            response = await gateway.call(
                provider=provider,
                prompt=prompt,
                system_prompt="You are responding to peer review challenges. Be rigorous but constructive.",
                temperature=0.2,
            )
            
            content = response.get("content", "")
            
            # Parse response
            parsed = self._parse_challenge_json(content)
            if parsed and "response" in parsed:
                return parsed["response"]
            
            return content[:1000]  # Fallback to raw content
            
        except Exception as e:
            self.logger.error("Agent response failed", 
                            agent=target_agent, 
                            error=str(e), 
                            deal_id=deal_id)
            return f"Response pending - encountered error: {str(e)}"

    def _extract_conclusion(self, agent: str, output: Dict[str, Any]) -> str:
        """Extract the main conclusion from an agent's output"""
        
        # Try various common keys
        for key in ["recommendation", "investment_thesis", "conclusion", "verdict", "summary"]:
            if key in output and output[key]:
                return str(output[key])
        
        # Fallback to reasoning
        return output.get("reasoning", "No explicit conclusion")[:500]

    def _determine_resolution_type(
        self, 
        challenge: AgentChallenge, 
        response: str
    ) -> str:
        """Determine how the challenge was resolved"""
        
        response_lower = response.lower()
        
        if "revise" in response_lower or "acknowledge" in response_lower:
            return "revised"
        elif "agree" in response_lower or "correct" in response_lower:
            return "agreed"
        elif "defend" in response_lower or "maintain" in response_lower:
            return "defended"
        else:
            return "disagreed"

    def _extract_consensus_points(
        self, 
        responses: Dict[str, str], 
        challenges: List[AgentChallenge]
    ) -> List[str]:
        """Extract points of consensus from responses"""
        
        consensus = []
        
        # Check for agreements in responses
        for agent, response in responses.items():
            if "agree" in response.lower() or "consensus" in response.lower():
                consensus.append(f"Agent {agent} agrees with peer assessment")
        
        # Check for low-severity challenges that were resolved
        resolved_low = [c for c in challenges if c.resolved and c.severity == ChallengeSeverity.LOW]
        if resolved_low:
            consensus.append(f"{len(resolved_low)} minor concerns resolved")
        
        return consensus

    def _extract_conflicts(
        self, 
        responses: Dict[str, str], 
        challenges: List[AgentChallenge]
    ) -> List[Dict[str, Any]]:
        """Extract remaining conflicts"""
        
        conflicts = []
        
        # High severity unresolved challenges
        for challenge in challenges:
            if not challenge.resolved or challenge.severity == ChallengeSeverity.HIGH:
                conflicts.append({
                    "topic": challenge.challenge_text,
                    "between": f"{challenge.challenger} vs {challenge.target}",
                    "severity": challenge.severity.value,
                    "round": challenge.round_number,
                })
        
        # Contradictions in responses
        if len(responses) >= 2:
            response_texts = list(responses.values())
            # Simple heuristic: if responses contain opposing language
            has_proceed = any("proceed" in r.lower() or "buy" in r.lower() for r in response_texts)
            has_reject = any("reject" in r.lower() or "pass" in r.lower() for r in response_texts)
            if has_proceed and has_reject:
                conflicts.append({
                    "topic": "Recommendation contradiction",
                    "detail": "Agents have conflicting final recommendations",
                    "severity": "high",
                })
        
        return conflicts

    def _generate_round_summary(
        self,
        round_number: int,
        challenges: List[AgentChallenge],
        responses: Dict[str, str],
        consensus: List[str],
        conflicts: List[Dict[str, Any]],
    ) -> str:
        """Generate a human-readable summary of the round"""
        
        summary = f"## Round {round_number} Summary\n\n"
        
        summary += f"**Challenges raised:** {len(challenges)}\n"
        for c in challenges:
            summary += f"- {c.challenger} → {c.target}: {c.challenge_text[:100]}... [{c.severity.value}]\n"
        
        summary += f"\n**Responses received:** {len(responses)}\n"
        
        if consensus:
            summary += f"\n**Consensus points:** {len(consensus)}\n"
            for c in consensus[:3]:
                summary += f"- {c}\n"
        
        if conflicts:
            summary += f"\n**Remaining conflicts:** {len(conflicts)}\n"
            for c in conflicts[:3]:
                summary += f"- {c.get('topic', 'Conflict')[:80]}... [{c.get('severity', 'unknown')}]\n"
        
        return summary

    def _calculate_confidence_adjustments(
        self,
        challenges: List[AgentChallenge],
        responses: Dict[str, str],
    ) -> Dict[str, float]:
        """Calculate confidence adjustments based on challenge outcomes"""
        
        adjustments: Dict[str, float] = {}
        
        for challenge in challenges:
            if challenge.resolved:
                if challenge.resolution_type == "revised":
                    # Agent acknowledged issue - reduce confidence
                    adjustments[challenge.target] = adjustments.get(challenge.target, 0) - 0.1
                elif challenge.resolution_type == "defended":
                    # Agent successfully defended - slightly increase confidence
                    adjustments[challenge.target] = adjustments.get(challenge.target, 0) + 0.05
            
            # Challenger gets small bump for rigorous review
            adjustments[challenge.challenger] = adjustments.get(challenge.challenger, 0) + 0.02
        
        return adjustments

    async def _generate_final_synthesis(
        self,
        agent_outputs: Dict[str, Dict[str, Any]],
        rounds: List[DebateRound],
        current_conclusions: Dict[str, str],
        context: Dict[str, Any],
        deal_id: str,
    ) -> str:
        """Generate final synthesis of the debate"""
        
        # Collect all data for synthesis
        consensus_points = []
        conflicts = []
        revision_needed = []
        
        for r in rounds:
            consensus_points.extend(r.consensus_points)
            conflicts.extend(r.conflicts)
        
        consensus_str = "\n".join([f"- {c}" for c in consensus_points[:5]]) if consensus_points else "No clear consensus reached"
        conflicts_str = "\n".join([f"- {c.get('topic', 'Conflict')}" for c in conflicts[:3]]) if conflicts else "No conflicts identified"
        
        prompt = f"""Synthesize this multi-round agent debate into a final recommendation.

DEAL: {context.get('target_company', 'Unknown')} - {deal_id}

CONSENSUS POINTS:
{consensus_str}

CONFLICTS:
{conflicts_str}

AGENT FINAL CONCLUSIONS:
{json.dumps(current_conclusions, indent=2)}

DEBATE SUMMARY:
{rounds[-1].summary if rounds else 'No rounds completed'}

Output JSON:
{{
    "synthesized_recommendation": "final recommendation text",
    "reasoning": "how you reached this conclusion considering the debate",
    "key_insights": ["insight 1", "insight 2"],
    "confidence": 0.0-1.0
}}
"""
        
        try:
            from app.core.llm.model_router import get_model_router
            from app.core.llm.llm_gateway import get_llm_gateway
            
            router = get_model_router()
            provider = router.get_provider_for_agent("debate_moderator")
            gateway = get_llm_gateway()
            
            response = await gateway.call(
                provider=provider,
                prompt=prompt,
                system_prompt="You are a Senior Investment Banking Associate synthesizing multi-agent debate. Provide a balanced final recommendation.",
                temperature=0.3,
            )
            
            content = response.get("content", "")
            parsed = self._parse_challenge_json(content)
            
            if parsed and "synthesized_recommendation" in parsed:
                return parsed["synthesized_recommendation"]
            
            return content[:1000]
            
        except Exception as e:
            self.logger.error("Final synthesis failed", error=str(e), deal_id=deal_id)
            return f"Synthesis pending - debate completed with {len(rounds)} rounds"

    def _generate_revision_requests(
        self,
        rounds: List[DebateRound],
        agent_outputs: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """Generate revision requests for agents with unresolved issues"""
        
        requests = []
        
        for round in rounds:
            for challenge in round.challenges:
                if challenge.severity == ChallengeSeverity.HIGH and not challenge.resolved:
                    requests.append({
                        "agent": challenge.target,
                        "feedback": f"High-severity challenge from {challenge.challenger}: {challenge.challenge_text}",
                        "round": challenge.round_number,
                    })
                elif challenge.resolved and challenge.resolution_type == "revised":
                    # Even resolved high-severity challenges should be re-validated
                    if challenge.severity in [ChallengeSeverity.HIGH, ChallengeSeverity.MEDIUM]:
                        requests.append({
                            "agent": challenge.target,
                            "feedback": f"Revised conclusion based on challenge: {challenge.response[:200]}...",
                            "round": challenge.round_number,
                        })
        
        # Deduplicate by agent
        unique_requests: Dict[str, Dict[str, str]] = {}
        for req in requests:
            agent = req["agent"]
            if agent not in unique_requests:
                unique_requests[agent] = req
        
        return list(unique_requests.values())

    def _collect_unresolved_issues(
        self,
        rounds: List[DebateRound],
    ) -> List[Dict[str, Any]]:
        """Collect all unresolved issues from debate"""
        
        unresolved = []
        
        for round in rounds:
            for challenge in round.challenges:
                if not challenge.resolved:
                    unresolved.append({
                        "issue": challenge.challenge_text,
                        "between": f"{challenge.challenger} -> {challenge.target}",
                        "severity": challenge.severity.value,
                        "round": challenge.round_number,
                    })
                elif challenge.severity == ChallengeSeverity.HIGH and challenge.resolution_type == "disagreed":
                    unresolved.append({
                        "issue": challenge.challenge_text,
                        "resolution": "disagreed - unresolved",
                        "severity": challenge.severity.value,
                        "round": challenge.round_number,
                    })
        
        return unresolved


# Singleton instance
_debate_engine: Optional[AgentDebateEngine] = None


def get_debate_engine(
    max_rounds: int = 3,
    timeout_per_round: int = 60,
) -> AgentDebateEngine:
    """Get or create the debate engine singleton"""
    global _debate_engine
    if _debate_engine is None:
        _debate_engine = AgentDebateEngine(
            max_rounds=max_rounds,
            timeout_per_round=timeout_per_round,
        )
    return _debate_engine