"""
Inter-Agent Message Bus — Peer-to-Peer Agent Communication

Enables agents to send queries directly to other agents and receive targeted responses,
rather than going through the orchestrator. This creates true collaborative reasoning.

Features:
- Message queue per deal
- Direct agent-to-agent queries
- Response handling with context
- Automatic timeout handling
"""

import asyncio
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
import structlog
import uuid

logger = structlog.get_logger()


class MessagePriority(str, Enum):
    """Priority levels for inter-agent messages"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class AgentMessage:
    """A message from one agent to another"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    deal_id: str = ""
    from_agent: str = ""
    to_agent: str = ""
    subject: str = ""
    content: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    priority: MessagePriority = MessagePriority.NORMAL
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    response: Optional[str] = None
    responded_at: Optional[str] = None
    status: str = "pending"  # pending, delivered, responded, timeout


class AgentMessageBus:
    """
    Message bus enabling direct peer-to-peer agent communication.
    
    Usage:
        bus = AgentMessageBus()
        await bus.send_message(deal_id="deal123", from_agent="financial_analyst", 
                              to_agent="risk_assessor", content="What are the key risks?")
        # Later, check for response
        response = await bus.get_response(message_id)
    """

    def __init__(self, timeout_seconds: int = 60):
        self.timeout = timeout_seconds
        self._messages: Dict[str, List[AgentMessage]] = {}  # deal_id -> messages
        self._pending_responses: Dict[str, asyncio.Future] = {}  # message_id -> future
        self.logger = logger.bind(module="agent_bus")

    def _get_deal_messages(self, deal_id: str) -> List[AgentMessage]:
        """Get or create message list for a deal"""
        if deal_id not in self._messages:
            self._messages[deal_id] = []
        return self._messages[deal_id]

    async def send_message(
        self,
        deal_id: str,
        from_agent: str,
        to_agent: str,
        subject: str,
        content: str,
        context: Optional[Dict[str, Any]] = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        await_response: bool = True,
    ) -> AgentMessage:
        """
        Send a message from one agent to another.
        
        Args:
            deal_id: The deal this message relates to
            from_agent: Source agent name
            to_agent: Target agent name  
            subject: Brief subject line
            content: The actual message content/query
            context: Additional context to pass (e.g., prior outputs)
            priority: Message priority level
            await_response: If True, wait for response; if False, fire-and-forget
            
        Returns:
            AgentMessage with the message details
        """
        msg = AgentMessage(
            deal_id=deal_id,
            from_agent=from_agent,
            to_agent=to_agent,
            subject=subject,
            content=content,
            context=context or {},
            priority=priority,
        )

        # Store message
        messages = self._get_deal_messages(deal_id)
        messages.append(msg)

        self.logger.info(
            "agent_message_sent",
            message_id=msg.id,
            from_agent=from_agent,
            to_agent=to_agent,
            subject=subject,
            priority=priority,
        )

        # Create future for response if needed
        if await_response:
            self._pending_responses[msg.id] = asyncio.get_event_loop().create_future()

        return msg

    async def get_messages_for_agent(
        self, deal_id: str, agent_name: str, status: Optional[str] = None
    ) -> List[AgentMessage]:
        """
        Get all messages addressed to a specific agent.
        
        Args:
            deal_id: The deal to check
            agent_name: The agent to get messages for
            status: Optional filter by status (e.g., "pending")
            
        Returns:
            List of AgentMessages
        """
        messages = self._get_deal_messages(deal_id)
        filtered = [m for m in messages if m.to_agent == agent_name]
        
        if status:
            filtered = [m for m in filtered if m.status == status]
            
        return sorted(filtered, key=lambda m: (
            MessagePriority(m.priority).value if isinstance(m.priority, str) else m.priority.value
        ), reverse=True)

    async def respond_to_message(
        self,
        message_id: str,
        response: str,
    ) -> bool:
        """
        Provide a response to a message.
        
        Args:
            message_id: The message being responded to
            response: The response content
            
        Returns:
            True if response was delivered, False if message not found
        """
        for deal_messages in self._messages.values():
            for msg in deal_messages:
                if msg.id == message_id:
                    msg.response = response
                    msg.responded_at = datetime.utcnow().isoformat()
                    msg.status = "responded"

                    # Resolve the future if one was waiting
                    if message_id in self._pending_responses:
                        self._pending_responses[message_id].set_result(response)
                        del self._pending_responses[message_id]

                    self.logger.info(
                        "agent_message_responded",
                        message_id=message_id,
                        from_agent=msg.from_agent,
                        to_agent=msg.to_agent,
                    )
                    return True
        return False

    async def get_response(
        self,
        message_id: str,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[str]:
        """
        Wait for a response to a message.
        
        Args:
            message_id: The message to get response for
            timeout_seconds: How long to wait (defaults to bus timeout)
            
        Returns:
            The response string, or None if timeout
        """
        timeout = timeout_seconds or self.timeout
        
        if message_id not in self._pending_responses:
            # Check if already responded
            for deal_messages in self._messages.values():
                for msg in deal_messages:
                    if msg.id == message_id and msg.response:
                        return msg.response
            return None

        try:
            response = await asyncio.wait_for(
                self._pending_responses[message_id], 
                timeout=timeout
            )
            return response
        except asyncio.TimeoutError:
            self.logger.warning(
                "agent_message_timeout",
                message_id=message_id,
                timeout_seconds=timeout,
            )
            # Mark message as timed out
            for deal_messages in self._messages.values():
                for msg in deal_messages:
                    if msg.id == message_id:
                        msg.status = "timeout"
            return None

    async def query_peer_agent(
        self,
        deal_id: str,
        requesting_agent: str,
        target_agent: str,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[str]:
        """
        Convenience method: send a query and get response in one call.
        
        This is the primary API agents should use to ask each other questions.
        
        Args:
            deal_id: The deal context
            requesting_agent: The agent making the request
            target_agent: The agent being asked
            query: The question to ask
            context: Additional context (prior outputs, data, etc.)
            timeout_seconds: Response timeout
            
        Returns:
            The response string, or None if failed/timeout
        """
        msg = await self.send_message(
            deal_id=deal_id,
            from_agent=requesting_agent,
            to_agent=target_agent,
            subject=f"Query from {requesting_agent}",
            content=query,
            context=context,
            priority=MessagePriority.NORMAL,
            await_response=True,
        )

        return await self.get_response(msg.id, timeout_seconds)

    def get_message_history(
        self, deal_id: str, agent_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get message history for a deal, optionally filtered by agent.
        
        Args:
            deal_id: The deal to get history for
            agent_name: Optional filter for specific agent (as sender or receiver)
            
        Returns:
            List of message dicts
        """
        messages = self._get_deal_messages(deal_id)
        
        if agent_name:
            messages = [
                m for m in messages 
                if m.from_agent == agent_name or m.to_agent == agent_name
            ]
        
        return [
            {
                "id": m.id,
                "from": m.from_agent,
                "to": m.to_agent,
                "subject": m.subject,
                "content": m.content[:200] + "..." if len(m.content) > 200 else m.content,
                "priority": m.priority,
                "status": m.status,
                "created_at": m.created_at,
                "responded_at": m.responded_at,
                "has_response": m.response is not None,
            }
            for m in messages
        ]

    def clear_deal_messages(self, deal_id: str) -> int:
        """
        Clear all messages for a deal (cleanup).
        
        Args:
            deal_id: The deal to clear messages for
            
        Returns:
            Number of messages cleared
        """
        if deal_id in self._messages:
            count = len(self._messages[deal_id])
            del self._messages[deal_id]
            return count
        return 0


# Singleton instance
_message_bus: Optional[AgentMessageBus] = None


def get_agent_message_bus() -> AgentMessageBus:
    """Get the global AgentMessageBus instance"""
    global _message_bus
    if _message_bus is None:
        _message_bus = AgentMessageBus()
    return _message_bus