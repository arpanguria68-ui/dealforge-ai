import json
import hashlib
import os
import structlog
from typing import Dict, Any, List, Optional
import redis.asyncio as redis

logger = structlog.get_logger()

from app.config import get_settings

settings = get_settings()
REDIS_URL = settings.REDIS_URL

# ── Configurable TTLs (seconds) ──
DEAL_TTL = settings.MEMORY_STALENESS_DAYS * 86400
CONV_TTL = 60 * 86400  # 60 days
ACTIVITY_TTL = 14 * 86400  # 14 days
SEARCH_CACHE_TTL = settings.SEARCH_CACHE_TTL
MAX_ACTIVITY_PER_DEAL = 500


# Data keys the dashboard reads from global activity events.
_GLOBAL_DATA_KEYS = ("confidence_basis", "synthesis_status")


def _slim_event(evt: dict) -> dict:
    slim = {k: v for k, v in evt.items() if k not in ("data", "result")}
    data = evt.get("data")
    if isinstance(data, dict):
        slim["data"] = {k: data[k] for k in _GLOBAL_DATA_KEYS if k in data}
    if isinstance(slim.get("reasoning"), str) and len(slim["reasoning"]) > 500:
        slim["reasoning"] = slim["reasoning"][:500] + "…"
    return slim


class RedisStore:
    _instance: Optional["RedisStore"] = None

    def __init__(self):
        self.client = redis.from_url(REDIS_URL, decode_responses=True)
        logger.info("redis_store_initialized", url=REDIS_URL)

    @classmethod
    def get_instance(cls) -> "RedisStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ═══════════════════════════════════════════════════════════
    #  Deal Store Operations
    # ═══════════════════════════════════════════════════════════

    async def get_deal(self, deal_id: str) -> Optional[Dict[str, Any]]:
        data = await self.client.get(f"deal:{deal_id}")
        if data:
            return json.loads(data)
        return None

    async def save_deal(self, deal_id: str, deal_data: Dict[str, Any]):
        await self.client.setex(f"deal:{deal_id}", DEAL_TTL, json.dumps(deal_data))

    async def list_deals(self) -> List[Dict[str, Any]]:
        keys = []
        async for key in self.client.scan_iter(match="deal:*", count=100):
            keys.append(key)

        deals = []
        if not keys:
            return deals

        rows = await self.client.mget(keys)
        for data in rows:
            if data:
                deals.append(json.loads(data))
        return deals

    async def update_deal(self, deal_id: str, update_data: Dict[str, Any]):
        deal = await self.get_deal(deal_id)
        if deal:
            deal.update(update_data)
            await self.save_deal(deal_id, deal)

    async def delete_deal(self, deal_id: str):
        await self.client.delete(f"deal:{deal_id}")
        await self.client.delete(f"activity:{deal_id}")

    # ═══════════════════════════════════════════════════════════
    #  Agent Activity Operations
    # ═══════════════════════════════════════════════════════════

    async def add_activity(self, evt: dict):
        deal_id = evt.get("deal_id")
        if deal_id:
            key = f"activity:{deal_id}"
            await self.client.rpush(key, json.dumps(evt))
            await self.client.ltrim(key, -MAX_ACTIVITY_PER_DEAL, -1)
            await self.client.expire(key, ACTIVITY_TTL)

        # Keep global activity log (recent 1000 events). It only feeds the
        # dashboard feed, so store a slim copy: full agent payloads (up to the
        # API body limit each) x 1000 entries could bloat Redis by gigabytes.
        # The per-deal log above keeps the full event for report generation.
        await self.client.rpush("global_activity", json.dumps(_slim_event(evt)))
        await self.client.ltrim("global_activity", -1000, -1)

    async def get_deal_activity(self, deal_id: str) -> List[dict]:
        items = await self.client.lrange(f"activity:{deal_id}", 0, -1)
        return [json.loads(i) for i in items]

    async def get_global_activity(self) -> List[dict]:
        items = await self.client.lrange("global_activity", 0, -1)
        return [json.loads(i) for i in items]

    # ═══════════════════════════════════════════════════════════
    #  Conversation Store Operations
    # ═══════════════════════════════════════════════════════════

    async def save_conversation(self, conv_id: str, conv_data: Dict[str, Any]):
        """Save or update a full conversation with TTL."""
        await self.client.setex(f"conv:{conv_id}", CONV_TTL, json.dumps(conv_data))
        # Track in sorted set for ordered listing (score = updatedAt timestamp)
        updated_at = conv_data.get("updatedAt", 0)
        await self.client.zadd("conv_index", {conv_id: updated_at})

    async def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        data = await self.client.get(f"conv:{conv_id}")
        if data:
            return json.loads(data)
        return None

    async def list_conversations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List conversations ordered by most recently updated."""
        # Get conv IDs from sorted set, newest first
        conv_ids = await self.client.zrevrange("conv_index", 0, limit - 1)
        conversations = []
        for conv_id in conv_ids:
            data = await self.client.get(f"conv:{conv_id}")
            if data:
                conversations.append(json.loads(data))
            else:
                # Stale index entry — clean up
                await self.client.zrem("conv_index", conv_id)
        return conversations

    async def delete_conversation(self, conv_id: str):
        await self.client.delete(f"conv:{conv_id}")
        await self.client.zrem("conv_index", conv_id)

    async def clear_all_conversations(self):
        """Delete all conversations and the index."""
        conv_ids = await self.client.zrange("conv_index", 0, -1)
        if conv_ids:
            keys = [f"conv:{cid}" for cid in conv_ids]
            await self.client.delete(*keys)
        await self.client.delete("conv_index")

    # ═══════════════════════════════════════════════════════════
    #  Search Result Cache (prevents re-fetching identical queries)
    # ═══════════════════════════════════════════════════════════

    def _search_cache_key(self, deal_id: str, tool: str, query: str) -> str:
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
        return f"search:{deal_id}:{tool}:{query_hash}"

    async def cache_search_result(
        self,
        deal_id: str,
        tool: str,
        query: str,
        result: Any,
        ttl: int = SEARCH_CACHE_TTL,
    ):
        """Cache a tool search result for a specific deal."""
        key = self._search_cache_key(deal_id, tool, query)
        await self.client.setex(key, ttl, json.dumps(result, default=str))
        logger.debug("search_cached", deal_id=deal_id, tool=tool, query=query[:50])

    async def get_cached_search(
        self, deal_id: str, tool: str, query: str
    ) -> Optional[Any]:
        """Retrieve a cached search result. Returns None on miss."""
        key = self._search_cache_key(deal_id, tool, query)
        data = await self.client.get(key)
        if data:
            logger.debug("search_cache_hit", deal_id=deal_id, tool=tool)
            return json.loads(data)
        return None

    async def invalidate_search_cache(self, deal_id: str):
        """Remove all cached searches for a deal."""
        pattern = f"search:{deal_id}:*"
        keys = []
        async for key in self.client.scan_iter(match=pattern, count=100):
            keys.append(key)
        if keys:
            await self.client.delete(*keys)
            logger.info(
                "search_cache_invalidated", deal_id=deal_id, keys_removed=len(keys)
            )

    # ═══════════════════════════════════════════════════════════
    #  Lifecycle
    # ═══════════════════════════════════════════════════════════

    async def close(self):
        await self.client.aclose()
