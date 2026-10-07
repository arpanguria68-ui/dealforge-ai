"""
User Input Gate — Mid-Execution Pause System

Allows agents to pause execution and request user input/clarification during task execution.
This creates a more interactive, consultant-like experience where users can guide the analysis.

Features:
- Agent-initiated pause requests
- Queued user input with context
- Configurable pause points
- Resume/override capabilities
"""

from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
import structlog
import uuid

logger = structlog.get_logger()


class PauseReason(str, Enum):
    """Reasons an agent might request user input"""
    DATAClarification = "data_clarification"
    ASSUMPTION_VALIDATION = "assumption_validation"
    SCOPE_CHANGE = "scope_change"
    RISK_CONFIRMATION = "risk_confirmation"
    ALTERNATIVE_PATH = "alternative_path"
    TIMEOUT_WAIT = "timeout_wait"


class PauseStatus(str, Enum):
    """Status of a pause request"""
    PENDING = "pending"
    AWAITING_INPUT = "awaiting_input"
    RESUMED = "resumed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass
class UserPauseRequest:
    """A request from an agent for user input during execution"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    deal_id: str = ""
    agent_name: str = ""
    task_id: str = ""
    question: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    reason: PauseReason = PauseReason.DATAClarification
    options: List[str] = field(default_factory=list)  # Multiple choice options
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    user_response: Optional[str] = None
    responded_at: Optional[str] = None
    status: PauseStatus = PauseStatus.PENDING


class UserInputGate:
    """
    Gate that allows agents to pause and request user input during execution.
    
    Usage:
        gate = UserInputGate()
        
        # In agent execution:
        if need_clarification:
            pause_request = await gate.request_pause(
                deal_id="deal123",
                agent_name="financial_analyst",
                question="What depreciation method should I use?",
                options=["Straight-line", "Double-declining", "Units of production"],
                context={...}
            )
            
        # Check for pending pauses (from frontend or background)
        pauses = gate.get_pending_pauses("deal123")
        
        # Provide response
        await gate.respond_to_pause(pause_id, "Straight-line")
    """

    def __init__(self, default_timeout_seconds: int = 300):
        self.default_timeout = default_timeout_seconds
        self.logger = logger.bind(module="user_input_gate")
        self._pauses: Dict[str, List[UserPauseRequest]] = {}  # deal_id -> pauses

    def _get_deal_pauses(self, deal_id: str) -> List[UserPauseRequest]:
        if deal_id not in self._pauses:
            self._pauses[deal_id] = []
        return self._pauses[deal_id]

    async def request_pause(
        self,
        deal_id: str,
        agent_name: str,
        question: str,
        reason: PauseReason = PauseReason.DATAClarification,
        options: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
        task_id: str = "",
    ) -> UserPauseRequest:
        """
        Request a pause to ask the user for input.
        
        Args:
            deal_id: The deal being analyzed
            agent_name: The agent requesting input
            question: The question to ask the user
            reason: Why input is needed
            options: Optional multiple choice options
            context: Additional context for the user
            task_id: The task being worked on
            
        Returns:
            UserPauseRequest that can be used to get response
        """
        request = UserPauseRequest(
            deal_id=deal_id,
            agent_name=agent_name,
            task_id=task_id,
            question=question,
            reason=reason,
            options=options or [],
            context=context or {},
            status=PauseStatus.AWAITING_INPUT,
        )

        pauses = self._get_deal_pauses(deal_id)
        pauses.append(request)

        self.logger.info(
            "user_pause_requested",
            pause_id=request.id,
            agent=agent_name,
            reason=reason,
            question=question[:100],
        )

        return request

    def get_pending_pauses(
        self, deal_id: str, agent_name: Optional[str] = None
    ) -> List[UserPauseRequest]:
        """Get all pending pause requests for a deal"""
        pauses = self._get_deal_pauses(deal_id)
        
        filtered = [p for p in pauses if p.status == PauseStatus.AWAITING_INPUT]
        
        if agent_name:
            filtered = [p for p in filtered if p.agent_name == agent_name]
            
        return filtered

    async def respond_to_pause(
        self,
        pause_id: str,
        response: str,
        deal_id: Optional[str] = None,
    ) -> bool:
        """
        Provide user response to a pause request.
        
        Args:
            pause_id: The pause request ID
            response: The user's response
            deal_id: Optional deal ID for faster lookup
            
        Returns:
            True if response was accepted, False if not found
        """
        # Search through all deals if not specified
        search_deals = [deal_id] if deal_id else list(self._pauses.keys())
        
        for did in search_deals:
            for pause in self._pauses.get(did, []):
                if pause.id == pause_id:
                    pause.user_response = response
                    pause.responded_at = datetime.utcnow().isoformat()
                    pause.status = PauseStatus.RESUMED

                    self.logger.info(
                        "user_pause_responded",
                        pause_id=pause_id,
                        agent=pause.agent_name,
                        response=response[:100],
                    )
                    return True
                    
        return False

    async def skip_pause(
        self,
        pause_id: str,
        deal_id: Optional[str] = None,
    ) -> bool:
        """Skip a pause request - agent will proceed with defaults"""
        search_deals = [deal_id] if deal_id else list(self._pauses.keys())
        
        for did in search_deals:
            for pause in self._pauses.get(did, []):
                if pause.id == pause_id:
                    pause.status = PauseStatus.SKIPPED
                    pause.responded_at = datetime.utcnow().isoformat()
                    
                    self.logger.info(
                        "user_pause_skipped",
                        pause_id=pause_id,
                        agent=pause.agent_name,
                    )
                    return True
                    
        return False

    def get_pause_history(
        self, deal_id: str, agent_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get history of all pause requests for a deal"""
        pauses = self._get_deal_pauses(deal_id)
        
        if agent_name:
            pauses = [p for p in pauses if p.agent_name == agent_name]
            
        return [
            {
                "id": p.id,
                "agent": p.agent_name,
                "task_id": p.task_id,
                "question": p.question,
                "reason": p.reason,
                "options": p.options,
                "status": p.status,
                "user_response": p.user_response,
                "created_at": p.created_at,
                "responded_at": p.responded_at,
            }
            for p in pauses
        ]

    def has_pending_pauses(self, deal_id: str) -> bool:
        """Check if there are any pending pauses for a deal"""
        return len(self.get_pending_pauses(deal_id)) > 0

    def clear_deal_pauses(self, deal_id: str) -> int:
        """Clear all pause requests for a deal"""
        if deal_id in self._pauses:
            count = len(self._pauses[deal_id])
            del self._pauses[deal_id]
            return count
        return 0


# Singleton
_user_input_gate: Optional[UserInputGate] = None


def get_user_input_gate() -> UserInputGate:
    """Get the global UserInputGate instance"""
    global _user_input_gate
    if _user_input_gate is None:
        _user_input_gate = UserInputGate()
    return _user_input_gate