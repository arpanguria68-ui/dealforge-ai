# Agentic eval run2 — WARN (10/12, 893s)

| check | ok | detail |
|---|---|---|
| settings_read | ✅ |  |
| settings_route_all_ollama | ✅ | 30 agents |
| deal_create | ✅ | 0s |
| doc_upload_indexed | ✅ | {'status': 'indexed', 'index_id': 'idx_674b910c', 'document_id': '674b910c-64e0-48fb-bb54-2288707b146b', 'total_pages': 1, 'total_chunks': 1} |
| clarify | ✅ | 4 questions in 13s |
| plan | ✅ | 12 tasks in 6s |
| execute_all | ❌ | 11/12 ok |
| execute_real_content | ❌ | 0 tasks with >200 chars from ollama |
| rag_retrieval | ✅ | 1 hits in 0s |
| rag_citations | ✅ | 1 cited |
| laya_triage | ✅ | backend=lmstudio |
| laya_tier | ✅ | tier=simple backend=lmstudio |
