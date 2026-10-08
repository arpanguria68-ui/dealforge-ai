from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    File,
    Request,
    Body,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
import asyncio
import uuid
import json
import os
import re
import aiosqlite
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime, timezone

from app.config import get_settings
from app.core.cors_config import get_cors_origins
from app.db.session import init_db, close_db, get_db, AsyncSessionLocal
from app.orchestrator.state import DealState, DealStage
from app.core.memory.pageindex_client import get_pageindex_client
from app.agents.base import get_agent_registry
from app.core.scoring.deal_scorer import DealScorer, risk_label
import structlog

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def _is_uncalibrated_source_report(data: Any) -> bool:
    return isinstance(data, dict) and (
        data.get("confidence_basis") == "not_calibrated_source_report"
        or data.get("synthesis_status") == "deterministic_source_report"
    )

REPORT_DOWNLOAD_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}
REQUIRED_REPORT_FORMATS = {"docx", "pdf", "pptx", "xlsx"}


def _expected_report_formats(documents: List[Dict[str, Any]]) -> set:
    """Formats a bundle must contain: what it was planned with, else the legacy four."""
    planned = {tuple(sorted(d.get("expected_formats") or [])) for d in documents}
    if len(planned) == 1:
        only = next(iter(planned))
        if only:
            return set(only)
    return REQUIRED_REPORT_FORMATS


def _explicit_public_ticker(text: str) -> Optional[str]:
    match = re.search(
        r"\((?:(?:NASDAQ|NYSE|NYSEAMERICAN|AMEX|OTC)\s*:\s*)?([A-Z]{1,5})\)"
        r"|\bticker\s*(?:is|=|:)\s*([A-Z]{1,5})\b",
        text or "",
    )
    return next((group for group in match.groups() if group), None) if match else None


def _explicit_company_name(text: str) -> Optional[str]:
    """Extract an issuer phrase immediately preceding its parenthesized ticker."""
    match = re.search(
        r"\b(?:for|of|assess|analyze|evaluate|screen|review|research|fetch)\s+"
        r"([A-Z][A-Za-z0-9&.',’ -]{1,79}?)\s+"
        r"\((?:(?:NASDAQ|NYSE|NYSEAMERICAN|AMEX|OTC)\s*:\s*)?[A-Z]{1,5}\)",
        text or "",
        re.IGNORECASE,
    )
    return re.sub(r"^(?:the\s+|for\s+)+", "", match.group(1).strip(" ,.-"), flags=re.IGNORECASE) if match else None


def _deal_identity_for_export(deal: Dict[str, Any], todo_list: Any) -> Dict[str, Any]:
    """Recover explicit issuer identity for legacy exports created with placeholders."""
    resolved = dict(deal)
    current = str(resolved.get("target_company") or "").strip().lower()
    if current not in {"", "target company", "the target", "unknown"}:
        return resolved
    items = todo_list.get("items", []) if isinstance(todo_list, dict) else getattr(todo_list, "items", [])
    prompt_parts = []
    for item in items or []:
        if isinstance(item, dict):
            prompt_parts.extend((str(item.get("title") or ""), str(item.get("description") or "")))
        else:
            prompt_parts.extend((str(getattr(item, "title", "") or ""), str(getattr(item, "description", "") or "")))
    prompt = " ".join(prompt_parts)
    company = _explicit_company_name(prompt)
    ticker = _explicit_public_ticker(prompt)
    fallback = todo_list.get("company_name") if isinstance(todo_list, dict) else getattr(todo_list, "company_name", None)
    if not company and fallback and str(fallback).strip().lower() not in {"target company", "the target", "unknown"}:
        company = str(fallback).strip()
    if company:
        resolved["target_company"] = company
    elif ticker:
        resolved["target_company"] = ticker
    if ticker:
        resolved["ticker"] = ticker
    return resolved

from app.core.redis_store import RedisStore


@asynccontextmanager
async def async_lifespan(app: FastAPI):
    """Application lifespan handler"""
    # Startup
    logger.info("Starting DealForge AI")
    await init_db()

    # Probes make billable completion calls, so run them only by operator request.
    if settings.LLM_STARTUP_PROBE:
        try:
            from app.core.llm.capability_probe import probe_fallback_chain

            logger.info("Running pre-flight LLM capability probe...")
            results = await probe_fallback_chain()
            healthy_count = sum(1 for r in results.values() if r["healthy"])
            logger.info(
                "LLM health check complete",
                healthy=f"{healthy_count}/{len(results)}",
            )
        except Exception as e:
            logger.warning("startup_probe_failed", error=str(e))

    # Load previously saved settings
    from app.core.settings_service import SettingsService

    svc = SettingsService.get_instance()
    svc._apply_to_system()
    logger.info("Runtime settings loaded from disk")

    # Initialize Redis Store
    RedisStore.get_instance()

    # Warm the Laya decision backend off the request path (checkpoint load).
    from app.core.laya.client import get_laya_client

    app.state.laya_warmup = asyncio.create_task(get_laya_client().warmup())

    # ── Initialize OfficeCLI (if auto-download enabled) ──
    if settings.OFFICECLI_AUTO_DOWNLOAD:
        try:
            from app.core.reports.officecli_service import get_officecli_service

            oc = get_officecli_service()
            if oc.is_available():
                logger.info("OfficeCLI initialized", path=oc._binary_path)
            else:
                logger.info("OfficeCLI not available (will auto-download on first use)")
        except Exception as e:
            logger.warning("officecli_init_failed", error=str(e))

    yield

    # Shutdown
    logger.info("Shutting down DealForge AI")
    await close_db()
    await RedisStore.get_instance().close()
    from app.core.laya.client import get_laya_client

    warmup = getattr(app.state, "laya_warmup", None)
    if warmup is not None and not warmup.done():
        warmup.cancel()
    await get_laya_client().aclose()


def get_orchestrator_instance():
    """Lazily import and initialize the orchestrator"""
    from app.orchestrator.graph import get_orchestrator

    return get_orchestrator()


# Create FastAPI app
settings = get_settings()
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Multi-Agent M&A Simulation Platform",
    lifespan=async_lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(settings.CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# Request body size guard for JSON APIs (uploads have their own limit above).
API_MAX_JSON_BYTES = int(float(os.getenv("API_MAX_JSON_MB", "5")) * 1024 * 1024)


@app.middleware("http")
async def limit_json_body_size(request: Request, call_next):
    content_type = request.headers.get("content-type", "")
    length = request.headers.get("content-length")
    if "application/json" in content_type and length and length.isdigit() and int(length) > API_MAX_JSON_BYTES:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=413,
            content={"detail": f"Request body exceeds {API_MAX_JSON_BYTES // (1024 * 1024)} MB"},
        )
    return await call_next(request)


# Security
security = HTTPBearer(auto_error=False)


def require_admin_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """Simple bearer token guard for admin/sensitive endpoints."""
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


# ===== Pydantic Models for API =====

from pydantic import BaseModel, Field


class DealCreateRequest(BaseModel):
    """Request to create a new deal"""

    name: str = Field(..., description="Deal name")
    description: Optional[str] = None
    target_company: str
    industry: Optional[str] = "technology"
    context: Optional[dict] = {}


class DealResponse(BaseModel):
    """Deal response model"""

    id: str
    name: str
    status: str
    target_company: str
    current_stage: str


class TemplateMergeRequest(BaseModel):
    """Request to merge template with data"""

    template_path: str
    output_path: str
    data: Dict[str, Any]


def _confined_path(path: str, *, enforce: bool = True) -> str:
    """Resolve a caller-supplied server path inside SERVER_PATH_ROOTS or 400."""
    from app.core.path_guard import PathNotAllowed, resolve_within_roots

    try:
        return str(resolve_within_roots(path, enforce=enforce))
    except PathNotAllowed as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class AgentRunRequest(BaseModel):
    """Request to run a specific agent"""

    agent_type: str
    task: str
    context: Optional[dict] = {}


class AgentRunResponse(BaseModel):
    """Agent run response"""

    agent_type: str
    success: bool
    data: dict
    reasoning: str
    confidence: float
    provider: Optional[str] = None
    execution_time_ms: Optional[float] = None


class DocumentUploadResponse(BaseModel):
    """Document upload response"""

    document_id: str
    filename: str
    pageindex_id: Optional[str] = None
    status: str


class DocumentQueryRequest(BaseModel):
    """Request to query documentation"""

    query: str
    deal_id: Optional[str] = None


class DealScoreResponse(BaseModel):
    """Deal scoring response"""

    total_score: float
    risk_level: str
    components: List[dict]
    recommendations: List[str]
    red_flags: List[str]
    green_flags: List[str]


# ===== API Routes =====


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "operational",
    }


@app.get("/api/health")
@app.get("/api/v1/health")
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    # Check OfficeCLI availability
    officecli_status = "unavailable"
    try:
        from app.core.reports.officecli_service import get_officecli_service

        oc = get_officecli_service()
        officecli_status = "ready" if oc.is_available() else "unavailable"
    except Exception:
        pass

    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "officecli": officecli_status,
    }


# ===== Deal Management Routes =====


@app.post("/api/v1/deals", response_model=DealResponse)
async def create_deal(request: DealCreateRequest):
    """Create a new deal and persist it in the in-memory store"""
    deal_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    logger.info(
        "Creating new deal",
        deal_id=deal_id,
        name=request.name,
        target=request.target_company,
    )

    deal = {
        "id": deal_id,
        "name": request.name,
        "status": "created",
        "target_company": request.target_company,
        "industry": request.industry or "technology",
        "current_stage": "init",
        "final_score": None,
        "final_recommendation": None,
        "agents_run": [],
        "created_at": now,
        "updated_at": now,
    }
    redis_store = RedisStore.get_instance()
    await redis_store.save_deal(deal_id, deal)

    return DealResponse(
        id=deal_id,
        name=request.name,
        status="created",
        target_company=request.target_company,
        current_stage="init",
        created_at=now,
    )


@app.get("/api/v1/deals")
async def list_deals():
    """List all deals from the Redis store"""
    redis_store = RedisStore.get_instance()
    deals = await redis_store.list_deals()
    return {"deals": deals}


async def update_deal(deal_id: str, request: Request):
    """Update a deal's status, stage, score, or recommendation."""
    redis_store = RedisStore.get_instance()
    deal = await redis_store.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    body = await request.json()

    if body.get("status") == "completed":
        from app.core.tasks.task_manager import get_task_manager

        task_lists = await get_task_manager().get_lists_for_deal(deal_id)
        if not task_lists or any(
            not task_list.items or any(item.status != "done" for item in task_list.items)
            for task_list in task_lists
        ):
            raise HTTPException(
                status_code=409,
                detail="Deal cannot be marked completed until every task in its saved plans is done.",
            )

    for key in ("status", "current_stage", "final_score", "final_recommendation"):
        if key in body:
            deal[key] = body[key]

    deal["updated_at"] = datetime.now(timezone.utc).isoformat()
    await redis_store.save_deal(deal_id, deal)
    logger.info("deal_updated", deal_id=deal_id, updates=list(body.keys()))
    return deal


@app.patch("/api/v1/deals/{deal_id}")
async def patch_deal(deal_id: str, request: Request):
    """Patch a deal record with status/stage/score updates."""
    return await update_deal(deal_id, request)


@app.get("/api/v1/deals/{deal_id}/provenance")
async def get_deal_provenance(
    deal_id: str, agent_name: Optional[str] = None, tool_name: Optional[str] = None
):
    """Get provenance records for a deal's tool executions"""
    from app.core.provenance import get_provenance_collector

    records = await get_provenance_collector().get_records(
        deal_id=deal_id, agent_name=agent_name, tool_name=tool_name
    )
    return {"records": records}


@app.get("/api/v1/deals/{deal_id}/provenance/export")
async def export_deal_provenance(deal_id: str):
    """Export full provenance chain for a deal"""
    from app.core.provenance import get_provenance_collector

    export_data = await get_provenance_collector().export_chain(deal_id)
    return export_data


@app.get("/api/v1/deals/{deal_id}/knowledge-graph")
async def get_deal_knowledge_graph(deal_id: str, label: Optional[str] = None):
    """Current knowledge-graph facts for a deal (metrics, risks, entities written by agents)."""
    from app.core.knowledge_graph.service import get_knowledge_graph

    graph = get_knowledge_graph()
    if label:
        return {"deal_id": deal_id, "facts": await graph.query_current_facts(deal_id, label)}
    return {"deal_id": deal_id, **await graph.deal_summary(deal_id)}


@app.get("/api/v1/deals/{deal_id}/agent-messages")
async def get_deal_agent_messages(deal_id: str):
    """Retrieve the inter-agent message history for a specific deal."""
    from app.orchestrator.agent_bus import get_agent_message_bus

    bus = get_agent_message_bus()
    messages = bus.get_message_history(deal_id)
    return {"deal_id": deal_id, "messages": messages}


@app.get("/api/v1/dashboard/metrics")
async def dashboard_metrics():
    """Return live dashboard KPIs and agent activity feed"""
    redis_store = RedisStore.get_instance()
    deals = await redis_store.list_deals()
    agent_activity = await redis_store.get_global_activity()

    total = len(deals)
    completed_deal_ids = {deal.get("id") for deal in deals if deal.get("status") == "completed"}
    uncalibrated_runs = {
        (event.get("deal_id"), event.get("agent_type"))
        for event in agent_activity
        if _is_uncalibrated_source_report(event.get("data"))
    }
    confidences = [
        float(confidence)
        for deal in deals
        if deal.get("id") in completed_deal_ids
        for agent_type, confidence in (deal.get("_confidence_scores") or {}).items()
        if (deal.get("id"), agent_type) not in uncalibrated_runs
        and isinstance(confidence, (int, float))
        and 0 <= confidence <= 1
    ]
    avg_confidence = (
        round(sum(confidences) / len(confidences) * 100, 1)
        if confidences
        else None
    )
    high_risk = sum(
        1 for d in deals if d.get("final_score") is not None and d["final_score"] < 0.5
    )
    active = sum(1 for d in deals if d["status"] in ("created", "running"))
    completed = sum(1 for d in deals if d["status"] == "completed")

    return {
        "total_deals": total,
        "active_deals": active,
        "completed_deals": completed,
        "avg_confidence": avg_confidence,
        "high_risk_alerts": high_risk,
        "deals": deals,
        "agent_activity": agent_activity[-20:],  # last 20 events
    }


@app.post("/api/v1/agent-activity")
async def log_agent_activity(event: dict):
    """Persist agent activity without inferring task-list or deal completion."""
    event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    redis_store = RedisStore.get_instance()
    await redis_store.add_activity(event)

    deal_id = event.get("deal_id")
    if deal_id:
        deal = await redis_store.get_deal(deal_id)
        if deal:
            deal["updated_at"] = event["timestamp"]

            # Track agent runs (avoid duplicate entries)
            agent_type = event.get("agent_type", "unknown")
            if agent_type not in deal.get("agents_run", []):
                deal["agents_run"] = deal.get("agents_run", []) + [agent_type]

            # Track per-agent confidence for final score calculation
            source_only = _is_uncalibrated_source_report(event.get("data"))
            if source_only:
                deal.get("_confidence_scores", {}).pop(agent_type, None)
            elif "confidence" in event:
                if "_confidence_scores" not in deal:
                    deal["_confidence_scores"] = {}
                deal["_confidence_scores"][agent_type] = float(event["confidence"])

            # Use explicit final_score if provided
            if event.get("final_score") is not None:
                deal["final_score"] = float(event["final_score"])

            await redis_store.save_deal(deal_id, deal)
            try:
                from app.core.document_store import DocumentStore

                await DocumentStore.get_instance().invalidate(deal_id)
            except Exception as exc:
                logger.warning(
                    "report_cache_invalidation_failed",
                    deal_id=deal_id,
                    error_type=type(exc).__name__,
                )

    return {"status": "logged"}


class RateOutputRequest(BaseModel):
    """Request to rate an agent output"""

    action_id: int
    rating: int = Field(..., ge=1, le=5)
    feedback: Optional[str] = ""


@app.post("/api/v1/rate-output")
async def rate_output(request: RateOutputRequest):
    """
    Submit a user rating for an agent action.
    This provides a strong 'user_feedback' reward signal to the RL loop.
    """
    from app.core.quality.agent_quality_store import AgentQualityStore
    from app.core.reflection.reflection_engine import RewardEngine

    # 1. Convert 1-5 rating to 0-1 scale
    feedback_score = (request.rating - 1) / 4.0

    # 2. Re-calculate reward with user feedback
    # Note: Ideally we'd fetch the original reflection_score,
    # but for now we'll assume a baseline or use the feedback directly.
    # In a full impl, we'd query agent_actions for the existing score.
    reward_engine = RewardEngine()

    # We use a high weight for user feedback when explicitly provided
    reward = reward_engine.compute_reward(
        reflection_score=feedback_score,  # Use feedback as proxy if reflection unknown
        user_feedback=feedback_score,
        task_completed=True,
    )

    # 3. Update the record
    store = AgentQualityStore()
    await store.initialize()
    await store.reward_action(
        request.action_id, reward, f"user_rating={request.rating} | {request.feedback}"
    )

    # 4. Trigger best practice update if rating is high
    if request.rating >= 4:
        # We need the agent_name and task_type
        async with aiosqlite.connect(store.db_path) as db:
            async with db.execute(
                "SELECT agent_name, task_type FROM agent_actions WHERE id = ?",
                (request.action_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    await store.update_best_practices(row[0], row[1])

    return {"status": "success", "reward": reward}


@app.get("/api/v1/deals/{deal_id}/report")
async def generate_deal_report(
    deal_id: str, format: str = "pdf", _: bool = Depends(require_admin_token)
):
    """
    Generate a McKinsey-style report for a deal.
    Supported formats: docx, pptx, xlsx, pdf
    """
    raise HTTPException(
        status_code=409,
        detail="Direct report generation is disabled. Generate in Reports Hub, review, approve, then download.",
    )

    # Kept temporarily for reference while older clients migrate to Reports Hub.
    from fastapi.responses import Response
    from app.core.reports.report_generator import (
        generate_pptx,
        generate_excel,
        generate_pdf,
        generate_docx,
    )

    redis_store = RedisStore.get_instance()
    deal = await redis_store.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    import re

    # Collect agent results from activity log
    activities = await redis_store.get_deal_activity(deal_id)
    from app.core.reports.document_planner import prepare_document_payload
    from app.agents.base import get_agent_registry
    agent_results, analyst_data, evidence_brief = await prepare_document_payload(
        get_agent_registry(), deal, activities
    )

    # Sanitize company name for Safe HTTP Headers
    raw_name = deal.get("target_company", "report")
    safe_name = re.sub(r"[^A-Za-z0-9]", "_", raw_name)
    # Collapse multiple underscores
    safe_name = re.sub(r"_+", "_", safe_name).strip("_")
    if not safe_name:
        safe_name = "report"

    fmt = format.lower()

    # Query Knowledge Base for context
    try:
        from app.core.memory.pageindex_client import get_pageindex_client
        from app.core.reports.report_generator import KBReportEnricher

        kb = get_pageindex_client()
        enricher = KBReportEnricher(kb)
        kb_context = await enricher.get_company_context(
            deal.get("target_company", ""), deal.get("industry", "")
        )
        format_context = await enricher.get_formatting_context(deal.get("name", ""))
        kb_references = enricher.get_references()

        analyst_data["_rag_context"] = {
            "kb_context": kb_context,
            "format_context": format_context,
            "references": kb_references,
            "chunks_used": len(kb_references),
        }
    except Exception as e:
        logger.warning(f"Failed to enrich report with KB data: {e}")

    # Fetch provenance records to embed in the report for auditing/footnotes
    from app.core.provenance import get_provenance_collector

    provenance_records = await get_provenance_collector().get_records(deal_id)
    deal_stage = deal.get("current_stage", "deep_dive")
    analyst_data["_evidence_brief"] = evidence_brief

    if fmt == "pptx":
        content = generate_pptx(
            deal, analyst_data, agent_results, provenance_records, deal_stage
        )
        media_type = (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        filename = f"DealForge_{safe_name}.pptx"
    elif fmt in ("xlsx", "excel"):
        content = generate_excel(
            deal, analyst_data, agent_results, provenance_records, deal_stage
        )
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"DealForge_{safe_name}.xlsx"
    elif fmt == "pdf":
        content = generate_pdf(
            deal, analyst_data, agent_results, provenance_records, deal_stage
        )
        media_type = "application/pdf"
        filename = f"DealForge_{safe_name}.pdf"
    elif fmt == "docx":
        content = generate_docx(
            deal, analyst_data, agent_results, provenance_records, deal_stage
        )
        media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        filename = f"DealForge_{safe_name}.docx"
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: {format}. Use docx, pptx, xlsx, or pdf.",
        )

    return Response(
        content=content,
        media_type=media_type,
        headers={**REPORT_DOWNLOAD_HEADERS, "Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ══════════════════════════════════════════════════════════════════
#  Document Hub — Generate Once, Download Instantly
# ══════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════
#  OfficeCLI Template Merge Endpoint
# ══════════════════════════════════════════════════════════════════


@app.post("/api/v1/documents/merge")
async def merge_document_template(
    request: TemplateMergeRequest, _: bool = Depends(require_admin_token)
):
    """
    Merge JSON data into a DOCX/XLSX/PPTX template using OfficeCLI.

    Replace {{variable}} placeholders in template with JSON data values.
    Supports .docx, .xlsx, .pptx formats.
    """
    from app.core.reports.officecli_service import get_officecli_service

    service = get_officecli_service()
    if not service.is_available():
        raise HTTPException(
            status_code=503,
            detail="OfficeCLI not available. Install officecli binary.",
        )

    template_path = _confined_path(request.template_path)
    output_path = _confined_path(request.output_path)
    result = await service.merge_template(template_path, output_path, request.data)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Merge failed"))

    return {"success": True, "output": output_path}


@app.get("/api/v1/documents/template/{template_path:path}/variables")
async def get_template_vars(template_path: str, _: bool = Depends(require_admin_token)):
    """Extract {{variable}} names from a template"""
    from app.core.reports.officecli_service import get_officecli_service

    service = get_officecli_service()
    if not service.is_available():
        raise HTTPException(status_code=503, detail="OfficeCLI not available")

    variables = service.get_template_variables(_confined_path(template_path))
    return {"variables": variables}


# ══════════════════════════════════════════════════════════════════
#  Batch Document Processing
# ══════════════════════════════════════════════════════════════════


class BatchMergeItem(BaseModel):
    template_path: str
    output_path: str
    data: Dict[str, Any]


class BatchMergeRequest(BaseModel):
    items: List[BatchMergeItem]
    parallel: bool = True


@app.post("/api/v1/documents/batch")
async def batch_merge_documents(
    request: BatchMergeRequest, _: bool = Depends(require_admin_token)
):
    """
    Batch merge multiple templates in parallel or sequential.

    Each item contains a template_path, output_path, and data dict.
    Set parallel=true for concurrent processing (faster but more memory).
    """
    from app.core.reports.officecli_service import get_officecli_service

    service = get_officecli_service()
    if not service.is_available():
        raise HTTPException(status_code=503, detail="OfficeCLI not available")

    # Validate every path up front so a bad item rejects the whole batch.
    for item in request.items:
        item.template_path = _confined_path(item.template_path)
        item.output_path = _confined_path(item.output_path)

    results = []
    if request.parallel:
        import asyncio

        async def merge_item(item: BatchMergeItem) -> Dict[str, Any]:
            result = await service.merge_template(
                item.template_path,
                item.output_path,
                item.data,
            )
            return {
                "template": item.template_path,
                "output": item.output_path,
                **result,
            }

        tasks = [merge_item(item) for item in request.items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        results = [
            r if isinstance(r, dict) else {"success": False, "error": str(r)}
            for r in results
        ]
    else:
        for item in request.items:
            result = await service.merge_template(
                item.template_path,
                item.output_path,
                item.data,
            )
            results.append(
                {
                    "template": item.template_path,
                    "output": item.output_path,
                    **result,
                }
            )

    success_count = sum(1 for r in results if r.get("success"))
    return {
        "total": len(request.items),
        "success": success_count,
        "failed": len(request.items) - success_count,
        "results": results,
    }


# ══════════════════════════════════════════════════════════════════
#  Document Validation
# ══════════════════════════════════════════════════════════════════


@app.post("/api/v1/documents/validate")
async def validate_document(path: str):
    """
    Validate a document for structural issues using OfficeCLI.

    Returns issues found in the document (formatting, consistency, etc).
    """
    from app.core.reports.officecli_service import get_officecli_service

    service = get_officecli_service()
    if not service.is_available():
        raise HTTPException(status_code=503, detail="OfficeCLI not available")

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Document not found: {path}")

    result = await service.validate_document(path)
    return result


# ══════════════════════════════════════════════════════════════════
#  Document Hub — Generate Once, Download Instantly
# ══════════════════════════════════════════════════════════════════


class ReportApprovalRequest(BaseModel):
    reviewer: str = Field(..., min_length=2, max_length=120)
    attestation: bool = Field(..., description="Reviewer confirms the report was checked for client release")


def _require_approved_report(metadata: Optional[Dict[str, Any]]) -> None:
    if not metadata:
        raise HTTPException(status_code=404, detail="Report artifact not found.")
    if not metadata.get("report_version") or not metadata.get("analysis_fingerprint"):
        raise HTTPException(
            status_code=409,
            detail="This legacy artifact has no verified report version. Regenerate it before client delivery.",
        )
    if metadata.get("release_status") != "approved":
        raise HTTPException(
            status_code=409,
            detail="Client delivery is blocked until a reviewer approves this report in Reports Hub.",
        )


class DocumentGenerateRequest(BaseModel):
    """Optional brief for an adaptive deliverable (omit for the legacy full pack)."""

    request: str = Field("", max_length=4000, description="Plain-language ask, e.g. 'IC memo for the board as PDF'")
    doc_type: Optional[str] = Field(None, description="dd_report | ic_memo | one_pager | risk_report | financial_summary")
    formats: Optional[List[str]] = Field(None, description="Subset of docx, pdf, xlsx, pptx")
    audience: Optional[str] = Field(None, max_length=120)
    fill_gaps: bool = Field(False, description="Run the agents that own missing required sections")
    max_gap_agents: int = Field(2, ge=0, le=4)
    use_architect: bool = True

    def is_adaptive(self) -> bool:
        return bool(
            self.request.strip() or (self.doc_type and self.doc_type != "dd_report")
            or self.formats or self.fill_gaps or self.audience
        )

    def to_workflow_request(self):
        from app.core.reports.document_workflow import DocumentRequest

        return DocumentRequest(
            request=self.request, doc_type=self.doc_type, formats=self.formats, audience=self.audience,
            fill_gaps=self.fill_gaps, max_gap_agents=self.max_gap_agents, use_architect=self.use_architect,
        )


async def _load_report_inputs(deal_id: str):
    """Deal + persisted task results (activity only enriches). Shared by plan/generate."""
    redis_store = RedisStore.get_instance()
    deal = await redis_store.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    if deal.get("status") not in {"completed", "ready"}:
        raise HTTPException(status_code=409, detail="Complete the analysis before generating deliverables.")
    from app.core.tasks.task_manager import get_task_manager
    from app.core.reports.document_planner import build_report_agent_results

    task_lists = await get_task_manager().get_lists_for_deal(deal_id)
    saved_tasks = [item for task_list in task_lists for item in task_list.items]
    if task_lists and (
        not any(item.status == "done" and isinstance(item.result, dict) for item in saved_tasks)
        or any(item.status != "done" or not isinstance(item.result, dict) for item in saved_tasks)
    ):
        raise HTTPException(
            status_code=409,
            detail="Saved analysis still has unfinished or unpersisted tasks; finish or review the run before generating deliverables.",
        )
    activities = await redis_store.get_deal_activity(deal_id)
    return deal, task_lists, activities, build_report_agent_results(task_lists, activities)


@app.post("/api/v1/deals/{deal_id}/documents/plan")
async def plan_deal_documents(
    deal_id: str,
    body: Optional[DocumentGenerateRequest] = Body(None),
    _: bool = Depends(require_admin_token),
):
    """Outline first: the adaptive plan (type, formats, sections, coverage, gaps,
    assumptions, questions) without generating or publishing anything."""
    from app.agents.base import get_agent_registry
    from app.core.reports.document_workflow import build_plan

    deal, _, _, report_inputs = await _load_report_inputs(deal_id)
    req = (body or DocumentGenerateRequest()).to_workflow_request()
    plan, evidence, risks = await build_plan(req, deal, report_inputs, get_agent_registry())
    return {
        "deal_id": deal_id,
        "plan": plan.as_dict(),
        "risk_register_size": len(risks),
        "evidence": {
            "successful_analyses": evidence.get("successful_analysis_count", 0),
            "sources": len(evidence.get("sources", [])),
            "financial_data_points": len(evidence.get("data_points", [])),
            "open_unknowns": len(evidence.get("unknowns", [])),
        },
    }


async def _generate_adaptive_documents(deal_id: str, deal: Dict[str, Any], report_inputs, body: "DocumentGenerateRequest"):
    import hashlib
    import re as _re

    from app.agents.base import get_agent_registry
    from app.core.document_store import DocumentStore
    from app.core.reports.document_workflow import run_document_workflow

    outcome = await run_document_workflow(body.to_workflow_request(), deal, report_inputs, get_agent_registry())
    plan, model = outcome["plan"], outcome["model"]
    artifacts, errors = outcome["artifacts"], outcome["errors"]
    safe_name = _re.sub(r"_+", "_", _re.sub(r"[^A-Za-z0-9]", "_", deal.get("target_company", "report"))).strip("_") or "report"
    fingerprint = hashlib.sha256(json.dumps(
        {"deal": deal, "plan": plan, "model": model}, sort_keys=True, default=str
    ).encode("utf-8")).hexdigest()
    metadata = {
        "target_company": deal.get("target_company", "Unknown"),
        "deal_name": deal.get("name", "Unknown"),
        "agents_count": len(outcome["agent_results"]),
        "safe_filename": f"{safe_name}_{plan['doc_type']}",
        "report_version": str(uuid.uuid4()),
        "analysis_fingerprint": fingerprint,
        "release_status": "pending_review",
        "review_status": model["review_status"],
        "review_warnings": model["warnings"],
        "doc_type": plan["doc_type"],
        "document_title": plan["title"],
        "audience": plan["audience"],
        "expected_formats": plan["formats"],
        "plan_sections": [s["key"] for s in plan["sections"]],
    }
    doc_store = DocumentStore.get_instance()
    formats_generated = []
    if artifacts and not errors and set(artifacts) == set(plan["formats"]):
        try:
            await doc_store.replace_documents(deal_id, artifacts, metadata)
            formats_generated = list(artifacts)
        except Exception as exc:
            errors.append({"format": "bundle", "error": f"Bundle publication failed ({type(exc).__name__})."})
    manifest = await doc_store.list_documents(deal_id)
    return {
        "deal_id": deal_id,
        "status": "complete" if formats_generated else ("partial" if artifacts else "failed"),
        "formats_generated": formats_generated,
        "errors": errors,
        "documents": manifest,
        "plan": plan,
        "gap_fill": outcome["gap_fill"],
        "review_actions": outcome["review_actions"],
        "coverage": {
            "successful_analyses": outcome["evidence"].get("successful_analysis_count", 0),
            "source_records": len(outcome["evidence"].get("sources", [])),
            "financial_data_points": len(outcome["evidence"].get("data_points", [])),
            "open_data_gaps": len(outcome["evidence"].get("unknowns", [])) + len(plan["gaps"]),
            "human_review_required": model["review_status"] == "review_required" or bool(plan["gaps"]),
            "release_status": "pending_review",
        },
    }


@app.post("/api/v1/deals/{deal_id}/documents/generate")
async def generate_deal_documents(
    deal_id: str,
    body: Optional[DocumentGenerateRequest] = Body(None),
    _: bool = Depends(require_admin_token),
):
    """
    Generate & cache all report formats (DOCX, PPTX, Excel, PDF) for a deal.

    This runs the BusinessAnalyst formatting layer and KB enrichment ONCE,
    then generates each format and caches successful, validated artifacts in Redis.
    """
    from app.core.document_store import DocumentStore
    from app.core.reports.report_generator import (
        generate_pptx,
        generate_excel,
        generate_pdf,
        generate_docx,
    )

    redis_store = RedisStore.get_instance()
    doc_store = DocumentStore.get_instance()

    import re

    # ── Step 1: Collect the persisted task results; activity is metadata only ──
    deal, task_lists, activities, report_inputs = await _load_report_inputs(deal_id)

    # Adaptive deliverable (type/format/audience/gap-filling from the brief).
    requested_formats = None
    if body is not None and body.is_adaptive():
        from app.core.reports.document_workflow import interpret_request

        intent = interpret_request(body.to_workflow_request())
        if intent["doc_type"] != "dd_report":
            return await _generate_adaptive_documents(deal_id, deal, report_inputs, body)
        requested_formats = intent["formats"]

    from app.agents.base import get_agent_registry
    from app.core.reports.document_planner import prepare_document_payload
    agent_results, analyst_data, evidence_brief = await prepare_document_payload(
        get_agent_registry(), deal, report_inputs
    )

    try:
        from app.core.reports.document_planner import extract_chat_context

        analyst_data["_chat_context"] = extract_chat_context(
            await redis_store.list_conversations(limit=200), deal_id
        )
    except Exception as exc:
        logger.warning("Report chat-context lookup unavailable", deal_id=deal_id, error_type=type(exc).__name__)

    # ── Step 3: Enrich with Knowledge Base (ONCE) ──
    try:
        from app.core.memory.pageindex_client import get_pageindex_client
        from app.core.reports.report_generator import KBReportEnricher

        kb = get_pageindex_client()
        enricher = KBReportEnricher(kb)
        kb_context = await enricher.get_company_context(
            deal.get("target_company", ""), deal.get("industry", "")
        )
        format_context = await enricher.get_formatting_context(deal.get("name", ""))
        kb_references = enricher.get_references()

        analyst_data["_rag_context"] = {
            "kb_context": kb_context,
            "format_context": format_context,
            "references": kb_references,
            "chunks_used": len(kb_references),
        }
    except Exception as e:
        logger.warning(f"Failed to enrich with KB data: {e}")

    # ── Step 4: Fetch provenance (ONCE) ──
    from app.core.provenance import get_provenance_collector

    provenance_records = await get_provenance_collector().get_records(deal_id)
    deal_stage = deal.get("current_stage", "deep_dive")
    analyst_data["_evidence_brief"] = evidence_brief

    # ── Step 5: Sanitize filename ──
    raw_name = deal.get("target_company", "report")
    safe_name = re.sub(r"[^A-Za-z0-9]", "_", raw_name)
    safe_name = re.sub(r"_+", "_", safe_name).strip("_") or "report"

    # ── Step 6: Generate and validate every format before publishing any ──
    formats_generated = []
    errors = []
    pending_artifacts = {}
    report_version = str(uuid.uuid4())
    import hashlib

    snapshot_bytes = json.dumps(
        {
            "deal": deal,
            "analysis": analyst_data,
            "agent_results": agent_results,
            "provenance": provenance_records,
        },
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    metadata = {
        "target_company": deal.get("target_company", "Unknown"),
        "deal_name": deal.get("name", "Unknown"),
        "agents_count": len(agent_results),
        "safe_filename": safe_name,
        "report_version": report_version,
        "analysis_fingerprint": hashlib.sha256(snapshot_bytes).hexdigest(),
        "release_status": "pending_review",
        "review_status": analyst_data.get("_document_qa", {}).get("status", "review_required"),
        "review_warnings": analyst_data.get("_document_qa", {}).get("warnings", []),
    }

    format_generators = {
        "pptx": generate_pptx,
        "xlsx": generate_excel,
        "pdf": generate_pdf,
        "docx": generate_docx,
    }
    if requested_formats:
        format_generators = {f: g for f, g in format_generators.items() if f in requested_formats} or format_generators
    metadata["expected_formats"] = sorted(format_generators)
    metadata["doc_type"] = "dd_report"

    from app.core.reports.report_guardrails import ReportGuardrails
    from starlette.concurrency import run_in_threadpool

    async def _render_one(fmt, generator):
        try:
            content = await run_in_threadpool(
                generator, deal, analyst_data, agent_results, provenance_records, deal_stage
            )
            validation = ReportGuardrails.validate_artifact(fmt, content)
            if not validation["valid"]:
                raise ValueError("Generated artifact failed structural validation.")

            # Validate with OfficeCLI if available
            try:
                from app.core.reports.officecli_service import get_officecli_service

                oc = get_officecli_service()
                if oc.is_available() and fmt in ("pptx", "xlsx", "docx"):
                    from tempfile import TemporaryDirectory
                    import tempfile

                    with TemporaryDirectory() as tmpdir:
                        temp_path = os.path.join(tmpdir, f"temp_{fmt}.{fmt}")
                        with open(temp_path, "wb") as f:
                            f.write(content)
                        validation = await oc.validate_document(temp_path)
                        if validation.get("issues") or validation.get("success") is False:
                            raise ValueError("Generated document failed OfficeCLI validation.")
            except Exception as ve:
                if isinstance(ve, ValueError):
                    raise
                logger.warning(f"Document validation failed for {fmt}", error=str(ve))
            pending_artifacts[fmt] = content
            logger.info(f"Document Hub: Validated {fmt.upper()}", deal_id=deal_id)
        except Exception as e:
            errors.append({"format": fmt, "error": f"Generation failed ({type(e).__name__})."})
            logger.error(
                "document_generation_failed",
                deal_id=deal_id,
                format=fmt,
                error_type=type(e).__name__,
            )

    # Formats are independent: render them concurrently (each in a worker thread).
    await asyncio.gather(*(_render_one(fmt, generator) for fmt, generator in format_generators.items()))

    if not errors and len(pending_artifacts) == len(format_generators):
        try:
            await doc_store.replace_documents(deal_id, pending_artifacts, metadata)
            formats_generated = list(pending_artifacts)
        except Exception as exc:
            errors.append({"format": "bundle", "error": f"Bundle publication failed ({type(exc).__name__})."})
            logger.error("document_bundle_publish_failed", deal_id=deal_id, error_type=type(exc).__name__)

    # A failed regeneration leaves the previously published bundle untouched.
    manifest = await doc_store.list_documents(deal_id)

    return {
        "deal_id": deal_id,
        "status": "complete" if not errors else ("partial" if formats_generated else "failed"),
        "formats_generated": formats_generated,
        "errors": errors,
        "documents": manifest,
        "coverage": {
            "chat_context_attached": bool(analyst_data.get("_chat_context")),
            "successful_analyses": evidence_brief.get("successful_analysis_count", 0),
            "source_records": len(evidence_brief.get("sources", [])),
            "financial_data_points": len(evidence_brief.get("data_points", [])),
            "open_data_gaps": len(evidence_brief.get("unknowns", [])),
            "human_review_required": analyst_data.get("_document_qa", {}).get("status") == "review_required",
            "release_status": "pending_review",
        },
    }


@app.post("/api/v1/deals/{deal_id}/documents/approve")
async def approve_deal_documents(
    deal_id: str,
    request: ReportApprovalRequest,
    _: bool = Depends(require_admin_token),
):
    """Record reviewer attestation before enabling client delivery downloads."""
    from app.core.document_store import DocumentStore

    if not request.attestation:
        raise HTTPException(status_code=422, detail="Reviewer attestation is required.")
    store = DocumentStore.get_instance()
    documents = await store.list_documents(deal_id)
    if not documents:
        raise HTTPException(status_code=404, detail="Generate report artifacts before approval.")
    versions = {document.get("report_version") for document in documents}
    fingerprints = {document.get("analysis_fingerprint") for document in documents}
    formats = {document.get("format") for document in documents}
    if (
        None in versions or len(versions) != 1 or None in fingerprints
        or len(fingerprints) != 1 or formats != _expected_report_formats(documents)
    ):
        raise HTTPException(
            status_code=409,
            detail="Report artifacts are incomplete or from different analysis versions. Regenerate the full bundle before approval.",
        )
    now = datetime.now(timezone.utc).isoformat()
    approved = []
    for document in documents:
        updated = await store.update_document_metadata(deal_id, document["format"], {
            "release_status": "approved",
            "approved_at": now,
            "approved_by": request.reviewer.strip(),
            "reviewer_attestation": True,
        })
        if updated:
            approved.append(document["format"])
    if not approved:
        raise HTTPException(status_code=404, detail="No report artifacts are available to approve.")
    logger.info("report_release_approved", deal_id=deal_id, reviewer=request.reviewer.strip(), formats=approved)
    return {
        "deal_id": deal_id,
        "report_version": next(iter(versions)),
        "release_status": "approved",
        "approved_by": request.reviewer.strip(),
        "formats": approved,
    }


@app.get("/api/v1/deals/{deal_id}/documents")
async def list_deal_documents(deal_id: str, _: bool = Depends(require_admin_token)):
    """
    Return a manifest of all available cached documents for a deal.
    Each entry includes format, size, generated timestamp, and status.
    """
    from app.core.document_store import DocumentStore

    redis_store = RedisStore.get_instance()
    deal = await redis_store.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    doc_store = DocumentStore.get_instance()
    documents = await doc_store.list_documents(deal_id)

    return {
        "deal_id": deal_id,
        "deal_name": deal.get("name", "Unknown"),
        "target_company": deal.get("target_company", "Unknown"),
        "has_documents": len(documents) > 0,
        "documents": documents,
    }


@app.get("/api/v1/deals/{deal_id}/documents/bundle")
async def download_deal_bundle(deal_id: str, _: bool = Depends(require_admin_token)):
    """
    Download a ZIP bundle containing all cached documents for a deal.
    """
    import zipfile
    import io
    from fastapi.responses import Response
    from app.core.document_store import DocumentStore

    if not await RedisStore.get_instance().get_deal(deal_id):
        raise HTTPException(status_code=404, detail="Deal not found")

    doc_store = DocumentStore.get_instance()
    documents = await doc_store.list_documents(deal_id)

    if not documents:
        raise HTTPException(status_code=404, detail="No cached report bundle is available.")

    versions = {document.get("report_version") for document in documents}
    fingerprints = {document.get("analysis_fingerprint") for document in documents}
    formats = {document.get("format") for document in documents}
    if (
        len(versions) != 1 or None in versions or len(fingerprints) != 1
        or None in fingerprints or formats != _expected_report_formats(documents)
    ):
        raise HTTPException(
            status_code=409,
            detail="Report bundle versions are inconsistent or unverified. Regenerate the full bundle.",
        )

    for document in documents:
        _require_approved_report(document)

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    safe_name = documents[0].get("safe_filename", "report")

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        included = 0
        for doc_meta in documents:
            fmt = doc_meta["format"]
            content = await doc_store.get_document(deal_id, fmt)
            if content:
                filename = f"DealForge_{safe_name}.{fmt}"
                zf.writestr(filename, content)
                included += 1

    if not included:
        raise HTTPException(
            status_code=404,
            detail="Cached report files expired. Regenerate the deliverables.",
        )

    zip_buffer.seek(0)
    zip_filename = f"DealForge_{safe_name}_Reports.zip"

    return Response(
        content=zip_buffer.read(),
        media_type="application/zip",
        headers={**REPORT_DOWNLOAD_HEADERS, "Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@app.get("/api/v1/deals/{deal_id}/documents/{fmt}")
async def download_deal_document(deal_id: str, fmt: str, _: bool = Depends(require_admin_token)):
    """
    Download a single cached document by format (pdf, pptx, xlsx).
    Returns cached bytes instantly — no regeneration.
    """
    from fastapi.responses import Response
    from app.core.document_store import DocumentStore

    doc_store = DocumentStore.get_instance()
    fmt = DocumentStore.get_extension(fmt.lower())

    if fmt not in ("pptx", "xlsx", "pdf", "docx"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format: {fmt}. Use pptx, xlsx, pdf, or docx.",
        )

    # Try cache first
    content = await doc_store.get_document(deal_id, fmt)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=f"No cached {fmt.upper()} document found. Call POST /documents/generate first.",
        )

    meta = await doc_store.get_document_meta(deal_id, fmt)
    _require_approved_report(meta)
    safe_name = (meta or {}).get("safe_filename", "report")
    filename = f"DealForge_{safe_name}.{fmt}"

    return Response(
        content=content,
        media_type=DocumentStore.get_content_type(fmt),
        headers={**REPORT_DOWNLOAD_HEADERS, "Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ══════════════════════════════════════════════════════════════════
#  Conversation Persistence Endpoints (Redis-backed)
# ══════════════════════════════════════════════════════════════════


@app.get("/api/v1/conversations")
async def list_conversations():
    """List all chat conversations from Redis."""
    redis_store = RedisStore.get_instance()
    conversations = await redis_store.list_conversations()
    return {"conversations": conversations}


@app.get("/api/v1/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Get a single conversation by ID."""
    redis_store = RedisStore.get_instance()
    conv = await redis_store.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.post("/api/v1/conversations")
async def create_conversation(request: Request):
    """Create or save a full conversation."""
    body = await request.json()
    conv_id = body.get("id")
    if not conv_id:
        raise HTTPException(status_code=400, detail="Conversation must have an 'id'")
    redis_store = RedisStore.get_instance()
    await redis_store.save_conversation(conv_id, body)
    return {"status": "saved", "id": conv_id}


@app.put("/api/v1/conversations/{conv_id}")
async def save_conversation(conv_id: str, request: Request):
    """Save/overwrite a full conversation (used by frontend sync)."""
    body = await request.json()
    body["id"] = conv_id  # Ensure ID consistency
    redis_store = RedisStore.get_instance()
    await redis_store.save_conversation(conv_id, body)
    return {"status": "saved", "id": conv_id}


@app.patch("/api/v1/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    """Partially update a conversation (title, add messages, etc.)."""
    redis_store = RedisStore.get_instance()
    conv = await redis_store.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    updates = await request.json()
    conv.update(updates)
    await redis_store.save_conversation(conv_id, conv)
    return {"status": "updated", "id": conv_id}


@app.delete("/api/v1/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation."""
    redis_store = RedisStore.get_instance()
    await redis_store.delete_conversation(conv_id)
    return {"status": "deleted", "id": conv_id}


@app.delete("/api/v1/conversations")
async def clear_all_conversations():
    """Delete all conversations."""
    redis_store = RedisStore.get_instance()
    await redis_store.clear_all_conversations()
    return {"status": "cleared"}


# ══════════════════════════════════════════════════════════════════
#  Knowledge Base / PageIndex Endpoints
# ══════════════════════════════════════════════════════════════════


@app.get("/api/v1/pageindex/stats")
async def pageindex_stats():
    """Return RAG index statistics for the Knowledge Base dashboard."""
    try:
        client = get_pageindex_client()
        raw = client.get_stats()
        return {
            "total_documents": raw.get("total_documents", 0),
            "total_nodes": raw.get("total_nodes", 0),
            "storage_dir": raw.get("storage_dir", "./storage"),
            "storage_size_mb": raw.get("storage_size_mb", 0),
        }
    except Exception as e:
        logger.error("pageindex_stats failed", error=str(e))
        return {
            "total_documents": 0,
            "total_nodes": 0,
            "storage_dir": "./storage",
            "storage_size_mb": 0,
        }


@app.get("/api/v1/pageindex/documents")
async def pageindex_documents():
    """List all indexed documents for the Knowledge Base dashboard."""
    try:
        client = get_pageindex_client()
        raw = client.get_stats()
        docs = raw.get("documents", [])
        return {
            "documents": docs,
            "mode": getattr(client, "mode", "local"),
        }
    except Exception as e:
        logger.error("pageindex_documents failed", error=str(e))
        return {"documents": [], "mode": "local"}


class _QueryBody(BaseModel):
    query: str
    top_k: int = 5
    deal_id: Optional[str] = None


@app.post("/api/v1/documents/query")
async def documents_query(body: _QueryBody):
    """Semantic search over the RAG Knowledge Base."""
    try:
        from app.core.laya.graph_nodes import sanitize_brief

        safe_query = sanitize_brief(body.query, max_chars=4000)
        client = get_pageindex_client()
        filters = {"deal_id": body.deal_id} if body.deal_id else None
        chunks = await client.query(query=safe_query, top_k=body.top_k, filters=filters)
        return {
            "query": safe_query,
            "results": [
                {
                    "content": sanitize_brief(c.content, max_chars=5000),
                    "page": c.page_number,
                    "relevance": c.relevance_score,
                    "chunk_id": c.chunk_id,
                    # Citation + provenance metadata (RAG v2; absent on cloud/legacy chunks)
                    "citation": (c.metadata or {}).get("citation", ""),
                    "filename": (c.metadata or {}).get("filename", ""),
                    "doc_id": (c.metadata or {}).get("doc_id", ""),
                    "section_path": (c.metadata or {}).get("section_path", []),
                    "laya_relevance": (c.metadata or {}).get("laya_relevance"),
                }
                for c in chunks
            ],
        }
    except Exception as e:
        logger.error("documents_query failed", error_type=type(e).__name__)
        raise HTTPException(status_code=500, detail="Knowledge search failed. Check service logs for a redacted diagnostic.")


# ── Upload limits ────────────────────────────────────────────────
# Uploads were read whole into memory with no size or type check.
UPLOAD_MAX_BYTES = int(float(os.getenv("UPLOAD_MAX_MB", "50")) * 1024 * 1024)
UPLOAD_MAX_FILES = int(os.getenv("UPLOAD_MAX_FILES", "50"))
UPLOAD_ALLOWED_EXTENSIONS = {
    e.strip().lower() for e in os.getenv(
        "UPLOAD_ALLOWED_EXTENSIONS",
        ".pdf,.docx,.doc,.txt,.md,.markdown,.csv,.json,.html,.htm,.xlsx,.xlsm,.xls,.pptx",
    ).split(",") if e.strip()
}


async def _save_upload_to_temp(file: UploadFile) -> str:
    """Stream an upload to a temp file, enforcing type and size limits (415/413)."""
    import tempfile

    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in UPLOAD_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix or '(none)'}'. Allowed: {sorted(UPLOAD_ALLOWED_EXTENSIONS)}",
        )
    written = 0
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > UPLOAD_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds the {UPLOAD_MAX_BYTES // (1024 * 1024)} MB upload limit",
                )
            tmp.write(chunk)
        if written == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        tmp.close()
        return tmp.name
    except BaseException:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


@app.post("/api/v1/documents/upload")
async def documents_upload(file: UploadFile = File(...), deal_id: Optional[str] = None):
    """Upload and index a document into the Knowledge Base."""
    import tempfile, os

    tmp_path: Optional[str] = None
    try:
        tmp_path = await _save_upload_to_temp(file)

        client = get_pageindex_client()
        metadata = {"original_filename": file.filename}
        if deal_id:
            metadata["deal_id"] = deal_id

        result = await client.ingest_document(tmp_path, metadata=metadata)

        return {
            "status": "indexed",
            "index_id": getattr(result, "index_id", ""),
            "document_id": getattr(result, "document_id", ""),
            "total_pages": getattr(result, "total_pages", 0),
            "total_chunks": getattr(result, "total_chunks", 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@app.post("/api/v1/documents/upload/bulk")
async def documents_upload_bulk(
    files: List[UploadFile] = File(...), deal_id: Optional[str] = None
):
    """Bulk upload and index multiple documents into the Knowledge Base."""
    import tempfile, os

    if len(files) > UPLOAD_MAX_FILES:
        raise HTTPException(
            status_code=413, detail=f"At most {UPLOAD_MAX_FILES} files per bulk upload"
        )
    client = get_pageindex_client()
    results = []

    for file in files:
        tmp_path: Optional[str] = None
        try:
            tmp_path = await _save_upload_to_temp(file)

            metadata = {"original_filename": file.filename}
            if deal_id:
                metadata["deal_id"] = deal_id

            res = await client.ingest_document(tmp_path, metadata=metadata)

            results.append(
                {
                    "filename": file.filename,
                    "status": "indexed",
                    "index_id": getattr(res, "index_id", ""),
                }
            )
        except Exception as e:
            # Per-file failures (incl. 413/415) are reported, not fatal to the batch.
            error = e.detail if isinstance(e, HTTPException) else str(e)
            logger.error(
                "bulk_upload_file_failed", filename=file.filename, error=str(error)
            )
            results.append(
                {"filename": file.filename, "status": "failed", "error": str(error)}
            )
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    return {"results": results}


class URLIngestRequest(BaseModel):
    url: str
    deal_id: Optional[str] = None


class DirectoryIngestRequest(BaseModel):
    directory_path: str
    deal_id: Optional[str] = None


async def _index_directory_background(directory: str, deal_id: Optional[str]):
    try:
        from pathlib import Path
        import os

        client = get_pageindex_client()
        supported_extensions = {".pdf", ".docx", ".md", ".txt", ".markdown"}
        path = Path(directory)

        if not path.is_dir():
            logger.error("invalid_directory_path", path=directory)
            return

        files_to_index = []
        for root, _, files in os.walk(directory):
            for file in files:
                if Path(file).suffix.lower() in supported_extensions:
                    files_to_index.append(Path(root) / file)

        if not files_to_index:
            logger.warning("no_supported_files_found", directory=directory)
            return

        for file_path in files_to_index:
            try:
                metadata = {
                    "original_filename": file_path.name,
                    "source": "local_directory",
                    "directory_path": directory,
                }
                if deal_id:
                    metadata["deal_id"] = deal_id

                await client.ingest_document(str(file_path), metadata=metadata)
                logger.info("indexed_local_file", file=file_path.name)
            except Exception as e:
                logger.error(
                    "local_file_index_failed", file=file_path.name, error=str(e)
                )

        logger.info(
            "directory_indexing_complete",
            directory=directory,
            count=len(files_to_index),
        )
    except Exception as e:
        logger.error(
            "directory_indexing_fatal_error", directory=directory, error=str(e)
        )


@app.post("/api/v1/documents/directory")
async def documents_ingest_directory(
    request: DirectoryIngestRequest,
    background_tasks: __import__("fastapi").BackgroundTasks,
):
    """Ingest documents from a local directory in the background."""
    # Server deployments confine imports to SERVER_PATH_ROOTS (otherwise any
    # caller could index and read back arbitrary server files); local desktop
    # mode keeps importing the user's own folders. See app/core/path_guard.py.
    from app.core.path_guard import restriction_enabled

    if restriction_enabled():
        request.directory_path = _confined_path(request.directory_path)
    # We do NOT validate path.is_dir() here because the UI might send
    # a Windows path (e.g., C:\) while this backend runs in a Linux container.
    # The background task will attempt resolution and log any errors gracefully.

    background_tasks.add_task(
        _index_directory_background,
        request.directory_path,
        request.deal_id,
    )

    return {
        "status": "indexing_started",
        "message": f"Background indexing started for directory: {request.directory_path}",
    }


@app.post("/api/v1/documents/url")
async def documents_ingest_url(request: URLIngestRequest):
    """Ingest content from a URL directly into the Knowledge Base."""
    try:
        # app.core.tools.scraper_tool never existed, so this endpoint always
        # failed; use the router's scraper (blocks private/internal addresses).
        from app.core.tools.tool_router import WebScraperTool

        scraper = WebScraperTool()
        result = await scraper.execute(request.url, max_chars=200_000)
        if not result.success:
            raise HTTPException(
                status_code=400, detail=f"Scraper failed: {result.error}"
            )
        text = (result.data or {}).get("text", "") if isinstance(result.data, dict) else str(result.data or "")
        if not text.strip():
            raise HTTPException(status_code=400, detail="No extractable text at URL")

        client = get_pageindex_client()
        metadata = {
            "original_filename": (result.data or {}).get("title") or request.url,
            "source": "url",
            "url": request.url,
        }
        if request.deal_id:
            metadata["deal_id"] = request.deal_id

        res = await client.ingest_text(text, metadata=metadata)

        return {
            "status": "indexed",
            "url": request.url,
            "index_id": getattr(res, "index_id", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("url_ingest_failed", url=request.url, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/documents/{index_id}")
async def documents_delete(index_id: str):
    """Delete an indexed document from the Knowledge Base."""
    try:
        client = get_pageindex_client()
        result = await client.delete_index(index_id)
        if result:
            return {"status": "deleted", "index_id": index_id}
        else:
            raise HTTPException(
                status_code=404, detail="Index not found or could not be deleted"
            )
    except Exception as e:
        logger.error("documents_delete_failed", index_id=index_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/deals/{deal_id}/run")
async def run_deal_workflow(deal_id: str):
    """Run complete deal workflow"""
    logger.info("Running deal workflow", deal_id=deal_id)

    orchestrator = get_orchestrator_instance()

    # Run the workflow
    final_state = await orchestrator.run_deal(
        deal_id=deal_id, deal_name=f"Deal-{deal_id[:8]}", context={"deal_id": deal_id}
    )

    return {
        "deal_id": deal_id,
        "status": final_state.get("current_stage"),
        "final_score": final_state.get("final_score"),
        "final_recommendation": final_state.get("final_recommendation"),
        "stage_history": final_state.get("stage_history", []),
        "completed_at": final_state.get("completed_at"),
    }


@app.get("/api/v1/deals/{deal_id}/status")
async def get_deal_status(deal_id: str):
    """Get persisted deal status and task progress."""
    redis_store = RedisStore.get_instance()
    deal = await redis_store.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    from app.core.tasks.task_manager import get_task_manager

    task_lists = await get_task_manager().get_lists_for_deal(deal_id)
    totals = {"total": 0, "pending": 0, "in_progress": 0, "done": 0, "blocked": 0}
    for task_list in task_lists:
        for item in task_list.items:
            totals["total"] += 1
            if item.status in totals:
                totals[item.status] += 1

    return {
        "deal_id": deal_id,
        "status": deal.get("status", "created"),
        "current_stage": deal.get("current_stage", "init"),
        "task_progress": totals,
        "task_lists": [
            {
                "id": task_list.id,
                "status": task_list.status,
                "summary": task_list.to_dict()["summary"],
            }
            for task_list in task_lists
        ],
    }


@app.get("/api/v1/deals/{deal_id}/results")
async def get_deal_results(deal_id: str):
    """Return persisted task analyses and agent activity for a deal."""
    from app.core.reports.evidence_brief import _contains_execution_error

    redis_store = RedisStore.get_instance()
    deal = await redis_store.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    from app.core.tasks.task_manager import get_task_manager

    task_lists = await get_task_manager().get_lists_for_deal(deal_id)
    activities = await redis_store.get_deal_activity(deal_id)
    activities_by_agent: Dict[str, List[dict]] = {}
    for event in activities:
        activities_by_agent.setdefault(event.get("agent_type", "unknown"), []).append(event)

    analyses = []
    seen_results = set()
    for task_list in task_lists:
        for item in task_list.items:
            if item.result is None:
                continue
            result_key = json.dumps(
                [item.assigned_agent, item.result], sort_keys=True, default=str
            )
            seen_results.add(result_key)
            matching_event = next(
                (
                    event
                    for event in reversed(activities_by_agent.get(item.assigned_agent, []))
                    if event.get("data") == item.result
                ),
                {},
            )
            analyses.append(
                {
                    "agent_type": item.assigned_agent,
                    "task_title": item.title,
                    "task_id": item.id,
                    "status": item.status,
                    "success": item.status == "done" and not _contains_execution_error(item.result),
                    "provider": matching_event.get("provider"),
                    "confidence": (
                        None
                        if _is_uncalibrated_source_report(item.result)
                        else matching_event.get("confidence")
                    ),
                    "reasoning": matching_event.get("reasoning"),
                    "timestamp": item.updated_at,
                    "data": item.result,
                }
            )

    for agent_type, events in activities_by_agent.items():
        for event in events:
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            result_key = json.dumps([agent_type, data], sort_keys=True, default=str)
            if result_key in seen_results:
                continue
            seen_results.add(result_key)
            analyses.append(
                {
                    "agent_type": agent_type,
                    "task_title": event.get("summary"),
                    "task_id": event.get("task_id"),
                    "status": "done",
                    "success": bool(event.get("success", True)) and not _contains_execution_error(
                        {"data": data, "reasoning": event.get("reasoning")}
                    ),
                    "provider": event.get("provider"),
                    "confidence": (
                        None
                        if _is_uncalibrated_source_report(data)
                        else event.get("confidence")
                    ),
                    "reasoning": event.get("reasoning"),
                    "timestamp": event.get("timestamp"),
                    "data": data,
                }
            )

    analyses.sort(key=lambda item: item.get("timestamp") or "")
    section_agents = {
        "financial_analysis": "financial_analyst",
        "legal_analysis": "legal_advisor",
        "risk_assessment": "risk_assessor",
        "market_research": "market_researcher",
        "valuation_analysis": "valuation_agent",
        "dcf_lbo_analysis": "dcf_lbo_architect",
    }
    latest_by_agent = {}
    for analysis in analyses:
        latest_by_agent[analysis["agent_type"]] = analysis["data"]

    task_progress = {"total": 0, "pending": 0, "in_progress": 0, "done": 0, "blocked": 0}
    for task_list in task_lists:
        for item in task_list.items:
            task_progress["total"] += 1
            if item.status in task_progress:
                task_progress[item.status] += 1

    from app.core.reports.evidence_brief import build_evidence_brief

    return {
        "deal_id": deal_id,
        "status": deal.get("status", "created"),
        "current_stage": deal.get("current_stage", "init"),
        "analyses": analyses,
        "evidence_brief": build_evidence_brief(deal, analyses),
        "task_progress": task_progress,
        **{
            section: latest_by_agent.get(agent_type, {})
            for section, agent_type in section_agents.items()
        },
        "final_score": deal.get("final_score"),
        "recommendation": deal.get("final_recommendation"),
    }


# ===== Agent Routes =====


@app.get("/api/v1/agents")
async def list_agents():
    """List all available agents"""
    registry = get_agent_registry()
    return {"agents": registry.list_agents()}


@app.post("/api/v1/agents/run", response_model=AgentRunResponse)
async def run_agent(request: AgentRunRequest):
    """Run a specific agent"""
    registry = get_agent_registry()
    agent = registry.get(request.agent_type)

    if not agent:
        raise HTTPException(
            status_code=404, detail=f"Agent '{request.agent_type}' not found"
        )

    logger.info("Running agent", agent_type=request.agent_type, task=request.task)

    result = await agent.run(request.task, context=request.context)

    return AgentRunResponse(
        agent_type=request.agent_type,
        success=result.success,
        data=result.data,
        reasoning=result.reasoning,
        confidence=result.confidence,
        provider=agent.llm.__class__.__name__.lower().replace("client", ""),
        execution_time_ms=result.execution_time_ms,
    )


# ===== Document & PageIndex Routes =====


@app.post("/api/v1/legacy/documents/upload")
async def upload_document(deal_id: str, file: UploadFile = File(...)):
    """Upload and index a document"""
    logger.info("Uploading document", deal_id=deal_id, filename=file.filename)

    import tempfile

    # Save file safely to a temporary file (type/size limits enforced)
    temp_path = await _save_upload_to_temp(file)

    # Index with PageIndex
    pageindex = get_pageindex_client()

    try:
        index_result = await pageindex.ingest_document(
            temp_path, metadata={"deal_id": deal_id, "filename": file.filename}
        )

        return DocumentUploadResponse(
            document_id=str(uuid.uuid4()),
            filename=file.filename,
            pageindex_id=index_result.index_id,
            status="indexed",
        )

    except Exception as e:
        logger.error("Document indexing failed", error=str(e))
        return DocumentUploadResponse(
            document_id=str(uuid.uuid4()), filename=file.filename, status="failed"
        )
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass


@app.post("/api/v1/legacy/documents/query")
async def query_documents(request: DocumentQueryRequest):
    """Query indexed documents"""
    pageindex = get_pageindex_client()
    query = request.query

    try:
        chunks = await pageindex.query(query, top_k=5)

        return {
            "query": query,
            "results": [
                {
                    "content": chunk.content,
                    "page": chunk.page_number,
                    "relevance": chunk.relevance_score,
                }
                for chunk in chunks
            ],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/legacy/pageindex/stats")
async def pageindex_stats():
    """Get self-hosted PageIndex storage stats"""
    pageindex = get_pageindex_client()
    return pageindex.get_stats()


@app.get("/api/v1/legacy/pageindex/documents")
async def pageindex_documents(deal_id: Optional[str] = None):
    """List all indexed documents"""
    pageindex = get_pageindex_client()
    if pageindex._local_service:
        docs = pageindex._local_service.list_documents(deal_id)
        return {
            "mode": "local",
            "documents": [
                {
                    "doc_id": d.doc_id,
                    "filename": d.filename,
                    "file_type": d.file_type,
                    "total_pages": d.total_pages,
                    "total_nodes": d.total_nodes,
                    "created_at": d.created_at,
                    "metadata": d.metadata,
                }
                for d in docs
            ],
        }
    return {"mode": "cloud", "documents": []}


@app.get("/api/v1/models/routing")
async def model_routing():
    """Show which LLM provider each agent uses with health status"""
    from app.core.llm.model_router import get_model_router

    router = get_model_router()
    table = router.get_routing_table()
    health_table = router.get_routing_table_with_health()
    return {
        "strategy": "local-first with cloud fallback",
        "routing_table": table,
        "health": health_table,
        "agents": list(table.keys()),
        "cloud_agents": [
            k for k, v in table.items() if v in ("gemini", "openai", "mistral")
        ],
        "local_agents": [k for k, v in table.items() if v in ("ollama", "lmstudio")],
        "note": "Agents assigned to local LLMs will auto-fallback to cloud if the local provider is offline.",
    }


# ===== Dynamic Model Discovery =====


@app.get("/api/v1/models/available-legacy")
async def list_available_models():
    """
    Query all configured LLM providers for their available models IN PARALLEL.
    Each provider has its own timeout so one stall won't block the others.
    Returns a dict keyed by provider with lists of model info.
    """
    import asyncio
    import httpx as _httpx

    settings = get_settings()

    async def _get_ollama(base_url: str):
        import os

        if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER"):
            base_url = base_url.replace("localhost", "host.docker.internal").replace(
                "127.0.0.1", "host.docker.internal"
            )
        try:
            async with _httpx.AsyncClient(timeout=4.0) as c:
                r = await c.get(f"{base_url}/api/tags")
                if r.status_code == 200:
                    models = r.json().get("models", [])
                    return (
                        "ollama",
                        {
                            "status": "online",
                            "models": [
                                {
                                    "id": m.get("name", ""),
                                    "name": m.get("name", "").split(":")[0],
                                    "parameter_size": m.get("details", {}).get(
                                        "parameter_size", ""
                                    ),
                                    "quantization": m.get("details", {}).get(
                                        "quantization_level", ""
                                    ),
                                    "family": m.get("details", {}).get("family", ""),
                                }
                                for m in models
                            ],
                        },
                    )
                return (
                    "ollama",
                    {"status": "error", "models": [], "error": f"HTTP {r.status_code}"},
                )
        except Exception as e:
            return ("ollama", {"status": "offline", "models": [], "error": str(e)})

    async def _get_lmstudio(base_url: str):
        import os

        if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER"):
            base_url = base_url.replace("localhost", "host.docker.internal").replace(
                "127.0.0.1", "host.docker.internal"
            )
        try:
            async with _httpx.AsyncClient(timeout=4.0) as c:
                r = await c.get(f"{base_url}/models")
                if r.status_code == 200:
                    models = r.json().get("data", [])
                    return (
                        "lmstudio",
                        {
                            "status": "online",
                            "models": [
                                {
                                    "id": m.get("id", ""),
                                    "name": (
                                        m.get("id", "").split("/")[-1]
                                        if "/" in m.get("id", "")
                                        else m.get("id", "")
                                    ),
                                    "owned_by": m.get("owned_by", ""),
                                }
                                for m in models
                            ],
                        },
                    )
                return (
                    "lmstudio",
                    {"status": "error", "models": [], "error": f"HTTP {r.status_code}"},
                )
        except Exception as e:
            return ("lmstudio", {"status": "offline", "models": [], "error": str(e)})

    async def _get_gemini(api_key: str):
        if not api_key:
            return (
                "gemini",
                {"status": "no_key", "models": [], "error": "GEMINI_API_KEY not set"},
            )
        try:
            async with _httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": api_key},
                    params={"pageSize": 100},
                )
                if r.status_code == 200:
                    all_models = r.json().get("models", [])
                    models = [
                        {
                            "id": m.get("name", "").replace("models/", ""),
                            "name": m.get("displayName", ""),
                            "input_token_limit": m.get("inputTokenLimit", 0),
                            "output_token_limit": m.get("outputTokenLimit", 0),
                        }
                        for m in all_models
                        if "generateContent" in m.get("supportedGenerationMethods", [])
                    ]
                    return ("gemini", {"status": "online", "models": models})
                return (
                    "gemini",
                    {"status": "error", "models": [], "error": f"HTTP {r.status_code}"},
                )
        except Exception as e:
            return ("gemini", {"status": "error", "models": [], "error": str(e)})

    async def _get_mistral(api_key: str):
        if not api_key:
            return (
                "mistral",
                {"status": "no_key", "models": [], "error": "MISTRAL_API_KEY not set"},
            )
        try:
            async with _httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(
                    "https://api.mistral.ai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if r.status_code == 200:
                    models = r.json().get("data", [])
                    return (
                        "mistral",
                        {
                            "status": "online",
                            "models": [
                                {
                                    "id": m.get("id", ""),
                                    "name": m.get("id", "").replace("-", " ").title(),
                                    "owned_by": m.get("owned_by", "mistralai"),
                                }
                                for m in models
                            ],
                        },
                    )
                return (
                    "mistral",
                    {"status": "error", "models": [], "error": f"HTTP {r.status_code}"},
                )
        except Exception as e:
            return ("mistral", {"status": "error", "models": [], "error": str(e)})

    async def _get_openai(api_key: str):
        if not api_key:
            return (
                "openai",
                {"status": "no_key", "models": [], "error": "OPENAI_API_KEY not set"},
            )
        try:
            async with _httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if r.status_code == 200:
                    models = r.json().get("data", [])
                    chat_models = [
                        {
                            "id": m.get("id", ""),
                            "name": m.get("id", ""),
                            "owned_by": m.get("owned_by", ""),
                        }
                        for m in models
                        if any(
                            k in m.get("id", "").lower() for k in ("gpt", "o3", "o1")
                        )
                    ]
                    return ("openai", {"status": "online", "models": chat_models})
                return (
                    "openai",
                    {"status": "error", "models": [], "error": f"HTTP {r.status_code}"},
                )
        except Exception as e:
            return ("openai", {"status": "error", "models": [], "error": str(e)})

    async def _get_vertex():
        # Fallback list for Vertex when its key-validation call succeeds.
        models = [
            {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash (GA)"},
            {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash (GA)"},
            {"id": "gemini-3.6-flash", "name": "Gemini 3.6 Flash (GA)"},
            {"id": "gemini-3.5-flash-lite", "name": "Gemini 3.5 Flash-Lite (GA)"},
            {"id": "gemini-3.1-flash-lite", "name": "Gemini 3.1 Flash-Lite (GA)"},
            {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro (Preview)"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
            {"id": "gemini-2.5-flash-lite", "name": "Gemini 2.5 Flash-Lite"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
        ]
        return ("vertex", {"status": "online", "models": models})

    # Query all providers IN PARALLEL — each with its own individual timeout
    results = await asyncio.gather(
        asyncio.wait_for(
            _get_ollama(getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")),
            timeout=5,
        ),
        asyncio.wait_for(
            _get_lmstudio(
                getattr(settings, "LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
            ),
            timeout=5,
        ),
        asyncio.wait_for(_get_gemini(settings.GEMINI_API_KEY or ""), timeout=10),
        asyncio.wait_for(_get_mistral(settings.MISTRAL_API_KEY or ""), timeout=10),
        asyncio.wait_for(_get_openai(settings.OPENAI_API_KEY or ""), timeout=10),
        asyncio.wait_for(_get_vertex(), timeout=5),
        return_exceptions=True,
    )

    output = {}
    for item in results:
        if isinstance(item, Exception):
            # Timeout or unexpected error â€” pick a name from exception context
            continue
        key, data = item
        output[key] = data

    return output


# ===== CV and LMT Workflow Endpoints =====


class CVSimulationRequest(BaseModel):
    """Request for GP-Led CV simulation"""

    total_nav: float
    existing_debt: float
    projected_cash_flows: List[float]
    gp_carried_interest: float = 0.20
    preferred_return: float = 0.08


@app.post("/api/v1/workflows/cv-simulation")
async def run_cv_simulation(request: CVSimulationRequest):
    """Run GP-Led Continuation Vehicle waterfall simulation"""
    from app.workflows.gp_led_cv import CVWaterfallSolver, CVSimulationConfig

    config = CVSimulationConfig(
        lpa_document_id="",
        bid_spread_data={},
        gp_carried_interest=request.gp_carried_interest,
        preferred_return=request.preferred_return,
    )
    solver = CVWaterfallSolver(config)

    result = solver.solve(
        total_nav=request.total_nav,
        existing_debt=request.existing_debt,
        projected_cash_flows=request.projected_cash_flows,
    )

    return result


class LMTScanRequest(BaseModel):
    """Request for LMT loophole scan"""

    agreement_text: str


@app.post("/api/v1/workflows/lmt-scan")
async def run_lmt_scan(request: LMTScanRequest):
    """Scan credit agreement for liability management loopholes"""
    from app.workflows.lmt_simulation import LoopholeDetector

    detector = LoopholeDetector()
    findings = detector.scan(request.agreement_text)

    return {
        "total_findings": len(findings),
        "findings": findings,
        "loophole_types": list(set(f["loophole_type"] for f in findings)),
    }


class MonteCarloRequest(BaseModel):
    """Request for Monte Carlo recovery simulation"""

    tranches: List[dict]
    total_collateral: float
    num_simulations: int = 10_000


@app.post("/api/v1/workflows/monte-carlo")
async def run_monte_carlo(request: MonteCarloRequest):
    """Run Monte Carlo recovery simulation across capital structure"""
    from app.workflows.lmt_simulation import MonteCarloRecoveryModel, LMTConfig

    config = LMTConfig(num_simulations=request.num_simulations)
    model = MonteCarloRecoveryModel(config)

    result = model.run_full_capital_structure(
        tranches=request.tranches,
        total_collateral=request.total_collateral,
    )

    return result


# ===== WebSocket for Real-time Updates =====


class ConnectionManager:
    """Manage WebSocket connections"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_json(message)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await manager.connect(websocket)

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)

            # Handle different message types
            if message.get("type") == "subscribe_deal":
                deal_id = message.get("deal_id")
                await websocket.send_json({"type": "subscribed", "deal_id": deal_id})

            elif message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            elif message.get("type") == "heartbeat":
                # Tax Agent heartbeat: force immediate IRR re-computation
                deal_id = message.get("deal_id")
                logger.info(
                    "Heartbeat received â€” triggering IRR re-computation",
                    deal_id=deal_id,
                )
                await manager.broadcast(
                    {
                        "type": "heartbeat_ack",
                        "deal_id": deal_id,
                        "action": "irr_recompute_triggered",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

            elif message.get("type") == "escalation_query":
                deal_id = message.get("deal_id")
                await websocket.send_json(
                    {
                        "type": "escalation_status",
                        "deal_id": deal_id,
                        "status": "active",
                    }
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ===== Codex Integration Routes =====


@app.post("/api/v1/codex/generate")
async def generate_code(request: dict):
    """Generate code using OpenAI Codex"""
    from app.core.llm.gemini_client import OpenAIClient

    client = OpenAIClient(model=settings.CODEX_MODEL)

    try:
        result = await client.generate_code(
            prompt=request.get("prompt", ""), language=request.get("language", "python")
        )

        return {"generated_code": result, "language": request.get("language", "python")}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
#  Task Management API (Project Manager / Todo Lists)
# ═══════════════════════════════════════════════════════════

from app.core.tasks.task_manager import get_task_manager


@app.post("/api/v1/deals/{deal_id}/tasks")
async def create_todo_list(deal_id: str, body: Dict[str, Any]):
    """Create a structured todo list for a deal analysis."""
    tm = get_task_manager()
    try:
        # Use ProjectManagerAgent if available, else create from body
        items = body.get("items", [])
        title = body.get("title", f"Deal Analysis: {deal_id}")
        description = body.get("description", "")

        if not items:
            # Auto-generate using ProjectManagerAgent template
            from app.agents.project_manager import ProjectManagerAgent

            pm = ProjectManagerAgent()
            result = await pm.run(
                task=body.get("task", f"Analyze deal {deal_id}"),
                context={
                    "deal_id": deal_id,
                    "company_name": body.get("company_name", deal_id),
                },
            )
            if result.success:
                return result.data
            raise HTTPException(status_code=500, detail=result.reasoning)

        todo = await tm.create_todo_list(
            deal_id=deal_id, title=title, items=items, description=description
        )
        return todo.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/deals/{deal_id}/tasks")
async def get_deal_tasks(deal_id: str):
    """Get all todo lists for a deal."""
    tm = get_task_manager()
    lists = await tm.get_lists_for_deal(deal_id)
    return {"deal_id": deal_id, "todo_lists": [tl.to_dict() for tl in lists]}


@app.get("/api/v1/deals/{deal_id}/exports/docx")
async def download_analysis_docx(deal_id: str, _: bool = Depends(require_admin_token)):
    """Synthesize persisted agent results and download a validated evidence-led DOCX."""
    from fastapi.responses import Response
    from app.core.reports.report_generator import generate_docx
    from app.core.reports.report_guardrails import ReportGuardrails

    redis_store = RedisStore.get_instance()
    deal = await redis_store.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    tm = get_task_manager()
    lists = await tm.get_lists_for_deal(deal_id)
    with_results = [todo for todo in lists if any(item.result is not None for item in todo.items)]
    if not with_results:
        raise HTTPException(status_code=404, detail="No saved analysis results are available for this deal")

    latest = max(with_results, key=lambda todo: todo.created_at or "")
    report_deal = _deal_identity_for_export(deal, latest.to_dict())
    import re

    prompts = " ".join(item.description or "" for item in latest.items)
    company_match = re.search(
        r"\b(?:fictional|company\s+(?:called|named)|target\s+company(?:\s+is)?|"
        r"analyze|acquire|buy|acquisition\s+of|merge\s+with|for)\s+"
        r"(?:the\s+)?(?:fictional\s+)?([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,3})",
        prompts,
        re.IGNORECASE,
    )
    if company_match and str(report_deal.get("target_company") or "").strip().lower() in {"", "target company", "the target", "unknown"}:
        report_deal["target_company"] = company_match.group(1).strip()
    activities = await redis_store.get_deal_activity(deal_id)
    if not activities:
        activities = [
            {
                "agent_type": item.assigned_agent or "unknown",
                "success": item.status == "done" and isinstance(item.result, dict),
                "summary": item.title,
                "reasoning": item.description,
                "data": item.result if isinstance(item.result, dict) else {},
                "timestamp": item.updated_at or item.created_at,
            }
            for item in latest.items
        ]

    try:
        from app.agents.base import get_agent_registry
        from app.core.reports.document_planner import prepare_document_payload
        from app.core.provenance import get_provenance_collector

        agent_results, analyst_data, evidence_brief = await prepare_document_payload(
            get_agent_registry(), report_deal, activities
        )
        analyst_data["_evidence_brief"] = evidence_brief
        provenance = await get_provenance_collector().get_records(deal_id)
        content = generate_docx(
            report_deal, analyst_data, agent_results, provenance,
            report_deal.get("current_stage", "deep_dive"),
        )
        validation = ReportGuardrails.validate_artifact("docx", content)
        if not validation["valid"]:
            raise ValueError("Generated Word report failed structural validation.")
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filename_base = re.sub(r"[^A-Za-z0-9_-]+", "_", report_deal.get("target_company", "deal-report")).strip("_") or "deal-report"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            **REPORT_DOWNLOAD_HEADERS,
            "Content-Disposition": f'attachment; filename="DealForge_{filename_base}.docx"',
        },
    )


@app.get("/api/v1/deals/{deal_id}/exports/json")
async def download_analysis_json(deal_id: str, _: bool = Depends(require_admin_token)):
    """Download the latest persisted analysis as a versioned JSON artifact."""
    from fastapi.responses import Response
    from app.core.reports.analysis_export import build_analysis_export_payload

    deal = await RedisStore.get_instance().get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    lists = await get_task_manager().get_lists_for_deal(deal_id)
    with_results = [todo for todo in lists if any(item.result is not None for item in todo.items)]
    if not with_results:
        raise HTTPException(status_code=404, detail="No saved analysis results are available for this deal")
    latest = max(with_results, key=lambda todo: todo.created_at or "")
    deal = _deal_identity_for_export(deal, latest.to_dict())
    payload = build_analysis_export_payload(deal, latest.to_dict())
    filename_base = re.sub(r"[^A-Za-z0-9_-]+", "_", payload["deal"]["target_company"] or "deal-analysis").strip("_") or "deal-analysis"
    return Response(
        content=json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        media_type="application/json",
        headers={
            **REPORT_DOWNLOAD_HEADERS,
            "Content-Disposition": f'attachment; filename="DealForge_{filename_base}.json"',
        },
    )


@app.get("/api/v1/tasks/all")
async def get_all_deal_tasks():
    """Get todo lists across all deals."""
    tm = get_task_manager()
    lists = await tm.list_all_lists()
    return {"todo_lists": [tl.to_dict() for tl in lists]}


@app.get("/api/v1/tasks/{list_id}")
async def get_todo_list(list_id: str):
    """Get a specific todo list."""
    tm = get_task_manager()
    todo = await tm.get_todo_list(list_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo list not found")
    return todo.to_dict()


@app.put("/api/v1/tasks/{list_id}/items/{task_id}")
async def update_task(list_id: str, task_id: str, body: Dict[str, Any]):
    """Update a specific task (edit title, description, reassign agent, change priority)."""
    tm = get_task_manager()
    item = await tm.update_task(list_id, task_id, body)
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")
    return item.to_dict()


@app.delete("/api/v1/tasks/{list_id}/items/{task_id}")
async def delete_task(list_id: str, task_id: str):
    """Remove a task from a list."""
    tm = get_task_manager()
    if not await tm.delete_task(list_id, task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}


@app.post("/api/v1/tasks/{list_id}/items")
async def add_task(list_id: str, body: Dict[str, Any]):
    """Add a new task to an existing list."""
    tm = get_task_manager()
    item = await tm.add_task(list_id, body)
    if not item:
        raise HTTPException(status_code=404, detail="Todo list not found")
    return item.to_dict()


@app.post("/api/v1/tasks/{list_id}/approve")
async def approve_todo_list(list_id: str):
    """Approve a todo list for execution."""
    tm = get_task_manager()
    todo = await tm.approve_list(list_id)
    if not todo:
        raise HTTPException(status_code=404, detail="Todo list not found")
    return {"success": True, "status": todo.status}


@app.patch("/api/v1/tasks/{list_id}/status")
async def update_todo_list_status(list_id: str, body: Dict[str, Any]):
    """Update execution status while enforcing valid task-list transitions."""
    tm = get_task_manager()
    try:
        todo = await tm.set_list_status(list_id, body.get("status", ""))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not todo:
        raise HTTPException(status_code=404, detail="Todo list not found")
    return {"success": True, "status": todo.status}


@app.post("/api/v1/tasks/{list_id}/execute")
async def execute_todo_list(list_id: str):
    """Execute all pending tasks in the approved todo list."""
    from app.agents.project_manager import ProjectManagerAgent

    pm = ProjectManagerAgent()
    result = await pm.execute_all(list_id, agent_registry=get_agent_registry())
    return result


@app.post("/api/v1/tasks/{list_id}/reorder")
async def reorder_tasks(list_id: str, body: Dict[str, Any]):
    """Reorder tasks in a list."""
    tm = get_task_manager()
    task_ids = body.get("task_ids", [])
    if not await tm.reorder_tasks(list_id, task_ids):
        raise HTTPException(status_code=404, detail="Todo list not found")
    return {"success": True}


# ═══════════════════════════════════════════════════════════
#  Knowledge Base Ingestion API
# ═══════════════════════════════════════════════════════════


@app.post("/api/v1/knowledge/ingest")
async def ingest_knowledge_base(
    body: Optional[Dict[str, Any]] = None, _: bool = Depends(require_admin_token)
):
    """Batch-ingest knowledge base documents into RAG."""
    from app.core.tasks.knowledge_ingestion import KnowledgeIngestionService

    body = body or {}

    pageindex = get_pageindex_client()
    service = KnowledgeIngestionService(pageindex)

    directory = body.get("directory")
    if directory:
        result = await service.ingest_directory(_confined_path(directory))
    else:
        result = await service.ingest_all_knowledge_bases()

    return {"status": "completed", "results": result}


@app.get("/api/v1/knowledge/status")
async def knowledge_status():
    """Get knowledge base ingestion status."""
    pageindex = get_pageindex_client()
    try:
        stats = pageindex.get_stats()
        return {"status": "available", "stats": stats}
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}


@app.get("/api/v1/knowledge/search")
async def knowledge_search(q: str, top_k: int = 5):
    """Search the knowledge base."""
    pageindex = get_pageindex_client()
    try:
        from app.core.laya.graph_nodes import sanitize_brief
        q = sanitize_brief(q, max_chars=4000)
        results = await pageindex.query(query=q, top_k=top_k)
        return {
            "query": q,
            "results": (
                [
                    {
                        "content": sanitize_brief(r.content[:500], max_chars=500),
                        "metadata": r.metadata,
                        "score": r.relevance_score,
                    }
                    for r in results
                ]
                if results
                else []
            ),
        }
    except Exception as e:
        logger.warning("knowledge_search_failed", error_type=type(e).__name__)
        return {"query": q, "results": [], "error": "Knowledge search failed. Check service logs for a redacted diagnostic."}


# ═══════════════════════════════════════════════════════════
#  Infographic / Chart Generation API
# ═══════════════════════════════════════════════════════════


@app.post("/api/v1/charts/preview")
async def generate_chart_preview(body: Dict[str, Any]):
    """Generate a chart preview from data."""
    from app.core.reports.infographic_engine import InfographicEngine
    import base64

    chart_type = body.get("type", "football_field")
    data = body.get("data", {})

    try:
        engine = InfographicEngine()
        if chart_type == "football_field":
            png = engine.football_field_chart(data.get("valuations", []))
        elif chart_type == "waterfall":
            png = engine.revenue_waterfall(
                data.get("labels", []), data.get("values", [])
            )
        elif chart_type == "risk_heatmap":
            png = engine.risk_heatmap(data.get("risk_data", {}))
        elif chart_type == "radar":
            png = engine.deal_score_radar(data.get("scores", {}))
        elif chart_type == "sensitivity":
            png = engine.sensitivity_table(
                data.get("row_label", ""),
                data.get("col_label", ""),
                data.get("row_values", []),
                data.get("col_values", []),
                data.get("matrix", []),
            )
        elif chart_type == "bubble":
            png = engine.market_position_bubble(data.get("companies", []))
        else:
            raise HTTPException(
                status_code=400, detail=f"Unknown chart type: {chart_type}"
            )

        return {
            "chart_type": chart_type,
            "image_base64": base64.b64encode(png).decode("utf-8"),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
#  Startup Intelligence API (Crunchbase / BrightData scraping)
# ═══════════════════════════════════════════════════════════


@app.get("/api/v1/startups/search")
async def search_startup(company: str, depth: str = "standard"):
    """Research a startup using public data scraping (no API key needed)."""
    from app.core.tools.startup_intelligence_tool import StartupIntelligenceTool

    tool = StartupIntelligenceTool()
    result = await tool.execute_async(company=company, depth=depth)
    if result.success:
        return result.data
    raise HTTPException(status_code=500, detail=result.error or "Search failed")


# ═══════════════════════════════════════════════════════════
#  Scrum Master Chat API — Plan + Execute via ProjectManager
# ═══════════════════════════════════════════════════════════


@app.post("/api/v1/chat/clarify")
async def chat_clarify(request: Request):
    """
    Scrum Master Phase 1+2: Look at user prompt and determine data needs + clarifying questions.
    Limited to MAX_CLARIFICATION_ROUNDS (default 1) to prevent infinite loops.
    """
    from app.core.validation.chat_guard import check_prompt

    MAX_CLARIFICATION_ROUNDS = 3

    body = await request.json()
    prompt = body.get("prompt", "")

    # ---- guardrail: validate prompt early ----
    guard = check_prompt(prompt)
    if not guard["valid"]:
        # FastAPI will automatically serialize this dict to JSON
        return {"error": "invalid_prompt", "details": guard}

    deal_id = body.get("deal_id", "unknown")
    company_name = body.get("company_name", "Target Company")
    if str(company_name or "").strip().lower() in {"", "target company", "the target", "unknown"}:
        company_name = _explicit_company_name(prompt) or "Target Company"
    clarification_round = body.get("clarification_round", 0)
    user_skipped = body.get("user_skipped", False)
    skipped_questions = body.get("skipped_questions", [])

    from app.agents.project_manager import ProjectManagerAgent
    from app.core.llm.model_router import get_model_router
    from app.core.llm import get_llm_client
    from app.config import get_settings

    settings = get_settings()
    llms_configured = any(
        [
            settings.GEMINI_API_KEY,
            settings.OPENAI_API_KEY,
            settings.MISTRAL_API_KEY,
        ]
    )

    # If no local mode is explicitly chosen and no API keys exist, warn the user
    # (Ollama is usually active by default, but we should make sure they have a real model)
    if (
        not llms_configured
        and not settings.OLLAMA_BASE_URL
        and not settings.LMSTUDIO_BASE_URL
    ):
        return {
            "phase": "clarification",
            "clarifying_questions": [
                {
                    "question": "No AI Models are configured. Please go to Settings and add an API key (Gemini, OpenAI, Mistral) or a Local LLM URL.",
                    "reasoning": "The application requires an active LLM connection to function.",
                }
            ],
        }

    from app.core.mcp.external_client import planning_tool_summaries

    live_mcp_tools = await planning_tool_summaries()

    router = get_model_router()
    provider, _ = await router.get_provider_for_text("project_manager", prompt)
    llm_client = get_llm_client(provider)

    pm = ProjectManagerAgent(llm_client=llm_client)
    result = await pm.generate_clarifying_questions(
        prompt,
        context={
            "deal_id": deal_id,
            "company_name": company_name,
            "user_prompt": prompt,
            "available_mcp_tools": live_mcp_tools,
            "clarification_round": clarification_round,
            "user_skipped_clarification": user_skipped,
            "skipped_questions": skipped_questions,
            "max_clarification_rounds": MAX_CLARIFICATION_ROUNDS,
            "routed_provider": provider,
        },
    )
    return result


@app.post("/api/v1/chat/clarify/feedback")
async def chat_clarify_feedback(request: Request):
    """
    Store clarification Q&A pairs and deal outcome scores into memory.
    Powers Tier 2 (self-learning memory) and Tier 3 (RL quality signal).

    Body:
        deal_type: str
        questions: list of question dicts that were asked
        user_answer: str — the user's combined reply
        task_score: float (0.0–1.0) — optional agent confidence score
        user_rating: "positive" | "negative" | null — optional user rating
    """
    from app.core.memory.clarification_memory import ClarificationMemory
    from app.core.memory.question_quality_store import QuestionQualityStore

    body = await request.json()
    deal_type = body.get("deal_type", "general")
    questions = body.get("questions", [])
    user_answer = body.get("user_answer", "")
    task_score = float(body.get("task_score", 0.75))
    user_rating = body.get("user_rating", None)

    # Tier 2: store Q&A pair for future memory recall
    memory = ClarificationMemory()
    if questions and user_answer:
        memory.store(deal_type, questions, user_answer)

    # Tier 3: record outcome into quality store
    quality = QuestionQualityStore()
    qt_asked = [q.get("type", "unknown") for q in questions]
    if qt_asked:
        quality.record_outcome(deal_type, qt_asked, task_score, user_rating)

    logger.info(
        "clarify_feedback_stored",
        deal_type=deal_type,
        n_questions=len(questions),
        task_score=task_score,
        user_rating=user_rating,
    )
    return {"status": "ok", "deal_type": deal_type, "stored_questions": len(questions)}


@app.post("/api/v1/chat/plan")
async def chat_plan(request: Request):
    """
    Scrum Master Phase 3: Create structured task plan after clarification.
    """
    from app.core.validation.chat_guard import check_prompt

    body = await request.json()
    prompt = body.get("prompt", "")
    local_only = bool(body.get("local_only")) or bool(re.search(
        r"\b(?:local[- ]only|lm\s*studio\s+only|no\s+(?:cloud|remote)\s+(?:llm|models?))\b",
        prompt,
        re.IGNORECASE,
    ))

    # guardrail: ensure prompt passes basic safety checks
    guard = check_prompt(prompt)
    if not guard["valid"]:
        return {"error": "invalid_prompt", "details": guard}

    deal_id = body.get("deal_id", "unknown")
    company_name = body.get("company_name", "Target Company")
    ticker = _explicit_public_ticker(prompt)
    if str(company_name or "").strip().lower() in {"", "target company", "the target", "unknown"}:
        company_name = _explicit_company_name(prompt) or (ticker or "Target Company")
    user_answers = body.get("user_answers", [])

    # Keep the deal identity consistent with the explicit issuer in this analysis,
    # so subsequent reports and structured exports do not retain a UI placeholder.
    if deal_id and company_name and company_name != "Target Company":
        try:
            store = RedisStore.get_instance()
            deal = await store.get_deal(deal_id)
            if deal:
                current_name = str(deal.get("target_company") or "").strip().lower()
                if current_name in {"", "target company", "the target", "unknown"}:
                    update = {"target_company": company_name}
                    if ticker:
                        update["ticker"] = ticker
                    await store.update_deal(deal_id, update)
        except Exception as exc:
            logger.warning("chat_plan_deal_identity_update_failed", deal_id=deal_id, error=str(exc))

    from app.agents.project_manager import ProjectManagerAgent
    from app.core.llm.model_router import get_model_router
    from app.core.llm import get_llm_client

    router = get_model_router()
    if local_only:
        configured = router.get_provider_for_agent("project_manager")
        provider = configured if configured in {"lmstudio", "ollama"} else "lmstudio"
    else:
        provider, _ = await router.get_provider_for_text("project_manager", prompt)
    llm_client = get_llm_client(provider)

    pm = ProjectManagerAgent(llm_client=llm_client)
    result = await pm.generate_plan_with_risks(
        prompt,
        context={
            "deal_id": deal_id,
            "company_name": company_name,
            "ticker": ticker or "",
            "user_prompt": prompt,
            "user_answers": user_answers,
            "routed_provider": provider,
            "focus_mode": body.get("focus_mode", "balanced"),
        },
    )

    return {
        "success": True,
        "reasoning": result.get("message", ""),
        "confidence": (result.get("laya_decision") or {}).get("track_confidence"),
        "execution_time_ms": result.get("execution_time_ms", 0),
        "data": {
            "todo_list": result.get("todo_list", {}),
            "laya_decision": result.get("laya_decision"),
            "selected_agents": result.get("selected_agents", []),
        },
    }


async def _await_agent_execution(execution, agent_type: str, timeout_seconds: int):
    try:
        return await asyncio.wait_for(execution, timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"{agent_type} exceeded the {timeout_seconds}-second execution deadline"
        ) from exc


@app.post("/api/v1/chat/execute-task")
async def chat_execute_task(request: Request):
    """Execute a single task from the scrum master's plan via the assigned agent."""
    from app.core.validation.chat_guard import check_prompt
    from app.api.stream import (
        emit_agent_starting,
        emit_agent_completed,
        emit_agent_error,
    )
    timeout_seconds = max(1, int(get_settings().AGENT_TIMEOUT_SECONDS))

    body = await request.json()
    agent_type = body.get("agent_type", "")
    task_description = body.get("task", "")

    # task description should be checked too in case it was user-generated
    guard = check_prompt(task_description)
    if not guard["valid"]:
        return {"error": "invalid_task_description", "details": guard}

    deal_id = body.get("deal_id", "")
    task_id = body.get("task_id", "")
    task_list_id = body.get("task_list_id")
    task_title = body.get("title", "")
    ticker = body.get("ticker", "") or ""
    company_name = body.get("company_name", "") or ""
    local_only = bool(body.get("local_only", False)) or bool(re.search(
        r"\b(?:local[- ]only|lm\s*studio\s+only|no\s+(?:cloud|remote)\s+(?:llm|models?))\b",
        task_description,
        re.IGNORECASE,
    ))

    upstream_results: List[Dict[str, Any]] = []
    if task_list_id and task_id:
        task_manager = get_task_manager()
        todo = await task_manager.get_todo_list(task_list_id)
        if not todo or todo.deal_id != deal_id:
            raise HTTPException(status_code=404, detail="Approved task list not found for this deal")
        if todo.status not in {"approved", "in_progress"}:
            raise HTTPException(status_code=409, detail="Task list must be approved before agent execution")
        planned_task = next((item for item in todo.items if item.id == task_id), None)
        if not planned_task:
            raise HTTPException(status_code=404, detail="Task is not part of the approved plan")
        completed_ids = {item.id for item in todo.items if item.status == "done"}
        unmet_dependencies = [dependency for dependency in planned_task.depends_on if dependency not in completed_ids]
        if unmet_dependencies:
            raise HTTPException(
                status_code=409,
                detail={"error": "dependencies_not_satisfied", "blocked_by": unmet_dependencies},
            )
        # Hand synthesis tasks (memo, curator, reasoning, scoring, architect) the
        # persisted results of the tasks they depend on. Previously they only
        # saw whatever the client chose to send in agent_outputs, and the memo
        # agent's agent_results was never set at all.
        upstream_results = [
            {
                "agent_type": item.assigned_agent, "agent": item.assigned_agent,
                "task_id": item.id, "task_title": item.title,
                "success": True, "data": item.result,
            }
            for item in todo.items
            if item.id in planned_task.depends_on and item.status == "done" and isinstance(item.result, dict)
        ]

    # Prefer an explicitly supplied public ticker in the user/task text over
    # heuristic company-name extraction, which can select an unrelated phrase.
    ticker = ticker or _explicit_public_ticker(task_description) or ""
    if not ticker and company_name.strip().lower() not in {"target company", "the target", "unknown"}:
        ticker = company_name

    # Fallback to todo_list metadata if ticker is missing
    deal_id = body.get("deal_id")
    if not ticker and deal_id:
        tm = get_task_manager()
        lists = await tm.get_lists_for_deal(deal_id)
        if lists:
            # Use the most recent list
            latest_list = sorted(lists, key=lambda x: x.created_at, reverse=True)[0]
            if not ticker:
                ticker = latest_list.ticker
            if not company_name:
                company_name = latest_list.company_name

    # If unresolvable, pass company_name as the ticker hint so agents can use it for web search
    if not ticker and company_name.strip().lower() not in {"target company", "the target", "unknown"}:
        ticker = company_name

    from app.agents.base import get_agent_registry
    from app.core.llm.model_router import get_model_router
    from app.core.llm import get_llm_client

    # ── Alias map: AGENT_CAPABILITIES key → actual registered agent name ──
    AGENT_NAME_ALIASES = {
        "due_diligence_agent": "due_diligence_agent",
        "treasury_agent": "treasury_cash",
        "prospectus_agent": "prospectus_processing",
        "data_curator_agent": "data_curator",
        "complex_reasoning_agent": "complex_reasoning",
        "report_architect_agent": "report_architect",
    }
    resolved_type = AGENT_NAME_ALIASES.get(agent_type, agent_type)

    try:
        registry = get_agent_registry()
        agent = registry.get(resolved_type)
        input_only = bool(
            agent
            and getattr(agent, "is_input_only_request", lambda _: False)(task_description)
        )
        if input_only:
            provider, used_fallback, client = "deterministic", False, None
        elif local_only:
            configured = get_model_router().get_provider_for_agent(resolved_type)
            provider = configured if configured in {"lmstudio", "ollama"} else "lmstudio"
            used_fallback, client = False, get_llm_client(provider)
        else:
            router = get_model_router()
            provider, used_fallback = await router.get_provider_for_text(
                resolved_type, task_description
            )
            client = get_llm_client(provider)

        if agent:
            if client is not None:
                agent.llm = client
            client_outputs = body.get("agent_outputs", {})
            server_outputs = {r["agent_type"]: r["data"] for r in upstream_results}
            agent_context = {
                "deal_id": deal_id,
                "task_id": task_id,
                "ticker": ticker,
                "company_name": company_name,
                "user_prompt": body.get("user_prompt", ""),
                # Persisted upstream results win over client-supplied copies.
                "agent_outputs": {**(client_outputs if isinstance(client_outputs, dict) else {}), **server_outputs},
                "agent_results": upstream_results,
                "routed_provider": provider,
                "local_only": local_only,
            }
            if deal_id and upstream_results:
                try:
                    from app.core.knowledge_graph.graph_store import render_graph_context
                    from app.core.knowledge_graph.service import get_knowledge_graph

                    graph_block = render_graph_context(await get_knowledge_graph().deal_summary(deal_id))
                    if graph_block:
                        agent_context["knowledge_graph_context"] = graph_block
                except Exception as exc:
                    logger.debug("execute_task_graph_context_unavailable", error=str(exc))
            agent._current_context = agent_context
            if input_only or resolved_type == "financial_analyst":
                execution = agent.run(task_description, context=agent_context)
            else:
                execution = agent.run_with_structure(task_description, context=agent_context)
            result = await _await_agent_execution(execution, agent_type, timeout_seconds)
        else:
            if deal_id:
                try:
                    await emit_agent_starting(
                        deal_id, agent_type, task_id or "", task_title or ""
                    )
                except Exception:
                    pass  # SSE is best-effort

            # Fallback: use a generic LLM call
            response = await _await_agent_execution(
                client.generate(
                    prompt=task_description,
                    system_prompt=f"You are a {agent_type.replace('_', ' ')} at an investment bank. "
                    f"Provide a thorough analysis.",
                ),
                agent_type,
                timeout_seconds,
            )
            result = type(
                "AgentOutput",
                (),
                {
                    "success": True,
                    "data": {"analysis": response.get("content", "")},
                    "reasoning": response.get("content", "")[:500],
                    "confidence": 0.75,
                    "execution_time_ms": 0,
                },
            )()

        # Emit SSE: agent completed (only if success)
        if deal_id and result.success and not agent:
            try:
                await emit_agent_completed(
                    deal_id,
                    agent_type,
                    task_id or "",
                    result.data,
                    result.reasoning,
                    result.confidence,
                    result.execution_time_ms or 0,
                )
            except Exception:
                pass  # SSE is best-effort

        actual_provider = (
            "deterministic"
            if isinstance(getattr(result, "data", None), dict)
            and result.data.get("synthesis_status") == "deterministic_source_report"
            else provider
        )
        return {
            "success": result.success,
            "reasoning": result.reasoning,
            "confidence": result.confidence,
            "execution_time_ms": result.execution_time_ms,
            "data": result.data,
            "provider": actual_provider,
            "used_fallback": used_fallback,
            "task_title": task_title,
            "task_id": task_id,
        }


    except Exception as e:
        logger.error("task_execution_error", agent=agent_type, error=str(e))

        # Emit SSE: agent error
        if deal_id:
            try:
                await emit_agent_error(deal_id, agent_type, task_id or "", str(e))
            except Exception:
                pass  # SSE is best-effort

        return {
            "success": False,
            "reasoning": str(e),
            "confidence": 0.0,
            "data": {"error": str(e)},
            "provider": "none",
            "task_title": task_title,
            "task_id": task_id,
        }


# ═══════════════════════════════════════════════════════════
#  Settings API — Persist & Load frontend settings
# ═══════════════════════════════════════════════════════════


@app.get("/api/v1/settings")
async def get_settings_api(_: bool = Depends(require_admin_token)):
    """Load saved settings."""
    from app.core.settings_service import SettingsService

    svc = SettingsService.get_instance()
    return svc.get_all()


@app.post("/api/v1/settings")
async def save_settings_api(
    request: Request,
    _: bool = Depends(require_admin_token),
):
    """Save settings and apply to running system."""
    from app.core.settings_service import SettingsService

    body = await request.json()
    svc = SettingsService.get_instance()
    updated = svc.update(body)
    # Hot-reload the live router so routing/model changes apply immediately
    try:
        from app.core.llm.model_router import get_model_router

        get_model_router().refresh_from_settings()
    except Exception as e:
        logger.warning("router_refresh_failed", error=str(e))
    return {"status": "saved", "settings": updated}


# ═══════════════════════════════════════════════════════════
#  LLM Gateway API — Usage stats + direct gateway calls
# ═══════════════════════════════════════════════════════════


@app.get("/api/v1/gateway/usage")
async def gateway_usage(_: bool = Depends(require_admin_token)):
    """Get LLM gateway usage stats (RPM/TPM/cache/cost)."""
    from app.core.llm.llm_gateway import get_llm_gateway

    gw = get_llm_gateway()
    return gw.get_usage_stats()


@app.post("/api/v1/gateway/call")
async def gateway_call(
    request: Request,
    _: bool = Depends(require_admin_token),
):
    """Make a direct LLM call through the gateway (with rate limiting + retry)."""
    from app.core.llm.llm_gateway import get_llm_gateway

    body = await request.json()
    gw = get_llm_gateway()
    result = await gw.call(
        provider=body.get("provider", "gemini"),
        prompt=body.get("prompt", ""),
        system_prompt=body.get("system_prompt"),
        max_tokens=body.get("max_tokens", 1024),
        temperature=body.get("temperature", 0.7),
    )
    return result


@app.post("/api/v1/gateway/hybrid")
async def gateway_hybrid(
    request: Request,
    _: bool = Depends(require_admin_token),
):
    """Hybrid reasoning: local compress → cloud reason."""
    from app.core.llm.llm_gateway import get_llm_gateway

    body = await request.json()
    gw = get_llm_gateway()
    result = await gw.hybrid_reasoning(
        question=body.get("question", ""),
        context=body.get("context", ""),
        cloud_provider=body.get("provider", "gemini"),
    )
    return result


# ═══════════════════════════════════════════════════════════
#  Gateway Rate Limit Update
# ═══════════════════════════════════════════════════════════


@app.post("/api/v1/gateway/limits")
async def update_gateway_limits(
    request: Request,
    _: bool = Depends(require_admin_token),
):
    """Update rate limits for a vendor."""
    from app.core.llm.llm_gateway import get_llm_gateway, VendorLimits

    body = await request.json()
    gw = get_llm_gateway()
    vendor = body.get("vendor", "gemini")
    gw.update_vendor_limits(
        vendor,
        VendorLimits(
            max_rpm=body.get("max_rpm", 50),
            max_tpm=body.get("max_tpm", 100_000),
            max_rpd=body.get("max_rpd", 10_000),
        ),
    )
    return {"status": "updated", "vendor": vendor}


# duplicate endpoint block removed


# ═══════════════════════════════════════════════════════════
#  Models Available API — query all LLM providers for model lists
# ═══════════════════════════════════════════════════════════

import asyncio as _asyncio
import httpx as _httpx


async def _fetch_gemini_models(api_key: str) -> dict:
    """Fetch available Gemini models from Google AI API."""
    if not api_key or api_key == "***":
        return {"status": "no_key", "models": []}
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": api_key},
                params={"pageSize": 100},
            )
            if resp.status_code == 200:
                data = resp.json()
                models_by_id = {}
                for model in data.get("models", []):
                    model_id = model.get("name", "").removeprefix("models/")
                    if (
                        not model_id.startswith("gemini-")
                        or model_id.startswith(("gemini-1.5-", "gemini-2.0-"))
                        or "generateContent"
                        not in model.get("supportedGenerationMethods", [])
                    ):
                        continue
                    models_by_id[model_id] = {
                        "id": model_id,
                        "name": model.get("displayName") or model_id,
                        "context_window": model.get("inputTokenLimit", 0),
                        "output_token_limit": model.get("outputTokenLimit", 0),
                    }
                preferred = [
                    "gemini-3.8-flash",
                    "gemini-3.7-flash",
                    "gemini-3.6-flash",
                    "gemini-3.5-flash",
                    "gemini-3.5-flash-lite",
                    "gemini-3.1-flash-lite",
                    "gemini-3.1-pro-preview",
                    "gemini-2.5-flash",
                    "gemini-2.5-flash-lite",
                    "gemini-2.5-pro",
                ]
                rank = {model_id: index for index, model_id in enumerate(preferred)}
                models = sorted(
                    models_by_id.values(),
                    key=lambda model: (rank.get(model["id"], len(rank)), model["id"]),
                )
                return {"status": "online", "models": models}
            return {
                "status": "error",
                "models": [],
                "error": f"HTTP {resp.status_code}",
            }
    except Exception as e:
        return {"status": "offline", "models": [], "error": str(e)}


async def _fetch_nvidia_models(api_key: str, base_url: str) -> dict:
    """List models currently exposed by the configured NVIDIA NIM endpoint."""
    if not api_key or api_key == "***":
        return {"status": "no_key", "models": []}
    endpoint = f"{base_url.rstrip('/')}/models"
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                return {
                    "status": "error",
                    "models": [],
                    "error": f"HTTP {resp.status_code}",
                }
            models_by_id = {
                model["id"]: {"id": model["id"], "name": model.get("id", "")}
                for model in resp.json().get("data", [])
                if model.get("id")
            }
            models = sorted(models_by_id.values(), key=lambda model: model["id"].lower())
            return {"status": "online", "models": models}
    except Exception:
        return {"status": "offline", "models": [], "error": "NVIDIA model endpoint unavailable"}


async def _fetch_mistral_models(api_key: str) -> dict:
    """Fetch available Mistral models."""
    if not api_key or api_key == "***":
        return {"status": "no_key", "models": []}
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.mistral.ai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                _mistral_ctx = {
                    "mistral-large": 128000,
                    "mistral-small": 128000,
                    "codestral": 256000,
                    "mistral-medium": 32000,
                }
                models = [
                    {
                        "id": m["id"],
                        "name": m.get("name", m["id"]),
                        "context_window": next(
                            (v for k, v in _mistral_ctx.items() if k in m["id"]), 32000
                        ),
                        "daily_limit": "Free: 1 RPM",
                    }
                    for m in data.get("data", [])
                ]
                return {"status": "online", "models": models}
            return {
                "status": "error",
                "models": [],
                "error": f"HTTP {resp.status_code}",
            }
    except Exception as e:
        return {"status": "offline", "models": [], "error": str(e)}


async def _fetch_openai_models(api_key: str) -> dict:
    """Fetch available OpenAI models."""
    if not api_key or api_key == "***":
        return {"status": "no_key", "models": []}
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                _openai_ctx = {
                    "gpt-4o": 128000,
                    "gpt-4o-mini": 128000,
                    "gpt-4-turbo": 128000,
                    "gpt-4": 8192,
                    "gpt-3.5-turbo": 16385,
                    "o1": 200000,
                    "o3": 200000,
                }
                gpt_models = [
                    {
                        "id": m["id"],
                        "name": m["id"],
                        "context_window": next(
                            (v for k, v in _openai_ctx.items() if k in m["id"]), 8192
                        ),
                        "daily_limit": "Tier 1: 500 RPM",
                    }
                    for m in data.get("data", [])
                    if "gpt" in m["id"] or "o1" in m["id"] or "o3" in m["id"]
                ]
                return {
                    "status": "online",
                    "models": sorted(gpt_models, key=lambda x: x["id"], reverse=True),
                }
            return {
                "status": "error",
                "models": [],
                "error": f"HTTP {resp.status_code}",
            }
    except Exception as e:
        return {"status": "offline", "models": [], "error": str(e)}


async def _fetch_openrouter_models(api_key: str) -> dict:
    """Fetch OpenRouter's live model catalog without exposing credential details."""
    if not api_key or api_key == "***":
        return {"status": "no_key", "models": []}
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                return {"status": "error", "models": [], "error": f"HTTP {resp.status_code}"}
            models_by_id = {}
            for model in resp.json().get("data", []):
                model_id = model.get("id")
                if not model_id:
                    continue
                models_by_id.setdefault(model_id, {
                    "id": model_id,
                    "name": model.get("name") or model_id,
                    "context_window": model.get("context_length"),
                    "prompt_price": (model.get("pricing") or {}).get("prompt"),
                    "completion_price": (model.get("pricing") or {}).get("completion"),
                })
            models = sorted(models_by_id.values(), key=lambda item: item["id"].lower())
            return {"status": "online", "models": models}
    except Exception:
        return {"status": "offline", "models": [], "error": "OpenRouter model catalog unavailable"}


async def _fetch_ollama_models(base_url: str) -> dict:
    """Fetch locally-running Ollama models."""
    try:
        async with _httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [
                    {"id": m["name"], "name": m["name"]} for m in data.get("models", [])
                ]
                return {"status": "online", "models": models}
            return {"status": "offline", "models": []}
    except Exception:
        return {"status": "offline", "models": []}


async def _fetch_lmstudio_models(base_url: str) -> dict:
    """Fetch locally-running LM Studio models."""
    try:
        async with _httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url}/models")
            if resp.status_code == 200:
                data = resp.json()
                models = [
                    {"id": m["id"], "name": m.get("name", m["id"])}
                    for m in data.get("data", [])
                ]
                return {"status": "online", "models": models}
            return {"status": "offline", "models": []}
    except Exception:
        return {"status": "offline", "models": []}


async def _fetch_vertex_models(
    api_key: str, project_id: str = "", location: str = "us-central1"
) -> dict:
    """Validate Vertex AI API key with a real test call and return available models."""
    if not api_key or api_key == "***":
        return {"status": "no_key", "models": []}

    # Key format validation — we no longer strictly enforce prefixes
    if not api_key:
        return {"status": "no_key", "models": []}

    # Keep a concise fallback list for Vertex when its test call succeeds.
    VERTEX_MODELS = [
        {
            "id": "gemini-3.8-flash",
            "name": "Gemini 3.8 Flash (GA)",
            "context_window": 1048576,
        },
        {
            "id": "gemini-3.7-flash",
            "name": "Gemini 3.7 Flash (GA)",
            "context_window": 1048576,
        },
        {
            "id": "gemini-3.6-flash",
            "name": "Gemini 3.6 Flash (GA)",
            "context_window": 1048576,
        },
        {
            "id": "gemini-3.5-flash-lite",
            "name": "Gemini 3.5 Flash-Lite (GA)",
            "context_window": 1048576,
        },
        {
            "id": "gemini-3.1-flash-lite",
            "name": "Gemini 3.1 Flash-Lite (GA)",
            "context_window": 1048576,
        },
        {
            "id": "gemini-3.1-pro-preview",
            "name": "Gemini 3.1 Pro (Preview)",
            "context_window": 1048576,
        },
        {
            "id": "gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "context_window": 1048576,
        },
        {
            "id": "gemini-2.5-flash-lite",
            "name": "Gemini 2.5 Flash-Lite",
            "context_window": 1048576,
        },
        {
            "id": "gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "context_window": 1048576,
        },
    ]

    # Validate the key by making a minimal real API call to Vertex REST endpoint
    # We use generateContent (non-streaming) for the test call for better stability
    test_url = (
        f"https://aiplatform.googleapis.com/v1/publishers/google/models/"
        f"gemini-2.5-flash-lite:generateContent?key={api_key}"
    )
    test_payload = {
        "contents": [{"role": "user", "parts": [{"text": "Hi"}]}],
        "generationConfig": {"maxOutputTokens": 5},
    }
    try:
        async with _httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(test_url, json=test_payload)
            if resp.status_code == 200:
                return {"status": "online", "models": VERTEX_MODELS}

            # Log the failure for debugging
            logger.error(
                "vertex_test_failed", status_code=resp.status_code, body=resp.text[:500]
            )

            if resp.status_code == 400:
                # 400 can mean project not set but key is valid — still usable
                err_body = resp.json().get("error", {})
                msg = err_body.get("message", "")
                if "API_KEY_INVALID" in msg or "API key not valid" in msg:
                    return {
                        "status": "error",
                        "models": [],
                        "error": f"API key rejected by Google: {msg}",
                    }
                # Other 400s (e.g., missing project) — key is valid, configuration issue
                return {"status": "online", "models": VERTEX_MODELS}
            elif resp.status_code in (401, 403):
                err_body = resp.json().get("error", {})
                msg = err_body.get("message", resp.text[:200])
                return {"status": "error", "models": [], "error": f"Auth failed: {msg}"}
            else:
                return {
                    "status": "error",
                    "models": [],
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
    except _httpx.ReadTimeout:
        logger.error("vertex_test_timeout")
        return {
            "status": "offline",
            "models": [],
            "error": "Request timed out (Google AI is slow)",
        }
    except Exception as e:
        logger.exception("vertex_test_exception", error=str(e))
        return {"status": "offline", "models": [], "error": str(e)}


@app.get("/api/v1/models/available")
async def get_available_models(_: bool = Depends(require_admin_token)):
    """
    Query all configured LLM providers simultaneously and return their available models.
    This powers the Settings page model selection dropdowns.
    Reads from SettingsService first (UI-saved config), falling back to env vars.
    """
    from app.core.settings_service import SettingsService

    svc = SettingsService.get_instance()

    def _key(svc_key: str, env_key: str, default: str = "") -> str:
        """Read from saved settings first, then env var, then default."""
        val = svc.get(svc_key, "")
        if val and val != "***":
            return val
        return os.environ.get(env_key, default)

    gemini_key = _key("gemini_api_key", "GEMINI_API_KEY")
    openai_key = _key("openai_api_key", "OPENAI_API_KEY")
    openrouter_key = _key("openrouter_api_key", "OPENROUTER_API_KEY")
    mistral_key = _key("mistral_api_key", "MISTRAL_API_KEY").strip()
    vertex_key = _key("vertex_api_key", "VERTEX_API_KEY")
    nvidia_key = _key("nvidia_api_key", "NVIDIA_API_KEY")
    nvidia_base_url = _key(
        "nvidia_base_url",
        "NVIDIA_BASE_URL",
        "https://integrate.api.nvidia.com/v1",
    )
    vertex_project = _key("vertex_project_id", "VERTEX_PROJECT_ID")
    vertex_location = svc.get(
        "vertex_location", os.environ.get("VERTEX_LOCATION", "us-central1")
    )
    ollama_url = _key("ollama_base_url", "OLLAMA_BASE_URL", "http://localhost:11434")
    lmstudio_url = _key(
        "lmstudio_base_url", "LMSTUDIO_BASE_URL", "http://localhost:1234/v1"
    )

    # Fetch all providers in parallel
    results = await _asyncio.gather(
        _fetch_gemini_models(gemini_key),
        _fetch_mistral_models(mistral_key),
        _fetch_openai_models(openai_key),
        _fetch_openrouter_models(openrouter_key),
        _fetch_vertex_models(
            vertex_key, project_id=vertex_project, location=vertex_location
        ),
        _fetch_nvidia_models(nvidia_key, nvidia_base_url),
        _fetch_ollama_models(ollama_url),
        _fetch_lmstudio_models(lmstudio_url),
        return_exceptions=True,
    )

    def _safe(r, fallback_status="offline"):
        if isinstance(r, Exception):
            return {"status": fallback_status, "models": [], "error": str(r)}
        return r

    return {
        "gemini": _safe(results[0]),
        "mistral": _safe(results[1]),
        "openai": _safe(results[2]),
        "openrouter": _safe(results[3]),
        "vertex": _safe(results[4]),
        "nvidia": _safe(results[5]),
        "ollama": _safe(results[6]),
        "lmstudio": _safe(results[7]),
    }


@app.post("/api/v1/models/test")
async def test_cloud_model(
    request: Request,
    _: bool = Depends(require_admin_token),
):
    """Verify model discovery and a bounded completion before saving a provider key."""
    body = await request.json()
    provider = body.get("provider", "")
    api_key = body.get("api_key", "")

    if not provider or not api_key or str(api_key).strip() in {"", "***", "placeholder_key"}:
        return {"ok": False, "error": "provider and api_key required"}

    try:
        from app.core.settings_service import SettingsService
        from app.core.llm.provider_probe import probe_provider

        model_key = f"{provider}_model"
        settings_service = SettingsService.get_instance()
        model = str(body.get("model") or settings_service.get(model_key) or "").strip()
        if not model:
            return {"ok": False, "error": "Select a model before testing this provider."}

        result = {"status": "unavailable", "models": []}
        if provider == "gemini":
            result = await _fetch_gemini_models(api_key)
        elif provider == "openai":
            result = await _fetch_openai_models(api_key)
        elif provider == "openrouter":
            result = await _fetch_openrouter_models(api_key)
        elif provider == "mistral":
            result = await _fetch_mistral_models(api_key)
        elif provider == "vertex":
            project_id = body.get("project_id", "")
            location = body.get("location", "us-central1")
            result = await _fetch_vertex_models(
                api_key, project_id=project_id, location=location
            )
        elif provider == "nvidia":
            base_url = body.get(
                "base_url", "https://integrate.api.nvidia.com/v1"
            )
            result = await _fetch_nvidia_models(api_key, base_url)
        else:
            return {"ok": False, "error": "Unknown provider"}

        base_url = body.get("base_url") if provider == "nvidia" else None
        generation = await probe_provider(provider, model, api_key, base_url=base_url)
        if generation.get("ok"):
            return {
                "ok": True,
                "models": result.get("models", []) if result.get("status") == "online" else [],
                "catalog_status": result.get("status", "unavailable"),
                "generation": generation,
            }
        return {"ok": False, "error": generation.get("error", "Model generation test failed."),
                "generation": generation}
    except Exception as e:
        logger.warning("provider_generation_test_failed", provider=provider, error_type=type(e).__name__)
        return {"ok": False, "error": f"Provider test failed ({type(e).__name__})."}


@app.get("/api/v1/llm/usage")
async def llm_usage_stats(_: bool = Depends(require_admin_token)):
    """
    Return live LLM usage stats: rate limits, token counts, API key health,
    and cache performance — for the frontend monitoring dashboard.
    """
    from app.core.llm.llm_gateway import get_llm_gateway

    settings = get_settings()
    gateway = get_llm_gateway()
    raw = gateway.get_usage_stats()

    # Enrich each vendor with API key status and utilization %
    api_key_map = {
        "gemini": settings.GEMINI_API_KEY,
        "openai": settings.OPENAI_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
        "mistral": settings.MISTRAL_API_KEY,
        "vertex": settings.VERTEX_API_KEY or settings.VERTEX_PROJECT_ID,
        "nvidia": settings.NVIDIA_API_KEY,
        "claude": settings.ANTHROPIC_API_KEY,
        "groq": settings.GROQ_API_KEY,
    }

    # Known model metadata (context windows + pricing tier hints)
    model_metadata = {
        "gemini": {
            "popular_models": [
                {"id": "gemini-3.8-flash", "context": "1M", "daily_free": "By plan"},
                {"id": "gemini-3.5-flash-lite", "context": "1M", "daily_free": "By plan"},
                {"id": "gemini-2.5-flash", "context": "1M", "daily_free": "By plan"},
            ]
        },
        "openai": {
            "popular_models": [
                {"id": "gpt-4o", "context": "128K", "daily_free": "Tier 1: 500 RPM"},
                {
                    "id": "gpt-4o-mini",
                    "context": "128K",
                    "daily_free": "Tier 1: 500 RPM",
                },
                {
                    "id": "gpt-4-turbo",
                    "context": "128K",
                    "daily_free": "Tier 1: 500 RPM",
                },
                {"id": "o1", "context": "200K", "daily_free": "Tier 1: 500 RPM"},
            ]
        },
        "openrouter": {"popular_models": []},
        "mistral": {
            "popular_models": [
                {
                    "id": "mistral-large-latest",
                    "context": "128K",
                    "daily_free": "Free: 1 RPM",
                },
                {
                    "id": "mistral-small-latest",
                    "context": "128K",
                    "daily_free": "Free: 1 RPM",
                },
                {
                    "id": "codestral-latest",
                    "context": "256K",
                    "daily_free": "Free: 1 RPM",
                },
            ]
        },
        "vertex": {
            "popular_models": [
                {"id": "gemini-2.5-flash", "context": "1M", "daily_free": "PAYG"},
                {"id": "gemini-2.5-pro", "context": "1M", "daily_free": "PAYG"},
            ]
        },
        "nvidia": {
            "popular_models": [
                {"id": "z-ai/glm-5.3", "context": "1M", "daily_free": "By model/plan"},
            ]
        },
        "claude": {
            "popular_models": [
                {"id": "claude-3-5-sonnet", "context": "200K", "daily_free": "PAYG"},
                {"id": "claude-3-7-sonnet", "context": "200K", "daily_free": "PAYG"},
            ]
        },
        "groq": {
            "popular_models": [
                {"id": "llama-3.1-70b-versatile", "context": "128K", "daily_free": "Free tier"},
                {"id": "llama-3.1-8b-instant", "context": "128K", "daily_free": "Free tier"},
            ]
        },
    }

    enriched_vendors = {}
    for vendor_name, vendor_data in raw.get("vendors", {}).items():
        key = api_key_map.get(vendor_name)
        rpm = vendor_data.get("rpm", {})
        tpm = vendor_data.get("tpm", {})
        rpd = vendor_data.get("rpd", {})

        # Utilisation percentages
        rpm_pct = round((rpm.get("current", 0) / max(rpm.get("limit", 1), 1)) * 100, 1)
        tpm_pct = round((tpm.get("current", 0) / max(tpm.get("limit", 1), 1)) * 100, 1)
        rpd_pct = round((rpd.get("current", 0) / max(rpd.get("limit", 1), 1)) * 100, 1)

        enriched_vendors[vendor_name] = {
            **vendor_data,
            "api_key_configured": bool(key),
            "api_key_masked": (
                f"{key[:4]}...{key[-4:]}"
                if key and len(key) > 8
                else ("***" if key else "")
            ),
            "rpm_pct": rpm_pct,
            "tpm_pct": tpm_pct,
            "rpd_pct": rpd_pct,
            "model_metadata": model_metadata.get(vendor_name, {}),
        }

    return {
        "vendors": enriched_vendors,
        "cache": raw.get("cache", {}),
        "recent_calls": raw.get("recent_calls", 0),
    }


@app.get("/api/v1/laya/status")
async def laya_status(_: bool = Depends(require_admin_token)):
    """Live Laya System-1 status for the Settings UI.

    Reports the resolved backend, LM Studio reachability + loaded models,
    and local/remote availability. Never raises — unreachable services
    report ``reachable: False``.
    """
    from app.core.laya.client import get_laya_client, laya_configured

    client = get_laya_client()
    base, model, model_source, loaded_models = await client._resolve_lmstudio_endpoint()
    from app.core.llm.model_router import get_model_router

    generation_healthy = await get_model_router().check_local_health("lmstudio", force=True)
    lmstudio: Dict[str, Any] = {
        "base_url": base,
        "model": model,
        "model_source": model_source,
        "loaded_models": loaded_models,
        "reachable": False,
        "generation_healthy": generation_healthy,
        "health_status": "healthy" if generation_healthy else "unhealthy",
        "models": [],
    }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(f"{base}/models")
            if resp.status_code == 200:
                lmstudio["reachable"] = True
                lmstudio["models"] = [
                    m.get("id", "")
                    for m in (resp.json().get("data", []) or [])
                    if m.get("id")
                ][:50]
    except Exception:
        pass

    return {
        "enabled": laya_configured(),
        "mode": client._mode(),
        "backend": client.backend,
        "lmstudio": lmstudio,
        "local": {"installed": client._local_importable()},
        "remote": {"url": client._remote_url()},
        "runtime": client.stats(),
    }


# ═══════════════════════════════════════════════════════════
#  DealForge 2.0 — MCP Integration API endpoints
# ═══════════════════════════════════════════════════════════


@app.get("/api/v1/mcp/status")
@app.get("/api/v1/mcp/providers")
async def mcp_providers(_: bool = Depends(require_admin_token)):
    """
    Returns the status of all registered MCP data providers, including
    runtime-configured ones (set via the Settings UI).
    """
    from app.core.mcp import get_provider_status

    return {"providers": get_provider_status()}


@app.get("/api/v1/mcp/tools")
async def mcp_tools(_: bool = Depends(require_admin_token)):
    """Discover live, read-only tools from configured Streamable HTTP MCP servers."""
    from app.core.mcp.external_client import planning_tool_summaries

    return {"tools": await planning_tool_summaries()}


@app.post("/api/v1/mcp/initialize")
async def mcp_initialize(
    request: Request,
    _: bool = Depends(require_admin_token),
):
    """
    Initialize and live-test an MCP provider API key.
    Persists the key in the runtime store for this session.
    Body: { "provider": "finnhub", "api_key": "..." }
    """
    from app.core.mcp import initialize_provider

    body = await request.json()
    provider = body.get("provider", "")
    api_key = body.get("api_key", "")
    if not provider or not api_key:
        return {"ok": False, "error": "provider and api_key are required"}
    result = await initialize_provider(provider, api_key)
    return result


@app.post("/api/v1/mcp/search")
async def mcp_search_company(
    request: Request,
    _: bool = Depends(require_admin_token),
):
    """Search for a company across all configured MCP providers."""
    from app.core.mcp import get_mcp_router

    body = await request.json()
    company_name = body.get("company_name", "")
    if not company_name:
        return {"error": "company_name is required"}
    router = get_mcp_router()
    result = await router.search_company(company_name)
    return result


# ═══════════════════════════════════════════════════════════
#  Scrum Master — Reasoning, Clarification & Planning API
# ═══════════════════════════════════════════════════════════


@app.post("/api/v1/scrum/clarify")
async def scrum_clarify(request: Request):
    """
    Phase 1 + 2 of the Scrum Master workflow:
    - Identify required data/files for the task
    - Determine what can be auto-fetched via MCP vs what user must provide
    - Generate clarifying questions with visible reasoning
    Body: { "task": "...", "context": {} }
    """
    from app.agents.project_manager import ProjectManagerAgent
    from app.core.mcp.external_client import planning_tool_summaries

    from app.core.validation.chat_guard import check_prompt

    body = await request.json()
    task = body.get("task", "")
    context = body.get("context", {})

    if not task:
        return {"error": "task is required"}

    # guardrail: simple sanity check on the task text
    guard = check_prompt(task)
    if not guard["valid"]:
        return {"error": "invalid_task", "details": guard}

    # Build MCP capability context for the agent
    context["available_mcp_tools"] = await planning_tool_summaries()

    agent = ProjectManagerAgent()
    result = await agent.generate_clarifying_questions(task, context)
    return result


@app.post("/api/v1/scrum/plan")
async def scrum_plan(request: Request):
    """
    Phase 3 of the Scrum Master workflow:
    - Takes user answers to clarification questions
    - Generates a structured MECE todo list with risk flags per task
    - Does NOT auto-execute — returns plan for user approval
    Body: { "task": "...", "context": {}, "answers": [...], "provided_data": {} }
    """
    from app.agents.project_manager import ProjectManagerAgent
    from app.core.mcp.external_client import planning_tool_summaries
    from app.core.validation.chat_guard import check_prompt

    body = await request.json()
    task = body.get("task", "")
    context = body.get("context", {})
    answers = body.get("answers", [])
    provided_data = body.get("provided_data", {})

    if not task:
        return {"error": "task is required"}

    guard = check_prompt(task)
    if not guard["valid"]:
        return {"error": "invalid_task", "details": guard}

    context["available_mcp_tools"] = await planning_tool_summaries()
    context["user_answers"] = answers
    context["provided_data"] = provided_data

    agent = ProjectManagerAgent()
    result = await agent.generate_plan_with_risks(task, context)
    return result


# ═══════════════════════════════════════════════════════════
#  DealForge 2.0 — Skills Library API endpoints
# ═══════════════════════════════════════════════════════════


@app.get("/api/v1/skills/list")
async def list_skills():
    """Return all available domain skills in the Skills Library."""
    from app.core.skills import list_available_skills, SKILL_MAP

    skills = list_available_skills()
    # Build a reverse map: filename → keywords that trigger it
    reverse_map: dict = {}
    for keyword, filename in SKILL_MAP.items():
        if filename not in reverse_map:
            reverse_map[filename] = []
        reverse_map[filename].append(keyword)
    return {
        "skill_count": len(skills),
        "skills": [
            {
                "filename": s,
                "trigger_keywords": reverse_map.get(s, [])[:5],  # Show first 5 keywords
            }
            for s in sorted(skills)
        ],
    }


# ═══════════════════════════════════════════════════════════
#  OFAS — Multi-Agent Financial Analysis Endpoints
# ═══════════════════════════════════════════════════════════

_ofas_missions: Dict[str, dict] = {}  # deal_id -> OFASMissionState


@app.get("/api/v1/ofas/templates")
async def ofas_list_templates():
    """List available OFAS deal type templates"""
    from app.agents.ofas_supervisor import DEAL_TYPE_TEMPLATES

    return {
        "templates": {
            k: {"description": v["description"], "task_count": len(v["tasks"])}
            for k, v in DEAL_TYPE_TEMPLATES.items()
        }
    }


@app.post("/api/v1/ofas/mission")
async def ofas_create_mission(request: Request):
    """Create and plan an OFAS mission"""
    body = await request.json()

    ticker = body.get("ticker", "")
    objective = body.get("objective", "")
    deal_type = body.get("deal_type", "standard_corporate")
    constraints = body.get("constraints", [])
    deal_id = body.get("deal_id", f"ofas_{ticker}_{uuid.uuid4().hex[:8]}")

    if not ticker or not objective:
        raise HTTPException(status_code=400, detail="ticker and objective are required")

    try:
        from app.agents.ofas_supervisor import OFASSupervisorAgent

        supervisor = OFASSupervisorAgent()
        result = await supervisor.run(
            task=objective,
            context={
                "action": "plan_mission",
                "ticker": ticker,
                "deal_type": deal_type,
                "deal_id": deal_id,
                "constraints": constraints,
            },
        )

        if result.success:
            mission = result.data.get("mission", {})
            _ofas_missions[deal_id] = mission
            return {
                "deal_id": deal_id,
                "status": "planned",
                "mission": mission,
                "metadata": {
                    "deal_type": deal_type,
                    "task_count": result.data.get("task_count", 0),
                    "agents_involved": result.data.get("agents_involved", []),
                    "ready_tasks": result.data.get("ready_tasks", []),
                },
            }
        else:
            raise HTTPException(
                status_code=400, detail=result.data.get("error", "Planning failed")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("OFAS mission creation failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/ofas/mission/{deal_id}/status")
async def ofas_mission_status(deal_id: str):
    """Get OFAS mission status"""
    mission = _ofas_missions.get(deal_id)
    if not mission:
        raise HTTPException(status_code=404, detail=f"Mission {deal_id} not found")

    try:
        from app.agents.ofas_supervisor import OFASSupervisorAgent

        supervisor = OFASSupervisorAgent()
        result = await supervisor.run(
            task="status",
            context={"action": "get_status", "mission": mission},
        )

        return {
            "deal_id": deal_id,
            **result.data,
        }

    except Exception as e:
        logger.error("OFAS status check failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/ofas/financial-data")
async def ofas_fetch_financial_data(request: Request):
    """Fetch financial data for a ticker (SEC EDGAR + Yahoo Finance)"""
    body = await request.json()

    ticker = body.get("ticker", "")
    statements = body.get("statements", ["income", "balance", "cashflow"])
    periods = body.get("periods", 5)
    frequency = body.get("frequency", "annual")

    if not ticker:
        raise HTTPException(status_code=400, detail="ticker or company_name is required")

    try:
        from app.core.tools.financial_data_api import FetchFinancialStatementsTool

        tool = FetchFinancialStatementsTool()
        result = await tool.execute(
            ticker=ticker,
            company_name=body.get("company_name"),
            statements=statements,
            periods=periods,
            frequency=frequency,
        )

        if result.success:
            return result.data
        else:
            raise HTTPException(status_code=404, detail=result.error)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Financial data fetch failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ── Phase 5: OFAS Monitoring & Review Gate Endpoints ──


@app.get("/api/v1/ofas/mission/{deal_id}/monitor")
async def ofas_mission_monitor(deal_id: str):
    """Get real-time monitoring data for an OFAS mission"""
    mission = _ofas_missions.get(deal_id)
    if not mission:
        raise HTTPException(status_code=404, detail=f"Mission {deal_id} not found")

    try:
        from app.orchestrator.ofas_engine import OFASExecutionEngine

        engine = OFASExecutionEngine()
        return engine.mission_monitor(mission)

    except Exception as e:
        logger.error("OFAS monitor failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/ofas/mission/{deal_id}/review-gate")
async def ofas_review_gate(deal_id: str, request: Request):
    """Evaluate a review gate checkpoint for an OFAS mission"""
    mission = _ofas_missions.get(deal_id)
    if not mission:
        raise HTTPException(status_code=404, detail=f"Mission {deal_id} not found")

    body = await request.json()
    gate_name = body.get("gate_name", "")
    human_decision = body.get("approved")

    if not gate_name:
        raise HTTPException(status_code=400, detail="gate_name is required")

    try:
        from app.orchestrator.ofas_engine import OFASExecutionEngine

        engine = OFASExecutionEngine()
        result = await engine.evaluate_review_gate(gate_name, mission, human_decision)
        return result.to_dict()

    except Exception as e:
        logger.error("OFAS review gate failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/ofas/mission/{deal_id}/recover")
async def ofas_recover_blocked(deal_id: str):
    """Attempt to recover blocked tasks in an OFAS mission"""
    mission = _ofas_missions.get(deal_id)
    if not mission:
        raise HTTPException(status_code=404, detail=f"Mission {deal_id} not found")

    try:
        from app.orchestrator.ofas_engine import OFASExecutionEngine

        engine = OFASExecutionEngine()
        updated = await engine.recover_blocked_tasks(mission, {})
        _ofas_missions[deal_id] = updated

        return {
            "deal_id": deal_id,
            "status": "recovery_attempted",
            "blocked_remaining": sum(
                1 for t in updated.get("tasks", []) if t["status"] == "blocked"
            ),
        }

    except Exception as e:
        logger.error("OFAS recovery failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ===== SSE Streaming API =====

from app.api.stream import router as stream_router
from app.api.ratings import router as ratings_router
from app.api.pauses import router as pauses_router

app.include_router(stream_router)
app.include_router(ratings_router)
app.include_router(pauses_router)


# ===== Main Entry Point =====


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info",
    )
