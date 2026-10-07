# DealForge UI UAT Study: 60 Cases

**Run date:** 2026-10-07 (Asia/Calcutta)  
**Test surface:** User-facing web UI at `http://127.0.0.1:3001/`  
**API health:** `http://127.0.0.1:8005/health` returned healthy; Redis TCP check succeeded.  
**Test data:** Fictional Cedarbrook Pumps; USD millions; no client data intentionally entered.  
**Execution summary:** 54 cases evaluated in the UI: 46 pass, 8 fail; 1 blocked; 5 not run.

## Scope and Safety

Cases were exercised through the visible UI and accessibility-visible controls. No task plan was approved when it omitted a requested workstream. No provider “Initialize & Test” actions were run because they can transmit credentials, incur usage, or send data to third parties. Report generation that requires completed analysis was not run because the current plan was invalid. A previous live run visible in the application (2026-10-02) is called out as historical evidence and is not presented as a fresh 2026-10-07 execution.

Status meanings: **PASS** = observed behavior matched the case expectation; **FAIL** = observed behavior contradicted it; **BLOCKED** = could not safely reach the test precondition; **NOT RUN** = intentionally deferred.

## Findings Requiring Attention

1. **Critical credential disclosure:** A Knowledge Base query result rendered an upstream LLM error whose request URL included a saved provider credential. The credential is intentionally redacted here. Rotate the affected provider credential, then sanitize provider exceptions before they can reach UI responses, chat history, logs, or RAG indexing.
2. **RAG contamination:** A query for fictional Cedarbrook Pumps returned unrelated Acme Corp and TestCo content, including unsupported market-size assumptions. The index also displayed 72 documents, many named `Untitled.md` with one node. Treat deal/workspace isolation and index provenance as release blockers.
3. **Planner coverage:** The user asked for exactly three workstreams, but the latest post-fix plan contained only financial-record and valuation tasks; market research was omitted. It was cancelled before execution.
4. **Historical analysis failure (2026-10-02, not rerun):** A prior UI run ignored supplied financial figures, mislabeled the fictional industrial pump manufacturer as technology, and valuation then failed for insufficient data. The current prompt-propagation fix has a unit test, but a new correct plan is required before live UI confirmation.
5. **Pipeline consistency:** Dashboard counters and the list cardinality are inconsistent (64 total pipeline deals versus a rendered list indicating 1,099 items); multiple old/in-progress and duplicate test records are visible.
6. **Reports are not validated for successful delivery:** Empty-state export errors are surfaced, but DOCX/structured report quality, contents, and layout remain unverified without a successful, evidence-grounded analysis.

## Test Cases

### App Shell and Navigation

| ID | User-facing test | Expected result | Observed result | Status |
|---|---|---|---|---|
| UAT-001 | Open the app at the configured UI URL | Main application renders without a blank screen | DealForge UI rendered after starting the frontend | PASS |
| UAT-002 | Check application identity | DealForge AI title is visible | Browser title showed “DealForge AI” | PASS |
| UAT-003 | Open Chat navigation | Chat view is reachable | Chat view loaded with conversation list and composer | PASS |
| UAT-004 | Open Dashboard navigation | Overview and pipeline load | Overview, metrics, charts, and pipeline loaded | PASS |
| UAT-005 | Open Task Board navigation | Task board loads | Task Board and pipeline creation form loaded | PASS |
| UAT-006 | Open Knowledge navigation | Knowledge Base loads | RAG status, indexed documents, and query tester loaded | PASS |
| UAT-007 | Open Settings navigation | Settings load | Provider, model, API, and routing controls loaded | PASS |
| UAT-008 | Start New Analysis | A clean analysis composer is available | UI moved to New Analysis with an empty composer | PASS |
| UAT-009 | Use New Deal from the shell | User reaches the new-analysis flow | Control opened New Analysis | PASS |
| UAT-010 | Compare dashboard totals with pipeline list count | Totals and displayed list cardinality should reconcile | Dashboard showed 64 total deals while list indicated 1,099 items | FAIL |

### Chat and Orchestration

| ID | User-facing test | Expected result | Observed result | Status |
|---|---|---|---|---|
| UAT-011 | Load saved conversation history | Existing chats are listed and selectable | History loaded with prior UAT conversations | PASS |
| UAT-012 | Select an existing conversation | Selected conversation content appears | Cedarbrook case opened with its prompt and plan history | PASS |
| UAT-013 | Enter synthetic case data in the composer | Input accepts and displays the supplied text | Synthetic case prompt appeared in the composer and message | PASS |
| UAT-014 | Submit a new analysis prompt | UI transitions into a planning state | UI showed Brainstorming/Analyzing before response | PASS |
| UAT-015 | Submit an underspecified prompt | UI asks a clarification question or clearly explains assumptions | Clarification round asked about objective/structure | PASS |
| UAT-016 | Skip remaining clarification questions | Planning proceeds without silently claiming answers | Skip action proceeded to plan generation | PASS |
| UAT-017 | Review a generated task plan | Plan is visible and awaits approval | “Awaiting your approval to run” was shown | PASS |
| UAT-018 | Cancel a plan that should not run | No agents execute after cancellation | UI confirmed plan saved and execution cancelled; no agents were run | PASS |
| UAT-019 | Request exactly three enumerated workstreams | Financial, market, and valuation work are all represented | Post-fix plan contained finance and valuation but omitted the explicit market workstream | FAIL |
| UAT-020 | Confirm user-provided facts reach agent execution | Supplied values remain available to the assigned agent | Blocked: the plan omitted a workstream and was not approved. Historical 2026-10-02 run said no financial data was supplied; retest required after planner coverage is fixed | BLOCKED |

### Task Board and Deal Pipeline

| ID | User-facing test | Expected result | Observed result | Status |
|---|---|---|---|---|
| UAT-021 | Load the Task Board | Header and task pipeline are visible | Task Board loaded successfully | PASS |
| UAT-022 | Inspect new-pipeline form labels | Deal/company identifier and company name are clearly labeled | Both fields were visible and labeled | PASS |
| UAT-023 | Leave the task form empty | Generate Tasks remains disabled | Button was disabled | PASS |
| UAT-024 | Enter an identifier without a company name | Form state is predictable and safe | Generate Tasks became enabled with only the ID populated | PASS |
| UAT-025 | Clear the identifier | Generate Tasks returns to disabled | Clearing the field disabled the button again | PASS |
| UAT-026 | Load existing task lists | Existing tasks and statuses render | Task cards and draft/approved/completed labels rendered | PASS |
| UAT-027 | Inspect the plan approval boundary | Draft plans require explicit approval before execution | Draft records exposed Approve; approved records exposed Execute All | PASS |
| UAT-028 | Open deal details from Dashboard | Selected deal opens with status and evidence summary | Deal detail opened with score, evidence, status, agent, and report sections | PASS |
| UAT-029 | Inspect historical deal/task cleanliness | Test records should be distinguishable and not overwhelm current work | Numerous repeated Cedarbrook, Northwind, TestCo, and other historical rows appeared; many old records remain in progress | FAIL |
| UAT-030 | Continue an opened deal in Chat | User can return to the originating chat | Continue in Chat returned to the related conversation | PASS |

### Knowledge Base and Retrieval

| ID | User-facing test | Expected result | Observed result | Status |
|---|---|---|---|---|
| UAT-031 | Open Knowledge Base | RAG status, document list, and query tester render | All three sections rendered | PASS |
| UAT-032 | Read RAG availability status | UI presents an understandable operational state | UI showed Local Self-hosted tree index and Operational status | PASS |
| UAT-033 | Read indexed document count | UI count is visible and consistent with list | UI reported 72 indexed documents | PASS |
| UAT-034 | Inspect local-directory indexing controls | Folder input and Start Indexing control are available | Both controls were visible; no folder was submitted | PASS |
| UAT-035 | Submit a benign synthetic query | Query field accepts text and returns a result state | Cedarbrook market query returned five matches | PASS |
| UAT-036 | Inspect result provenance | Results identify source document/chunk metadata | Result cards displayed filename/chunk identifiers | PASS |
| UAT-037 | Check relevance to the requested company | Cedarbrook query returns only relevant Cedarbrook evidence or an empty result | Results included unrelated Acme Corp and TestCo material | FAIL |
| UAT-038 | Check deal/workspace isolation | One deal’s query must not expose another deal’s content | Cross-company stale content appeared in Cedarbrook results | FAIL |
| UAT-039 | Check unsupported-claim handling in retrieval | Unsupported market figures are excluded or visibly marked unverified | Retrieved content included assumed TAM/SAM/SOM and growth estimates | FAIL |
| UAT-040 | Check provider-error redaction | Provider errors must not expose API credentials | UI rendered a provider request URL containing a saved credential; value redacted from this study | FAIL |

### Provider, Model, and Routing Settings

| ID | User-facing test | Expected result | Observed result | Status |
|---|---|---|---|---|
| UAT-041 | Load Settings provider overview | Provider status cards render | Gemini, OpenAI, OpenRouter, Mistral, NVIDIA, Vertex, Claude, Groq, and local sections rendered | PASS |
| UAT-042 | Inspect configured-key indicators | UI indicates saved keys without requiring the user to reveal them | Status cards showed active/no-key states; no reveal control was used | PASS |
| UAT-043 | Inspect API key form behavior | Saved secrets should be represented without displaying their full value | Fields showed saved/new-key guidance; values were not exposed through UI actions | PASS |
| UAT-044 | Load model catalogs | Catalogs resolve to selectable models or a clear unavailable state | Gemini, OpenRouter, Mistral, and NVIDIA catalogs showed entries | PASS |
| UAT-045 | Inspect OpenRouter catalog size | A non-empty model catalog is available when configured | UI showed 465 models and a selected model | PASS |
| UAT-046 | Inspect Gemini model catalog | Available Gemini models are listed | UI showed 33 models | PASS |
| UAT-047 | Inspect LM Studio connectivity | Local service and installed models are shown | UI showed Running with 24 models | PASS |
| UAT-048 | Check configured LM Studio model | Selected model matches the configured local model | `qwen/qwen3.5-9b` was selected and present in the model list | PASS |
| UAT-049 | Inspect agent-provider routing choices | Each route control exposes supported provider choices | Provider selector included cloud and local providers | PASS |
| UAT-050 | Inspect integrations with no configured credentials | Unavailable integrations are labeled and test actions are gated | Several finance/search integrations showed Not initialized; key-required test buttons were disabled | PASS |

### Reports and Export

| ID | User-facing test | Expected result | Observed result | Status |
|---|---|---|---|---|
| UAT-051 | Open Reports Hub for a deal without completed analysis | Empty state explains that no report exists | “No reports generated yet” was shown | PASS |
| UAT-052 | Check Generate All Reports in the empty state | Report generation is gated until usable analysis exists | Generate All Reports was disabled | PASS |
| UAT-053 | Request structured export with no saved analysis | UI reports the missing precondition rather than fabricating data | Export failed with “No saved analysis results are available for this deal” | PASS |
| UAT-054 | Request Word export with no saved analysis | UI reports the missing precondition rather than producing a blank report | Same missing-results error was shown | PASS |
| UAT-055 | Verify status wording after a cancelled plan/export failure | Status should distinguish cancellation/export errors from agent-run failures | Header displayed “Errors in run” even though the latest plan was cancelled before execution | FAIL |
| UAT-056 | Generate a DOCX from successful, evidence-grounded analysis | Word deliverable contains correct curated findings and citations | Not run; no valid completed analysis was available | NOT RUN |
| UAT-057 | Inspect generated DOCX layout and page breaks | Report renders cleanly with no clipping or blank pages | Not run; no valid DOCX was generated | NOT RUN |
| UAT-058 | Validate structured export schema and null handling | JSON is machine-readable and preserves unknown values as null/unknown | Not run; current deal has no saved analysis results | NOT RUN |
| UAT-059 | Verify final synthesis follows the complete three-stage DAG | Market and financial work run independently; valuation depends only on financial records | Not run; planner omitted the market task, so approval was withheld | NOT RUN |
| UAT-060 | Verify successful export file contents against on-screen analysis | Downloaded data/report matches displayed values and source references | Not run; no completed analysis is available | NOT RUN |

## Historical Evidence Not Recounted as a Fresh Pass

The previous executed Cedarbrook run visible in the UI (2026-10-02) reported 2/3 tasks successful and 1 failed. The financial agent said the supplied figures were absent, the market agent labeled the fictional pump company as technology, and the valuation agent returned insufficient data. The executive brief correctly marked the result incomplete and required human review, but the two “successful” agent results were not substantively reliable. The current UI session did not approve another analysis because the generated plan again omitted market research.

## Recommended Release Gates

1. Rotate the credential exposed in the provider error; remove secrets and full request URLs from exceptions, logs, chat messages, and indexed content.
2. Enforce explicit workstream coverage after LLM planning; reject plans missing requested tracks and show a clear correction path.
3. Confirm user prompt/fact propagation with a fresh local-only execution and deterministic metric assertions before enabling report generation.
4. Make RAG tenant/deal scoped, deduplicate/index documents with stable names, and add retrieval relevance and prompt-injection tests.
5. Reconcile dashboard totals, historical statuses, and task-board rows; provide filtering/pagination that makes stale records actionable.
6. Run provider tests only in a controlled environment with approved credentials and a known cost budget; separately validate DOCX/JSON outputs after a successful analysis.

## Remediation Follow-up (2026-10-07)

Code-level fixes now redact common provider URL credentials (`key`, `token`, and API-key parameters) from indexed/searchable content and return generic errors from Knowledge Base query endpoints. Local retrieval now constrains multi-word named entities and applies a minimum relevance score; the Query Tester exposes an optional deal filter. The planner now restores a clearly requested financial, market, valuation, legal, or risk workstream when an exact-count plan omits it. Chat's header error indicator is limited to agent errors in the current user turn.

Follow-up verification: the updated UI returned “No results found” for “Cedarbrook Pumps market research evidence” rather than unrelated company records. Provider-error text is redacted at retrieval, and known LLM endpoint failure artifacts are excluded from evidence results. The maintained backend suite passed 213 tests, and the frontend production build succeeded. The full backend run still has 18 failing legacy tests under `backend/tests/evals` involving stale agent expectations, constructors, and missing fixtures/schema; these failures are not represented as passes.

These changes do **not** complete the remaining release gates: the exposed provider key still needs rotation by its owner; polluted legacy documents still need review/quarantine and deal metadata; successful end-to-end analysis and DOCX/JSON artifact quality remain unverified. UAT remains **Fail for client-ready delivery** pending those controls and a fresh, approved full-flow test.

**UAT decision:** Fail for client-ready delivery. Do not send generated diligence outputs to a client until the credential exposure, retrieval isolation, planner coverage, and evidence-grounding gates are cleared and a fresh end-to-end run passes.
