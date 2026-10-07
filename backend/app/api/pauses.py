"""
User Pause API — Frontend Integration for Mid-Execution Pauses

Provides endpoints for the frontend to:
- Poll for pending pause requests
- Respond to pause requests
- Skip pause requests
- Get pause history
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import structlog

from app.orchestrator.user_input_gate import get_user_input_gate, PauseReason, PauseStatus
from app.orchestrator.agent_bus import get_agent_message_bus
from app.orchestrator.confidence_gate import get_confidence_gate

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/pauses", tags=["pauses"])

# Initialize gates
user_input_gate = get_user_input_gate()
message_bus = get_agent_message_bus()
confidence_gate = get_confidence_gate()


class PauseResponse(BaseModel):
    pause_id: str
    response: str


class PauseSkip(BaseModel):
    pause_id: str


@router.get("/{deal_id}")
async def get_pending_pauses(deal_id: str):
    """Get all pending pause requests for a deal"""
    pauses = user_input_gate.get_pending_pauses(deal_id)
    return {
        "deal_id": deal_id,
        "pending_pauses": [
            {
                "id": p.id,
                "agent": p.agent_name,
                "task_id": p.task_id,
                "question": p.question,
                "reason": p.reason,
                "options": p.options,
                "context": p.context,
                "created_at": p.created_at,
            }
            for p in pauses
        ],
        "count": len(pauses),
    }


@router.post("/{deal_id}/respond")
async def respond_to_pause(deal_id: str, request: PauseResponse):
    """Provide user response to a pause request"""
    success = await user_input_gate.respond_to_pause(
        pause_id=request.pause_id,
        response=request.response,
        deal_id=deal_id,
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="Pause request not found")
    
    return {"success": True, "message": "Response recorded"}


@router.post("/{deal_id}/skip")
async def skip_pause(deal_id: str, request: PauseSkip):
    """Skip a pause request - agent will proceed with defaults"""
    success = await user_input_gate.skip_pause(
        pause_id=request.pause_id,
        deal_id=deal_id,
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="Pause request not found")
    
    return {"success": True, "message": "Pause skipped"}


@router.get("/{deal_id}/history")
async def get_pause_history(deal_id: str):
    """Get full history of pause requests for a deal"""
    history = user_input_gate.get_pause_history(deal_id)
    return {
        "deal_id": deal_id,
        "pauses": history,
    }


# ─── Message Bus API ───

@router.get("/{deal_id}/messages")
async def get_agent_messages(deal_id: str, agent_name: Optional[str] = None):
    """Get inter-agent message history for a deal"""
    messages = message_bus.get_message_history(deal_id, agent_name)
    return {
        "deal_id": deal_id,
        "messages": messages,
    }


# ─── Validation API ───

@router.get("/{deal_id}/validations")
async def get_validation_history(deal_id: str, agent_name: Optional[str] = None):
    """Get peer validation history for a deal"""
    validations = confidence_gate.get_validation_history(deal_id, agent_name)
    return {
        "deal_id": deal_id,
        "validations": validations,
    }


@router.get("/{deal_id}/validations/pending")
async def get_pending_validations(deal_id: str):
    """Get pending peer validations for a deal"""
    pending = confidence_gate.get_pending_validations(deal_id)
    return {
        "deal_id": deal_id,
        "pending": [
            {
                "id": v.id,
                "requesting_agent": v.requesting_agent,
                "target_agent": v.target_agent,
                "confidence": v.confidence,
                "concerns": v.concerns,
                "created_at": v.created_at,
            }
            for v in pending
        ],
    }