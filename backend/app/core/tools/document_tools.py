"""Agent tool: build validated documents and decks with Python libraries.

Agents must never "write" a deliverable as LLM text. They call this tool,
which runs the adaptive document workflow (plan → compose from curated
evidence → render with python-docx / reportlab / openpyxl / python-pptx) and
returns real files plus their validation status.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

import structlog

from app.core.tools.base_tool import BaseTool, ToolResult

logger = structlog.get_logger(__name__)


class BuildDocumentTool(BaseTool):
    output_quality = "data"
    timeout_seconds = 180

    def __init__(self):
        super().__init__(
            name="build_document",
            description=(
                "Build a deliverable file (IC memo, one-pager, risk report, financial summary, "
                "board deck, or full diligence pack) as DOCX, PDF, XLSX or PPTX using Python "
                "document libraries. Content comes only from the supplied agent results; every "
                "file is re-opened and validated. Use this instead of writing documents as text."
            ),
        )

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "Plain-language ask, e.g. 'IC memo for the board as PDF'."},
                "doc_type": {
                    "type": "string",
                    "enum": ["ic_memo", "one_pager", "risk_report", "financial_summary", "board_deck"],
                    "description": "Document type (optional; inferred from request).",
                },
                "formats": {"type": "array", "items": {"type": "string", "enum": ["docx", "pdf", "xlsx", "pptx"]}},
                "audience": {"type": "string"},
                "deal_id": {"type": "string", "description": "Deal whose recorded analysis to use."},
                "deal": {"type": "object", "description": "Deal record (id, name, target_company, industry)."},
                "agent_results": {
                    "type": "array", "items": {"type": "object"},
                    "description": "Agent outputs [{agent_type, success, data}] to build from.",
                },
                "content": {
                    "type": "object",
                    "description": (
                        "Alternative to agent_results: document text you wrote, as "
                        "{title?, sections: {<section_key>: {paragraphs?, bullets?, table?, sources?}}, sources?}. "
                        "Requires doc_type. Validated against the document guide and marked review-required."
                    ),
                },
            },
            "required": [],
        }

    async def execute(
        self,
        request: str = "",
        doc_type: Optional[str] = None,
        formats: Optional[List[str]] = None,
        audience: Optional[str] = None,
        deal_id: Optional[str] = None,
        deal: Optional[Dict[str, Any]] = None,
        agent_results: Optional[List[Dict[str, Any]]] = None,
        content: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> ToolResult:
        from app.core.reports.document_workflow import DocumentRequest, run_document_workflow

        if content is not None:
            return await self._build_from_content(doc_type, content, formats, audience, deal)

        deal = dict(deal or {})
        if deal_id and not deal.get("id"):
            deal["id"] = deal_id
            try:
                from app.core.redis_store import RedisStore

                stored = await RedisStore.get_instance().get_deal(deal_id)
                if stored:
                    deal = {**stored, **deal}
            except Exception as exc:
                logger.debug("build_document_deal_lookup_unavailable", error=str(exc))
        results = [r for r in (agent_results or []) if isinstance(r, dict)]
        if not results:
            return ToolResult(False, None, error="agent_results are required to build a document from evidence.")
        for item in results:
            item.setdefault("agent_type", item.get("agent") or "unknown")
            item.setdefault("success", isinstance(item.get("data"), dict))

        req = DocumentRequest(
            request=request or "", doc_type=doc_type, formats=formats, audience=audience,
            fill_gaps=False, use_architect=False,
        )
        outcome = await run_document_workflow(req, deal, results, registry=None)
        files = {fmt: base64.b64encode(blob).decode("ascii") for fmt, blob in outcome["artifacts"].items()}
        if not files:
            return ToolResult(False, None, error="; ".join(e["error"] for e in outcome["errors"]) or "No file produced.")
        plan = outcome["plan"]
        return ToolResult(
            success=not outcome["errors"],
            data={
                "doc_type": plan["doc_type"],
                "title": plan["title"],
                "formats": sorted(files),
                "files_base64": files,
                "sizes_bytes": {fmt: len(blob) for fmt, blob in outcome["artifacts"].items()},
                "sections": [s["key"] for s in plan["sections"]],
                "gaps": plan["gaps"],
                "review_status": outcome["model"]["review_status"],
                "errors": outcome["errors"],
                "built_with": "python-docx / reportlab / openpyxl / python-pptx",
            },
            error="; ".join(e["error"] for e in outcome["errors"]) or None,
        )

    async def _build_from_content(
        self, doc_type: Optional[str], content: Dict[str, Any], formats: Optional[List[str]],
        audience: Optional[str], deal: Optional[Dict[str, Any]],
    ) -> ToolResult:
        """LLM-written content -> guide validation -> the same renderers as evidence-built documents."""
        from app.core.reports import doc_guidance
        from app.core.reports.document_workflow import render_formats

        if not doc_type:
            return ToolResult(False, None, error="doc_type is required when content is supplied.")
        try:
            result = doc_guidance.validate_content(doc_type, content, formats=formats, audience=audience, deal=deal)
        except doc_guidance.ContentError as exc:
            return ToolResult(False, None, error=str(exc))
        if not result.ok:
            return ToolResult(False, {"issues": result.issues}, error="; ".join(
                f"{i['section'] + ': ' if i['section'] else ''}{i['message']}" for i in result.errors))
        model = result.build_model()
        artifacts, errors = await render_formats(model, result.formats)
        if not artifacts:
            return ToolResult(False, None, error="; ".join(e["error"] for e in errors) or "No file produced.")
        return ToolResult(
            success=not errors,
            data={
                "doc_type": result.doc_type, "title": result.title, "formats": sorted(artifacts),
                "files_base64": {f: base64.b64encode(b).decode("ascii") for f, b in artifacts.items()},
                "sizes_bytes": {f: len(b) for f, b in artifacts.items()},
                "sections": [s["key"] for s in result.sections], "issues": result.issues,
                "review_status": model["review_status"], "errors": errors,
                "content_source": "llm_supplied",
                "built_with": "python-docx / reportlab / openpyxl / python-pptx",
            },
            error="; ".join(e["error"] for e in errors) or None,
        )
