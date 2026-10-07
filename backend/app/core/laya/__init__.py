"""Laya System-1 decision layer for DealForge AI.

Laya (https://github.com/NandhaKishorM/laya) is a non-autoregressive
decision engine: typed choice / score / yes-no decisions over text in a
single forward pass (~33ms on GPU, ~200-450ms on CPU), no text generation,
nothing to parse, nothing to hallucinate.

DealForge currently spends full autoregressive LLM calls on decisions that
do not need generation at all:

- LangGraph conditional-edge routing (which agent / node next)
- confidence gating (is this output trustworthy?)
- tool pre-selection (which of 30+ tools fits this task?)
- RAG re-ranking (is this chunk relevant to the query?)
- debate judging (do these two conclusions agree?)

This package wraps Laya behind a fail-soft client: every method returns
``None`` when Laya is unavailable/disabled so callers always fall back to
the existing LLM path. Nothing here may raise at import time and nothing
may require torch at import time.
"""

from app.core.laya.client import get_laya_client, LayaDecisionClient, LayaResult
from app.core.laya.presets import (
    DEAL_TRIAGE_QUESTIONS,
    CONFIDENCE_QUESTIONS,
    RAG_RELEVANCE_QUESTION,
    TOOL_ROUTING_CRITERIA,
    COMPLEXITY_QUESTIONS,
    DEBATE_AGREEMENT_QUESTION,
)

__all__ = [
    "get_laya_client",
    "LayaDecisionClient",
    "LayaResult",
    "DEAL_TRIAGE_QUESTIONS",
    "CONFIDENCE_QUESTIONS",
    "RAG_RELEVANCE_QUESTION",
    "TOOL_ROUTING_CRITERIA",
    "COMPLEXITY_QUESTIONS",
    "DEBATE_AGREEMENT_QUESTION",
]
