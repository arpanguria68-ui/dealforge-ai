"""
User Rating API — Enables thumbs up/down on agent outputs
This closes the RL loop for the self-improvement system.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import structlog

from app.core.quality.agent_quality_store import AgentQualityStore
from app.core.memory.question_quality_store import QuestionQualityStore

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/rate", tags=["ratings"])

# Initialize stores
quality_store = AgentQualityStore()
question_store = QuestionQualityStore()

# Lazy initialization flag
_initialized = False

async def _ensure_quality_store_initialized():
    global _initialized
    if not _initialized:
        await quality_store.initialize()
        _initialized = True


class RatingRequest(BaseModel):
    deal_id: str
    agent_type: str
    rating: int  # 1-5 scale
    feedback: Optional[str] = None


class RatingResponse(BaseModel):
    success: bool
    message: str


@router.post("", response_model=RatingResponse)
async def rate_agent_output(request: RatingRequest):
    """
    Rate an agent's output (1-5 stars).
    
    This feeds into:
    - AgentQualityStore: Updates best practices based on high-scoring actions
    - QuestionQualityStore: Learns which question types lead to better outcomes
    
    Rating scale:
    - 1-2: Poor -negative feedback
    - 3: Average - neutral
    - 4-5: Excellent - positive feedback
    """
    if not 1 <= request.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    
    # Normalize rating to 0-1 scale for reward engine
    normalized_score = request.rating / 5.0
    
    try:
        # Ensure quality store is initialized before use
        await _ensure_quality_store_initialized()
        
        # Log the rating to quality store
        await quality_store.log_action(
            agent_name=request.agent_type,
            task_type="user_rated",
            deal_context={"deal_id": request.deal_id},
            action_payload={
                "rating": request.rating,
                "feedback": request.feedback,
                "normalized_score": normalized_score
            }
        )
        
        # If rating is high (4-5), consider updating best practices
        if request.rating >= 4:
            await quality_store.update_best_practices(
                agent_name=request.agent_type,
                task_type="user_rated"
            )
        
        logger.info(
            "user_rating_recorded",
            deal_id=request.deal_id,
            agent=request.agent_type,
            rating=request.rating,
            normalized_score=normalized_score
        )
        
        return RatingResponse(
            success=True,
            message=f"Rating of {request.rating}/5 recorded for {request.agent_type}"
        )
        
    except Exception as e:
        logger.error("rating_save_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to save rating: {str(e)}")


@router.get("/deal/{deal_id}")
async def get_deal_ratings(deal_id: str):
    """
    Get all ratings for a specific deal.
    """
    # This would query the quality store for ratings associated with a deal
    # For now, return a placeholder
    return {
        "deal_id": deal_id,
        "ratings": [],
        "average_score": None
    }


class PlanRatingRequest(BaseModel):
    deal_type: str
    question_types_asked: list[str]
    task_score: float  # From scoring agent
    user_rated_plan: Optional[str] = None  # "positive", "negative", or None


@router.post("/plan", response_model=RatingResponse)
async def rate_plan_quality(request: PlanRatingRequest):
    """
    Rate the quality of clarifying questions asked by the scrum master.
    
    This helps the QuestionQualityStore learn which question types
    lead to better task outcomes.
    """
    try:
        question_store.record_outcome(
            deal_type=request.deal_type,
            question_types_asked=request.question_types_asked,
            task_score=request.task_score,
            user_rated_plan=request.user_rated_plan
        )
        
        return RatingResponse(
            success=True,
            message=f"Plan quality recorded for {request.deal_type}"
        )
        
    except Exception as e:
        logger.error("plan_rating_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))