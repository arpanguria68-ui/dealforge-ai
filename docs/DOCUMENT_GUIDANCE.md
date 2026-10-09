# Document guidance layer

A small contract between an LLM and the document renderers. The model writes
**content**; Python owns **structure, validation and presentation**.

```
guidance  ->  LLM response  ->  parse  ->  validate  ->  document model  ->  DOCX / PDF / XLSX / PPTX
```

Code: `backend/app/core/reports/doc_guidance.py`. Renderers are unchanged
(`document_workflow.render_*`), so every format and the artifact validation
(`ReportGuardrails.deep_validate`) work for LLM content too.

## 1. Ask the model (guidance)

`GET /api/v1/documents/guidance/{doc_type}` returns:

| Field | Use |
|---|---|
| `guide` | Per-section purpose, shape (`paragraphs` / `bullets` / `table`), required flag, limits |
| `instructions` | Prompt text to give the model |
| `response_schema` | JSON Schema for structured-output / tool-input APIs |

Document types: `ic_memo`, `one_pager`, `risk_report`, `financial_summary`, `board_deck`, `dd_report`.

## 2. Send the response back

Canonical shape (lenient shapes such as plain strings or bullet lists are also accepted):

```json
{
  "title": "Acme IC Memo",
  "sections": {
    "executive_summary": {"paragraphs": ["..."], "sources": ["S1"]},
    "financial_metrics": {"table": {"columns": ["Metric","Period","Value","Source"], "rows": [["Revenue","FY24","$118M","S1"]]}, "sources": ["S1"]}
  },
  "sources": [{"id": "S1", "title": "Acme 10-K", "url": "https://..."}]
}
```

* **HTTP:** `POST /api/v1/deals/{id}/documents/from-content` with `{doc_type, content, formats?, audience?, dry_run?}`.
  `dry_run: true` validates only. Valid content is rendered and published as a *pending-review* release.
* **Agents:** the `build_document` tool accepts `content` (plus `doc_type`) instead of `agent_results`.

## 3. What validation does

| Result | Cases |
|---|---|
| **Error** (nothing is built) | Not JSON / not an object; missing or empty required section; malformed table or row wider than its columns; content over 200 KB |
| **Warning** (built, carried into the document) | Unknown section (ignored); over the length target; figures with no `sources`; cited source id not listed; cell over 400 chars |

Rules that always hold: nothing is silently truncated or "fixed" beyond padding short
table rows; control characters are stripped; LLM-written documents are always
`review_required` and say so on the first page.

## Adding a section or document type

Add the section to `SECTION_TITLES` and `SECTION_GUIDE` (purpose, guidance, shape, limits),
then list it under a type in `DOC_TYPES`. The guide, schema, prompt and validator follow.
