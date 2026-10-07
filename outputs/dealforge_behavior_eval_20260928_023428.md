# DealForge Behavior and AI Eval

- Timestamp: 2026-09-28T02:36:28
- Frontend: http://localhost:3000
- API: http://127.0.0.1:8005
- Score: 5/7 (71%)

## Results

- PASS `frontend_reachable` (2.076s): HTTP 200; 570 bytes
- PASS `api_health` (0.404s): HTTP 200; status=healthy
- PASS `agent_registry` (0.001s): HTTP 200; registered_agents=29
- PASS `model_routing_health` (0.001s): HTTP 200; providers=30; unhealthy=[]
- PASS `empty_prompt_guardrail` (0.001s): HTTP 200; error=invalid_prompt
- FAIL `planning_quality` (95.622s): HTTP 200; 15 tasks; coverage=['financial', 'market', 'legal', 'risk', 'valuation', 'memo']; unknown_agents=['complex_reasoning_agent', 'data_curator_agent', 'report_architect_agent']
- FAIL `financial_agent_usefulness` (21.596s): HTTP 200; success=True; content=False; useful_hits=[]

## Key Interpretation

- A green health endpoint only proves the server is alive; the agent usefulness check verifies whether the AI layer returns usable content.
- Planning quality includes both task coverage and whether assigned agents are executable registered agent IDs.
- Raw payloads are preserved in the JSON report for debugging.
