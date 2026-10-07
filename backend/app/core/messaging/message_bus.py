"""
Inter-Agent Messaging Bus for DealForge AI
Allows agents to coordinate, share insights, and subscribe to deal-specific updates.
"""

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Set
import structlog

logger = structlog.get_logger()

@dataclass
class AgentMessage:
    """Standardized message format for inter-agent communication"""
    sender: str
    msg_type: str  # insight_discovered, status_update, peer_review_request, debate_point
    payload: Dict[str, Any]
    deal_id: Optional[str] = None
    recipient: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class MessageBus:
    """
    Singleton messaging bus for agents.
    Uses an internal pub/sub mechanism to route messages.
    """
    _instance = None
    _subscribers: Dict[str, Set[asyncio.Queue]] = {} # deal_id -> set of queues

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MessageBus, cls).__new__(cls)
            cls._instance._subscribers = {}
        return cls._instance

    async def publish(self, message: AgentMessage):
        """Publish a message to the bus"""
        logger.info(
            "message_published",
            sender=message.sender,
            type=message.msg_type,
            deal_id=message.deal_id
        )

        # 1. Internal Pub/Sub
        deal_id = message.deal_id or "global"
        if deal_id in self._subscribers:
            for queue in self._subscribers[deal_id]:
                await queue.put(message)

        # 2. Redis Persistence for [Area 1]
        try:
            from app.core.redis_store import RedisStore
            redis = RedisStore.get_instance()
            msg_json = json.dumps(message.to_dict())
            # Store in a deal-specific message list (last 100 messages)
            key = f"messages:{deal_id}"
            await redis.client.lpush(key, msg_json)
            await redis.client.ltrim(key, 0, 99)
            # Set TTL to match deal staleness (30 days)
            await redis.client.expire(key, 86400 * 30)
        except Exception as e:
            logger.warning("redis_publish_failed", error=str(e))

        # 3. External SSE Integration (best effort)
        try:
            from app.api.stream import emit_agent_event
            if message.deal_id:
                await emit_agent_event(
                    message.deal_id, 
                    "agent_message", 
                    message.to_dict()
                )
        except Exception as e:
            logger.warning("sse_publish_failed", error=str(e))

    def subscribe(self, deal_id: str) -> asyncio.Queue:
        """Subscribe to messages for a specific deal"""
        queue = asyncio.Queue()
        if deal_id not in self._subscribers:
            self._subscribers[deal_id] = set()
        self._subscribers[deal_id].add(queue)
        return queue

    async def get_messages(self, deal_id: str, limit: int = 50) -> List[AgentMessage]:
        """Fetch historical messages for a deal from Redis"""
        try:
            from app.core.redis_store import RedisStore
            redis = RedisStore.get_instance()
            key = f"messages:{deal_id}"
            raw_msgs = await redis.client.lrange(key, 0, limit - 1)
            
            messages = []
            for m in raw_msgs:
                data = json.loads(m)
                messages.append(AgentMessage(**data))
            return messages
        except Exception as e:
            logger.warning("redis_fetch_messages_failed", error=str(e))
            return []

    def unsubscribe(self, deal_id: str, queue: asyncio.Queue):
        """Unsubscribe from the bus"""
        if deal_id in self._subscribers:
            self._subscribers[deal_id].discard(queue)
            if not self._subscribers[deal_id]:
                del self._subscribers[deal_id]

def get_message_bus() -> MessageBus:
    return MessageBus()
