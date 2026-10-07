"""
SSE (Server-Sent Events) Streaming API for Real-time Agent Updates

This provides Perplexity/Claude-like streaming experience where the frontend
receives real-time updates about agent execution progress.
"""

import asyncio
import json
import uuid
from typing import AsyncGenerator, Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
import structlog

from app.core.redis_store import RedisStore
from app.config import get_settings

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/stream", tags=["streaming"])
security = HTTPBearer(auto_error=False)


def require_admin_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    settings = get_settings()
    expected = (settings.ADMIN_API_TOKEN or "").strip()
    enforce = settings.REQUIRE_ADMIN_TOKEN or bool(expected)
    if not enforce:
        return True

    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin token enforcement enabled but ADMIN_API_TOKEN is not configured",
        )

    token = ((credentials.credentials if credentials else "") or "").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return True


# Active SSE connections stored in memory
_active_streams: Dict[str, "SSEStreamManager"] = {}


class SSEStreamManager:
    """Manages SSE connections for a specific deal or conversation"""

    def __init__(self, deal_id: str, conversation_id: Optional[str] = None):
        self.deal_id = deal_id
        self.conversation_id = conversation_id
        self.clients: Dict[str, asyncio.Queue] = {}

    def add_client(self, client_id: str) -> asyncio.Queue:
        """Add a new client to this stream"""
        queue = asyncio.Queue()
        self.clients[client_id] = queue
        return queue

    def remove_client(self, client_id: str):
        """Remove a client from this stream"""
        self.clients.pop(client_id, None)

    async def broadcast(self, event_type: str, data: Dict[str, Any]):
        """Broadcast an event to all connected clients"""
        message = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
        # Remove disconnected clients
        disconnected = []
        for client_id, queue in self.clients.items():
            try:
                await queue.put(message)
            except Exception:
                disconnected.append(client_id)
        for client_id in disconnected:
            self.clients.pop(client_id, None)

    async def emit(self, event_type: str, data: Dict[str, Any]):
        """Emit a single event to all clients"""
        await self.broadcast(event_type, data)


async def sse_event_generator(
    deal_id: str, client_id: str, task_ids: list[str] = None
) -> AsyncGenerator[str, None]:
    """
    Generate SSE events for a specific client and deal.

    Yields:
        SSE-formatted strings that the browser can consume via EventSource
    """
    stream_manager = _active_streams.get(deal_id)
    if not stream_manager:
        stream_manager = SSEStreamManager(deal_id)
        _active_streams[deal_id] = stream_manager

    queue = stream_manager.add_client(client_id)

    try:
        # Send initial connection confirmation
        yield f"event: connected\ndata: {json.dumps({'deal_id': deal_id, 'client_id': client_id})}\n\n"

        # Listen for events and yield them to the client
        while True:
            try:
                # Wait for messages with timeout to allow graceful disconnect
                message = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"event: {message['type']}\ndata: {json.dumps(message['data'])}\n\n"
            except asyncio.TimeoutError:
                # Send keepalive comment every 30 seconds
                yield f": keepalive\n\n"
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("SSE stream error", error=str(e), deal_id=deal_id)
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                break

    finally:
        stream_manager.remove_client(client_id)
        # Clean up empty stream managers
        if not stream_manager.clients:
            _active_streams.pop(deal_id, None)


@router.get("/events/{deal_id}")
async def stream_events(
    deal_id: str,
    tasks: Optional[str] = None,
):
    """
    SSE endpoint for real-time streaming of deal agent events.

    Connect with: new EventSource(`/api/v1/stream/events/${dealId}`)

    Events:
        - connected: Initial connection confirmation
        - agent_starting: An agent has started execution
        - agent_progress: Agent progress update (with partial results)
        - agent_completed: Agent finished with full results
        - agent_error: Agent execution failed
        - phase_changed: Workflow phase changed (planning -> executing -> synthesizing)
        - deal_complete: All agents finished, deal ready
        - error: General error

    Query params:
        - tasks: Optional comma-separated list of task IDs to filter events
    """
    client_id = str(uuid.uuid4())
    task_ids = tasks.split(",") if tasks else None

    return StreamingResponse(
        sse_event_generator(deal_id, client_id, task_ids),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


async def emit_agent_event(deal_id: str, event_type: str, data: Dict[str, Any]):
    """
    Emit an agent event to all connected SSE clients for a deal.

    Call this from agent execution code to stream real-time updates.
    """
    stream_manager = _active_streams.get(deal_id)
    if stream_manager:
        await stream_manager.emit(event_type, data)


# Helper functions for different event types
async def emit_agent_starting(
    deal_id: str, agent_type: str, task_id: str, task_title: str
):
    await emit_agent_event(
        deal_id,
        "agent_starting",
        {
            "agent_type": agent_type,
            "task_id": task_id,
            "task_title": task_title,
            "status": "starting",
        },
    )


async def emit_agent_progress(
    deal_id: str,
    agent_type: str,
    task_id: str,
    progress: float,
    partial_result: Optional[Dict[str, Any]] = None,
):
    await emit_agent_event(
        deal_id,
        "agent_progress",
        {
            "agent_type": agent_type,
            "task_id": task_id,
            "progress": progress,
            "partial_result": partial_result,
        },
    )


async def emit_agent_completed(
    deal_id: str,
    agent_type: str,
    task_id: str,
    result: Dict[str, Any],
    reasoning: str,
    confidence: float,
    execution_time_ms: float,
):
    await emit_agent_event(
        deal_id,
        "agent_completed",
        {
            "agent_type": agent_type,
            "task_id": task_id,
            "result": result,
            "reasoning": reasoning,
            "confidence": confidence,
            "execution_time_ms": execution_time_ms,
        },
    )


async def emit_agent_error(deal_id: str, agent_type: str, task_id: str, error: str):
    await emit_agent_event(
        deal_id,
        "agent_error",
        {"agent_type": agent_type, "task_id": task_id, "error": error},
    )


async def emit_phase_changed(deal_id: str, phase: str, details: Dict[str, Any]):
    await emit_agent_event(deal_id, "phase_changed", {"phase": phase, **details})


async def emit_deal_complete(
    deal_id: str,
    final_score: float,
    recommendation: str,
    agent_results: list[Dict[str, Any]],
):
    await emit_agent_event(
        deal_id,
        "deal_complete",
        {
            "final_score": final_score,
            "recommendation": recommendation,
            "agent_results": agent_results,
        },
    )


@router.post("/emit/{deal_id}")
async def emit_custom_event(
    deal_id: str,
    request: Request,
    _: bool = Depends(require_admin_token),
):
    """
    Manual endpoint to emit custom events (for testing or external triggers).
    """
    body = await request.json()
    event_type = body.get("event_type", "custom")
    data = body.get("data", {})

    await emit_agent_event(deal_id, event_type, data)

    return {"status": "emitted", "event_type": event_type}


@router.get("/active/{deal_id}")
async def get_active_streams(
    deal_id: str,
    _: bool = Depends(require_admin_token),
):
    """Check if there are active streams for a deal"""
    stream_manager = _active_streams.get(deal_id)
    return {
        "deal_id": deal_id,
        "active": stream_manager is not None,
        "client_count": len(stream_manager.clients) if stream_manager else 0,
    }
