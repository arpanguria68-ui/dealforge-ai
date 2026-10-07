"""
Agent Confidence Gate — Peer Validation System

When an agent's confidence falls below a threshold, it automatically requests
validation from a peer agent before proceeding. This creates a "consultant-like"
behavior where agents can flag uncertainties and ask for peer review.

Features:
- Configurable confidence thresholds
- Automatic peer validation requests
- Integration with message bus for agent-to-agent communication
- Validation results stored in state
"""

import asyncio
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from dataclasses import dataclass, field
import structlog
import uuid

from app.orchestrator.agent_bus import get_agent_message_bus, MessagePriority

logger = structlog.get_logger()

# Default confidence thresholds
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.6  # Below this triggers peer validation
DEFAULT_CRITICAL_CONFIDENCE_THRESHOLD = 0.4  # Below this requires human review


@dataclass
class PeerValidationRequest:
    """A request for peer validation of an agent's output"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    deal_id: str = ""
    requesting_agent: str = ""
    target_agent: str = ""
    topic: str = ""
    original_output: str = ""
    confidence: float = 0.0
    concerns: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    validation_response: Optional[str] = None
    validated_at: Optional[str] = None
    status: str = "pending"  # pending, validated, rejected, timeout


class ConfidenceGate:
    """
    Confidence-based peer validation system.
    
    When an agent produces output with low confidence, this gate automatically
    requests validation from a peer agent before proceeding.
    
    Usage:
        gate = ConfidenceGate()
        
        # After agent runs
        result = await agent.run(task, context)
        
        # Check if validation needed
        validation = await gate.check_and_validate(
            deal_id="deal123",
            agent_name="financial_analyst",
            output=result.reasoning,
            confidence=result.confidence,
            context=context
        )
        
        if validation.needs_validation:
            # Use validated output
            output = validation.validated_output
    """

    def __init__(
        self,
        low_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
        critical_threshold: float = DEFAULT_CRITICAL_CONFIDENCE_THRESHOLD,
        peer_map: Optional[Dict[str, str]] = None,
    ):
        self.low_threshold = low_threshold
        self.critical_threshold = critical_threshold
        self.logger = logger.bind(module="confidence_gate")
        
        # Default peer mappings (which agent to ask for validation)
        self.peer_map = peer_map or {
            "financial_analyst": "risk_assessor",
            "legal_advisor": "risk_assessor",
            "market_researcher": "financial_analyst",
            "valuation_agent": "financial_analyst",
            "risk_assessor": "legal_advisor",
            "complex_reasoning_agent": "scoring_agent",
            "data_curator_agent": "market_researcher",
        }
        
        self._validations: Dict[str, List[PeerValidationRequest]] = {}  # deal_id -> validations

    def _get_deal_validations(self, deal_id: str) -> List[PeerValidationRequest]:
        if deal_id not in self._validations:
            self._validations[deal_id] = []
        return self._validations[deal_id]

    def should_validate(self, confidence: float) -> bool:
        """Determine if output needs peer validation based on confidence"""
        return confidence < self.low_threshold

    def requires_human_review(self, confidence: float) -> bool:
        """Determine if confidence is so low it requires human review"""
        return confidence < self.critical_threshold

    def get_peer_agent(self, agent_name: str) -> Optional[str]:
        """Get the appropriate peer agent to validate this agent's output"""
        return self.peer_map.get(agent_name)

    async def check_and_validate(
        self,
        deal_id: str,
        agent_name: str,
        output: str,
        confidence: float,
        context: Dict[str, Any],
        timeout_seconds: int = 45,
    ) -> PeerValidationRequest:
        """
        Check confidence and request peer validation if needed.
        
        Args:
            deal_id: The deal being analyzed
            agent_name: The agent that produced the output
            output: The agent's output/reasoning
            confidence: The confidence score (0-1)
            context: Additional context for the peer agent
            timeout_seconds: How long to wait for validation
            
        Returns:
            PeerValidationRequest with validation results
        """
        # Determine validation needed
        needs_validation = self.should_validate(confidence)
        needs_human = self.requires_human_review(confidence)

        # Laya System-1 pre-gate (~100ms, no LLM call): fast-pass strong
        # outputs, escalate ones with detected red flags. Fail-soft — any
        # Laya failure keeps the original threshold decision.
        try:
            from app.core.laya.client import get_laya_client

            gate = await get_laya_client().gate_confidence(output or "")
            if gate is not None:
                # Skipping peer review removes scrutiny, so it needs every
                # question answered by a calibrated backend. Uncalibrated
                # (LM Studio) or partial answers may only escalate.
                can_fast_pass = gate.get("complete") and gate.get("calibrated")
                if (
                    can_fast_pass
                    and not needs_human
                    and gate["combined"] >= 0.75
                    and gate["red_flag_p"] < 0.4
                ):
                    request = PeerValidationRequest(
                        deal_id=deal_id,
                        requesting_agent=agent_name,
                        target_agent="",
                        topic=f"Laya fast-pass for {agent_name}",
                        original_output=output[:500] if len(output) > 500 else output,
                        confidence=confidence,
                        concerns=[],
                    )
                    request.status = "laya_fast_pass"
                    request.validated_at = datetime.utcnow().isoformat()
                    self._get_deal_validations(deal_id).append(request)
                    self.logger.info(
                        "laya_fast_pass",
                        deal_id=deal_id,
                        agent=agent_name,
                        combined=gate["combined"],
                    )
                    return request
                if gate["red_flag_p"] >= 0.7:
                    needs_validation = True
                    self.logger.info(
                        "laya_escalated_to_peer",
                        deal_id=deal_id,
                        agent=agent_name,
                        red_flag_p=gate["red_flag_p"],
                    )
        except Exception as e:
            self.logger.warning("laya_pregate_failed", error=str(e))
        
        # Get peer agent
        peer_agent = self.get_peer_agent(agent_name) if needs_validation else None
        
        # Create validation request
        request = PeerValidationRequest(
            deal_id=deal_id,
            requesting_agent=agent_name,
            target_agent=peer_agent or "",
            topic=f"Validation request for {agent_name}",
            original_output=output[:500] if len(output) > 500 else output,  # Truncate for prompt
            confidence=confidence,
            concerns=self._generate_concerns(confidence),
        )
        
        # Store request
        validations = self._get_deal_validations(deal_id)
        validations.append(request)
        
        if needs_human:
            request.status = "needs_human"
            self.logger.warning(
                "confidence_critical",
                deal_id=deal_id,
                agent=agent_name,
                confidence=confidence,
                threshold=self.critical_threshold,
            )
            return request
            
        if not needs_validation or not peer_agent:
            request.status = "no_validation_needed"
            return request

        # Request peer validation via message bus
        self.logger.info(
            "requesting_peer_validation",
            deal_id=deal_id,
            agent=agent_name,
            peer_agent=peer_agent,
            confidence=confidence,
        )

        # Build validation query
        validation_query = self._build_validation_query(
            agent_name=agent_name,
            output=output,
            context=context,
        )

        # Use message bus to query peer agent
        bus = get_agent_message_bus()
        
        try:
            response = await bus.query_peer_agent(
                deal_id=deal_id,
                requesting_agent=agent_name,
                target_agent=peer_agent,
                query=validation_query,
                context={
                    "original_output": output,
                    "confidence": confidence,
                    **context,
                },
                timeout_seconds=timeout_seconds,
            )
            
            if response:
                request.validation_response = response
                request.validated_at = datetime.utcnow().isoformat()
                request.status = "validated"
                
                self.logger.info(
                    "peer_validation_completed",
                    validation_id=request.id,
                    peer_agent=peer_agent,
                )
            else:
                request.status = "timeout"
                
        except Exception as e:
            self.logger.error(
                "peer_validation_failed",
                validation_id=request.id,
                error=str(e),
            )
            request.status = "error"

        return request

    def _generate_concerns(self, confidence: float) -> List[str]:
        """Generate list of concerns based on confidence level"""
        concerns = []
        
        if confidence < 0.4:
            concerns.append("Critical: Very low confidence in analysis")
            concerns.append("Data quality concerns - insufficient information")
            concerns.append("Multiple assumptions may not hold")
        elif confidence < 0.6:
            concerns.append("Moderate confidence - peer review recommended")
            concerns.append("Some key metrics may need verification")
            concerns.append("Consider alternative scenarios")
        else:
            concerns.append("Minor concerns - standard review")
            
        return concerns

    def _build_validation_query(
        self,
        agent_name: str,
        output: str,
        context: Dict[str, Any],
    ) -> str:
        """Build a validation query for the peer agent"""
        return f"""You are asked to validate the output from the {agent_name.replace('_', ' ')} agent.

OUTPUT TO VALIDATE:
{output}

CONTEXT:
- Deal ID: {context.get('deal_id', 'unknown')}
- Task: {context.get('task_name', 'general analysis')}

Please provide feedback on:
1. Are there any factual errors or logical gaps in this analysis?
2. What additional data or checks would improve confidence?
3. Are there alternative perspectives that should be considered?

Provide a brief validation assessment (1-2 paragraphs)."""

    def get_validation_history(
        self, deal_id: str, agent_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get validation history for a deal"""
        validations = self._get_deal_validations(deal_id)
        
        if agent_name:
            validations = [v for v in validations if v.requesting_agent == agent_name]
        
        return [
            {
                "id": v.id,
                "requesting_agent": v.requesting_agent,
                "target_agent": v.target_agent,
                "confidence": v.confidence,
                "status": v.status,
                "has_validation": v.validation_response is not None,
                "created_at": v.created_at,
                "validated_at": v.validated_at,
            }
            for v in validations
        ]

    def get_pending_validations(self, deal_id: str) -> List[PeerValidationRequest]:
        """Get all pending validations for a deal"""
        return [
            v for v in self._get_deal_validations(deal_id)
            if v.status == "pending"
        ]


# Singleton
_confidence_gate: Optional[ConfidenceGate] = None


def get_confidence_gate() -> ConfidenceGate:
    """Get the global ConfidenceGate instance"""
    global _confidence_gate
    if _confidence_gate is None:
        _confidence_gate = ConfidenceGate()
    return _confidence_gate